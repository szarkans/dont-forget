#!/usr/bin/env python3
"""Find this session's transcript and hand it out in readable pieces.

Session close is done by a fresh sub-agent, because whoever lived through the session is
the worst possible auditor of it: their context is full, they are tired, and they are
blind to whatever became routine along the way. A fresh reader needs the transcript as a
file — "remember the conversation" is exactly the failure being designed around.

The raw JSONL is not that file. It is megabytes of tool payloads, attachments and
internal bookkeeping around a much smaller conversation, so this pulls out what was said
and leaves the machinery behind.

A long session is split rather than duplicated: each piece goes to its own reader. The
split follows the compaction boundaries the transcript already records, because that is
where the session actually lost its memory; with no boundary recorded it falls back to
equal pieces by size.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
# Above this many characters of conversation, one reader is being asked to hold too much —
# which is the very problem this exists to avoid.
SPLIT_ABOVE = 120_000


def project_dir(cwd: Path | None = None) -> Path:
    """Claude Code names a project's directory after its path.

    Both separators and dots are flattened to a hyphen, so /home/x/.claude/skills/plugin
    becomes -home-x--claude-skills-plugin — the double hyphen is the dot of ".claude".
    """
    here = (cwd or Path.cwd()).resolve()
    return PROJECTS / str(here).replace("/", "-").replace(".", "-")


def newest_transcript(cwd: Path | None = None) -> Path | None:
    directory = project_dir(cwd)
    files = [path for path in directory.glob("*.jsonl") if path.is_file()]
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def _text(content) -> str:
    """Whatever the entry actually says, whichever shape this version wrote it in."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content
                         if isinstance(part, dict) and part.get("type") == "text")
    return ""


def turns(path: Path) -> list[dict]:
    """The conversation: who said what, in order, with compaction boundaries kept."""
    out: list[dict] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("subtype") == "compact_boundary" or entry.get("isCompactSummary"):
                out.append({"role": "compaction", "text": "--- the context was compacted here ---"})
                continue
            if entry.get("type") not in ("user", "assistant"):
                continue
            message = entry.get("message") or {}
            said = _text(message.get("content") if isinstance(message, dict) else None)
            if said.strip():
                out.append({"role": entry.get("type"), "text": said.strip()})
    return out


def split(conversation: list[dict], limit: int = SPLIT_ABOVE) -> list[list[dict]]:
    """One piece per reader: at the compaction boundaries, else in equal halves by size."""
    total = sum(len(turn["text"]) for turn in conversation)
    if total <= limit:
        return [conversation] if conversation else []

    pieces, current = [], []
    for turn in conversation:
        if turn["role"] == "compaction" and current:
            pieces.append(current)
            current = []
            continue
        current.append(turn)
    if current:
        pieces.append(current)
    if len(pieces) > 1:
        return pieces

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
    return pieces


def render(piece: list[dict]) -> str:
    return "\n\n".join(f"[{turn['role']}]\n{turn['text']}" for turn in piece)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, help="a transcript file; default is this session's newest")
    parser.add_argument("--piece", type=int, help="print this piece (1-based) instead of the summary")
    parser.add_argument("--limit", type=int, default=SPLIT_ABOVE)
    args = parser.parse_args()

    path = args.path or newest_transcript()
    if path is None or not path.is_file():
        raise SystemExit(f"no transcript found in {project_dir()}")
    pieces = split(turns(path), args.limit)
    if args.piece:
        if not 1 <= args.piece <= len(pieces):
            raise SystemExit(f"piece {args.piece} of {len(pieces)}: out of range")
        print(render(pieces[args.piece - 1]))
        return
    print(json.dumps({
        "path": str(path),
        "pieces": len(pieces),
        "turns": sum(len(piece) for piece in pieces),
        "characters": sum(len(turn["text"]) for piece in pieces for turn in piece),
        "raw_bytes": path.stat().st_size,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
