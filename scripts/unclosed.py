#!/usr/bin/env python3
"""Sessions that ended without running the session skill, so their gotchas stay unsaved.

The hook can only know a session is finished by looking at the transcript afterwards:
there is no close event, and the Stop hook fires all the time. So the test here is
mechanical — interactive, enough real turns, quiet for half an hour, no real invocation
of `/dont-forget:session` — and not an agent's memory of how the session went.

A mention is not an invocation. Stop hooks and digests quote the command in plain text,
and counting that as a close reports sessions as saved that never ran the skill.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import tempfile
import time
from pathlib import Path

from common import HOME_DIR

PROJECTS = Path.home() / ".claude" / "projects"
CACHE = HOME_DIR / "unclosed-cache.json"
CLOSING = HOME_DIR / "closing-sessions.jsonl"
CLOSED = HOME_DIR / "closed-sessions.jsonl"
DISMISSED = HOME_DIR / "unclosed-dismissed.jsonl"

DAYS = 14
# Quiet this long in an interactive session usually means it is over; anything fresher
# may still be running in another window and closing it would audit half a conversation.
STALE = 30 * 60
MIN_TURNS = 3
MESSAGE_CHARS = 100
CACHE_VERSION = 1

CLOSE_COMMAND = "<command-name>/dont-forget:session"
CLOSE_SKILL = "dont-forget:session"


def _recorded(log: Path) -> set[str]:
    """Session ids already logged in an append-only file. A broken line loses itself only."""
    ids = set()
    try:
        with log.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    ids.add(json.loads(line)["session_id"])
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
    except OSError:
        pass
    return ids


def record_pending(path: Path, log: Path = CLOSING) -> None:
    """Note that this transcript is being audited; the session note is what closes it.

    A summary read can be abandoned halfway, so `--path` records a pending entry only.
    A write of a `type: session` note moves every pending entry to closed-sessions.jsonl.
    """
    session_id = path.stem
    if session_id in _recorded(CLOSED) or session_id in _recorded(log):
        return
    entry = {"session_id": session_id, "path": str(path),
             "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def close_pending(pending_log: Path = CLOSING, closed_log: Path = CLOSED) -> None:
    """Move audited sessions into the closed log once their session note is written.

    The move is all-or-nothing: the pending file is emptied only after every readable
    entry has been appended, so a failed append leaves the work offerable again.
    """
    try:
        lines = pending_log.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return
    known, entries = _recorded(closed_log), []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or not entry.get("session_id"):
            continue
        if entry["session_id"] in known:
            continue
        known.add(entry["session_id"])
        entries.append(entry)
    if entries:
        closed_log.parent.mkdir(parents=True, exist_ok=True)
        with closed_log.open("a", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    pending_log.write_text("", encoding="utf-8")


def _user_text(content) -> str | None:
    """The words a user turn carries, whether written as a string or as content blocks.

    None means the entry carries no words at all — a tool result or an attachment —
    and is no user turn, however it is shaped.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    texts = [block.get("text", "") for block in content
             if isinstance(block, dict) and block.get("type") == "text"]
    return "\n".join(texts) if texts else None


def facts(path: Path) -> dict:
    """The few things the digest needs from one transcript.

    Only user lines and lines that might carry a Skill call are parsed: the rest is tool
    payload and attachments, which is the bulk of a real transcript and none of the answer.
    """
    found = {"session_id": path.stem, "cwd": "", "entrypoint": "", "first_message": "",
             "user_turns": 0, "closed": False}
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            maybe_user = '"user"' in line
            maybe_skill = '"Skill"' in line and '"tool_use"' in line
            if not (maybe_user or maybe_skill):
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("sessionId"):
                found["session_id"] = entry["sessionId"]
            message = entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if entry.get("type") == "user":
                found["cwd"] = found["cwd"] or entry.get("cwd") or ""
                found["entrypoint"] = found["entrypoint"] or entry.get("entrypoint") or ""
                text = _user_text(content)
                if text is None:
                    continue
                if CLOSE_COMMAND in text:
                    found["closed"] = True
                elif not entry.get("isMeta") and not text.startswith("<"):
                    found["user_turns"] += 1
                    if not found["first_message"]:
                        found["first_message"] = " ".join(text.split())[:MESSAGE_CHARS]
            elif maybe_skill and entry.get("type") == "assistant":
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
                    if block.get("name") == "Skill" and arguments.get("skill") == CLOSE_SKILL:
                        found["closed"] = True
    return found


