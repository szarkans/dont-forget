#!/usr/bin/env python3
"""Dependency-free self-check for transcript.py."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from transcript import project_dir, render, split, turns  # noqa: E402

SCRIPT = Path(__file__).with_name("transcript.py")

# The directory name flattens both separators and dots, which is where the double hyphen
# of "-home-x--claude" comes from. Guessing this wrong means finding no transcript at all.
assert project_dir(Path("/home/x/.claude/skills/plugin")).name == "-home-x--claude-skills-plugin"

with tempfile.TemporaryDirectory(prefix="dont-forget-transcript-") as tmp:
    path = Path(tmp) / "session.jsonl"
    path.write_text("\n".join([
        json.dumps({"type": "user", "message": {"content": "what did we decide about deploys"}}),
        # Tool payloads, attachments and bookkeeping are the bulk of a real transcript and
        # none of it is conversation.
        json.dumps({"type": "attachment", "content": "x" * 5000}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "We decided to block deploys without migrations."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "git log"}}]}}),
        json.dumps({"type": "system", "content": "hook fired"}),
        json.dumps({"type": "assistant", "message": {"content": []}}),
        "not json at all",
        json.dumps({"type": "user", "message": {"content": "good, write it down"}}),
    ]) + "\n", encoding="utf-8")

    conversation = turns(path)
    assert [turn["role"] for turn in conversation] == ["user", "assistant", "user"], conversation
    assert "block deploys" in conversation[1]["text"]
    assert "git log" not in conversation[1]["text"], "a tool call is not something that was said"
    assert "xxxx" not in render(conversation), "attachments must not reach the reader"

    # A short session goes to one reader whole.
    assert len(split(conversation, limit=10_000)) == 1

    # A long one is split at the compaction boundary, because that is where the session
    # itself lost its memory.
    compacted = [
        {"role": "user", "text": "a" * 400},
        {"role": "compaction", "text": "--- compacted ---"},
        {"role": "user", "text": "b" * 400},
    ]
    pieces = split(compacted, limit=100)
    assert len(pieces) == 2, pieces
    assert pieces[0][0]["text"].startswith("a") and pieces[1][0]["text"].startswith("b")

    # With no boundary recorded it still splits, by size, rather than handing one reader
    # everything — which is the failure the whole design is avoiding.
    plain = [{"role": "user", "text": "c" * 100} for _ in range(10)]
    by_size = split(plain, limit=250)
    assert len(by_size) >= 2, by_size
    assert sum(len(piece) for piece in by_size) == 10, by_size

    summary = json.loads(subprocess.run(
        [sys.executable, str(SCRIPT), "--path", str(path)],
        capture_output=True, text=True, check=True).stdout)
    assert summary["turns"] == 3 and summary["pieces"] == 1, summary
    assert summary["raw_bytes"] > summary["characters"] * 10, summary

    piece = subprocess.run([sys.executable, str(SCRIPT), "--path", str(path), "--piece", "1"],
                           capture_output=True, text=True, check=True)
    assert "block deploys" in piece.stdout

    out_of_range = subprocess.run([sys.executable, str(SCRIPT), "--path", str(path), "--piece", "9"],
                                  capture_output=True, text=True)
    assert out_of_range.returncode != 0 and "out of range" in out_of_range.stderr

print("ok")
