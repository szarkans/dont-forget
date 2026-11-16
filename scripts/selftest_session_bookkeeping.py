#!/usr/bin/env python3
"""Exact writer closure and concurrent audit registration, using scratch data only."""

import _selftest_env  # noqa: F401

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import unclosed

SCRIPTS = Path(__file__).parent
WRITER = SCRIPTS / "vault-write.py"
CONTENT = "---\ntype: session\n---\n# Audit\n\n[[hub]]\n"
IDENTITY_KEYS = ("PI_SESSION_ID", "PI_SESSION_FILE", "OPENCODE_SESSION_ID",
                 "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CLAUDE_CODE_SESSION_ID")


def environment(home: Path, **identity) -> dict:
    env = {key: value for key, value in os.environ.items() if key not in IDENTITY_KEYS}
    env.update(HOME=str(home / "user"), DONT_FORGET_HOME=str(home),
               CODEX_HOME=str(home / "codex"), XDG_DATA_HOME=str(home / "data"))
    env.update(identity)
    return env


def logs(home: Path) -> tuple[Path, Path]:
    return home / "closing-sessions.jsonl", home / "closed-sessions.jsonl"


def register(home: Path, session_id: str) -> None:
    pending, closed = logs(home)
    unclosed.record_pending(home / "source.jsonl", pending, session_id, closed_log=closed)


def ids(path: Path) -> list[str]:
    return ([json.loads(line)["session_id"] for line in path.read_text().splitlines()]
            if path.exists() else [])


def write(vault: Path, home: Path, name: str = "Session — audit.md", content: str = CONTENT,
          identity: dict | None = None, **payload) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(WRITER), "--vault", str(vault)],
                          input=json.dumps({"filename": name, "content": content, **payload}),
                          env=environment(home, **(identity or {})), capture_output=True,
                          text=True, check=False)


def saved(done: subprocess.CompletedProcess) -> dict:
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["status"] in ("created", "replaced", "exists-same"), result
    return result


def wait_for(path: Path, child: subprocess.Popen) -> None:
    deadline = time.monotonic() + 10
    while not path.exists():
        assert child.poll() is None, child.communicate()
        assert time.monotonic() < deadline, f"timed out waiting for {path.name}"
        time.sleep(0.01)


