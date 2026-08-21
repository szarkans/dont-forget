#!/usr/bin/env python3
"""Read recent open session threads from the disposable SQLite index."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path


DEFAULT_DB = Path.home() / ".dont-forget" / "index.db"
OPEN_ITEM = re.compile(r"^- \[ \](?:\s+.*)?$", re.MULTILINE)


def encoded(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def empty_payload() -> dict:
    return {"tails": [], "note": ""}


def read_tails(db_path: Path, window: int) -> list[str]:
    if not db_path.is_file():
        return []

    cutoff = (date.today() - timedelta(days=max(0, window))).isoformat()
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = con.execute(
            """SELECT n.path, c.body
               FROM notes n JOIN chunks c ON c.note_id = n.id
               WHERE lower(n.type) = 'session'
                 AND date(n.date) >= date(?)
                 AND instr(lower(c.heading_path), 'pending') > 0
               ORDER BY date(n.date) DESC, n.path, c.ord""",
            (cutoff,),
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return []

    tails = []
    for path, body in rows:
        session_name = Path(path).stem
        for match in OPEN_ITEM.finditer(body):
            item = match.group(0).strip()[2:].strip()
            tails.append(f"{item} (Session — {session_name})")
    return tails


def fit_budget(tails: list[str], note: str, budget: int) -> dict:
    # main() terminates the JSON with one newline; reserve it in the stdout cap.
    budget = max(0, budget - 1)
    complete = {"tails": tails, "note": note if tails else ""}
    if len(encoded(complete)) <= budget:
        return complete

    for count in range(len(tails), -1, -1):
        omitted = len(tails) - count
        marker = f"> _truncated: {omitted} more open threads (see handoff index)"
        candidate = {"tails": tails[:count] + [marker], "note": note}
        if len(encoded(candidate)) <= budget:
            return candidate

    return empty_payload()


def scan(db_path: Path, window: int, budget: int) -> dict:
    tails = read_tails(db_path, window)
    note = (
        f"dont-forget: open threads from the last {window} days. "
        "/dont-forget:about to recall, /dont-forget:this to persist."
    )
    return fit_budget(tails, note, budget)


def hook_payload(payload: dict) -> dict:
    context = ""
    if payload["tails"]:
        context = "\n".join([payload["note"], *(f"- {tail}" for tail in payload["tails"])])
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--window", type=int, default=7)
    parser.add_argument("--budget", type=int, default=8192)
    parser.add_argument("--hook", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    payload = scan(args.db, args.window, args.budget)
    output = hook_payload(payload) if args.hook else payload
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
