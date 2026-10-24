#!/usr/bin/env python3
"""The meaning audit: candidates by vectors, verdicts carried across runs, the decision page.

Runs under the plugin venv (numpy, scikit-learn); started with any other python it re-runs
itself there. The model is never loaded: the index already holds a vector for every chunk.

  candidates  find candidates, write batches only for those no reader has judged yet
  merge       fold reader answers into verdicts.json, refusing answers that do not match
  page        build the decision page for what changes meaning
"""

from __future__ import annotations

import os
import sys

from semantic import VENV_PYTHON

try:
    import numpy as np
except ImportError:
    if VENV_PYTHON.is_file() and os.path.realpath(sys.executable) != os.path.realpath(VENV_PYTHON):
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])
    raise SystemExit(f"audit_semantic.py needs numpy from the plugin venv ({VENV_PYTHON}); "
                     "run setup.py --install-semantic")

import argparse
import collections
import datetime
import hashlib
import json
import re
import shutil
from pathlib import Path

import audit_common as ac
from common import DEFAULT_DB, connect_ro, vault_from_config
from semantic import vector_db_path

PLUGIN = Path(__file__).resolve().parent.parent
READERS = PLUGIN / "skills" / "audit" / "readers"
SITE = PLUGIN / "skills" / "audit" / "site"

POOL_TYPES = ("atom", "godnote", "source", "lead")
GROUP_DISTANCE = 0.55   # average-linkage cut on centred cosine distance
GROUP_MIN = 4
COVERED = 0.5           # a godnote linking this share of a group already owns it
PAIR_COS = 0.6
BATCH = {"group": 20, "cross": 20, "same": 40, "stale": 20}

REPLACES = re.compile(r"(?i)(supersedes|заменяет|отменяет|отменённое|отмененное|устарел[аио]?\s+из-за|corrects)"
                      r"\b[^\n]*?\[\[([^\]|#]+)")
SELF_STALE = re.compile(r"(?im)^\s*>?\s*[⚠️\s]*\**\s*устарело\b")
CORRECTION = re.compile(r"(?m)^## (Поправка\b.*)$")
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря")


def load(db: Path, vault: Path):
    """Every note, with its vector (normalised mean of its chunks) when the index has one."""
    vectors_path = vector_db_path(db)
    if not vectors_path.is_file():
        raise SystemExit(f"no vectors at {vectors_path}: run index.py --embed first")
    vcon = connect_ro(vectors_path)
    vec = {h: np.frombuffer(b, dtype=np.float16).astype(np.float32)
           for h, b in vcon.execute("SELECT hash, vector FROM chunk_vectors")}
    vcon.close()
    con = connect_ro(db)
    notes = {}
    for nid, path, title, typ, kind, project, date, died in con.execute(
            "SELECT id,path,title,type,kind,project,date,died FROM notes"):
        notes[nid] = {"id": nid, "path": path, "title": title, "type": (typ or "").casefold(),
                      "kind": (kind or "").casefold(), "project": project or "", "date": date or "",
                      "died": died or "", "file": str(vault / path), "chunks": []}
    for nid, body in con.execute("SELECT note_id, body FROM chunks ORDER BY note_id, ord"):
        v = vec.get(hashlib.sha256(body.encode()).hexdigest())
        if v is not None:
            notes[nid]["chunks"].append(v)
    for n in notes.values():
        chunks = n.pop("chunks")
        if chunks:
            m = np.mean(chunks, 0)
            n["v"] = m / np.linalg.norm(m)
    neighbours = collections.defaultdict(set)
    for s, d in con.execute("SELECT src_note_id, dst_note_id_or_null FROM links WHERE dst_note_id_or_null IS NOT NULL"):
        neighbours[s].add(d)
        neighbours[d].add(s)
    con.close()
    return notes, neighbours


def pool_matrix(notes: dict):
    """Live notes of the pool types, and their vectors centred on the mean of that set.

    Granite's raw cosines sit packed near 0.85, so nearest neighbours mean little until the
    shared direction is taken out. Leads count toward the centre but are not candidates.
    """
    live = [n for n in notes.values() if "v" in n and n["type"] in POOL_TYPES and not n["died"]]
    if not live:
        raise SystemExit("no live note has a vector: run index.py --embed first")
    centre = np.mean([n["v"] for n in live], 0)
    pool = [n for n in live if n["type"] != "lead"]
    m = np.stack([n["v"] for n in pool]) - centre
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    return pool, m


