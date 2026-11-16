#!/usr/bin/env python3
"""Act on the meaning audit. Standard library only.

  --safe            add the wiki-links readers asked for: cross-project pairs they kept, pairs
                    where one note refines the other, cards of a group that an existing godnote
                    already carries. Links only, and the vault is in git, so this is reversible.
  --decisions DIR   read the page marks (an export of the `decisions` collection, one
                    <key>.json per finding) and write heavy.json: the work left for an agent.

Nothing else in the vault is ever written here. `--dry` prints what --safe would add.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import audit_common as ac
from common import vault_from_config

KEY = re.compile(r"^[glds]-[0-9a-f]{16}$")
# The vault writes its links section in either language; a second "## Links" under "## Связи" is clutter.
LINKS_SECTION = re.compile(r"^## (?:Links|Связи|Ссылки)[ \t]*(?:\r?\n|\Z)(?:[ \t]*\r?\n)*(?:[ \t]*- .*(?:\r?\n|\Z))*", re.M)


def with_link(text: str, target: str) -> str | None:
    """`text` with `- [[target]]` added at the end of its links list, or None if it already links there.

    The new lines end the way the file's own lines do, so a CRLF note stays CRLF.
    """
    if re.search(r"\[\[" + re.escape(target) + r"(\]\]|\||#)", text):
        return None
    nl = "\r\n" if "\r\n" in text else "\n"
    line = f"- [[{target}]]{nl}"
    m = LINKS_SECTION.search(text)
    if not m:
        return text.rstrip("\r\n") + nl + nl + "## Links" + nl + line
    rest = text[m.end():].lstrip("\r\n")
    return text[:m.end()].rstrip("\r\n") + nl + line + (nl + rest if rest else "")


def safe_links(cands: dict, verdicts: dict) -> tuple[list[tuple[dict, dict, str]], list[str]]:
    """(from, to, why) for every link the verdicts make safe, once each, and the godnotes a reader
    named but the run lacks. Two findings often ask for the same link; it is written once."""
    out, missing, seen = [], [], set()
    for c, v, why in ac.sort_findings(cands, verdicts)["safe"]:
        if c["kind"] == "group":
            god = ac.godnote_named(c, v.get("extend"))
            if god is None:
                missing.append(v.get("extend"))
                continue
            links = [(m, god) for m in c["members"] if m["path"] != god["path"]]
        else:
            links = [(c["a"], c["b"]), (c["b"], c["a"])]
        for src, dst in links:
            if (src["path"], dst["path"]) not in seen:
                seen.add((src["path"], dst["path"]))
                out.append((src, dst, why))
    return out, missing


def git(vault: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True)


def vault_unclean(vault: Path) -> str | None:
    """Why --safe may not write here, or None. Undo by git is the promise, so git must hold the vault
    and nothing of the user's may be waiting uncommitted to be swept into our commit."""
    inside = git(vault, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return f"{vault} is not in a git repository, so the links could not be undone; commit the vault to git first"
    status = git(vault, "status", "--porcelain", "--", ".", ":(exclude).obsidian")
    if status.returncode != 0:
        return f"git status failed in {vault}: {status.stderr.strip()}"
    if status.stdout.strip():
        changed = [line[3:] for line in status.stdout.splitlines()]
        return (f"{vault} has uncommitted changes ({', '.join(changed[:5])}{' …' if len(changed) > 5 else ''}); "
                "commit or stash them first, so the audit's commit holds only its links")
    return None


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.audit-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def cmd_safe(run: Path, vault: Path, dry: bool) -> dict:
    cands = json.loads((run / "candidates.json").read_text(encoding="utf-8"))
    verdicts = ac.load_verdicts()
    if not dry and (why := vault_unclean(vault)):
        raise SystemExit(f"--safe refused: {why}. `--dry` shows the list without writing.")
    links, missing = safe_links(cands, verdicts)
    by_file = {}
    for src, dst, why in links:
        by_file.setdefault(src["path"], []).append((dst, why))
    added, there, gone, target_gone, unreadable, changed, kinds = [], 0, [], [], [], [], Counter()
    new_sha = {}
    for rel, targets in by_file.items():
        path = vault / rel
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except FileNotFoundError:
            gone.append(rel)
            continue
        except (OSError, UnicodeDecodeError) as error:
            unreadable.append(f"{rel}: {type(error).__name__}")
            continue
        here = []
        for dst, why in targets:
            if not (vault / dst["path"]).is_file():
                target_gone.append(f"{rel} -> {dst['path']}")
                continue
            linked = with_link(text, Path(dst["path"]).stem)
            if linked is None:
                there += 1
                continue
            text = linked
            here.append({"file": rel, "link": Path(dst["path"]).stem, "why": why})
        if not here:
            continue
        if not dry:
            try:
                same = path.read_bytes() == raw
            except OSError:
                same = False
            if not same:
                changed.append(rel)
                continue
            data = text.encode("utf-8")
            write_atomic(path, data)
            new_sha[rel] = (hashlib.sha256(raw).hexdigest(), hashlib.sha256(data).hexdigest())
        added += here
        for item in here:
            kinds[item["why"]] += 1
    commit = None
    if new_sha:
        refresh_shas(verdicts, new_sha)
        commit = commit_links(vault, sorted(new_sha), f"audit {run.name}: {len(added)} links")
    return {"dry": dry, "links_added": len(added), "already_there": there, "by_kind": dict(kinds),
            "godnote_not_found": missing, "file_gone": sorted(gone), "target_gone": target_gone,
            "unreadable": unreadable, "changed_while_running": changed, "commit": commit, "added": added}


def refresh_shas(verdicts: dict, new_sha: dict) -> None:
    """A link we added does not change what a note says, so verdicts that read it stay standing."""
    touched = False
    for v in verdicts.values():
        for rel, (before, after) in new_sha.items():
            if v.get("sha", {}).get(rel) == before:
                v["sha"][rel] = after
                touched = True
    if touched:
        ac.write_json(ac.VERDICTS, verdicts)


def commit_links(vault: Path, files: list[str], message: str) -> str:
    done = git(vault, "commit", "--quiet", "-m", message, "--", *files)
    if done.returncode != 0:
        raise SystemExit(f"links written but not committed in {vault}: {done.stderr.strip() or done.stdout.strip()}; "
                         f"files: {', '.join(files)}")
    return git(vault, "rev-parse", "--short", "HEAD").stdout.strip()


def _strict_json(path: Path):
    """JSON with duplicate object keys refused: a repeated key is two answers, not one."""
    def pairs(items):
        seen = set()
        for key, _ in items:
            if key in seen:
                raise ValueError(f"duplicate key {key!r}")
            seen.add(key)
        return dict(items)
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)


