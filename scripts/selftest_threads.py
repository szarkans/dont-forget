#!/usr/bin/env python3
"""Dependency-free self-check for threads.py."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from threads import close, closed_keys, evidence, key  # noqa: E402

SCRIPT = Path(__file__).with_name("threads.py")

# The key follows the line the digest shows — spacing folded, session name included — so
# the user can paste back what they see, and the same sentence recorded in two different
# sessions closes in one of them only.
assert key("merge  PR #41  (Session — a)") == key("merge PR #41 (Session — a)")
assert key("merge PR #41 (Session — a)") != key("merge PR #41 (Session — b)")
assert key("merge PR #41 (Session — a)") != key("merge PR #42 (Session — a)")

with tempfile.TemporaryDirectory(prefix="dont-forget-threads-") as tmp:
    log = Path(tmp) / "closed.jsonl"
    assert closed_keys(log) == set(), "a missing log is an empty log, not an error"

    entry = close("run the release checklist against prod — user runs it", "done in abc1234", log)
    assert entry["key"] in closed_keys(log)
    assert entry["reason"] == "done in abc1234"

    # Append-only: a second closure does not disturb the first.
    close("другой хвост, кириллицей", "закрыт руками", log)
    assert len(closed_keys(log)) == 2
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["thread"].startswith("run the release checklist"), lines

    # An append-only file can be cut short mid-write. That line loses itself; every
    # thread closed before it stays closed.
    with log.open("a", encoding="utf-8") as handle:
        handle.write('{"key": "truncated-')
    assert len(closed_keys(log)) == 2, closed_keys(log)

    # A closure with no reason is a guess, and the script refuses it.
    refused = subprocess.run(
        [sys.executable, str(SCRIPT), "--log", str(log), "--close", "something"],
        capture_output=True, text=True)
    assert refused.returncode != 0 and "reason" in refused.stderr, refused

    # Nothing in the world to point at means no evidence — never a quiet yes.
    assert evidence("the suites were never run on bash 3.2") is None
    assert evidence("DNS has not been repointed yet") is None

    # A commit that exists in this repository is evidence; one that does not, is not.
    here = Path(__file__).resolve().parent.parent
    head = subprocess.run(["git", "-C", str(here), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    found = evidence(f"check commit {head} landed")
    assert found and found["kind"] == "commit", found
    assert evidence("check commit deadbee landed") is None
    # Existing is not landed: a commit that is not in this branch's history proves nothing
    # about a thread that asks for it to be merged.
    orphan = subprocess.run(["git", "-C", str(here), "hash-object", "-w", "--stdin"],
                            input="not a commit", capture_output=True, text=True, check=True)
    assert evidence(f"merge {orphan.stdout.strip()[:8]} into main") is None

    checked = json.loads(subprocess.run(
        [sys.executable, str(SCRIPT), "--log", str(log), "--check", "nothing to point at here"],
        capture_output=True, text=True, check=True).stdout)
    assert checked[0]["evidence"] is None and checked[0]["closed_already"] is False, checked

print("ok")