def ref(n: dict) -> dict:
    return {k: n[k] for k in ("title", "project", "date", "path", "file")}


def find_groups(pool, m, neighbours) -> list[dict]:
    from sklearn.cluster import AgglomerativeClustering
    atoms = [i for i, n in enumerate(pool) if n["type"] == "atom"]
    gods = [i for i, n in enumerate(pool) if n["type"] == "godnote"]
    if len(atoms) < GROUP_MIN:
        return []
    dist = 1 - m[atoms] @ m[atoms].T
    np.fill_diagonal(dist, 0)
    labels = AgglomerativeClustering(n_clusters=None, metric="precomputed", linkage="average",
                                     distance_threshold=GROUP_DISTANCE).fit_predict(np.clip(dist, 0, None))
    clusters = collections.defaultdict(list)
    for i, label in zip(atoms, labels):
        clusters[label].append(i)
    out = []
    for members in clusters.values():
        if len(members) < GROUP_MIN:
            continue
        ids = {pool[i]["id"] for i in members}
        if any(len(neighbours[pool[g]["id"]] & ids) / len(ids) >= COVERED for g in gods):
            continue
        centre = m[members].mean(0)
        members.sort(key=lambda i: -float(m[i] @ centre))
        # Three, not one: the godnote that owns the topic is often second or third by meaning,
        # and a reader shown only the first concludes the topic has none.
        near = sorted(gods, key=lambda g: -float(m[g] @ centre))[:3]
        out.append({"kind": "group", "key": ac.finding_key("group", [pool[i]["title"] for i in members]),
                    "members": [ref(pool[i]) for i in members],
                    "nearest_godnotes": [{**ref(pool[g]), "links_to_group": len(neighbours[pool[g]["id"]] & ids)}
                                         for g in near]})
    return out


def find_pairs(pool, m, neighbours) -> tuple[list[dict], list[dict]]:
    cross, same = [], []
    sims = m @ m.T
    for a, b in zip(*np.where(np.triu(sims > PAIR_COS, 1))):
        a, b = int(a), int(b)
        na, nb = pool[a], pool[b]
        if nb["id"] in neighbours[na["id"]]:
            continue
        is_cross = na["project"] and nb["project"] and na["project"] != nb["project"]
        item = {"sim": round(float(sims[a, b]), 3), "a": ref(na), "b": ref(nb)}
        # Across projects a link is "unexpected" only with no path at all; inside one project a
        # duplicate or a dispute hides just as well behind a shared hub.
        if is_cross and not neighbours[na["id"]] & neighbours[nb["id"]]:
            cross.append({"kind": "cross", "key": ac.finding_key("cross", [na["title"], nb["title"]]), **item})
        elif not is_cross:
            same.append({"kind": "same", "key": ac.finding_key("same", [na["title"], nb["title"]]), **item})
    cross.sort(key=lambda x: -x["sim"])
    same.sort(key=lambda x: -x["sim"])
    return cross, same


