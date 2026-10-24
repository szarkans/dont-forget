"""What the meaning audit's two scripts must agree on: where its state lives, how a finding is
named, and which findings are applied by themselves, which wait for the user, which are dropped.

Standard library only: audit_semantic.py runs under the plugin venv, audit_apply.py under the
system python, and both import this.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from common import HOME_DIR

AUDIT_DIR = HOME_DIR / "audit"
VERDICTS = AUDIT_DIR / "verdicts.json"
RUNS = AUDIT_DIR / "runs"

KINDS = ("group", "cross", "same", "stale")
LETTER = {"group": "g", "cross": "l", "same": "d", "stale": "s"}

# Reader verdicts on a same-project pair. "уточняет" is only a missing link; the other three
# change what the vault says and go to the user.
SAME_ON_PAGE = ("устарело", "спор", "дубль")


def finding_key(kind: str, titles: list[str]) -> str:
    """The one name of a finding. Verdicts, page marks and applied links all meet on it, so a
    rebuild, a rerun or a reordered member list must give the same key for the same notes."""
    digest = hashlib.sha1("|".join(sorted(titles)).encode("utf-8")).hexdigest()
    return f"{LETTER[kind]}-{digest[:16]}"


def load_verdicts(path: Path = VERDICTS) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def write_json(path: Path, value) -> None:
    """Write through a temporary file, so a crash never leaves half a verdicts file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def latest_run(runs: Path = RUNS) -> Path:
    found = sorted(p for p in runs.glob("*") if (p / "candidates.json").is_file()) if runs.is_dir() else []
    if not found:
        raise SystemExit(f"no audit run under {runs}: run `audit_semantic.py candidates` first")
    return found[-1]


def godnote_named(candidate: dict, title: str | None) -> dict | None:
    """The nearest godnote a reader's `extend` names. Readers sometimes drop the genre prefix."""
    if not title:
        return None
    bare = title.split(" — ", 1)[-1]
    return next((g for g in candidate.get("nearest_godnotes", [])
                 if g["title"] == title or g["title"].split(" — ", 1)[-1] == bare), None)


def sort_findings(candidates: dict, verdicts: dict) -> dict:
    """Split read candidates into what is safe to apply, what goes on the page, and what was dropped.

    safe: (candidate, verdict, why) — links only. page: (candidate, verdict). A same-project
    "устарело" whose old note a confirmed stale finding already covers is the same news twice.
    """
    safe, page, dropped, unread = [], [], Counter(), 0
    stale_old = set()
    for c in candidates.get("stale", []):
        v = verdicts.get(c["key"])
        if v and v.get("confirmed"):
            stale_old.add(c["old"]["path"])
    for kind in KINDS:
        for c in candidates.get(kind, []):
            v = verdicts.get(c["key"])
            if v is None:
                unread += 1
            elif kind == "group" and v.get("keep") and v.get("extend"):
                safe.append((c, v, "в godnote"))
            elif kind == "group" and v.get("keep"):
                page.append((c, v))
            elif kind == "cross" and v.get("keep"):
                safe.append((c, v, "между проектами"))
            elif kind == "same" and v.get("verdict") == "уточняет":
                safe.append((c, v, "уточняет"))
            elif (kind == "same" and v.get("verdict") in SAME_ON_PAGE
                  and not (v["verdict"] == "устарело" and {c["a"]["path"], c["b"]["path"]} & stale_old)):
                page.append((c, v))
            elif kind == "stale" and v.get("confirmed"):
                page.append((c, v))
            else:
                dropped[kind] += 1
    return {"safe": safe, "page": page, "dropped": dict(dropped), "unread": unread}


def sides(candidate: dict, verdict: dict) -> tuple[dict, dict | None]:
    """(old, new) of a pair finding, each a note plus what the reader says it claims.

    Readers describe "a" in old_say and "b" in new_say unless they mark b as the old one.
    A stale finding with no second note has new = None: the mark sits in the note itself.
    """
    if candidate["kind"] == "stale":
        old, new = candidate["old"], candidate.get("new")
    elif verdict.get("old") == "b":
        old, new = candidate["b"], candidate["a"]
    else:
        old, new = candidate["a"], candidate["b"]
    return ({**old, "says": verdict.get("old_say", "")},
            {**new, "says": verdict.get("new_say", "")} if new else None)


def page_verdict(candidate: dict, verdict: dict) -> str:
    if candidate["kind"] == "stale":
        return "заголовок устарел" if candidate["how"] == "поправка" else "устарело"
    return verdict["verdict"]
