#!/usr/bin/env python3
"""Dependency-free self-check for handoff-upsert.py."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("handoff-upsert.py")


def invoke(vault: Path, session: str, count: int, date: str) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"session_note": session, "open_count": count, "date": date})
    return subprocess.run([sys.executable, str(SCRIPT), "--vault", str(vault)], input=payload,
                          text=True, capture_output=True, check=False)


with tempfile.TemporaryDirectory(prefix="dont-forget-handoff-") as directory:
    vault = Path(directory)
    target = vault / "Meta — DF Handoff.md"

    created = invoke(vault, "Session — 2026-08-21 first", 3, "2026-08-21")
    assert created.returncode == 0, created.stderr
    assert target.read_text().startswith("# Meta — DF Handoff\n")
    assert "- 2026-08-21 · df · open 3 · [[Session — 2026-08-21 first]]" in target.read_text()

    added = invoke(vault, "Session — 2026-08-21 second", 1, "2026-08-21")
    assert added.returncode == 0, added.stderr
    assert target.read_text().count("[[Session — 2026-08-21 second]]") == 1

    repeated = invoke(vault, "Session — 2026-08-21 first", 2, "2026-08-21")
    assert repeated.returncode == 0, repeated.stderr
    text = target.read_text()
    assert text.count("[[Session — 2026-08-21 first]]") == 1
    assert "open 2" in next(line for line in text.splitlines() if "[[Session — 2026-08-21 first]]" in line)

    long_session = "Session — 2026-08-21 " + "тема" * 19
    assert invoke(vault, long_session, 7, "2026-08-21").returncode == 0
    long_line = next(line for line in target.read_text().splitlines() if f"[[{long_session}]]" in line)
    assert len(long_line.encode()) <= 200
    assert f"[[{long_session}]]" in long_line

    old_lines = [f"- 2020-01-{day:02d} · df · open 1 · [[Session old {day}]]" + " x" * 900
                 for day in range(1, 32)]
    target.write_text("# Meta — DF Handoff\n" + "\n".join(old_lines) + "\n")
    overflow = invoke(vault, "Session — 2026-08-21 overflow", 1, "2026-08-21")
    assert overflow.returncode == 0, overflow.stderr
    text = target.read_text()
    assert len(text.encode()) <= 56 * 1024
    assert "[[Session — 2026-08-21 overflow]]" in text
    assert "> _overflow: " in text
    assert "[[Session old 1]]" not in text

print("ok")
