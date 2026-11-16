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
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from common import HOME_DIR
from session_sources import (active_session_identity, claude_projects, codex_root, codex_session,
                             opencode_session, opencode_sessions, omp_root, omp_session,
                             parse_rollout_name, resolve_codex_thread, session_facts,
                             validate_session_id)

if os.name == "nt":
    import msvcrt
else:
    import fcntl

PROJECTS = claude_projects()
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
CACHE_VERSION = 3


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


@contextmanager
def _pending_lock(pending_log: Path):
    """Serialize registrations and closes on a stable inode, not the replaced log."""
    pending_log.parent.mkdir(parents=True, exist_ok=True)
    with pending_log.with_name(f".{pending_log.name}.lock").open("a+b") as lock:
        if os.name == "nt":
            lock.seek(0)
            if not lock.read(1):
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def record_pending(path: str | Path, log: Path = CLOSING, session_id: str | None = None,
                   *, closed_log: Path = CLOSED) -> None:
    """Register an audit; only a session note for this exact id can close it."""
    session_id = validate_session_id(session_id or Path(path).stem)
    with _pending_lock(log):
        if session_id in _recorded(closed_log) or session_id in _recorded(log):
            return
        entry = {"session_id": session_id, "path": str(path),
                 "ts": datetime.datetime.now().isoformat(timespec="seconds")}
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def close_pending(session_id: str, pending_log: Path = CLOSING,
                  closed_log: Path = CLOSED) -> str:
    """Close one canonical id, returning closed, already-closed, or not-pending.

    Append before removing the matching pending rows, under the registration lock.
    A failed append leaves them pending; a failed replacement can be retried without
    duplicating the closed entry. Unrelated rows, including damaged ones, are preserved.
    """
    session_id = validate_session_id(session_id)
    with _pending_lock(pending_log):
        known = session_id in _recorded(closed_log)
        try:
            lines = pending_log.read_bytes().splitlines(keepends=True)
        except FileNotFoundError:
            return "already-closed" if known else "not-pending"
        remaining, matching = [], None
        for line in lines:
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                entry = None
            if isinstance(entry, dict) and entry.get("session_id") == session_id:
                matching = matching or entry
            else:
                remaining.append(line)
        if matching is None:
            return "already-closed" if known else "not-pending"
        if not known:
            closed_log.parent.mkdir(parents=True, exist_ok=True)
            with closed_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(matching, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=pending_log.parent, prefix=".closing-",
                                             delete=False) as handle:
                temporary = Path(handle.name)
                handle.writelines(remaining)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, pending_log)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return "already-closed" if known else "closed"


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
    """The few things the digest needs from one Claude transcript.

    Only user lines are parsed: the rest is tool payload and attachments, which is the
    bulk of a real transcript and none of the answer. A skill invocation is not evidence
    that the audit happened — only a session note or an explicit dismissal closes a
    session — so this reads words, not commands.
    """
    found = {"session_id": path.stem, "cwd": "", "entrypoint": "", "first_message": "",
             "user_turns": 0, "closed": False}
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if '"user"' not in line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("sessionId"):
                found["session_id"] = entry["sessionId"]
            if entry.get("type") != "user":
                continue
            found["cwd"] = found["cwd"] or entry.get("cwd") or ""
            found["entrypoint"] = found["entrypoint"] or entry.get("entrypoint") or ""
            message = entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            text = _user_text(content)
            if text is None or entry.get("isMeta") or text.startswith("<"):
                continue
            found["user_turns"] += 1
            if not found["first_message"]:
                found["first_message"] = " ".join(text.split())[:MESSAGE_CHARS]
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


def _save_cache_safe(files: dict, stats: dict) -> None:
    """A cache write failure costs the next scan time, not the list in this one."""
    temporary = None
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=CACHE.parent, prefix=".unclosed-",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps({"version": CACHE_VERSION, "files": files},
                                    ensure_ascii=False).encode("utf-8"))
        os.replace(temporary, CACHE)
    except OSError as error:
        message = f"unclosed-session cache could not be written: {error}"
        stats["errors"].append(message)
        print(f"dont-forget: {message}", file=sys.stderr)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _cache_entry(cache: dict, key: str, stamp: tuple) -> dict | None:
    entry = cache.get(key)
    if entry and entry.get("mtime") == stamp[0] and entry.get("size") == stamp[1]:
        return entry
    return None


