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
        ],
    )
    con.executemany(
        "INSERT INTO chunks VALUES(?,?,?,?,?)",
        [
            (1, 1, "Next steps / PENDING", "- [ ] ship fix\n- [x] done\n- [ ] write docs\n- [ ] " + "long tail " * 30, 0),
            (2, 1, "Notes", "- [ ] must stay hidden", 1),
            (3, 2, "Pending", "- [ ] too old", 0),
        ],
    )
    con.commit()
    con.close()

    payload, raw = invoke(db, "--window", "7")
    assert payload["tails"] == [
        "[ ] ship fix (Session — Fresh session)",
        "[ ] write docs (Session — Fresh session)",
        "[ ] " + "long tail " * 30 + "(Session — Fresh session)",
    ]
    assert "last 7 days" in payload["note"]
    assert b"too old" not in raw and b"must stay hidden" not in raw and b"done" not in raw

    one_tail_size = len(HOT_SCAN["encoded"]({"tails": [payload["tails"][0], "> _truncated: 2 more open threads (see handoff index)"], "note": payload["note"]}))
    truncated, truncated_raw = invoke(db, "--window", "7", "--budget", str(one_tail_size + 1))
    assert truncated["tails"][0] == payload["tails"][0]
    assert truncated["tails"][-1] == "> _truncated: 2 more open threads (see handoff index)"
    assert len(truncated_raw) <= one_tail_size + 1

    missing, _ = invoke(root / "missing.db")
    assert missing == {"tails": [], "note": ""}

    hook, _ = invoke(db, "--hook")
    context = hook["hookSpecificOutput"]["additionalContext"]
    assert hook["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert context.startswith("dont-forget: open threads")
    assert "- [ ] ship fix (Session — Fresh session)" in context

print("ok")
