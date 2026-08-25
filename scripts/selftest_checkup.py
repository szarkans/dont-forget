#!/usr/bin/env python3
"""Dependency-free self-check for checkup.py."""

import json
import sqlite3
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).with_name("checkup.py")
SCHEMA = """
CREATE TABLE notes(id INTEGER PRIMARY KEY, path TEXT, title TEXT, type TEXT,
 project TEXT, date TEXT, reviewed TEXT, mtime INTEGER, sha256 TEXT);
CREATE TABLE chunks(id INTEGER PRIMARY KEY, note_id INTEGER, heading_path TEXT, body TEXT, ord INTEGER);
CREATE TABLE links(src_note_id INTEGER, dst_name TEXT, dst_note_id_or_null INTEGER);
"""

with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "index.db"
    feedback = Path(tmp) / "feedback.jsonl"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    old = (date.today() - timedelta(days=61)).isoformat()
    notes = [
        (1, "normal.md", "Normal", "atom", "", date.today().isoformat(), "", 0, "a"),
        (2, "orphan.md", "Orphan", "note", "", "", "", 0, "b"),
        (3, "broken.md", "Broken", "note", "", "", "", 0, "c"),
        (4, "stale.md", "Stale", "source", "", old, "", 0, "d"),
        (5, "islanda.md", "Island A", "note", "", "", "", 0, "e"),
        (6, "islandb.md", "Island B", "note", "", "", "", 0, "f"),
        (7, "hub.md", "Hub", "note", "", "", "", 0, "g"),
    ]
    con.executemany("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?)", notes)
    con.executemany("INSERT INTO chunks VALUES(?,?,?,?,?)", [(1, 1, "", "one", 0), (2, 4, "", "two", 0)])
    # Main body {1,4,7} is the largest component and counts as home. {5,6} is a two-note
    # island the old EXISTS check missed; 2 and 3 are lone orphans (3's only link is broken).
    con.executemany("INSERT INTO links VALUES(?,?,?)",
                    [(1, "Stale", 4), (3, "Missing", None), (5, "Island B", 6), (7, "Normal", 1)])
    con.commit()
    con.close()
    result = subprocess.run(
        ["python3", str(SCRIPT), "--db", str(db), "--feedback", str(feedback)], check=True, capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    assert data["totals"] == {"notes_by_type": {"atom": 1, "note": 5, "source": 1}, "notes": 7, "chunks": 2, "links": 4}
    assert data["islands"] == {"count": 3, "notes": 4, "items": [
        {"size": 2, "members": [{"title": "Island A", "path": "islanda.md"},
                                {"title": "Island B", "path": "islandb.md"}]},
        {"size": 1, "members": [{"title": "Broken", "path": "broken.md"}]},
        {"size": 1, "members": [{"title": "Orphan", "path": "orphan.md"}]},
    ]}
    assert data["broken_links"] == {"count": 1, "items": [{"src_title": "Broken", "dst_name": "Missing"}]}
    assert data["stale"]["count"] == 1
    assert data["stale"]["items"] == [{"title": "Stale", "path": "stale.md", "age_days": 61}]
    zeros = {name: 0 for name in ("saved-work", "noise", "false-note", "proven-miss")}
    assert data["feedback"] == {"last_7_days": zeros, "total": zeros}

    now = datetime.now(timezone.utc)
    events = [
        {"ts": now.isoformat(), "verdict": "noise"},
        {"ts": (now - timedelta(days=8)).isoformat(), "verdict": "saved-work"},
    ]
    feedback.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    result = subprocess.run(
        ["python3", str(SCRIPT), "--db", str(db), "--feedback", str(feedback)],
        check=True, capture_output=True, text=True,
    )
    counts = json.loads(result.stdout)["feedback"]
    assert counts["last_7_days"] == {**zeros, "noise": 1}
    assert counts["total"] == {**zeros, "saved-work": 1, "noise": 1}

print("ok")