def _codex_groups(cutoff: float, before: float) -> tuple[list[dict], list[str]]:
    """One selected rollout per stable thread, plus visible ambiguity diagnostics."""
    groups: dict[str, list[Path]] = {}
    errors: list[str] = []
    root = codex_root()
    if not root.is_dir():
        return [], errors
    try:
        paths = list(root.rglob("rollout-*"))
    except OSError as error:
        errors.append(f"Codex session store could not be read: {root}: {error}")
        return [], errors
    for path in paths:
        if path.suffix not in (".jsonl", ".zst") or not path.is_file():
            continue
        identity = parse_rollout_name(path.name)
        if identity is None:
            continue
        try:
            stat = path.stat()
        except OSError as error:
            errors.append(f"Codex rollout could not be read: {path}: {error}")
            continue
        if not cutoff <= stat.st_mtime <= before:
            continue
        groups.setdefault(identity[0], []).append(path)
    candidates = []
    for thread, paths in groups.items():
        try:
            selected = resolve_codex_thread(thread)
        except ValueError as error:
            errors.append(f"Codex thread {thread}: {error}")
            continue
        try:
            stat = selected.stat()
        except OSError as error:
            errors.append(f"Codex rollout could not be read: {selected}: {error}")
            continue
        if cutoff <= stat.st_mtime <= before:
            candidates.append({"path": str(selected), "mtime": stat.st_mtime,
                               "size": stat.st_size, "thread": thread})
    return candidates, errors


def _omp_candidates(cutoff: float, before: float) -> tuple[list[dict], list[str]]:
    candidates, errors = [], []
    root = omp_root()
    if not root.is_dir():
        return candidates, errors
    try:
        paths = list(root.glob("*/*.jsonl"))
    except OSError as error:
        errors.append(f"OMP session store could not be read: {root}: {error}")
        return candidates, errors
    for path in paths:
        try:
            stat = path.stat()
        except OSError as error:
            errors.append(f"OMP transcript could not be read: {path}: {error}")
            continue
        if cutoff <= stat.st_mtime <= before:
            candidates.append({"path": str(path), "mtime": stat.st_mtime, "size": stat.st_size})
    return candidates, errors


