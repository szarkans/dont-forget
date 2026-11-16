#!/usr/bin/env python3
"""Find a session's transcript and hand it out in readable pieces, on any host.

Session close is done by a fresh sub-agent, because whoever lived through the session is
the worst possible auditor of it: their context is full, they are tired, and they are
blind to whatever became routine along the way. A fresh reader needs the transcript as a
file — "remember the conversation" is exactly the failure being designed around.

The raw stores are not that file. They are megabytes of tool payloads, attachments and
internal bookkeeping around a much smaller conversation, so this pulls out what was said
and leaves the machinery behind. Every host's store converges in `session_sources` to the
same shape — turns with roles and text — and this script is the single front door:
`--harness H --session ID` names a session explicitly, a bare run asks the process's own
host who it is, and `--path` reads one file or `opencode://`/`devin://` URI directly.

A long session is split rather than duplicated: each piece goes to its own reader. The
split follows the compaction boundaries the transcript already records, because that is
where the session actually lost its memory; with no boundary recorded it falls back to
equal pieces by size.

The first summary of an audit freezes a snapshot under DONT_FORGET_HOME/audit-transcripts
and registers the session as closing. Readers then run `--snapshot F --piece N` against
that frozen file, so a live transcript that keeps growing cannot change the audit under
their feet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from common import HOME_DIR
from session_sources import (active_session_identity, claude_projects, codex_session,
                             devin_export, devin_session, devin_state_db, image_line,
                             json_lines, omp_active, omp_session, omp_transcript_path,
                             opencode_db, opencode_export, opencode_session,
                             resolve_codex_thread, validate_session_id)
from unclosed import record_pending

PROJECTS = Path.home() / ".claude" / "projects"
# Above this many characters of conversation, one reader is being asked to hold too much —
# which is the very problem this exists to avoid.
SPLIT_ABOVE = 120_000
SNAPSHOTS = HOME_DIR / "audit-transcripts"


def project_dir(cwd: Path | None = None) -> Path:
    """Claude Code names a project's directory after its path.

    Both separators and dots are flattened to a hyphen, so /home/x/.claude/skills/plugin
    becomes -home-x--claude-skills-plugin — the double hyphen is the dot of ".claude".
    """
    here = (cwd or Path.cwd()).resolve()
    return PROJECTS / str(here).replace("/", "-").replace(".", "-")


def newest_transcript(cwd: Path | None = None) -> Path | None:
    """This session's transcript: the one Claude Code names, else the newest by mtime.

    Two sessions can run in the same folder, and then the newest file belongs to the
    other one. CLAUDE_CODE_SESSION_ID is the only thing that tells them apart. Falling
    back is still allowed — a hand-run script has no id — but never quietly.
    """
    directory = project_dir(cwd)
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if session_id:
        named = directory / f"{session_id}.jsonl"
        if named.is_file():
            return named
        reason = f"CLAUDE_CODE_SESSION_ID={session_id} has no transcript"
    else:
        reason = "CLAUDE_CODE_SESSION_ID is not set"
    files = [path for path in directory.glob("*.jsonl") if path.is_file()]
    newest = max(files, key=lambda path: path.stat().st_mtime) if files else None
    if newest is not None:
        print(f"{reason}; using newest by mtime: {newest}", file=sys.stderr)
    return newest


# A tool call is evidence: a command that failed, a pull request opened, a file written.
# The audit is asked whether errors were resolved and whether external systems were
# updated, and the answer to both lives in the calls — so they are kept, as one line each,
# while their payloads and results are not.
TOOL_LINE = 300


def _text(content) -> str:
    """What the entry says, plus a trace of what it did, in whichever shape it was written."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    said = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            said.append(part.get("text", ""))
        elif part.get("type") == "tool_use":
            arguments = json.dumps(part.get("input", {}), ensure_ascii=False)[:TOOL_LINE]
            said.append(f"<used {part.get('name', 'a tool')}: {arguments}>")
        elif part.get("type") == "image":
            said.append(image_line(part))
    return "\n".join(said)


