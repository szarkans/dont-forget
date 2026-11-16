#!/usr/bin/env python3
"""Regression proof for Devin reading: sessions.db, ATIF exports, the session map."""

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
from common import HOME_DIR
from session_sources import devin_session, devin_sessions, record_session_map
import unclosed

SCRIPT = Path(__file__).with_name("transcript.py")


def invoke(*args, env=None, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, env=env, cwd=cwd)


def user_text(text):
    return json.dumps({"role": "user", "content": text,
                       "metadata": {"is_user_input": True}})


with tempfile.TemporaryDirectory(prefix="dont-forget-devin-") as directory:
    root = Path(directory)
    data = Path(os.environ["XDG_DATA_HOME"]) / "devin/cli"
    data.mkdir(parents=True)
    db = data / "sessions.db"
    connection = sqlite3.connect(db)
    connection.executescript("""
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, working_directory TEXT NOT NULL,
          backend_type TEXT NOT NULL, model TEXT NOT NULL, agent_mode TEXT NOT NULL,
          created_at INTEGER NOT NULL, last_activity_at INTEGER NOT NULL,
          title TEXT, main_chain_id INTEGER, shell_last_seen_index INTEGER DEFAULT 0,
          cogs_json TEXT, workspace_dirs TEXT, hidden INTEGER NOT NULL DEFAULT 0,
          metadata TEXT);
        CREATE TABLE message_nodes (
          row_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
          node_id INTEGER NOT NULL, parent_node_id INTEGER,
          chat_message TEXT NOT NULL, created_at INTEGER NOT NULL, metadata TEXT);
    """)
    now = int(time.time())
    workdir = root / "work"
    workdir.mkdir()
    connection.execute(
        "INSERT INTO sessions (id, working_directory, backend_type, model, agent_mode,"
        " created_at, last_activity_at, main_chain_id, hidden) VALUES (?,?,?,?,?,?,?,?,?)",
        ("main-session", str(workdir), "cli", "swe", "code", now - 3700, now - 3600, 10, 0))
    connection.execute(
        "INSERT INTO sessions (id, working_directory, backend_type, model, agent_mode,"
        " created_at, last_activity_at, main_chain_id, hidden) VALUES (?,?,?,?,?,?,?,?,?)",
        ("sub-agent", str(workdir), "cli", "swe", "code", now - 3600, now - 3500, 2, 1))
    rows = [
        (1, None, {"role": "system", "content": "PRIVATE SYSTEM PROMPT"}),
        (2, 1, {"role": "user", "content": "DEVIN question one",
                "metadata": {"is_user_input": True}}),
        (3, 2, {"role": "assistant", "content": "DEVIN answer one",
                # The live store writes tool_calls as a Python repr, not JSON.
                "tool_calls": repr([{"function": {"name": "exec",
                                                 "arguments": '{"cmd":"git log"}'}}])}),
        (4, 3, {"role": "tool", "content": "PRIVATE TOOL RESULT", "name": "exec"}),
        (5, 4, {"role": "assistant",
                # ...while some rows carry a JSON string instead.
                "tool_calls": json.dumps([{"function": {"name": "edit",
                                                        "arguments": "{}"}}]),
                "content": "DEVIN answer two"}),
        (6, 5, {"role": "user", "content": "DEVIN question two",
                "metadata": {"is_user_input": True}}),
        (7, 6, {"role": "assistant", "content": "DEVIN final"}),
        # A sibling branch off the root that the main chain must not include.
        (8, 2, {"role": "user", "content": "SIDE BRANCH off the chain",
                "metadata": {"is_user_input": True}}),
        (9, 7, {"role": "user", "content": "DEVIN question three",
                "metadata": {"is_user_input": True}}),
        (10, 9, {"role": "assistant", "content": "DEVIN tail"}),
    ]
    for node_id, parent, message in rows:
        connection.execute(
            "INSERT INTO message_nodes (session_id, node_id, parent_node_id,"
            " chat_message, created_at) VALUES (?,?,?,?,?)",
            ("main-session", node_id, parent, json.dumps(message), now - 50 + node_id))
    for node_id in (1, 2):
        connection.execute(
            "INSERT INTO message_nodes (session_id, node_id, parent_node_id,"
            " chat_message, created_at) VALUES (?,?,?,?,?)",
            ("sub-agent", node_id, node_id - 1 or None, user_text(f"sub {node_id}"),
             now - 40 + node_id))
    connection.commit()
    connection.close()
    os.environ["CHISEL_SESSION_DB"] = str(db)

    # The chain follows main_chain_id back to the root: roles are filtered, tool_calls
    # decode from either storage encoding, side branches stay out.
    session = devin_session("main-session")
    assert session["harness"] == "devin" and session["session_id"] == "main-session"
    texts = [turn["text"] for turn in session["turns"]]
    assert any("DEVIN question one" in t for t in texts), texts
    assert any("git log" in t for t in texts), texts
    joined = "\n".join(texts)
    assert "PRIVATE" not in joined and "SIDE BRANCH" not in joined, joined

    # The CLI resolves the same id, freezes a snapshot and records the audit id.
    env = {**os.environ, "CHISEL_SESSION_DB": str(db)}
    summary = invoke("--harness", "devin", "--session", "main-session", env=env)
    assert summary.returncode == 0, summary.stderr
    data_out = json.loads(summary.stdout)
    assert data_out["audit_session_id"] == "devin:main-session", data_out
    snapshot = Path(data_out["snapshot_file"])
    assert snapshot.is_file() and "DEVIN question one" in snapshot.read_text()
    pending = [json.loads(line) for line in
               (HOME_DIR / "closing-sessions.jsonl").read_text().splitlines()]
    assert pending[-1]["session_id"] == "devin:main-session", pending

    # The hook's session map is what a later reader trusts for identity.
    record_session_map("devin", "main-session", str(workdir.resolve()))
    auto = invoke("--harness", "devin", env=env, cwd=workdir)
    assert auto.returncode == 0, auto.stderr
    assert json.loads(auto.stdout)["session_id"] == "main-session", auto.stdout

    # An ATIF export reads through --path, and also stands in for a session id whose
    # row is absent from the store.
    transcripts_dir = data / "transcripts"
    transcripts_dir.mkdir()
    export = {"schema_version": "atif-v1", "session_id": "ended-session",
              "agent": {"cwd": "/work"},
              "steps": [
                  {"step_id": 1, "source": "user", "message": "ATIF hello"},
                  {"step_id": 2, "source": "agent", "message": "ATIF reply",
                   "tool_calls": [{"function": {"name": "read", "arguments": "{}"}}]},
                  {"step_id": 3, "source": "system", "message": "PRIVATE"}]}
    atif = transcripts_dir / "ended-session.json"
    atif.write_text(json.dumps(export))
    via_path = invoke("--path", atif, env=env)
    assert via_path.returncode == 0, via_path.stderr
    out = json.loads(via_path.stdout)
    assert out["harness"] == "devin" and out["audit_session_id"] == "devin:ended-session", out
    via_id = invoke("--harness", "devin", "--session", "ended-session", env=env)
    assert via_id.returncode == 0 and "ATIF hello" in Path(
        json.loads(via_id.stdout)["snapshot_file"]).read_text(), via_id.stderr

    # Unknown and malformed sessions fail loudly and record nothing.
    before = (HOME_DIR / "closing-sessions.jsonl").read_text()
    for bad in (("--harness", "devin", "--session", "no-such-session"),
                ("--harness", "devin", "--session", "")):
        failed = invoke(*bad, env=env)
        assert failed.returncode != 0, (bad, failed.stdout)
        assert (HOME_DIR / "closing-sessions.jsonl").read_text() == before

    # Discovery lists interactive sessions as devin:// candidates; the unclosed scan
    # keeps hidden sub-agent rows out and honours the canonical exclusion.
    found = devin_sessions(now - 3600, now + 60)
    by_id = {row["session_id"]: row for row in found}
    assert "main-session" in by_id and "sub-agent" in by_id, by_id
    assert by_id["sub-agent"]["hidden"], by_id
    stats = {}
    listed = {row["session_id"] for row in unclosed.scan(stats=stats)}
    assert "devin:main-session" in listed, listed
    assert "devin:sub-agent" not in listed, listed
    without_self = {row["session_id"] for row in unclosed.scan(
        stats={}, current_session_id="devin:main-session")}
    assert "devin:main-session" not in without_self, without_self

print("ok")
