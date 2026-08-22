#!/usr/bin/env python3
"""Assert-based self-test for hot-scan.py."""

import json
import runpy
import sqlite3
import subprocess
import tempfile
from datetime import date, timedelta
from pathlib import Path


SCRIPT = Path(__file__).with_name("hot-scan.py")
HOT_SCAN = runpy.run_path(SCRIPT)


SCHEMA = """
CREATE TABLE notes(id INTEGER PRIMARY KEY, path TEXT, title TEXT, type TEXT,
 project TEXT, date TEXT, reviewed TEXT, mtime INTEGER, sha256 TEXT);
CREATE TABLE chunks(id INTEGER PRIMARY KEY, note_id INTEGER, heading_path TEXT,
 body TEXT, ord INTEGER);
"""


def invoke(db: Path, *args: str) -> tuple[dict, bytes]:
    result = subprocess.run(
        ["python3", str(SCRIPT), "--db", str(db), *args],
        check=True,
        capture_output=True,
    )
    return json.loads(result.stdout), result.stdout


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    db = root / "index.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    fresh = date.today().isoformat()
    old = (date.today() - timedelta(days=30)).isoformat()
    con.executemany(
        "INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?)",
        [
            (1, "sessions/Fresh session.md", "Fresh", "session", "", fresh, "", 0, "a"),
            (2, "sessions/Old session.md", "Old", "session", "", old, "", 0, "b"),
            (3, "sessions/Other project.md", "Other", "session", "ACME Corp", fresh, "", 0, "c"),
            (4, "sessions/Zeta cyrillic.md", "Zeta", "session", "", fresh, "", 0, "d"),
        ],
    )
    con.executemany(
        "INSERT INTO chunks VALUES(?,?,?,?,?)",
        [
            (1, 1, "Next steps / PENDING", "- [ ] ship fix\n- [x] done\n- [ ] write docs\n- [ ] " + "long tail " * 30, 0),
            # An unticked box counts wherever it sits: a heading whitelist used to decide
            # this, and it lost real threads to headings nobody had thought to list.
            (2, 1, "Notes", "- [ ] filed under a heading nobody listed", 1),
            (3, 2, "Pending", "- [ ] too old", 0),
            (4, 3, "Pending", "- [ ] belongs to another project", 0),
            # SQLite lower() only folds ASCII, so a whitelist could never match this one.
            (5, 4, "Осталось", "- [ ] a thread under a non-ASCII heading", 0),
        ],
    )
    con.commit()
    con.close()

    payload, raw = invoke(db, "--window", "7", "--project", "")
    assert payload["tails"] == [
        "[ ] ship fix (Session — Fresh session)",
        "[ ] write docs (Session — Fresh session)",
        ("[ ] " + "long tail " * 30).strip()[:199] + "… (Session — Fresh session)",
        "[ ] filed under a heading nobody listed (Session — Fresh session)",
        "[ ] belongs to another project (Session — Other project)",
        "[ ] a thread under a non-ASCII heading (Session — Zeta cyrillic)",
    ], payload
    assert "last 7 days" in payload["note"]
    assert b"too old" not in raw and b"done" not in raw

    one_tail_size = len(HOT_SCAN["encoded"]({"tails": [payload["tails"][0], "> _truncated: 5 more open threads"], "note": payload["note"]}))
    truncated, truncated_raw = invoke(db, "--window", "7", "--project", "", "--budget", str(one_tail_size + 1))
    assert truncated["tails"][0] == payload["tails"][0]
    assert truncated["tails"][-1] == "> _truncated: 5 more open threads"
    assert len(truncated_raw) <= one_tail_size + 1

    # The digest is per project: threads from other projects must not leak into a session.
    scoped, scoped_raw = invoke(db, "--window", "7", "--project", "acme")
    assert scoped["tails"] == ["[ ] belongs to another project (Session — Other project)"], scoped
    assert b"ship fix" not in scoped_raw
    assert "open threads in acme" in scoped["note"]
    assert HOT_SCAN["same_project"]("ACME Corp", "acme") and not HOT_SCAN["same_project"]("widgets", "acme")

    missing, _ = invoke(root / "missing.db", "--project", "")
    assert missing == {"tails": [], "note": ""}

    hook, _ = invoke(db, "--hook", "--project", "")
    context = hook["hookSpecificOutput"]["additionalContext"]
    assert hook["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert context.startswith("dont-forget: open threads")
    assert "not instructions" in context
    assert "- [ ] ship fix (Session — Fresh session)" in context

print("ok")
