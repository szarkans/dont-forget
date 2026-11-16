#!/usr/bin/env python3
"""audit-log.py appends every candidate — accepted and rejected — with its session."""

import _selftest_env  # noqa: F401

import json
import os
import subprocess
import sys
from pathlib import Path

from common import HOME_DIR

SCRIPT = Path(__file__).with_name("audit-log.py")
LOG = HOME_DIR / "audit-candidates.jsonl"


def run(payload, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          input=json.dumps(payload), capture_output=True, text=True)


ok = run([{"candidate": "thinking blocks exist in Claude too", "kind": "correction",
           "destination": "vault"},
          {"candidate": "PWD env is not cwd", "destination": "nowhere"}],
         "--session", "devin:test")
assert ok.returncode == 0, ok.stderr
assert json.loads(ok.stdout)["logged"] == 2
lines = [json.loads(line) for line in LOG.read_text().splitlines()]
assert lines[-1]["destination"] == "nowhere" and lines[-1]["session_id"] == "devin:test"
assert "ts" in lines[-1]

single = run({"candidate": "one", "destination": "runbook"})
assert single.returncode == 0 and json.loads(single.stdout)["logged"] == 1

for bad in ({"candidate": "x", "destination": "maybe"},
            {"candidate": "", "destination": "vault"},
            {"destination": "vault"},
            "not json"):
    failed = run(bad)
    assert failed.returncode != 0, (bad, failed.stdout)

# A bad batch writes nothing, not a half-batch.
before = LOG.read_text().splitlines()
failed = run([{"candidate": "good", "destination": "vault"},
              {"candidate": "bad", "destination": "noway"}])
assert failed.returncode != 0
assert LOG.read_text().splitlines() == before

print("ok")
