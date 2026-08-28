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
            # The client's own repository: its name is a prefix of the project above, and
            # its notes must not leak into that project's digest.
            (5, "sessions/Prefix kin.md", "Kin", "session", "acme", fresh, "", 0, "e"),
            (6, "sessions/Fenced.md", "Fenced", "session", "", fresh, "", 0, "f"),
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
            (6, 5, "Pending", "- [ ] kin by prefix only", 0),
            # A note that documents how threads are written hands over an example, not a
            # task — and the fence around that example can be split across two chunks.
            (7, 6, "How to", "Threads look like this:\n\n```\n- [ ] example inside a fence\n```\n\n- [ ] a real thread", 0),
            (8, 6, "Split", "Long example:\n\n```\n- [ ] first half of a split fence", 1),
            (9, 6, "Split", "- [ ] second half of a split fence\n```\n", 2),
        ],
    )
    con.commit()
    con.close()

    payload, raw = invoke(db, "--window", "7", "--project", "")
    # Same-day notes are ordered by path, so Fenced.md leads and Zeta closes the list.
    assert payload["tails"] == [
        "[ ] a real thread (Session — Fenced)",
        "[ ] ship fix (Session — Fresh session)",
        "[ ] write docs (Session — Fresh session)",
        ("[ ] " + "long tail " * 30).strip()[:199] + "… (Session — Fresh session)",
        "[ ] filed under a heading nobody listed (Session — Fresh session)",
        "[ ] belongs to another project (Session — Other project)",
        "[ ] kin by prefix only (Session — Prefix kin)",
        "[ ] a thread under a non-ASCII heading (Session — Zeta cyrillic)",
    ], payload
    assert "last 7 days" in payload["note"]
    assert b"too old" not in raw and b"done" not in raw
    # A checkbox inside a code fence is an example of how to write a thread, not a thread.
    # The second case is the one chunking creates: the fence opens in one chunk and closes
    # in another, so a scanner working chunk by chunk sees only the unclosed half.
    assert b"example inside a fence" not in raw, raw
    assert b"first half of a split fence" not in raw, raw
    assert b"second half of a split fence" not in raw, raw

    one_tail_size = len(HOT_SCAN["encoded"]({"tails": [payload["tails"][0], "> _truncated: 7 more open threads"], "note": payload["note"]}))
    truncated, truncated_raw = invoke(db, "--window", "7", "--project", "", "--budget", str(one_tail_size + 1))
    assert truncated["tails"][0] == payload["tails"][0]
    assert truncated["tails"][-1] == "> _truncated: 7 more open threads"
    assert len(truncated_raw) <= one_tail_size + 1

    # Projectless threads follow matched ones so budget truncation drops them first.
    scoped, scoped_raw = invoke(db, "--window", "7", "--project", "acme-corp")
    assert scoped["tails"] == [
        "[ ] belongs to another project (Session — Other project)",
        "[ ] a real thread (Session — Fenced, no project)",
        "[ ] ship fix (Session — Fresh session, no project)",
        "[ ] write docs (Session — Fresh session, no project)",
        ("[ ] " + "long tail " * 30).strip()[:199] + "… (Session — Fresh session, no project)",
        "[ ] filed under a heading nobody listed (Session — Fresh session, no project)",
        "[ ] a thread under a non-ASCII heading (Session — Zeta cyrillic, no project)",
    ], scoped
    assert b"kin by prefix only" not in scoped_raw, scoped_raw
    assert b", no project)" in scoped_raw
    assert "open threads in acme-corp" in scoped["note"]
    # Spelling drift is case and separators, and that is all normalising has to fold. On a
    # live vault of 894 notes every real pair of spellings differed by no more than this.
    assert HOT_SCAN["same_project"]("ACME Corp", "acme-corp")
    assert HOT_SCAN["same_project"]("Catcraft", "catcraft")
    assert HOT_SCAN["same_project"]("acme_corp", "acme-corp")
    # A shared prefix is not kinship: on that same vault every prefix pair the old rule
    # matched was two different projects, and the widest of them dragged 259 foreign notes
    # into a digest.
    assert not HOT_SCAN["same_project"]("acme", "acme-corp")
    assert not HOT_SCAN["same_project"]("catcraft", "catcraft-wiki")
    assert not HOT_SCAN["same_project"]("catcraft", "cat")
    assert not HOT_SCAN["same_project"]("widgets", "acme")
    assert not HOT_SCAN["same_project"]("", "acme")

    missing, _ = invoke(root / "missing.db", "--project", "")
    assert missing == {"tails": [], "note": ""}

    hook, _ = invoke(db, "--hook", "--project", "")
    context = hook["hookSpecificOutput"]["additionalContext"]
    assert hook["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert context.startswith("dont-forget: open threads")
    assert "not instructions" in context
    assert "- [ ] ship fix (Session — Fresh session)" in context

print("ok")
