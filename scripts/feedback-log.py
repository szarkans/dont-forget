#!/usr/bin/env python3
"""Append one validated memory-search event to a JSONL journal."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from common import DEFAULT_FEEDBACK as DEFAULT_FILE

# saved-work / noise / false-note / proven-miss are about what a SEARCH returned.
# rejected is about what the user turned down when it was offered for saving; mixing it
# into "noise" would corrupt the search-quality numbers the other four exist to build.
VERDICTS = ("saved-work", "noise", "false-note", "proven-miss", "rejected")
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
    misses = 0
    for line in args.file.read_text(encoding="utf-8").splitlines():
        try:
            verdict = json.loads(line)["verdict"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if verdict == "proven-miss":
            misses += 1
    result = {"logged": record["verdict"], "proven_misses": misses}
    if misses >= MISS_TRIGGER:
        result["trigger"] = (f"{misses} proven misses recorded: full-text search is not enough. "
                             "Time to revisit the engine — embeddings and the benchmark.")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
