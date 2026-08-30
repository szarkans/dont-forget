#!/usr/bin/env python3
"""Close an open thread without rewriting the note that recorded it.

A session note is a dated snapshot of how things stood that day. Ticking a box in it after
the fact edits history, so closure cannot live there — but it has to live somewhere, or
"done" and "forgotten" stay indistinguishable and the digest keeps offering work that was
finished weeks ago.

So it lives here: one append-only line per closed thread, outside the vault, keyed by the
text of the thread itself. The note stays exactly as written, the digest stops offering
the thread, and search still finds both.

Three paths close a thread, and all three are needed:

1. Evidence from the outside world, decided by code. A thread naming an issue, a pull
   request or a commit is resolved against the host, and closes with nobody's opinion
   involved. This needs the `gh` CLI; without it this path simply reports nothing, and
   the other two still work.
2. The agent proposes and the user closes. The proposal must carry its evidence, or
   deciding costs more than the thread is worth. If nobody is there — a hook ended the
   session — nothing closes. That is the safe refusal.
3. It falls out of the hot list by freshness, which needs no bookkeeping at all.

Most threads have no external anchor ("the suites were never run on bash 3.2"), which is
why path 2 exists; and path 1 exists because a thread that says "merge PR #41" should not
need a human at all.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from common import HOME_DIR

CLOSED_LOG = HOME_DIR / "closed-threads.jsonl"

# What counts as evidence pointing at the outside world.
ISSUE = re.compile(r"(?:(?P<repo>[\w.-]+/[\w.-]+))?#(?P<number>\d{1,6})\b")
COMMIT = re.compile(r"\b(?P<sha>[0-9a-f]{7,40})\b")


def key(thread: str) -> str:
    """Identify a thread by the line the digest shows, so the note needs no marker.

    The line the digest shows — text plus the session it came from — is what the user can
    actually copy, and it carries the note's identity, so the same sentence written in two
    projects closes in one of them only. Hashing the untruncated body instead meant the
    key never matched what anyone could paste back.
    """
    return hashlib.sha256(" ".join(thread.split()).encode("utf-8")).hexdigest()[:16]


def closed_keys(log_path: Path = CLOSED_LOG) -> set[str]:
    """Every key closed so far. One broken line loses itself, never the whole history.

    An append-only file can be cut short by a crash mid-write, and reading the whole file
    as all-or-nothing would resurrect every thread ever closed.
    """
    keys = set()
    try:
        with log_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    keys.add(json.loads(line)["key"])
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
    except OSError:
        return keys
    return keys


def close(thread: str, reason: str, log_path: Path = CLOSED_LOG) -> dict:
    entry = {"key": key(thread), "thread": " ".join(thread.split())[:300], "reason": reason,
             "closed": datetime.date.today().isoformat()}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _gh(args: list[str]) -> dict | None:
    if shutil.which("gh") is None:
        return None
    try:
        result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def evidence(thread: str, repo: str | None = None) -> dict | None:
    """Ask the host whether the thing this thread names is finished.

    Returns None whenever the answer is not a clear yes — no reference, no `gh`, no
    network, still open. Silence is never read as done.
    """
    matches = list(ISSUE.finditer(thread))
    if matches:
        # Every reference has to be finished, not just the first one written down. A
        # thread saying "issue #13 and #14 are open" used to answer with whichever came
        # first: close #13 and the whole thread reads as proven, while #14 still holds
        # the work. One unfinished reference withholds the evidence for all of them.
        seen = []
        for match in matches:
            args = ["issue", "view", match.group("number"), "--json", "state,title,url"]
            if match.group("repo") or repo:
                args += ["--repo", match.group("repo") or repo]
            found = _gh(args) or _gh([arg if arg != "issue" else "pr" for arg in args])
            if not (found and str(found.get("state", "")).upper() in ("CLOSED", "MERGED")):
                return None
            seen.append((match.group(0), found))
        # Evidence about the things named, not a verdict on the thread. "Backport the
        # merged PR #41" mentions a merged PR and is not itself done, so the caller
        # decides whether the thread asked for exactly what this evidence shows.
        return {"kind": "issue",
                "reference": ", ".join(reference for reference, _ in seen),
                "state": ", ".join(item["state"] for _, item in seen),
                "title": " | ".join(item.get("title", "") for _, item in seen),
                "url": " ".join(item.get("url", "") for _, item in seen),
                "covers": "the referenced item only" if len(seen) == 1
                          else f"the {len(seen)} referenced items only"}
    commit = COMMIT.search(thread)
    if commit and shutil.which("git") is not None:
        sha = commit.group("sha")
        # Existing is not the same as landed. A commit sitting on an abandoned branch
        # would otherwise close "merge abc1234 into main" while main has never seen it.
        try:
            reachable = subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"],
                                       capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if reachable.returncode == 0:
            return {"kind": "commit", "reference": sha, "state": "in this branch's history"}
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="close an open thread without rewriting its note")
    parser.add_argument("--log", type=Path, default=CLOSED_LOG)
    parser.add_argument("--repo", help="owner/name, when a bare #123 means another repository")
    parser.add_argument("--close", metavar="THREAD", help="record this thread as closed")
    parser.add_argument("--reason", default="", help="why it is closed; required with --close")
    parser.add_argument("--check", metavar="THREAD", action="append",
                        help="ask the host whether this thread's reference is finished")
    parser.add_argument("--list", action="store_true", help="print the keys already closed")
    args = parser.parse_args()

    if args.close:
        if not args.reason:
            raise SystemExit("--close needs --reason: a closure with no evidence is a guess")
        print(json.dumps(close(args.close, args.reason, args.log), ensure_ascii=False))
        return
    if args.check:
        already = closed_keys(args.log)
        out = [{"thread": thread, "closed_already": key(thread) in already,
                "evidence": evidence(thread, args.repo)} for thread in args.check]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    if args.list:
        print(json.dumps(sorted(closed_keys(args.log)), indent=2))
        return
    parser.print_help()


if __name__ == "__main__":
    main()