def _load_cache(path: Path = CACHE) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return {}
    files = data.get("files")
    return files if isinstance(files, dict) else {}


def _save_cache(files: dict, path: Path = CACHE) -> None:
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".unclosed-",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps({"version": CACHE_VERSION, "files": files},
                                    ensure_ascii=False).encode("utf-8"))
        os.replace(temporary, path)
    except OSError:
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def scan(root: Path | None = None, days: int = DAYS, seconds: float | None = None,
         stats: dict | None = None) -> list[dict]:
    """Unclosed interactive sessions of the window, newest first.

    A cache per path keyed by (mtime, size) is what keeps the startup digest inside its
    ten seconds: transcripts change on every tool call, so anything finer would be cold
    every time, and anything coarser would keep reading files that have not moved.
    `seconds` caps the reading on a cold cache; what is left is read on the next start,
    and `stats["unscanned"]` says how many files that cap skipped, so a short list is
    never mistaken for the whole one.
    """
    root = root or PROJECTS
    if stats is not None:
        stats["unscanned"] = 0
    now = time.time()
    cutoff = now - days * 86400
    current = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    closed = _recorded(CLOSED)
    dismissed = _recorded(DISMISSED)
    cache = _load_cache()
    deadline = now + seconds if seconds is not None else None
    files: dict[str, dict] = {}
    out: list[dict] = []
    for path in root.glob("*/*.jsonl"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if not path.is_file() or stat.st_mtime < cutoff or stat.st_mtime > now - STALE:
            continue
        key = str(path)
        entry = cache.get(key)
        if not (entry and entry.get("mtime") == stat.st_mtime and entry.get("size") == stat.st_size):
            if deadline is not None and time.time() > deadline:
                if stats is not None:
                    stats["unscanned"] += 1
                continue
            try:
                entry = facts(path)
            except OSError:
                continue
            entry["mtime"], entry["size"] = stat.st_mtime, stat.st_size
        files[key] = entry
        if entry.get("entrypoint") != "cli" or entry.get("user_turns", 0) < MIN_TURNS:
            continue
        if entry["session_id"] in closed or entry["session_id"] in dismissed:
            continue
        if entry["session_id"] == current or entry.get("closed"):
            continue
        out.append({"session_id": entry["session_id"], "path": key, "mtime": stat.st_mtime,
                    "cwd": entry.get("cwd", ""), "first_message": entry.get("first_message", ""),
                    "user_turns": entry["user_turns"]})
    _save_cache(files)
    out.sort(key=lambda session: (session["mtime"], session["path"]), reverse=True)
    return out


def dismiss(session_id: str, reason: str = "", log: Path = DISMISSED) -> dict:
    entry = {"session_id": session_id, "reason": reason,
             "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description="list sessions that never ran the session skill")
    parser.add_argument("--list", action="store_true", help="one line per session, newest first")
    parser.add_argument("--days", type=int, default=DAYS)
    parser.add_argument("--dismiss", metavar="SESSION_ID", help="stop listing this session")
    parser.add_argument("--reason", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.dismiss:
        print(json.dumps(dismiss(args.dismiss, args.reason), ensure_ascii=False))
        if not args.list:
            return
    if not (args.list or args.json):
        parser.print_help()
        return

    sessions = scan(days=args.days)
    if args.json:
        print(json.dumps(sessions, ensure_ascii=False, indent=2))
        return
    for session in sessions:
        day = datetime.datetime.fromtimestamp(session["mtime"]).strftime("%Y-%m-%d")
        print(f"{day}  {session['cwd']}  {session['first_message']}  {session['path']}")
    print(f"{len(sessions)} unclosed session(s) in {args.days} days")


if __name__ == "__main__":
    main()
