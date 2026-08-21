#!/usr/bin/env python3
"""Append one validated memory-search event to a JSONL journal."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_FILE = Path.home() / ".dont-forget" / "feedback.jsonl"
VERDICTS = ("saved-work", "noise", "false-note", "proven-miss")
# Deferred is not cancelled only while the return condition is mechanical (SPEC §11).
MISS_TRIGGER = 3


def validated(value):
    if not isinstance(value, dict):
        raise ValueError("input must be a JSON object")
    verdict = value.get("verdict")
    query = value.get("query")
    notes = value.get("notes")
    note = value.get("note")
    if verdict not in VERDICTS:
        raise ValueError("invalid verdict")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if not isinstance(notes, list) or not all(isinstance(item, str) for item in notes):
        raise ValueError("notes must be a list of strings")
    if not isinstance(note, str) or "\n" in note or "\r" in note:
        raise ValueError("note must be one line")
    return {"verdict": verdict, "query": query, "notes": notes, "note": note}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    args = parser.parse_args()
    try:
        event = validated(json.load(sys.stdin))
    except (json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))
    record = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    args.file.parent.mkdir(parents=True, exist_ok=True)
    with args.file.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    misses = sum(1 for line in args.file.read_text(encoding="utf-8").splitlines()
                 if line.strip() and json.loads(line).get("verdict") == "proven-miss")
    result = {"logged": record["verdict"], "proven_misses": misses}
    if misses >= MISS_TRIGGER:
        result["trigger"] = (f"{misses} proven misses recorded: full-text search is not enough. "
                             "Time to revisit the engine — embeddings and the benchmark.")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
