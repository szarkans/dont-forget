#!/usr/bin/env python3
"""Append audit candidates — accepted and rejected alike — to a JSONL journal.

The session audit shows its findings table to the user; what the table said used to
die with the terminal scrollback, so a wrongly judged `nowhere` left no trace. The
orchestrating agent pipes every row here before the write phase: what was proposed,
where it was sent, and what became of it. Reading is the filter, not logging.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from common import HOME_DIR

DEFAULT_FILE = HOME_DIR / "audit-candidates.jsonl"
DESTINATIONS = {"vault", "repo rules", "runbook", "nowhere"}


def validated(value):
    if not isinstance(value, dict):
        raise ValueError("each candidate must be a JSON object")
    candidate = value.get("candidate")
    destination = value.get("destination")
    if not isinstance(candidate, str) or not candidate.strip():
        raise ValueError("candidate must be a non-empty string")
    if destination not in DESTINATIONS:
        raise ValueError(f"destination must be one of {sorted(DESTINATIONS)}")
    record = {"candidate": candidate.strip(), "destination": destination}
    for key in ("kind", "outcome"):
        if isinstance(value.get(key), str) and value[key].strip():
            record[key] = value[key].strip()
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default="",
                        help="the audit_session_id these candidates belong to")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        parser.error(str(error))
    rows = payload if isinstance(payload, list) else [payload]
    try:
        records = [validated(row) for row in rows]
    except ValueError as error:
        parser.error(str(error))
    stamp = datetime.now(timezone.utc).isoformat()
    args.file.parent.mkdir(parents=True, exist_ok=True)
    with args.file.open("a", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(
                {"ts": stamp, "session_id": args.session, **record},
                ensure_ascii=False) + "\n")
    print(json.dumps({"logged": len(records), "file": str(args.file)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