def _mark(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    v = value.get("v")
    c = value.get("c", "")
    if v not in ("yes", "no", None) or not isinstance(c, str):
        return None
    return {"v": v, "c": c.strip()}


def read_marks(source: Path, *, expected_run_id: str,
               allowed_keys: set[str]) -> tuple[dict, list[str]]:
    """Validated marks from the page's JSON export, or from a legacy per-key directory.

    Everything is checked before a single verdict is written. A file export names the page
    it came from; a directory does not, so it is accepted only when the caller explicitly
    bound it to the current page after showing the user that mapping.
    """
    bad: list[str] = []
    if not source.exists():
        return {}, [f"no such decisions path: {source}"]
    if source.is_dir():
        marks: dict[str, dict] = {}
        stems: dict[str, list[Path]] = {}
        for path in sorted(source.rglob("*.json")):
            if not KEY.match(path.stem):
                continue
            stems.setdefault(path.stem, []).append(path)
        for stem, paths in stems.items():
            if len(paths) > 1:
                bad.append(f"{stem}: repeated legacy mark files: {', '.join(str(p) for p in paths)}")
                continue
            try:
                body = _strict_json(paths[0])
            except (OSError, ValueError) as error:
                bad.append(f"{paths[0]}: {error}")
                continue
            if not isinstance(body, dict):
                bad.append(f"{paths[0]}: not an object")
                continue
            extra = set(body) - {"v", "c", "at"}
            if extra:
                bad.append(f"{paths[0]}: unknown fields: {', '.join(sorted(extra))}")
                continue
            mark = _mark(body)
            if mark is None:
                bad.append(f"{paths[0]}: v must be yes/no/null and c a string")
                continue
            if mark["v"] or mark["c"]:
                marks[stem] = mark
        return marks, bad

    try:
        data = _strict_json(source)
    except (OSError, ValueError) as error:
        return {}, [f"{source}: {error}"]
    if not isinstance(data, dict):
        return {}, [f"{source}: not an object"]
    if data.get("version") != 1:
        return {}, [f"{source}: unsupported decisions version"]
    if data.get("run_id") != expected_run_id:
        return {}, [f"{source}: decisions belong to another page run "
                    f"({data.get('run_id')!r}, expected {expected_run_id!r})"]
    decisions = data.get("decisions")
    if not isinstance(decisions, dict):
        return {}, [f"{source}: decisions must be an object"]
    marks = {}
    for key, value in decisions.items():
        if key not in allowed_keys:
            bad.append(f"{source}: unknown finding key {key!r}")
            continue
        mark = _mark(value)
        if mark is None:
            bad.append(f"{source}: {key}: v must be yes/no/null and c a string")
            continue
        if mark["v"] or mark["c"]:
            marks[key] = mark
    return marks, bad


def _snapshot(run: Path) -> tuple[dict, str]:
    path = run / "page-snapshot.json"
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"no page snapshot in {run}: run the page command and show it to the user first")
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read page snapshot {path}: {error}") from error
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1 \
            or not isinstance(snapshot.get("shown"), list):
        raise SystemExit(f"unsupported page snapshot: {path}")
    return snapshot, ac.page_fingerprint(snapshot)


