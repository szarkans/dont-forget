#!/usr/bin/env python3
"""Upsert a session pointer in the dont-forget handoff index."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

MAX_LINE_BYTES = 200
MAX_KB = 56
POINTER_DATE = re.compile(r"^- (\d{4}-\d{2}-\d{2})\b")


def config() -> dict:
    path = Path.home() / ".dont-forget" / "config.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def pointer(session_note: str, open_count: int, date: str) -> str:
    link = f"[[{session_note}]]"
    prefix = f"- {date} · df · open {open_count} · "
    budget = MAX_LINE_BYTES - len(link.encode())
    while len(prefix.encode()) > max(0, budget):
        prefix = prefix[:-1]
    return prefix + link


def render(existing: str, new_line: str, session_note: str) -> str:
    lines = existing.splitlines() if existing else ["# Meta — DF Handoff"]
    marker = f"[[{session_note}]]"
    lines = [line for line in lines if not line.startswith("> _overflow:")]
    matches = [index for index, line in enumerate(lines) if marker in line]
    if matches:
        lines[matches[0]] = new_line
        lines = [line for index, line in enumerate(lines) if index == matches[0] or marker not in line]
    else:
        lines.append(new_line)
    dropped = 0
    limit = MAX_KB * 1024
    while len(("\n".join(lines) + "\n" + (f"> _overflow: {dropped} dropped\n" if dropped else "")).encode()) > limit:
        dated = [(POINTER_DATE.match(line).group(1), index) for index, line in enumerate(lines)
                 if POINTER_DATE.match(line) and line != new_line]
        if not dated:
            break
        _, oldest = min(dated)
        lines.pop(oldest)
        dropped += 1
    if dropped:
        lines.append(f"> _overflow: {dropped} dropped")
    return "\n".join(lines) + "\n"


def atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                         prefix=".handoff-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", type=Path)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        session_note, date, open_count = payload["session_note"], payload["date"], payload["open_count"]
        if not isinstance(session_note, str) or not session_note or "]" in session_note:
            raise ValueError("session_note must be a non-empty wikilink-safe string")
        if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise ValueError("date must be YYYY-MM-DD")
        if not isinstance(open_count, int) or isinstance(open_count, bool) or open_count < 0:
            raise ValueError("open_count must be a non-negative integer")
        settings = config()
        vault = args.vault or Path(settings["vault"]).expanduser()
        note = settings.get("handoff_note", "Meta — DF Handoff")
        if not isinstance(note, str) or not note or Path(note).name != note:
            raise ValueError("handoff_note must be a filename stem")
        target = vault / f"{note}.md"
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        atomic_write(target, render(existing, pointer(session_note, open_count, date), session_note))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    print(json.dumps({"status": "upserted", "path": target.name}, separators=(",", ":")))


if __name__ == "__main__":
    main()