with tempfile.TemporaryDirectory(prefix="dont-forget-session-bookkeeping-") as directory:
    root = Path(directory)
    vault, home = root / "vault", root / "home"
    vault.mkdir()
    pending, closed = logs(home)
    register(home, "omp:A")
    register(home, "omp:B")

    # The source of a historical audit beats the active session and never closes B.
    done = write(vault, home, session_id="omp:A", identity={"PI_SESSION_ID": "B"})
    result = saved(done)
    assert result["session_closure"] == {"session_id": "omp:A", "status": "closed"}, result
    assert ids(pending) == ["omp:B"] and ids(closed) == ["omp:A"]
    assert (vault / "sessions" / "Session — audit.md").read_text() == CONTENT

    # Replaying an identical note is idempotent, including the closure journal.
    result = saved(write(vault, home, session_id="omp:A"))
    assert result["status"] == "exists-same", result
    assert result["session_closure"] == {"session_id": "omp:A", "status": "already-closed"}
    register(home, "omp:A")
    assert ids(pending) == ["omp:B"] and ids(closed) == ["omp:A"]

    # Missing or foreign identity never guesses from the one remaining pending entry.
    unmatched = (({}, "no-identity"), ({"PI_SESSION_ID": "foreign"}, "not-pending"),
                 ({"CODEX_THREAD_ID": "B"}, "not-pending"),
                 ({"OPENCODE_SESSION_ID": "B"}, "not-pending"),
                 ({"CLAUDE_CODE_SESSION_ID": "B"}, "not-pending"),
                 ({"PI_SESSION_ID": "", "CODEX_THREAD_ID": "B",
                   "CLAUDE_CODE_SESSION_ID": "B"}, "no-identity"),
                 ({"PI_SESSION_FILE": "", "CODEX_THREAD_ID": "B"}, "no-identity"),
                 ({"PI_SESSION_ID": "malformed id"}, "no-identity"),
                 ({"CLAUDE_CODE_SESSION_ID": "omp:B"}, "no-identity"))
    for number, (identity, expected) in enumerate(unmatched):
        before = pending.read_bytes(), closed.read_bytes()
        done = write(vault, home, f"Session — unmatched {number}.md", identity=identity)
        result = saved(done)
        assert result["session_closure"]["status"] == expected, result
        assert result["session_closure"]["warning"] in done.stderr, done.stderr
        assert before == (pending.read_bytes(), closed.read_bytes())
        assert (vault / "sessions" / f"Session — unmatched {number}.md").exists()

    # Supplied-but-malformed ids are errors before either create or replace mutates a note.
    before = pending.read_bytes(), closed.read_bytes()
    malformed = (None, 42, [], {}, "", " ", "omp:", "claude:A", "foreign:A",
                 "omp:A:B", "omp:A\n", "A/B", "A\\B", "A\0B")
    for value in malformed:
        failed = write(vault, home, "Session — invalid.md", session_id=value)
        assert failed.returncode == 2 and "session_id" in failed.stderr, failed
        assert not (vault / "sessions" / "Session — invalid.md").exists()
        assert before == (pending.read_bytes(), closed.read_bytes())
    failed = write(vault, home, content=CONTENT + "replacement\n", action="replace",
                   expected_sha=hashlib.sha256(CONTENT.encode()).hexdigest(), session_id=None)
    assert failed.returncode == 2, failed.stderr
    assert (vault / "sessions" / "Session — audit.md").read_text() == CONTENT

    # A rejected/conflicting note cannot close an audit; ordinary notes cannot either.
    rejected = write(vault, home, "Session — rejected.md", content="---\ntype: session\n---\n",
                     session_id="omp:B")
    result = json.loads(rejected.stdout)
    assert result["status"] == "rejected", result
    assert result["session_closure"] == {"session_id": "omp:B", "status": "not-written"}
    conflict = write(vault, home, content=CONTENT + "different\n", session_id="omp:B")
    result = json.loads(conflict.stdout)
    assert result["status"] == "conflict" and result["session_closure"]["status"] == "not-written"
    ordinary = write(vault, home, "Atom — ordinary.md", content="---\ntype: atom\n---\n[[hub]]\n",
                     session_id="omp:B")
    assert "session_closure" not in saved(ordinary)
    assert before == (pending.read_bytes(), closed.read_bytes())

    # An identical replay repairs an earlier write that had no usable source identity.
    result = saved(write(vault, home, "Session — unmatched 0.md", session_id="omp:B"))
    assert result["status"] == "exists-same" and result["session_closure"]["status"] == "closed"
    assert ids(pending) == [] and ids(closed) == ["omp:A", "omp:B"]

    # Canonical namespaces apply equally to active OMP, Codex, OpenCode and raw Claude ids.
    cases = (({"PI_SESSION_ID": "same"}, "omp:same"),
             ({"CODEX_THREAD_ID": "same"}, "codex:same"),
             ({"CODEX_SESSION_ID": "legacy"}, "codex:legacy"),
             ({"OPENCODE_SESSION_ID": "same"}, "opencode:same"),
             ({"CLAUDE_CODE_SESSION_ID": "same"}, "same"))
    for number, (identity, canonical) in enumerate(cases):
        register(home, canonical)
        register(home, f"omp:unrelated-{number}")
        result = saved(write(vault, home, f"Session — host {number}.md", identity=identity))
        assert result["session_closure"] == {"session_id": canonical, "status": "closed"}, result
        assert canonical not in ids(pending) and f"omp:unrelated-{number}" in ids(pending)

    # Two host families in one process are an inherited neighbour, not an identity:
    # the note is saved but no audit is closed on a guess.
    before = pending.read_bytes(), closed.read_bytes()
    result = saved(write(vault, home, "Session — mixed hosts.md",
                         identity={"PI_SESSION_ID": "same", "CODEX_THREAD_ID": "foreign"}))
    assert result["session_closure"]["status"] == "no-identity", result
    assert before == (pending.read_bytes(), closed.read_bytes())

    # A failed archive append cannot erase pending work. Damaged unrelated bytes survive
    # a later successful close instead of being silently dropped during a rewrite.
    error_home = root / "append-failure"
    error_pending, error_closed = logs(error_home)
    register(error_home, "omp:retry")
    damaged = b"not json\n\xff\n"
    with error_pending.open("ab") as handle:
        handle.write(damaged)
    error_closed.mkdir()
    before_pending = error_pending.read_bytes()
    try:
        unclosed.close_pending("omp:retry", error_pending, error_closed)
    except OSError:
        pass
    else:
        raise AssertionError("closing succeeded with an unwritable archive")
    assert error_pending.read_bytes() == before_pending
    error_closed.rmdir()
    with patch.object(unclosed.os, "replace", side_effect=OSError("replace failed")):
        try:
            unclosed.close_pending("omp:retry", error_pending, error_closed)
        except OSError:
            pass
        else:
            raise AssertionError("closing hid a failed pending replacement")
    assert error_pending.read_bytes() == before_pending
    assert ids(error_closed) == ["omp:retry"]
    assert unclosed.close_pending("omp:retry", error_pending, error_closed) == "already-closed"
    assert error_pending.read_bytes() == damaged
    assert ids(error_closed) == ["omp:retry"]

    # A writer bookkeeping error is visible without pretending the saved note vanished.
    error_home = root / "writer-failure"
    error_pending, error_closed = logs(error_home)
    register(error_home, "omp:failure")
    error_closed.mkdir()
    done = write(vault, error_home, "Session — bookkeeping error.md", session_id="omp:failure")
    result = saved(done)
    assert result["session_closure"]["status"] == "error", result
    assert result["session_closure"]["warning"] in done.stderr
    assert ids(error_pending) == ["omp:failure"]
    assert (vault / "sessions" / "Session — bookkeeping error.md").exists()

    # Pause a close after its snapshot, then contend with a genuine second process.
    # Without a shared lock, registration can finish before the stale rewrite loses B.
    close_code = '''
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import unclosed
pending, closed, ready, release = map(Path, sys.argv[2:])
original = Path.read_bytes
def snapshot(path):
    data = original(path)
    if path == pending:
        ready.touch()
        deadline = time.monotonic() + 10
        while not release.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("close barrier timed out")
            time.sleep(0.01)
    return data
Path.read_bytes = snapshot
print(unclosed.close_pending("omp:A", pending, closed))
'''
    register_code = '''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import unclosed
home, attempted = map(Path, sys.argv[2:])
attempted.touch()
unclosed.record_pending(home / "B.jsonl", home / "closing-sessions.jsonl", "omp:B",
                        closed_log=home / "closed-sessions.jsonl")
'''
    writer_code = '''
import runpy, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
script, attempted, vault = sys.argv[2:]
Path(attempted).touch()
sys.argv = [script, "--vault", vault]
runpy.run_path(script, run_name="__main__")
'''
    for operation in ("register", "write"):
        concurrent_home = root / f"concurrent-{operation}"
        concurrent_pending, concurrent_closed = logs(concurrent_home)
        register(concurrent_home, "omp:A")
        if operation == "write":
            register(concurrent_home, "omp:B")
        ready, release, attempted = (concurrent_home / name for name in ("ready", "release", "attempted"))
        first = subprocess.Popen([sys.executable, "-c", close_code, str(SCRIPTS),
                                  str(concurrent_pending), str(concurrent_closed), str(ready), str(release)],
                                 env=environment(concurrent_home), stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        second = None
        try:
            wait_for(ready, first)
            if operation == "register":
                command = [sys.executable, "-c", register_code, str(SCRIPTS),
                           str(concurrent_home), str(attempted)]
            else:
                command = [sys.executable, "-c", writer_code, str(SCRIPTS), str(WRITER),
                           str(attempted), str(vault)]
            second = subprocess.Popen(command, env=environment(concurrent_home), stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if operation == "write":
                second.stdin.write(json.dumps({"filename": "Session — concurrent B.md",
                                               "content": CONTENT, "session_id": "omp:B"}))
                second.stdin.close()
                second.stdin = None
            wait_for(attempted, second)
            try:
                second.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                pass
            else:
                raise AssertionError(("competing operation bypassed the close lock", second.communicate()))
            release.touch()
            first_out, first_error = first.communicate(timeout=10)
            second_out, second_error = second.communicate(timeout=10)
            assert first.returncode == 0 and first_out.strip() == "closed", (first_out, first_error)
            assert second.returncode == 0, second_error
            if operation == "register":
                assert ids(concurrent_pending) == ["omp:B"]
                assert ids(concurrent_closed) == ["omp:A"]
            else:
                assert json.loads(second_out)["session_closure"] == {"session_id": "omp:B", "status": "closed"}
                assert ids(concurrent_pending) == []
                assert ids(concurrent_closed) == ["omp:A", "omp:B"]
        finally:
            release.touch()
            for child in (first, second):
                if child is not None and child.poll() is None:
                    child.kill()
                    child.communicate()

print("ok")