def _verified_page(run: Path, snapshot: dict, candidates: dict, verdicts: dict) -> dict:
    """Current candidates/verdicts must still be the page that was shown."""
    raw = (run / "candidates.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != snapshot.get("candidates_sha256"):
        raise SystemExit("candidates changed since the page was built; rebuild and show the page again")
    shown = snapshot["shown"]
    by_key = {c["key"]: c for kind in ac.KINDS for c in candidates.get(kind, [])}
    found = ac.sort_findings(candidates, verdicts)
    current = {}
    for c, v in found["page"]:
        current[c["key"]] = (ac.note_shas(c), {k: val for k, val in v.items() if k != "decided"})
    for c, v in found["decided"]:
        if v.get("decided", {}).get("run") == run.name:
            current[c["key"]] = (ac.note_shas(c), {k: val for k, val in v.items() if k != "decided"})
    expected = {item["key"]: (item.get("candidate"), item.get("verdict")) for item in shown}
    if current != expected:
        raise SystemExit("the page no longer matches current candidates/verdicts; rebuild and show it again")
    return by_key


def _check_notes(vault: Path, run: Path, snapshot: dict, marks: dict) -> None:
    """A marked finding whose notes changed on disk is stale, not a decision to apply."""
    shown = {item["key"]: item for item in snapshot["shown"]}
    stale = []
    for key in marks:
        item = shown.get(key)
        if item is None:
            continue
        for relative, expected in (item.get("candidate") or {}).items():
            if not expected:
                continue
            path = vault / relative
            try:
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                stale.append(relative)
                continue
            if actual != expected:
                stale.append(relative)
    if stale:
        raise SystemExit("these notes changed since the page was shown; rebuild the page first: "
                         + ", ".join(sorted(set(stale))))


def cmd_decisions(run: Path, vault: Path, source: Path, *, allow_unbound: bool) -> dict:
    if source.is_dir() and not allow_unbound:
        raise SystemExit("legacy decision folders are not bound to a page: reopen the page, download "
                         "its decisions file, or pass --allow-unbound-decisions --run RUN after "
                         "confirming the mapping with the user")
    snapshot, run_id = _snapshot(run)
    allowed = {item["key"] for item in snapshot["shown"]}
    marks, bad = read_marks(source, expected_run_id=run_id, allowed_keys=allowed)
    if bad:
        raise SystemExit("decisions refused, nothing was written:\n" + "\n".join(bad))
    cands = json.loads((run / "candidates.json").read_text(encoding="utf-8"))
    verdicts = ac.load_verdicts()
    by_key = _verified_page(run, snapshot, cands, verdicts)
    _check_notes(vault, run, snapshot, marks)

    # Work from the page the user saw, not from whatever the verdicts say now.
    ordered = []
    for item in snapshot["shown"]:
        candidate = by_key.get(item["key"])
        if candidate is not None:
            ordered.append((candidate, item["verdict"]))
    decided_now = [(c, v) for c, v in ac.sort_findings(cands, verdicts)["decided"]
                   if v.get("decided", {}).get("run") == run.name]
    seen = {c["key"] for c, _ in ordered}
    ordered.extend((c, v) for c, v in decided_now if c["key"] not in seen)
    page = {c["key"]: (c, v) for c, v in ordered}
    file = lambda note: str(vault / note["path"])
    heavy = {"pairs": [], "godnotes": [], "tasks": []}
    rejected = 0
    marks_not_on_page = sorted(set(marks) - set(page))
    for key, (c, v) in page.items():
        mark = marks.get(key, {"v": None, "c": ""})
        if mark["v"]:
            decide(verdicts, c, v, mark, run.name)
        if mark["v"] == "no":
            rejected += 1
            continue
        if mark["v"] != "yes":
            if mark["c"]:
                notes = c["members"] if c["kind"] == "group" else [n for n in ac.sides(c, v) if n]
                heavy["tasks"].append({"key": key, "about": v.get("name") or v.get("topic", ""),
                                       "comment": mark["c"], "files": [file(n) for n in notes]})
            continue
        if c["kind"] == "group":
            heavy["godnotes"].append({"key": key, "name": v.get("name"), "thesis": v.get("thesis"),
                                      "ripe": bool(v.get("ripe")), "rule_file": v.get("rule_file"),
                                      "members": [file(m) for m in c["members"]], "comment": mark["c"]})
            continue
        old, new = ac.sides(c, v)
        heavy["pairs"].append({"key": key, "verdict": ac.page_verdict(c, v), "topic": v.get("topic", ""),
                               "fix": v.get("fix", ""), "new_title": v.get("new_title"),
                               "old": {"file": file(old), "says": old["says"]},
                               "new": {"file": file(new), "says": new["says"]} if new else None,
                               "comment": mark["c"]})
    ac.write_json(run / "heavy.json", heavy)
    ac.write_json(ac.VERDICTS, verdicts)
    return {"heavy": str(run / "heavy.json"), "run_id": run_id,
            "pairs": len(heavy["pairs"]), "godnotes": len(heavy["godnotes"]),
            "tasks": len(heavy["tasks"]), "rejected": rejected,
            "unmarked": len(page) - len(marks.keys() & page.keys()),
            "marks_not_on_page": marks_not_on_page,
            "bad_marks": []}


def decide(verdicts: dict, candidate: dict, verdict: dict, mark: dict, run: str) -> None:
    """Record the user's yes/no on the finding, so it never returns to the page, and on "yes" also
    on the stale findings the agreed fix will raise. A comment alone is a task, not a decision."""
    verdicts[candidate["key"]] = {**verdict, "decided": {"v": mark["v"], "c": mark["c"], "run": run}}
    if mark["v"] != "yes":
        return
    for key in ac.follow_up_keys(candidate, verdict):
        later = verdicts.setdefault(key, {"key": key, "kind": "stale"})
        later.setdefault("decided", {"v": "yes", "c": "", "run": run, "via": candidate["key"]})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--safe", action="store_true")
    mode.add_argument("--decisions", type=Path)
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--allow-unbound-decisions", action="store_true")
    parser.add_argument("--vault", type=Path)
    parser.add_argument("--run", type=Path, help="run folder; the latest one by default")
    args = parser.parse_args()
    run = args.run or ac.latest_run()
    if args.vault:
        vault = args.vault
    elif args.run:
        vault = Path(json.loads((run / "candidates.json").read_text(encoding="utf-8"))["vault"])
    else:
        vault = vault_from_config()
    if args.safe:
        done = cmd_safe(run, vault, args.dry)
    else:
        if args.allow_unbound_decisions and not args.run:
            sys.exit("--allow-unbound-decisions requires an explicit --run")
        done = cmd_decisions(run, vault, args.decisions, allow_unbound=args.allow_unbound_decisions)
    print(json.dumps(done, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
