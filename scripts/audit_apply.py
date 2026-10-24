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
import json
import re
import sys
from collections import Counter
from pathlib import Path

import audit_common as ac
from common import vault_from_config

KEY = re.compile(r"^[glds]-[0-9a-f]{16}$")
# The vault writes its links section in either language; a second "## Links" under "## Связи" is clutter.
LINKS_SECTION = re.compile(r"^## (?:Links|Связи|Ссылки)[ \t]*(?:\n|\Z)(?:[ \t]*\n)*(?:[ \t]*- .*(?:\n|\Z))*", re.M)


def with_link(text: str, target: str) -> str | None:
    """`text` with `- [[target]]` added at the end of its links list, or None if it already links there."""
    if re.search(r"\[\[" + re.escape(target) + r"(\]\]|\||#)", text):
        return None
    line = f"- [[{target}]]\n"
    m = LINKS_SECTION.search(text)
    if not m:
        return text.rstrip("\n") + "\n\n## Links\n" + line
    rest = text[m.end():].lstrip("\n")
    return text[:m.end()].rstrip("\n") + "\n" + line + ("\n" + rest if rest else "")


def safe_links(run: Path) -> tuple[list[tuple[dict, dict, str]], list[str]]:
    """(from, to, why) for every link the verdicts make safe, and the godnotes a reader named but the run lacks."""
    cands = json.loads((run / "candidates.json").read_text(encoding="utf-8"))
    out, missing = [], []
    for c, v, why in ac.sort_findings(cands, ac.load_verdicts())["safe"]:
        if c["kind"] == "group":
            god = ac.godnote_named(c, v.get("extend"))
            if god is None:
                missing.append(v.get("extend"))
                continue
            out += [(m, god, why) for m in c["members"] if m["path"] != god["path"]]
        else:
            out += [(c["a"], c["b"], why), (c["b"], c["a"], why)]
    return out, missing


def cmd_safe(run: Path, vault: Path, dry: bool) -> dict:
    links, missing = safe_links(run)
    added, there, gone, kinds = [], 0, [], Counter()
    for src, dst, why in links:
        path = vault / src["path"]
        if not path.is_file():
            gone.append(src["path"])
            continue
        text = with_link(path.read_text(encoding="utf-8"), Path(dst["path"]).stem)
        if text is None:
            there += 1
            continue
        if not dry:
            path.write_text(text, encoding="utf-8")
        added.append({"file": src["path"], "link": Path(dst["path"]).stem, "why": why})
        kinds[why] += 1
    return {"dry": dry, "links_added": len(added), "already_there": there, "by_kind": dict(kinds),
            "godnote_not_found": missing, "file_gone": sorted(set(gone)), "added": added}


def read_marks(folder: Path) -> dict:
    """{key: {"v": "yes"|"no"|None, "c": comment}} from an export folder, any depth."""
    marks = {}
    for f in sorted(folder.rglob("*.json")):
        if KEY.match(f.stem):
            body = json.loads(f.read_text(encoding="utf-8"))
            marks[f.stem] = {"v": body.get("v"), "c": (body.get("c") or "").strip()}
    return marks


def cmd_decisions(run: Path, vault: Path, folder: Path) -> dict:
    cands = json.loads((run / "candidates.json").read_text(encoding="utf-8"))
    page = {c["key"]: (c, v) for c, v in ac.sort_findings(cands, ac.load_verdicts())["page"]}
    marks = read_marks(folder)
    file = lambda note: str(vault / note["path"])
    heavy = {"pairs": [], "godnotes": [], "tasks": []}
    rejected = 0
    for key, (c, v) in page.items():
        mark = marks.get(key, {"v": None, "c": ""})
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
    unknown = sorted(set(marks) - page.keys())
    return {"heavy": str(run / "heavy.json"), "pairs": len(heavy["pairs"]), "godnotes": len(heavy["godnotes"]),
            "tasks": len(heavy["tasks"]), "rejected": rejected,
            "unmarked": len(page) - len(marks.keys() & page.keys()), "marks_not_on_page": unknown}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--safe", action="store_true")
    mode.add_argument("--decisions", type=Path)
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--vault", type=Path)
    parser.add_argument("--run", type=Path, help="run folder; the latest one by default")
    args = parser.parse_args()
    vault = args.vault or vault_from_config()
    run = args.run or ac.latest_run()
    if args.safe:
        done = cmd_safe(run, vault, args.dry)
    else:
        if not args.decisions.is_dir():
            sys.exit(f"no such folder: {args.decisions}")
        done = cmd_decisions(run, vault, args.decisions)
    print(json.dumps(done, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