def scan(root: Path | None = None, days: int = DAYS, seconds: float | None = None,
         stats: dict | None = None, *, current_session_id: str | None = None) -> list[dict]:
    """Unclosed interactive sessions of the window, newest first.

    A cache per path keyed by (mtime, size) is what keeps the startup digest inside its
    ten seconds: transcripts change on every tool call, so anything finer would be cold
    every time, and anything coarser would keep reading files that have not moved.
    `seconds` caps the reading on a cold cache; what is left is read on the next start,
    and `stats["unscanned"]` says how many files that cap skipped, so a short list is
    never mistaken for the whole one. Discovery failures are reported, never rendered as
    "nothing was there".

    Only `current_session_id` is excluded: a parent's inherited variables name another
    session and must not hide a real one.
    """
    include_other = root is None
    root = root or PROJECTS
    if stats is None:
        stats = {}
    stats.setdefault("unscanned", 0)
    stats.setdefault("errors", [])
    stats.setdefault("unclassified", 0)
    now = time.time()
    cutoff = now - days * 86400
    closed = _recorded(CLOSED)
    dismissed = _recorded(DISMISSED)
    cache = _load_cache()
    deadline = now + seconds if seconds is not None else None
    files: dict[str, dict] = {}
    out: list[dict] = []

    def fresh(key: str, stamp: tuple, build) -> dict | None:
        entry = _cache_entry(cache, key, stamp)
        if entry is not None:
            files[key] = entry
            return entry
        if deadline is not None and time.time() > deadline:
            stats["unscanned"] += 1
            return None
        try:
            entry = build()
        except (OSError, ValueError, sqlite3.Error) as error:
            stats["errors"].append(f"{key}: {error}")
            print(f"dont-forget: cannot read {key}: {error}", file=sys.stderr)
            return None
        entry["mtime"], entry["size"] = stamp
        files[key] = entry
        return entry

    try:
        claude_paths = list(root.glob("*/*.jsonl"))
    except OSError as error:
        stats["errors"].append(f"Claude session store could not be read: {root}: {error}")
        claude_paths = []
    for path in claude_paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        if not path.is_file() or stat.st_mtime < cutoff or stat.st_mtime > now - STALE:
            continue
        key = str(path)
        entry = fresh(key, (stat.st_mtime, stat.st_size), lambda path=path: facts(path))
        if entry is None:
            continue
        if entry.get("entrypoint") != "cli" or entry.get("user_turns", 0) < MIN_TURNS:
            continue
        if entry["session_id"] in closed or entry["session_id"] in dismissed:
            continue
        if entry["session_id"] == current_session_id:
            continue
        out.append({"session_id": entry["session_id"], "harness": "claude", "path": key,
                    "mtime": stat.st_mtime, "cwd": entry.get("cwd", ""),
                    "first_message": entry.get("first_message", ""),
                    "user_turns": entry["user_turns"]})

    if include_other:
        omp, omp_errors = _omp_candidates(cutoff, now - STALE)
        codex, codex_errors = _codex_groups(cutoff, now - STALE)
        stats["errors"].extend(omp_errors + codex_errors)
        candidates = omp + codex
        try:
            candidates.extend(opencode_sessions(cutoff, now - STALE))
        except (OSError, sqlite3.Error, ValueError) as error:
            stats["errors"].append(f"OpenCode session discovery failed: {error}")
            print(f"dont-forget: OpenCode session discovery failed: {error}", file=sys.stderr)
        for candidate in sorted(candidates, key=lambda item: item["mtime"], reverse=True):
            key = candidate["path"]
            stamp = (candidate["mtime"], candidate.get("size"))

            def build(candidate=candidate, key=key):
                if key.startswith("opencode://"):
                    return session_facts(opencode_session(key.removeprefix("opencode://")))
                if "thread" in candidate:
                    return session_facts(codex_session(Path(key)))
                return session_facts(omp_session(Path(key)))

            entry = fresh(key, stamp, build)
            if entry is None:
                continue
            canonical = entry["session_id"]
            if canonical in closed or canonical in dismissed or canonical == current_session_id:
                continue
            if entry["user_turns"] < MIN_TURNS:
                continue
            if entry["harness"] == "omp":
                if entry.get("role") == "sub":
                    continue
                if entry.get("role") is None:
                    if entry.get("role_conflict"):
                        stats["errors"].append(
                            f"OMP session {canonical} has conflicting role markers; classified unknown")
                    stats["unclassified"] += 1
                    out.append({**entry, "path": key, "unclassified": True})
                    continue
            elif entry.get("entrypoint") not in ("cli", "vscode"):
                continue
            out.append({**entry, "path": key})
    _save_cache_safe(files, stats)
    out.sort(key=lambda session: (session["mtime"], session["path"]), reverse=True)
    return out


def dismiss(session_id: str, reason: str = "", log: Path = DISMISSED) -> dict:
    entry = {"session_id": session_id, "reason": reason,
             "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _current_identity() -> str | None:
    """The canonical id of this process's own session, or None when not unambiguous."""
    harness, identity = active_session_identity()
    if not identity or harness == "unknown":
        return None
    return identity if harness == "claude" else f"{harness}:{identity}"


def main() -> None:
    parser = argparse.ArgumentParser(description="list sessions that never ran the session skill")
    parser.add_argument("--list", action="store_true", help="one line per session, newest first")
    parser.add_argument("--days", type=int, default=DAYS)
    parser.add_argument("--dismiss", metavar="SESSION_ID", help="stop listing this session")
    parser.add_argument("--reason", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.dismiss:
        print(json.dumps(dismiss(validate_session_id(args.dismiss), args.reason), ensure_ascii=False))
        if not args.list and not args.json:
            return
    if not (args.list or args.json):
        parser.print_help()
        return

    stats: dict = {}
    sessions = scan(days=args.days, stats=stats, current_session_id=_current_identity())
    if args.json:
        print(json.dumps({"sessions": sessions, "errors": stats.get("errors", []),
                          "unscanned": stats.get("unscanned", 0),
                          "unclassified": stats.get("unclassified", 0)},
                         ensure_ascii=False, indent=2))
        return
    for session in sessions:
        day = datetime.datetime.fromtimestamp(session["mtime"]).strftime("%Y-%m-%d")
        kind = "unclassified" if session.get("unclassified") else session.get("entrypoint", "")
        print(f"{day}  {kind}  {session['cwd']}  {session['first_message']}  {session['path']}")
    print(f"{len(sessions)} unclosed session(s) in {args.days} days")
    for error in stats.get("errors", []):
        print(f"dont-forget: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
