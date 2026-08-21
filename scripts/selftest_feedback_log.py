#!/usr/bin/env python3
"""Dependency-free self-check for feedback-log.py."""

import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).with_name("feedback-log.py")

with tempfile.TemporaryDirectory() as tmp:
    journal = Path(tmp) / "nested" / "feedback.jsonl"
    event = {"verdict": "saved-work", "query": "release rule", "notes": ["rules/release.md"], "note": "Avoided repeating the investigation."}
    subprocess.run(
        ["python3", str(SCRIPT), "--file", str(journal)],
        input=json.dumps(event), text=True, check=True,
    )
    record = json.loads(journal.read_text(encoding="utf-8"))
    assert {key: record[key] for key in event} == event
    assert datetime.fromisoformat(record["ts"]).utcoffset().total_seconds() == 0

    # The return-to-the-engine trigger has to fire by itself, or "deferred" becomes "cancelled".
    for n in range(3):
        miss = {"verdict": "proven-miss", "query": f"q{n}", "notes": [], "note": "found later by other words"}
        done = subprocess.run(
            ["python3", str(SCRIPT), "--file", str(journal)],
            input=json.dumps(miss), text=True, capture_output=True, check=True,
        )
        answer = json.loads(done.stdout)
        assert answer["proven_misses"] == n + 1
        assert ("trigger" in answer) == (n + 1 >= 3), answer

    bad = subprocess.run(
        ["python3", str(SCRIPT), "--file", str(journal)],
        input='{"verdict":"guess","query":"x","notes":[],"note":"x"}',
        text=True, capture_output=True,
    )
    assert bad.returncode != 0
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 4

print("ok")
