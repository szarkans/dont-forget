#!/usr/bin/env python3
"""Regression proof for local Codex/OpenCode reading and session bookkeeping."""

import _selftest_env  # noqa: F401

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import transcript
import unclosed
from common import HOME_DIR
from session_sources import codex_session, opencode_session, read_db, resolve_codex_thread

SCRIPT = Path(__file__).with_name("transcript.py")


def invoke(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


with tempfile.TemporaryDirectory(prefix="dont-forget-portability-") as directory:
    root = Path(directory)
    cwd = str(Path.cwd())
    codex = Path(os.environ["CODEX_HOME"]) / "sessions/2026/10/30"
    codex.mkdir(parents=True)
    path = codex / "rollout-2026-10-30T12-00-00-exact-id.jsonl"
    records = [{"type": "session_meta", "payload": {"id": "exact-id", "cwd": cwd, "source": "cli"}}]
    for n in range(3):
        records.extend([
            {"type": "event_msg", "payload": {"type": "user_message", "message": f"question {n}"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": f"question {n}"}]}},
            {"type": "response_item", "payload": {"type": "reasoning", "summary": "PRIVATE REASONING"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": f"answer {n}"}]}},
        ])
    records.extend([
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "arguments": '{"cmd":"git status"}'}},
        {"type": "response_item", "payload": {"type": "function_call_output", "output": "PRIVATE RESULT"}},
        {"type": "compacted", "payload": {"message": "checkpoint summary"}},
        {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "PRIVATE INSTRUCTIONS"}]}},
    ])
    path.write_text("\n".join(map(json.dumps, records)) + "\n")
    conversation = transcript.turns(path)
    assert len([t for t in conversation if t["role"] == "user"]) == 3
    rendered = transcript.render(conversation)
    assert "git status" in rendered and "checkpoint summary" in rendered
    assert "PRIVATE" not in rendered
    summary = invoke("--path", path)
    assert summary.returncode == 0, summary.stderr
    assert json.loads(summary.stdout)["harness"] == "codex"
    pending = HOME_DIR / "closing-sessions.jsonl"
    assert json.loads(pending.read_text().splitlines()[0])["session_id"] == "codex:exact-id"

    # Unknown/empty input must fail before recording an audit as started.
    before = pending.read_bytes()
    for content in ('{"unknown": "format"}', '', '{"type":"session_meta","payload":{"id":"empty"}}'):
        bad = root / "unknown.jsonl"
        bad.write_text(content)
        failed = invoke("--path", bad)
        assert failed.returncode != 0 and failed.stderr, failed
        assert pending.read_bytes() == before

    # An active id beats file mtime and a missing id never picks a neighbour.
    os.environ["CODEX_THREAD_ID"] = "exact-id"
    selected = invoke()
    assert selected.returncode == 0, selected.stderr
    assert json.loads(selected.stdout)["path"] == str(path)
    os.environ["CODEX_THREAD_ID"] = "absent-id"
    missing = invoke()
    assert missing.returncode != 0 and "absent-id" in missing.stderr
    del os.environ["CODEX_THREAD_ID"]

    exported = root / "opencode.json"
    exported.write_text(json.dumps({"info": {"id": "export-id", "directory": cwd}, "messages": [
        {"info": {"role": "user"}, "parts": [{"type": "text", "text": "hello"}, {"type": "file", "url": "PRIVATE FILE"}]},
        {"info": {"role": "assistant"}, "parts": [{"type": "reasoning", "text": "PRIVATE THOUGHT"}, {"type": "text", "text": "world"}, {"type": "tool", "tool": "bash", "state": {"input": {"command": "pwd"}, "output": "PRIVATE RESULT"}}]},
    ]}, indent=2))
    exported_turns = transcript.turns(exported)
    assert len(exported_turns) == 2 and "PRIVATE" not in transcript.render(exported_turns)
    assert "pwd" in transcript.render(exported_turns)
    # OpenCode 2's native JSON export has flat typed messages, unlike the v1
    # info/parts envelope and unlike the SQLite rows' nested data column.
    exported.write_text(json.dumps({"info": {"id": "v2-export", "directory": cwd}, "messages": [
        {"id": "u", "type": "user", "text": "flat question"},
        {"id": "a", "type": "assistant", "content": [{"type": "text", "text": "flat answer"}]},
        {"id": "c", "type": "compaction", "summary": "flat checkpoint"},
    ]}))
    assert [turn["text"] for turn in transcript.turns(exported)] == ["flat question", "flat answer", "flat checkpoint"]

    db = Path(os.environ["XDG_DATA_HOME"]) / "opencode/opencode.db"
    db.parent.mkdir(parents=True)
    connection = sqlite3.connect(db)
    for table in ("session", "session_v2"):
        connection.execute(f"CREATE TABLE {table}(id TEXT PRIMARY KEY,directory TEXT,parent_id TEXT,time_updated INTEGER)")
    connection.execute("CREATE TABLE message(id TEXT,session_id TEXT,time_created INTEGER,data TEXT)")
    connection.execute("CREATE TABLE part(id TEXT,message_id TEXT,time_created INTEGER,data TEXT)")
    connection.execute("CREATE TABLE session_message(id TEXT,session_id TEXT,type TEXT,seq INTEGER,data TEXT)")
    connection.execute("CREATE TABLE session_part(message_id TEXT,session_id TEXT,ordinal INTEGER,type TEXT,data TEXT)")
    old = time.time() - 3600
    for table, sid, parent in (("session", "v1", None), ("session_v2", "v2", None), ("session_v2", "child", "v2")):
        connection.execute(f"INSERT INTO {table} VALUES(?,?,?,?)", (sid, cwd, parent, int(old * 1000)))
        for n in range(3):
            if table == "session":
                connection.execute("INSERT INTO message VALUES(?,?,?,?)", (str(n), sid, n, json.dumps({"role": "user"})))
                connection.execute("INSERT INTO part VALUES(?,?,?,?)", (str(n), str(n), n, json.dumps({"type": "text", "text": f"v1 question {n}"})))
            else:
                connection.execute("INSERT INTO session_message VALUES(?,?,?,?,?)", (str(n), sid, "user", n, json.dumps({"text": f"v2 question {n}"})))
    connection.execute("INSERT INTO session_message VALUES(?,?,?,?,?)", ("reply", "v2", "assistant", 3, json.dumps({"content": [{"type": "text", "text": "INLINE DUP"}, {"type": "tool", "name": "bash", "state": {"input": {"command": "pwd"}}}]})))
    # Normalized parts replace the inline content; ordinal order wins over insertion order,
    # and a part from another session is not this session's text.
    connection.execute("INSERT INTO session_part VALUES(?,?,?,?,?)", ("reply", "v2", 1, "tool", json.dumps({"name": "bash", "state": {"input": {"command": "pwd"}}})))
    connection.execute("INSERT INTO session_part VALUES(?,?,?,?,?)", ("reply", "v2", 0, "text", json.dumps({"text": "part one"})))
    connection.execute("INSERT INTO session_part VALUES(?,?,?,?,?)", ("other", "other", 0, "text", json.dumps({"text": "FOREIGN PART"})))
    connection.commit()
    connection.close()
    for sid in ("v1", "v2"):
        parsed = opencode_session(sid)
        assert len([t for t in parsed["turns"] if t["role"] == "user"]) == 3, parsed
        cli = invoke("--path", f"opencode://{sid}", "--piece", "1")
        assert cli.returncode == 0 and f"{sid} question" in cli.stdout, cli.stderr
    v2 = opencode_session("v2")
    reply = [t["text"] for t in v2["turns"] if t["role"] == "assistant"]
    assert len(reply) == 1 and "part one" in reply[0] and "pwd" in reply[0], reply
    assert "INLINE DUP" not in reply[0] and "FOREIGN PART" not in reply[0], reply

    # A compact export may carry a long info object before messages; the session's own
    # location is the cwd, not the export path.
    compact = root / "compact-export.json"
    compact.write_text(json.dumps({"info": {"id": "v2-compact", "padding": "i" * 5000,
                                            "location": {"directory": "/work/compact"}},
                                   "messages": [
                                       {"type": "user", "text": "compact question"},
                                       {"type": "assistant", "content": [{"type": "text", "text": "compact answer"}]},
                                   ]}))
    compact_session = transcript.load_session(compact)
    assert compact_session["cwd"] == "/work/compact", compact_session
    assert [t["text"] for t in compact_session["turns"]] == ["compact question", "compact answer"]
    with read_db() as connection:
        try:
            connection.execute("DELETE FROM session")
        except sqlite3.OperationalError as error:
            assert "readonly" in str(error)
        else:
            raise AssertionError("OpenCode reader opened a writable connection")

    # Codex paginated lineage: an ancestor prefix bounded by byte and ordinal, its own
    # records after the fork, and no duplicated replacement history.
    alt = root / "codex-lineage"
    sessions = alt / "sessions/2026/11/01"
    sessions.mkdir(parents=True)
    parent = sessions / "rollout-2026-11-01T10-00-00-aaaaaaaa-0000-0000-0000-000000000000.jsonl"
    parent_rows = [
        {"type": "session_meta", "ordinal": 0,
         "payload": {"id": "aaaaaaaa-0000-0000-0000-000000000000", "cwd": str(root),
                     "source": "cli", "history_mode": "paginated"}},
        {"type": "response_item", "ordinal": 1,
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "parent Q1"}]}},
        {"type": "response_item", "ordinal": 2,
         "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "parent A1"}]}},
    ]
    parent.write_text("\n".join(json.dumps(row) for row in parent_rows) + "\n")
    cutoff = parent.stat().st_size
    child = sessions / "rollout-2026-11-01T11-00-00-bbbbbbbb-0000-0000-0000-000000000000.jsonl"
    child_rows = [
        {"type": "session_meta", "ordinal": 3,
         "payload": {"id": "bbbbbbbb-0000-0000-0000-000000000000", "cwd": str(root),
                     "source": "cli", "history_mode": "paginated",
                     "history_base": {"thread_id": "aaaaaaaa-0000-0000-0000-000000000000",
                                      "end_ordinal_exclusive": 3, "end_byte_offset": cutoff}}},
        {"type": "response_item", "ordinal": 4,
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "child Q2"}]}},
        {"type": "response_item", "ordinal": 5,
         "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "child A2"}]}},
        {"type": "compacted", "ordinal": 6,
         "payload": {"message": "checkpoint summary",
                     "replacement_history": [{"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "REPLACEMENT DUP"}]}]}},
        {"type": "compacted", "ordinal": 7, "payload": {"message": "", "replacement_history": []}},
        {"type": "response_item", "ordinal": 8,
         "metadata": {"compaction_output": True},
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "COMPACTION OUTPUT"}]}},
        {"type": "response_item", "ordinal": 9,
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "after compact"}]}},
    ]
    child.write_text("\n".join(json.dumps(row) for row in child_rows) + "\n")
    with parent.open("a") as handle:
        handle.write(json.dumps({"type": "response_item", "ordinal": 3,
                                 "payload": {"type": "message", "role": "user",
                                             "content": [{"type": "input_text", "text": "LATE PARENT"}]}}) + "\n")
    previous_codex = os.environ.get("CODEX_HOME")
    os.environ["CODEX_HOME"] = str(alt)
    try:
        lineage = codex_session(child)
        rendered = transcript.render(lineage["turns"])
        assert [turn["text"] for turn in lineage["turns"] if turn["role"] == "user"] == ["parent Q1", "child Q2", "after compact"], lineage
        assert "parent A1" in rendered and "child A2" in rendered, rendered
        assert "checkpoint summary" in rendered and "context compacted (no plaintext summary)" in rendered, rendered
        assert "LATE PARENT" not in rendered and "REPLACEMENT DUP" not in rendered, rendered
        assert "COMPACTION OUTPUT" not in rendered, rendered

        if shutil.which("zstd"):
            compressed = child.with_name(child.name + ".zst")
            subprocess.run(["zstd", "-q", "-f", str(child), "-o", str(compressed)], check=True)
            # Plain and compressed are one source, and the plain representation wins.
            from session_sources import resolve_codex_rollout
            assert resolve_codex_rollout("bbbbbbbb-0000-0000-0000-000000000000") == child
            child.unlink()
            from_zst = codex_session(compressed)
            assert transcript.render(from_zst["turns"]) == rendered, "compressed rollout differs"
            codex_session(compressed, metadata_only=True)
            children = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_text().split()
            comms = [Path(f"/proc/{pid}/comm").read_text().strip() for pid in children if Path(f"/proc/{pid}/comm").exists()]
            assert "zstd" not in comms, comms
            broken = sessions / f"rollout-2026-11-01T12-00-00-{'c' * 8}.jsonl.zst"
            broken.write_bytes(b"not a zstd frame")
            try:
                codex_session(broken, metadata_only=True)
                raise AssertionError("a broken compressed rollout was accepted")
            except ValueError as error:
                assert "zstd" in str(error), error

        # The state database names the selected rollout after a revert, even when a
        # sibling file is newer; a missing paginated source is an error, not a fallback.
        state = alt / "state_5.sqlite"
        connection = sqlite3.connect(state)
        connection.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT, history_mode TEXT)")
        old_path = sessions / "rollout-2026-11-01T13-00-00-dddddddd-0000-0000-0000-000000000000.jsonl"
        new_path = sessions / "rollout-2026-11-01T14-00-00-dddddddd-0000-0000-0000-000000000000_eeeeeeee-0000-0000-0000-000000000000.jsonl"
        for candidate in (old_path, new_path):
            candidate.write_text(json.dumps({"type": "session_meta", "payload": {
                "id": "dddddddd-0000-0000-0000-000000000000", "cwd": str(root),
                "source": "cli", "history_mode": "legacy"}}) + "\n")
        os.utime(old_path, (1_000_000_000, 1_000_000_000))
        os.utime(new_path, (2_000_000_000, 2_000_000_000))
        connection.execute("INSERT INTO threads VALUES(?,?,?)",
                           ("dddddddd-0000-0000-0000-000000000000", str(old_path), "legacy"))
        connection.commit()
        connection.close()
        assert resolve_codex_thread("dddddddd-0000-0000-0000-000000000000") == old_path
        connection = sqlite3.connect(state)
        connection.execute("UPDATE threads SET history_mode='paginated' WHERE id=?",
                           ("dddddddd-0000-0000-0000-000000000000",))
        connection.commit()
        connection.close()
        old_path.unlink()
        try:
            resolve_codex_thread("dddddddd-0000-0000-0000-000000000000")
            raise AssertionError("a missing paginated rollout fell back to a sibling")
        except ValueError as error:
            assert "paginated" in str(error) and "missing" in str(error), error
    finally:
        if previous_codex is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = previous_codex

    # Discovery includes both providers, excludes children/current sessions, and shares
    # the same canonical ids with audit bookkeeping.
    os.utime(path, (old, old))
    unclosed.PROJECTS = root / "no-claude"
    found = {s["session_id"] for s in unclosed.scan()}
    assert {"codex:exact-id", "opencode:v1", "opencode:v2"} <= found, found
    assert "opencode:child" not in found
    # An inherited variable is not the current session; only the explicit canonical id
    # excludes a session from discovery.
    os.environ["OPENCODE_SESSION_ID"] = "v2"
    assert "opencode:v2" in {s["session_id"] for s in unclosed.scan()}
    assert "opencode:v2" not in {s["session_id"] for s in unclosed.scan(current_session_id="opencode:v2")}
    del os.environ["OPENCODE_SESSION_ID"]
    assert invoke("--harness", "opencode", "--session", "v2").returncode == 0
    assert unclosed.close_pending("codex:exact-id") == "closed"
    assert unclosed.close_pending("opencode:v2") == "closed"
    found = {s["session_id"] for s in unclosed.scan()}
    assert "codex:exact-id" not in found and "opencode:v2" not in found, found

    # Explicit Claude ids must register an audit too, not only --path callers.
    claude_home = root / "claude-home"
    claude_path = claude_home / ".claude/projects/fixture/claude-exact.jsonl"
    claude_path.parent.mkdir(parents=True)
    claude_path.write_text(json.dumps({
        "type": "user", "sessionId": "claude-exact", "cwd": cwd,
        "message": {"content": "historical Claude conversation"},
    }) + "\n")
    selected = subprocess.run(
        [sys.executable, str(SCRIPT), "--harness", "claude", "--session", "claude-exact"],
        env=dict(os.environ, HOME=str(claude_home)), capture_output=True, text=True)
    assert selected.returncode == 0, selected.stderr
    assert json.loads(selected.stdout)["audit_session_id"] == "claude-exact"
    assert unclosed.close_pending("claude-exact") == "closed"

print("ok")