def _claude_session(path: Path) -> dict:
    """The Claude JSONL store as a session dict: who said what, boundaries kept."""
    conversation: list[dict] = []
    meta: dict = {}
    for entry in json_lines(path):
        if entry.get("subtype") == "compact_boundary" or entry.get("isCompactSummary"):
            conversation.append(
                {"role": "compaction", "text": "--- the context was compacted here ---"})
            continue
        if entry.get("type") not in ("user", "assistant"):
            continue
        if not meta.get("session_id") and entry.get("sessionId"):
            meta["session_id"] = entry["sessionId"]
        if not meta.get("cwd") and entry.get("cwd"):
            meta["cwd"] = entry["cwd"]
        message = entry.get("message") or {}
        said = _text(message.get("content") if isinstance(message, dict) else None)
        if said.strip():
            conversation.append({"role": entry.get("type"), "text": said.strip()})
    if not conversation:
        raise ValueError(f"no conversation found in {path}")
    return {"harness": "claude", "session_id": meta.get("session_id") or path.stem,
            "cwd": meta.get("cwd", ""), "entrypoint": "cli",
            "turns": conversation, "path": str(path)}


def load_session(source) -> dict:
    """One store-neutral session dict from a path or `opencode://`/`devin://` URI.

    Format is detected from content, not from the filename: a Codex rollout starts with
    `session_meta`, an OMP transcript with `session`, a JSON file is an OpenCode or
    Devin export, and anything else is tried as Claude's record stream. An unreadable
    or empty source is an error — an audit silently attached to the wrong thing is
    worse than none.
    """
    text = str(source)
    if text.startswith("opencode://"):
        return opencode_session(text[len("opencode://"):])
    if text.startswith("devin://"):
        return devin_session(text)
    path = Path(text)
    if path.suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"cannot read {path}: {error}") from error
        if isinstance(data, dict) and isinstance(data.get("steps"), list):
            return devin_export(data, str(path))
        if isinstance(data, dict) and ("info" in data or "messages" in data):
            return opencode_export(data, str(path))
        raise ValueError(f"unrecognized transcript JSON: {path}")
    first = None
    for entry in json_lines(path):
        # OMP opens with a `title` record before its `session` header.
        if entry.get("type") == "title":
            continue
        first = entry
        break
    if first is None:
        raise ValueError(f"empty transcript: {path}")
    if first.get("type") == "session_meta":
        return codex_session(path)
    if first.get("type") == "session":
        return omp_session(path)
    return _claude_session(path)


def turns(path: Path) -> list[dict]:
    return load_session(path)["turns"]


