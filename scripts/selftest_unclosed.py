#!/usr/bin/env python3
"""Assert-based self-test for unclosed.py and the digest section built on it."""

import _selftest_env  # noqa: F401

import json
import os
import runpy
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import unclosed as UNCLOSED  # noqa: E402
from common import HOME_DIR  # noqa: E402

SCRIPTS = Path(__file__).parent
SCRIPT = SCRIPTS / "unclosed.py"
TRANSCRIPT = SCRIPTS / "transcript.py"
HOT_SCAN = SCRIPTS / "hot-scan.py"


def user(text, **extra) -> dict:
    return {"type": "user", "message": {"content": text}, **extra}


def assistant(content) -> dict:
    return {"type": "assistant", "message": {"content": content}}


def turns(prefix: str, count: int = 3) -> list[dict]:
    return [user(f"{prefix} message {n}") for n in range(count)]


with tempfile.TemporaryDirectory(prefix="dont-forget-unclosed-") as tmp:
    root = Path(tmp)
    home = root / "home"
    projects = home / ".claude" / "projects"
    projects.mkdir(parents=True)
    UNCLOSED.PROJECTS = projects
    env = {**os.environ, "HOME": str(home)}
    old = time.time() - 3600
    paths: dict[str, Path] = {}

    def write(session_id: str, records: list[dict], entrypoint: str = "cli",
              mtime: float = old, subagents: bool = False) -> Path:
        directory = projects / "proj"
        if subagents:
            directory = directory / session_id / "subagents"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{session_id}.jsonl"
        lines = [json.dumps({**record, "sessionId": session_id, "cwd": f"/work/{session_id}",
                             "entrypoint": entrypoint}) for record in records]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.utime(path, (mtime, mtime))
        paths[session_id] = path
        return path

    # Live in the window and unclosed.
    write("plain", [user("plain " + "detail " * 30), *turns("plain")[1:]])
    write("close-later", turns("close later"))
    for number in range(6):
        write(f"extra-{number}", turns(f"extra {number}"))
    # A mention is not an invocation. Stop hooks and digests quote the command in plain text,
    # and counting that as a close reports sessions as saved that never ran the skill.
    write("mention", [
        user("the digest says /dont-forget:session is the way"),
        assistant([{"type": "text", "text": "Run /dont-forget:session to close it."}]),
        user("ok"),
        user("later"),
    ])
    # Only the Skill tool's own `skill` field counts, and only the full name: this call
    # reads the session skill and is not it.
    write("skill-wrong", [
        user("first"),
        user("second"),
        assistant([{"type": "tool_use", "name": "Skill",
                    "input": {"skill": "dont-forget:about", "args": "/dont-forget:session"}}]),
        assistant([{"type": "tool_use", "name": "Skill", "input": {"skill": "session"}}]),
        user("third"),
    ])
    # A pasted screenshot arrives as content blocks; the text beside it is still a turn.
    write("blocks", [
        user([{"type": "text", "text": "look at this screenshot"},
              {"type": "image", "source": {"type": "base64", "data": "x"}}]),
        user([{"type": "text", "text": "and this one"}]),
        user([{"type": "tool_result", "content": "output only"}]),
        user([{"type": "text", "text": "third"}]),
    ])

    # Three real invocation shapes, all closed.
    write("closed-command", [
        user("first"),
        user("second"),
        user("<command-name>/dont-forget:session</command-name>\n"
             "<command-message>session</command-message>"),
        user("third"),
    ])
    write("closed-skill", [
        user("first"),
        user("second"),
        assistant([{"type": "tool_use", "name": "Skill",
                    "input": {"skill": "dont-forget:session"}}]),
        user("third"),
    ])
    write("closed-log", turns("logged"))
    closed_log = HOME_DIR / "closed-sessions.jsonl"
    closed_log.parent.mkdir(parents=True, exist_ok=True)
    closed_log.write_text(json.dumps({"session_id": "closed-log", "path": str(paths["closed-log"]),
                                      "ts": "2026-10-27T00:00:00"}) + "\n", encoding="utf-8")

    # Everything below has a reason not to be listed.
    write("sdk", turns("headless"), entrypoint="sdk-cli")
    write("short", turns("short", count=2))
    write("dismissed", turns("dismiss me"))
    write("current", turns("still mine"))
    write("fresh", turns("still running"), mtime=time.time())
    write("nested", turns("a subagent's words"), subagents=True)

    expected = {"plain", "close-later", "mention", "skill-wrong", "blocks",
                *(f"extra-{n}" for n in range(6))}

    # Dismissal is its own log, and the CLI is how it gets written.
    dismissed = subprocess.run([sys.executable, str(SCRIPT), "--dismiss", "dismissed",
                                "--reason", "handled elsewhere"], env=env, check=True,
                               capture_output=True, text=True)
    assert json.loads(dismissed.stdout)["reason"] == "handled elsewhere", dismissed.stdout
    assert (HOME_DIR / "unclosed-dismissed.jsonl").is_file()

    # The session running right now may still be alive: it never lists itself.
    os.environ["CLAUDE_CODE_SESSION_ID"] = "current"
    env["CLAUDE_CODE_SESSION_ID"] = "current"
    sessions = UNCLOSED.scan()
    found = {session["session_id"] for session in sessions}
    assert found == expected, (found, expected)
    by_id = {session["session_id"]: session for session in sessions}
    assert by_id["plain"]["user_turns"] == 3, by_id["plain"]
    assert len(by_id["plain"]["first_message"]) == 100, by_id["plain"]
    assert [session["mtime"] for session in sessions] == sorted(
        (session["mtime"] for session in sessions), reverse=True)
    for skipped in ("closed-command", "closed-skill", "closed-log", "sdk", "short",
                    "dismissed", "current", "fresh", "nested"):
        assert skipped not in found, skipped
    # A second pass is served by the cache and must answer the same.
    assert {session["session_id"] for session in UNCLOSED.scan()} == expected

    assert by_id["blocks"]["user_turns"] == 3, by_id["blocks"]
    assert by_id["blocks"]["first_message"] == "look at this screenshot", by_id["blocks"]

    listed = subprocess.run([sys.executable, str(SCRIPT), "--list"], env=env, check=True,
                            capture_output=True, text=True)
    assert "11 unclosed session(s) in 14 days" in listed.stdout, listed.stdout
    assert str(paths["plain"]) in listed.stdout, listed.stdout

    # A summary read of another session only marks it as closing: the audit can be
    # abandoned, so the digest keeps offering it until its session note is written.
    closing_log = HOME_DIR / "closing-sessions.jsonl"
    for _ in range(2):
        subprocess.run([sys.executable, str(TRANSCRIPT), "--path", str(paths["close-later"])],
                       env=env, check=True, capture_output=True)
    pending = [json.loads(line) for line in closing_log.read_text(encoding="utf-8").splitlines()]
    assert [entry["session_id"] for entry in pending] == ["close-later"], pending
    assert pending[0]["path"] == str(paths["close-later"]), pending
    assert "close-later" in {session["session_id"] for session in UNCLOSED.scan()}
    assert "close-later" not in closed_log.read_text(encoding="utf-8")

    (root / "vault").mkdir()

    def vault_write(filename: str, content: str) -> dict:
        done = subprocess.run(
            [sys.executable, str(SCRIPTS / "vault-write.py"), "--vault", str(root / "vault")],
            input=json.dumps({"filename": filename, "content": content}), env=env,
            check=True, capture_output=True, text=True)
        return json.loads(done.stdout)

    # A note that is not a session leaves the pending entry alone.
    assert vault_write("Atom — unrelated.md",
                       "---\ntype: atom\n---\nA claim. [[hub]]\n")["status"] == "created"
    assert closing_log.read_text(encoding="utf-8").strip(), "a non-session note moved it"
    assert "close-later" in {session["session_id"] for session in UNCLOSED.scan()}

    # The session note is the closure: after it lands the pending file is empty and the
    # session is gone from both the closed log and the digest.
    assert vault_write("Session — 2026-10-27 close-later.md",
                       "---\ntype: session\ndate: 2026-10-27\n---\n# S\n\n[[hub]]\n"
                       )["status"] == "created"
    assert closing_log.read_text(encoding="utf-8") == ""
    entries = [json.loads(line) for line in closed_log.read_text(encoding="utf-8").splitlines()]
    recorded = [entry for entry in entries if entry["session_id"] == "close-later"]
    assert len(recorded) == 1, recorded
    assert recorded[0]["path"] == str(paths["close-later"]), recorded
    assert "close-later" not in {session["session_id"] for session in UNCLOSED.scan()}

    # The session closing itself runs the summary without --path, and records nothing:
    # its real Skill invocation in the transcript is what marks it closed.
    workdir = root / "self-session"
    workdir.mkdir()
    self_dir = projects / str(workdir.resolve()).replace("/", "-").replace(".", "-")
    self_dir.mkdir()
    (self_dir / "self.jsonl").write_text(
        json.dumps(user("closing myself")) + "\n", encoding="utf-8")
    closed_before = closed_log.read_text(encoding="utf-8")
    subprocess.run([sys.executable, str(TRANSCRIPT)], cwd=workdir, env=env, check=True,
                   capture_output=True)
    assert closing_log.read_text(encoding="utf-8") == ""
    assert closed_log.read_text(encoding="utf-8") == closed_before

    # The digest carries the section, capped at five, with the pointer to the rest.
    hook = subprocess.run([sys.executable, str(HOT_SCAN), "--db", str(root / "missing.db"),
                           "--project", "", "--hook"], env=env, check=True,
                          capture_output=True, text=True)
    context = json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Unclosed sessions — 10 in 14 days" in context, context
    assert "`transcript.py --path <path>`" in context, context
    assert context.count("\n- ") == 5, context
    assert ('The rest: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/unclosed.py" --list`.'
            in context), context
    assert '— "the digest says /dont-forget:session is the way"' in context, context

    # A cold scan stopped by its deadline says how many files it never read, and the
    # header admits the count is a floor rather than a total.
    cache = HOME_DIR / "unclosed-cache.json"
    cache.unlink(missing_ok=True)
    stats: dict = {}
    assert UNCLOSED.scan(seconds=-1, stats=stats) == []
    eligible = [path for path in projects.glob("*/*.jsonl")
                if time.time() - UNCLOSED.DAYS * 86400 < path.stat().st_mtime
                < time.time() - UNCLOSED.STALE]
    assert stats["unscanned"] == len(eligible), (stats, len(eligible))
    hot = runpy.run_path(str(HOT_SCAN))
    shortened = hot["hook_payload"]({
        "tails": [], "gotchas": [], "godnotes": [], "note": "n",
        "unclosed": ["one line"], "unclosed_total": 7, "unclosed_unscanned": 3,
    })["hookSpecificOutput"]["additionalContext"]
    assert "Unclosed sessions — at least 7 in 14 days" in shortened, shortened

    empty_home = root / "empty-home"
    empty_home.mkdir()
    empty = subprocess.run([sys.executable, str(HOT_SCAN), "--db", str(root / "missing.db"),
                            "--project", "", "--hook"], env={**env, "HOME": str(empty_home)},
                           check=True, capture_output=True, text=True)
    assert "Unclosed sessions" not in empty.stdout, empty.stdout

print("ok")
