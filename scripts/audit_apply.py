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


def read_marks(folder: Path) -> tuple[dict, list[str]]:
    """{key: {"v": "yes"|"no"|None, "c": comment}} from an export folder, any depth, and the files
    that are not a mark: a broken export is reported, not a traceback."""
    marks, bad = {}, []
    for f in sorted(folder.rglob("*.json")):
        if not KEY.match(f.stem):
            continue
        try:
            body = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            bad.append(f"{f}: {type(error).__name__}")
            continue
        if not isinstance(body, dict):
            bad.append(f"{f}: not an object")
            continue
        marks[f.stem] = {"v": body.get("v") if body.get("v") in ("yes", "no") else None,
                         "c": str(body.get("c") or "").strip()}
    return marks, bad


def cmd_decisions(run: Path, vault: Path, folder: Path) -> dict:
    cands = json.loads((run / "candidates.json").read_text(encoding="utf-8"))
    verdicts = ac.load_verdicts()
    found = ac.sort_findings(cands, verdicts)
    # Findings this run already recorded as decided stay readable, so a second pass over the
    # same export gives the same heavy list instead of an empty one.
    shown = found["page"] + [(c, v) for c, v in found["decided"] if v["decided"].get("run") == run.name]
    order = {c["key"]: i for i, c in enumerate(c for kind in ac.KINDS for c in cands.get(kind, []))}
    page = {c["key"]: (c, v) for c, v in sorted(shown, key=lambda cv: order[cv[0]["key"]])}
    marks, bad = read_marks(folder)
    file = lambda note: str(vault / note["path"])
    heavy = {"pairs": [], "godnotes": [], "tasks": []}
    rejected = 0
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
    unknown = sorted(set(marks) - page.keys())
    return {"heavy": str(run / "heavy.json"), "pairs": len(heavy["pairs"]), "godnotes": len(heavy["godnotes"]),
            "tasks": len(heavy["tasks"]), "rejected": rejected,
            "unmarked": len(page) - len(marks.keys() & page.keys()), "marks_not_on_page": unknown,
            "bad_marks": bad}


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
        if not args.decisions.is_dir():
            sys.exit(f"no such folder: {args.decisions}")
        done = cmd_decisions(run, vault, args.decisions)
    print(json.dumps(done, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
