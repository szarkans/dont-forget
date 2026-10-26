#!/usr/bin/env python3
"""Dependency-free self-check for transcript.py."""

import _selftest_env  # noqa: F401

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import transcript  # noqa: E402
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
    # A tool call IS evidence — the audit is asked whether errors were resolved and
    # whether external systems were updated, and only the calls answer that — but it
    # arrives as one line, not as its payload and result.
    assert "git log" in conversation[1]["text"], conversation[1]
    assert "used Bash" in conversation[1]["text"], conversation[1]
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

    # A boundary says where memory was lost, not how much sits on either side: a huge
    # stretch before the first compaction still has to be cut for its reader.
    lopsided = ([{"role": "user", "text": "a" * 100} for _ in range(10)]
                + [{"role": "compaction", "text": "--- compacted ---"}]
                + [{"role": "user", "text": "b" * 100}])
    lopsided_pieces = split(lopsided, limit=250)
    assert len(lopsided_pieces) >= 3, lopsided_pieces
    assert all(sum(len(t["text"]) for t in piece) <= 600 for piece in lopsided_pieces), lopsided_pieces

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

    # Parallel sessions in one folder share a project directory, so the newest file can
    # be the other session's. The id Claude Code passes names ours and beats mtime; only
    # when that file is absent does the mtime fallback run — and it says so, because
    # quietly handing back someone else's session is the bug.
    transcript.PROJECTS = Path(tmp) / "projects"
    workdir = Path(tmp) / "parallel"
    directory = transcript.project_dir(workdir)
    directory.mkdir(parents=True)
    ours = directory / "older-session.jsonl"
    ours.write_text(json.dumps({"type": "user", "message": {"content": "ours"}}) + "\n",
                    encoding="utf-8")
    other = directory / "newer-session.jsonl"
    other.write_text(json.dumps({"type": "user", "message": {"content": "theirs"}}) + "\n",
                     encoding="utf-8")
    # The other session lands last, not merely later.
    os.utime(ours, (1_000_000_000, 1_000_000_000))
    os.utime(other, (2_000_000_000, 2_000_000_000))

    os.environ["CLAUDE_CODE_SESSION_ID"] = "older-session"
    with contextlib.redirect_stderr(io.StringIO()) as err:
        assert transcript.newest_transcript(workdir) == ours, "session id must beat mtime"
    assert not err.getvalue(), "a named session is not a fallback"

    os.environ["CLAUDE_CODE_SESSION_ID"] = "gone-session"
    with contextlib.redirect_stderr(io.StringIO()) as err:
        assert transcript.newest_transcript(workdir) == other
    note = err.getvalue()
    assert "CLAUDE_CODE_SESSION_ID" in note and "newest" in note, note
    assert len(note.splitlines()) == 1, note
    del os.environ["CLAUDE_CODE_SESSION_ID"]

print("ok")