def find_stale(notes: dict, vault: Path) -> list[dict]:
    """Notes still alive for search although the vault says they are not quite right.

    Three signs: another note says it replaces this one; the note marks itself outdated; the
    note carries a `## Поправка` section, so its title may now claim the opposite of its body.
    """
    by_name = {}
    for n in notes.values():
        by_name[n["title"].casefold()] = n
        by_name[Path(n["path"]).stem.casefold()] = n
    replaced, marked, corrected = [], [], []
    for n in notes.values():
        if "/sessions/" in "/" + n["path"]:
            continue
        try:
            text = (vault / n["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            print(f"stale: cannot read {n['path']}: {error}", file=sys.stderr)
            continue
        for match in REPLACES.finditer(text):
            old = by_name.get(match.group(2).strip().casefold())
            if old and not old["died"] and old["id"] != n["id"]:
                replaced.append((old, n, match.group(1).lower(), None))
        if n["died"]:
            continue
        if SELF_STALE.search(text):
            marked.append((n, None, "сама пишет «устарело»", None))
        heading = CORRECTION.search(text)
        if heading:
            corrected.append((n, None, "поправка", heading.group(1).strip()))
    out, seen = [], set()
    for old, new, how, heading in replaced + marked + corrected:
        if old["path"] in seen:
            continue
        seen.add(old["path"])
        titles = [old["title"]] + ([new["title"]] if new else [])
        item = {"kind": "stale", "key": ac.finding_key("stale", titles), "how": how,
                "old": ref(old), "new": ref(new) if new else None}
        if heading:
            item["correction"] = heading
        out.append(item)
    return out


def prompt(kind: str, inp: Path, out: Path, vault: Path) -> str:
    text = (READERS / "common.txt").read_text(encoding="utf-8") + "\n" + (READERS / f"{kind}.txt").read_text(encoding="utf-8")
    return text.replace("INPUT", str(inp)).replace("OUTPUT", str(out)).replace("VAULT", str(vault))


def unmerged(batches: Path, verdicts: dict) -> list[str]:
    bad = []
    for ann in sorted(batches.glob("*.ann.json")):
        try:
            keys = {x.get("key") for x in json.loads(ann.read_text(encoding="utf-8"))}
        except (ValueError, AttributeError):
            keys = {None}
        if not keys <= verdicts.keys():
            bad.append(ann.name)
    return bad


def cmd_candidates(args) -> dict:
    vault = args.vault or vault_from_config()
    verdicts = ac.load_verdicts()
    run = ac.RUNS / datetime.date.today().isoformat()
    batches = run / "batches"
    if batches.is_dir():
        waiting = unmerged(batches, verdicts)
        if waiting:
            raise SystemExit(f"answers not merged yet: {', '.join(waiting)} in {batches}; run `merge` first")
        shutil.rmtree(batches)
    batches.mkdir(parents=True)
    notes, neighbours = load(args.db, vault)
    pool, m = pool_matrix(notes)
    cross, same = find_pairs(pool, m, neighbours)
    found = {"group": find_groups(pool, m, neighbours), "cross": cross, "same": same,
             "stale": find_stale(notes, vault)}
    ac.write_json(run / "candidates.json", {"date": run.name, "vault": str(vault), **found})
    summary = {"run": str(run), "total": {}, "carried": {}, "to_read": {}, "batches": []}
    for kind in ac.KINDS:
        todo = [c for c in found[kind] if c["key"] not in verdicts]
        summary["total"][kind] = len(found[kind])
        summary["carried"][kind] = len(found[kind]) - len(todo)
        summary["to_read"][kind] = len(todo)
        for n, start in enumerate(range(0, len(todo), BATCH[kind])):
            inp, out = batches / f"{kind}{n}.in.json", batches / f"{kind}{n}.ann.json"
            ac.write_json(inp, todo[start:start + BATCH[kind]])
            text_path = batches / f"{kind}{n}.prompt.txt"
            text_path.write_text(prompt(kind, inp, out, vault), encoding="utf-8")
            summary["batches"].append({"kind": kind, "items": len(todo[start:start + BATCH[kind]]),
                                       "prompt": str(text_path), "answer": str(out)})
    return summary


def cmd_merge(args) -> dict:
    run = args.run or ac.latest_run()
    batches = run / "batches"
    verdicts = ac.load_verdicts()
    errors, fresh, not_answered = [], {}, []
    for inp in sorted(batches.glob("*.in.json")):
        ann = inp.with_name(inp.name.replace(".in.json", ".ann.json"))
        if not ann.is_file():
            not_answered.append(ann.name)
            continue
        asked = {c["key"]: c["kind"] for c in json.loads(inp.read_text(encoding="utf-8"))}
        try:
            answers = json.loads(ann.read_text(encoding="utf-8"))
            got = [a["key"] for a in answers]
        except (ValueError, TypeError, KeyError) as error:
            errors.append(f"{ann.name}: not a list of objects with a key ({type(error).__name__})")
            continue
        foreign = sorted(set(got) - asked.keys())
        missing = sorted(asked.keys() - set(got))
        if foreign or missing or len(got) != len(set(got)):
            errors.append(f"{ann.name}: keys do not match {inp.name}"
                          f" (not asked: {foreign}, not answered: {missing}, repeated: {len(got) - len(set(got))})")
            continue
        for a in answers:
            fresh[a["key"]] = {**a, "kind": asked[a["key"]], "run": run.name}
    if errors:
        raise SystemExit("merge refused, verdicts.json unchanged:\n" + "\n".join(errors))
    verdicts.update(fresh)
    ac.write_json(ac.VERDICTS, verdicts)
    return {"merged": len(fresh), "not_answered": not_answered, "verdicts": len(verdicts)}


def russian_date(iso: str) -> str:
    d = datetime.date.fromisoformat(iso)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def cmd_page(args) -> dict:
    from sklearn.manifold import TSNE
    run = args.run or ac.latest_run()
    cands = json.loads((run / "candidates.json").read_text(encoding="utf-8"))
    vault = args.vault or Path(cands["vault"])
    sorted_ = ac.sort_findings(cands, ac.load_verdicts())
    notes, neighbours = load(args.db, vault)
    pool, m = pool_matrix(notes)
    xy = TSNE(n_components=2, metric="cosine", perplexity=min(30.0, len(pool) - 1.0), init="pca",
              random_state=7).fit_transform(m)
    xy = (xy - xy.min(0)) / np.where(np.ptp(xy, 0) > 0, np.ptp(xy, 0), 1)
    clean = lambda t: t.split(" — ", 1)[-1]
    shown = [{"t": clean(n["title"]), "p": n["project"], "k": n["type"] if n["type"] == "godnote" else n["kind"],
              "x": round(float(x), 4), "y": round(float(y), 4)} for n, (x, y) in zip(pool, xy)]
    index = {n["path"]: i for i, n in enumerate(pool)}
    by_path = {n["path"]: n for n in notes.values()}

    def at(note: dict) -> int:
        """A finding may name a note the map does not hold (a died or vectorless one): it gets an off-map dot."""
        if note["path"] not in index:
            index[note["path"]] = len(shown)
            shown.append({"t": clean(note["title"]), "p": note["project"], "k": "", "x": None, "y": None})
        return index[note["path"]]

    groups, disputes = [], []
    rank = {"устарело": 0, "заголовок устарел": 0, "спор": 1, "дубль": 2}
    for c, v in sorted_["page"]:
        if c["kind"] == "group":
            ids = [at(x) for x in c["members"]]
            node = {x["path"]: by_path[x["path"]]["id"] for x in c["members"] if x["path"] in by_path}
            groups.append({"key": c["key"], "name": v.get("name") or "", "thesis": v.get("thesis") or "",
                           "members": ids, "badge": "созрело до правила" if v.get("ripe")
                           else "уже в правилах" if v.get("rule_file") else "новая godnote",
                           "linked_pairs": sum(1 for x in node.values() for y in node.values()
                                               if x < y and y in neighbours[x])})
            continue
        verdict = ac.page_verdict(c, v)
        old, new = ac.sides(c, v)
        side_a = c["a"]["path"] if c["kind"] == "same" else None
        letter = lambda s: ("a" if s["path"] == side_a else "b") if side_a else None
        if new is None:
            new_side = {"id": None, "when": "пометка в самой заметке" if c["how"] != "поправка" else c.get("correction", "поправка"),
                        "say": v.get("new_say", "") + (f" Новый заголовок: «{v['new_title']}»." if v.get("new_title") else "")}
        else:
            new_side = {"id": at(new), "when": new["date"][:10], "say": new["says"], "side": letter(new)}
        disputes.append({"key": c["key"], "topic": v.get("topic", ""), "verdict": verdict, "fix": v.get("fix", ""),
                         "ordered": c["kind"] == "stale" or bool(v.get("old")),
                         "old": {"id": at(old), "when": old["date"][:10], "say": old["says"], "side": letter(old)},
                         "new": new_side})
    groups.sort(key=lambda g: (g["badge"] != "созрело до правила", -len(g["members"])))
    disputes.sort(key=lambda d: rank[d["verdict"]])
    data = {"date": russian_date(cands["date"]), "notes": shown,
            "stats": {"notes": len(pool), "groups": len(groups), "links": len(sorted_["safe"]), "disputes": len(disputes)},
            "dropped": sorted_["dropped"], "godnotes": groups, "disputes": disputes}
    site = run / "site"
    if site.is_dir():
        shutil.rmtree(site)
    shutil.copytree(SITE, site)
    (site / "data.js").write_text("window.AUDIT = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")
    return {"site": str(site / "index.html"), **data["stats"], "unread": sorted_["unread"], "dropped": sorted_["dropped"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("candidates", "merge", "page"):
        p = sub.add_parser(name)
        p.add_argument("--db", type=Path, default=DEFAULT_DB)
        p.add_argument("--vault", type=Path)
        if name != "candidates":
            p.add_argument("--run", type=Path, help="run folder; the latest one by default")
    args = parser.parse_args()
    done = {"candidates": cmd_candidates, "merge": cmd_merge, "page": cmd_page}[args.cmd](args)
    print(json.dumps(done, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
