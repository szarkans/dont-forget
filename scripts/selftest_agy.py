#!/usr/bin/env python3
"""Regression proof for Antigravity reading: transcript_full.jsonl, the summary
db, the session map, and the injectSteps hook contract."""

import _selftest_env  # noqa: F401

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import transcript
from session_sources import agy_session, agy_sessions, \
    active_session_identity
import unclosed

SCRIPT = Path(__file__).with_name("transcript.py")
HOT_SCAN = Path(__file__).with_name("hot-scan.py")


def invoke(script, *args, env=None, cwd=None, stdin=None):
    return subprocess.run([sys.executable, str(script), *map(str, args)],
                          capture_output=True, text=True, env=env, cwd=cwd,
                          input=stdin)


with tempfile.TemporaryDirectory(prefix="dont-forget-agy-") as directory:
    root = Path(directory)
    data = root / "gemini"
    workdir = root / "work"
    workdir.mkdir()
    conversation = "11112222-3333-4444-5555-666677778888"
    logs = (data / "antigravity-cli" / "brain" / conversation
            / ".system_generated" / "logs")
    logs.mkdir(parents=True)
    entries = [
        {"step_index": 0, "type": "SYSTEM_MESSAGE",
         "content": "private system prompt"},
        {"step_index": 1, "type": "USER_INPUT",
         "content": "<USER_REQUEST>\nAGY question one\n</USER_REQUEST>"},
        {"step_index": 2, "type": "PLANNER_RESPONSE",
         "content": "AGY answer one",
         "tool_calls": [{"name": "run_command", "args": {"cmd": "ls"}}]},
        {"step_index": 3, "type": "GENERIC",
         "content": "tool payload stays out"},
        {"step_index": 4, "type": "USER_INPUT",
         "content": "<USER_REQUEST>\nAGY question two\n</USER_REQUEST>"},
        {"step_index": 5, "type": "PLANNER_RESPONSE", "content": "AGY answer two"},
        {"step_index": 6, "type": "USER_INPUT",
         "content": "<USER_REQUEST>\nAGY question three\n</USER_REQUEST>"},
        {"step_index": 7, "type": "PLANNER_RESPONSE", "content": "AGY answer three"},
    ]
    full = logs / "transcript_full.jsonl"
    full.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    # transcript.jsonl exists too but transcript_full wins.
    (logs / "transcript.jsonl").write_text(json.dumps(entries[0]) + "\n")
    db = data / "antigravity-cli" / "conversation_summaries.db"
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE conversation_summaries (conversation_id TEXT, title TEXT,"
        " workspace_uris TEXT, last_modified_time TEXT)")
    now = time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 7200))
    connection.execute(
        "INSERT INTO conversation_summaries VALUES (?,?,?,?)",
        (conversation, "agy convo", json.dumps([f"file://{workdir}"]), stamp))
    connection.commit()
    connection.close()

    os.environ["AGY_DATA_HOME"] = str(data)

    # Direct load: the full log wins, envelopes are unwrapped, GENERIC is skipped.
    session = agy_session(conversation)
    roles = [turn["role"] for turn in session["turns"]]
    texts = [turn["text"] for turn in session["turns"]]
    assert roles == ["user", "assistant"] * 3, roles
    assert "AGY question one" in texts[0], texts
    assert "run_command" in texts[1], texts
    assert all("tool payload" not in t for t in texts), texts
    assert session["cwd"] == str(workdir), session["cwd"]
    assert session["harness"] == "agy" and session["session_id"] == conversation

    # The summary db feeds the unclosed-session scan.
    found = agy_sessions(now - 10800, now + 60)
    assert [row["session_id"] for row in found] == [conversation], found
    assert found[0]["path"] == f"agy://{conversation}"
    listing = unclosed.scan(days=1)
    assert any(row.get("harness") == "agy"
               and row.get("session_id") == f"agy:{conversation}"
               for row in listing), listing[:1]

    # transcript.py resolves by harness+id and freezes a namespaced audit id.
    result = invoke(SCRIPT, "--harness", "agy", "--session", conversation)
    assert result.returncode == 0, result.stderr
    audit = json.loads(result.stdout)
    assert audit["harness"] == "agy", audit
    assert audit["audit_session_id"] == f"agy:{conversation}", audit
    assert audit["turns"] == 6, audit["turns"]
    assert audit["path"].endswith("transcript_full.jsonl"), audit["path"]

    # A missing conversation is an explicit error, not an empty session.
    missing = invoke(SCRIPT, "--harness", "agy", "--session",
                     "00000000-0000-0000-0000-000000000000")
    assert missing.returncode != 0 and "no transcript" in missing.stderr, missing.stderr

    # The hook contract: conversationId in, injectSteps out, session map written.
    hook = invoke(HOT_SCAN, "--hook", "--harness", "agy", "--db", "/dev/null",
                  cwd=workdir,
                  stdin=json.dumps({"conversationId": conversation}))
    assert hook.returncode == 0, hook.stderr
    output = json.loads(hook.stdout)
    assert set(output) == {"injectSteps"}, output
    assert isinstance(output["injectSteps"], list), output
    previous = os.getcwd()
    os.chdir(workdir)
    try:
        assert active_session_identity("agy") == ("agy", conversation), \
            active_session_identity("agy")
    finally:
        os.chdir(previous)
    auto = invoke(SCRIPT, "--harness", "agy", cwd=workdir)
    assert auto.returncode == 0, auto.stderr
    assert json.loads(auto.stdout)["session_id"] == conversation

print("ok")