def _claude_by_id(session_id: str) -> Path:
    """The transcript Claude names after this session id, wherever its project sits."""
    matches = [path for path in claude_projects().glob(f"*/{session_id}.jsonl")
               if path.is_file()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise ValueError(f"several Claude transcripts named {session_id}: "
                         + ", ".join(map(str, matches)))
    raise ValueError(f"Claude session {session_id} has no transcript under "
                     f"{claude_projects()}")


def resolve_session(harness: str, session_id: str, leaf: str | None = None) -> dict:
    """The session this host names, read through its own store."""
    if harness == "claude":
        return _claude_session(_claude_by_id(session_id))
    if harness == "codex":
        return codex_session(resolve_codex_thread(session_id))
    if harness == "opencode":
        return opencode_session(session_id)
    if harness == "omp":
        return omp_session(omp_transcript_path(session_id), leaf_id=leaf)
    if harness == "devin":
        return devin_session(session_id)
    raise ValueError(f"unknown harness: {harness}")


def active_session(harness: str, leaf: str | None = None) -> dict:
    """The running session on an explicit host, via its own native fields."""
    if harness == "omp":
        return omp_active(leaf_id=leaf)
    harness_name, identity = active_session_identity(harness)
    if not identity:
        raise ValueError(f"the {harness} session advertises no id; pass --session")
    return resolve_session(harness_name, identity, leaf=leaf)


def auto_session() -> dict:
    """The running process's own session, by its host's native identity.

    Auto trusts exactly one host family at a time. Claude keeps its mtime fallback
    because its store layout makes the fallback sane and it says so on stderr; the
    other hosts fail closed, because guessing a neighbouring session is the bug this
    whole layer exists to kill.
    """
    harness, identity = active_session_identity()
    if harness == "claude":
        path = newest_transcript()
        if path is None:
            raise ValueError(f"no transcript found in {project_dir()}")
        return _claude_session(path)
    if harness == "omp":
        return omp_active()
    if harness != "unknown":
        if not identity:
            raise ValueError(
                f"the {harness} session advertises no id; pass --session explicitly")
        return resolve_session(harness, identity)
    path = newest_transcript()
    if path is not None:
        return _claude_session(path)
    raise ValueError("no session identity found: run inside a session, or pass "
                     "--harness and --session (or --path) explicitly")


def split(conversation: list[dict], limit: int = SPLIT_ABOVE) -> list[list[dict]]:
    """One piece per reader: at the compaction boundaries, else in equal halves by size."""
    total = sum(len(turn["text"]) for turn in conversation)
    if total <= limit:
        return [conversation] if conversation else []

    at_compaction, current = [], []
    for turn in conversation:
        if turn["role"] == "compaction" and current:
            # The summary belongs to the stretch it closes: it stays at the end of
            # this piece rather than vanishing at the boundary it created.
            current.append(turn)
            at_compaction.append(current)
            current = []
            continue
        current.append(turn)
    if current:
        at_compaction.append(current)

    # A compaction boundary says where the session lost its memory; it says nothing about
    # how much sits on either side. A 400k stretch before the first compaction is still
    # too much for one reader, so every piece is then cut down to the limit as well.
    pieces = []
    for piece in at_compaction:
        pieces.extend(_by_size(piece, limit))
    return pieces


def _by_size(conversation: list[dict], limit: int) -> list[list[dict]]:
    total = sum(len(turn["text"]) for turn in conversation)
    if total <= limit:
        return [conversation] if conversation else []
    parts = max(2, -(-total // limit))
    target, pieces, current, used = total / parts, [], [], 0
    for turn in conversation:
        current.append(turn)
        used += len(turn["text"])
        if used >= target and len(pieces) < parts - 1:
            pieces.append(current)
            current, used = [], 0
    if current:
        pieces.append(current)
    # A piece that is only a boundary marker is no reader's assignment: it merges back
    # into the piece it closes, so "ALPHA checkpoint" is seen and nobody reads a stub.
    merged: list[list[dict]] = []
    for piece in pieces:
        if merged and all(turn["role"] == "compaction" for turn in piece):
            merged[-1].extend(piece)
        else:
            merged.append(piece)
    return merged


def render(piece: list[dict]) -> str:
    return "\n\n".join(f"[{turn['role']}]\n{turn['text']}" for turn in piece)


def audit_session_id(session: dict) -> str:
    """The canonical bookkeeping id: raw for Claude, namespaced for everyone else."""
    session_id = session["session_id"]
    return session_id if session["harness"] == "claude" \
        else f"{session['harness']}:{session_id}"


def _raw_bytes(session: dict) -> int:
    """Size of the underlying store: file bytes when there is a file, else the store's."""
    path = Path(session["path"])
    if path.is_file():
        return path.stat().st_size
    if session["harness"] == "opencode" and opencode_db().is_file():
        return opencode_db().stat().st_size
    if session["harness"] == "devin" and devin_state_db().is_file():
        return devin_state_db().stat().st_size
    return len(json.dumps(session["turns"], ensure_ascii=False))


def freeze_snapshot(session: dict, audit_id: str) -> Path:
    """The immutable file every reader of this audit gets, in place of the live store."""
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    snapshot = {key: session.get(key) for key in
                ("harness", "session_id", "cwd", "entrypoint", "path", "turns")}
    snapshot["audit_session_id"] = audit_id
    snapshot["frozen_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    name = f"{audit_id.replace(':', '-')}-{int(time.time() * 1000)}.json"
    target = SNAPSHOTS / name
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=SNAPSHOTS, prefix=".snapshot-",
                                         delete=False, mode="w", encoding="utf-8") as handle:
            temporary = Path(handle.name)
            json.dump(snapshot, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", help="a transcript file, or an opencode:// / devin:// URI")
    parser.add_argument("--harness", choices=("claude", "codex", "opencode", "omp", "devin"),
                        help="which host's store holds the session; needs --session")
    parser.add_argument("--session", help="the session id inside that host's store")
    parser.add_argument("--leaf", default=None,
                        help="OMP only: the branch leaf the live session is on")
    parser.add_argument("--piece", type=int,
                        help="print this piece (1-based) instead of the summary")
    parser.add_argument("--limit", type=int, default=SPLIT_ABOVE)
    parser.add_argument("--snapshot",
                        help="read a frozen snapshot instead of a live source")
    parser.add_argument("--discard-snapshot", metavar="FILE",
                        help="delete a snapshot file once its audit is written")
    args = parser.parse_args()

    if args.discard_snapshot:
        try:
            Path(args.discard_snapshot).unlink()
        except OSError as error:
            raise SystemExit(f"cannot discard {args.discard_snapshot}: {error}")
        print(json.dumps({"discarded": args.discard_snapshot}, ensure_ascii=False))
        return

    frozen = None
    if args.snapshot:
        try:
            frozen = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SystemExit(f"cannot read snapshot {args.snapshot}: {error}")
        conversation = frozen.get("turns")
        if not isinstance(conversation, list):
            raise SystemExit(f"snapshot {args.snapshot} carries no conversation")
        source_desc = args.snapshot
    else:
        if args.session is not None and not args.harness:
            raise SystemExit("--session needs --harness")
        if args.session is not None and not args.session.strip():
            raise SystemExit("--session is empty")
        if args.leaf is not None and args.harness != "omp":
            raise SystemExit("--leaf only means anything for --harness omp")
        try:
            if args.path:
                session = load_session(args.path)
            elif args.session is not None:
                session = resolve_session(args.harness, args.session, leaf=args.leaf)
            elif args.harness:
                session = active_session(args.harness, leaf=args.leaf)
            else:
                session = auto_session()
        except (OSError, ValueError) as error:
            raise SystemExit(f"dont-forget: {error}") from error
        conversation = session["turns"]
        if not conversation:
            raise SystemExit(
                f"dont-forget: no readable conversation found in {session['path']}")
        source_desc = session["path"]

    pieces = split(conversation, max(1, args.limit))
    if args.piece:
        if not 1 <= args.piece <= len(pieces):
            raise SystemExit(f"piece {args.piece} of {len(pieces)}: out of range")
        print(render(pieces[args.piece - 1]))
        return

    if frozen is not None:
        summary = {key: frozen.get(key) for key in
                   ("harness", "session_id", "audit_session_id", "path", "cwd")}
        summary.update({"snapshot_file": source_desc, "pieces": len(pieces),
                        "turns": len(conversation),
                        "characters": sum(len(turn["text"]) for turn in conversation)})
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    audit_id = validate_session_id(audit_session_id(session))
    snapshot_file = freeze_snapshot(session, audit_id)
    try:
        record_pending(source_desc, session_id=audit_id)
    except (OSError, ValueError) as error:
        print(f"dont-forget: could not record the session as closing: {error}",
              file=sys.stderr)
    summary = {
        "path": source_desc,
        "snapshot_file": str(snapshot_file),
        "harness": session["harness"],
        "session_id": session["session_id"],
        "audit_session_id": audit_id,
        "cwd": session.get("cwd", ""),
        "pieces": len(pieces),
        "turns": len(conversation),
        "characters": sum(len(turn["text"]) for turn in conversation),
        "raw_bytes": _raw_bytes(session),
    }
    for key in ("leaf_id", "branch_source"):
        if session.get(key) is not None:
            summary[key] = session[key]
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
