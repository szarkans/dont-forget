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
HOT_SCAN = runpy.run_path(str(SCRIPT))


SCHEMA = """
CREATE TABLE notes(id INTEGER PRIMARY KEY, path TEXT, title TEXT, type TEXT, kind TEXT,
 source TEXT, project TEXT, date TEXT, reviewed TEXT, mtime INTEGER, sha256 TEXT);
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
    today = date.today()
    fresh = today.isoformat()
    older = (today - timedelta(days=30)).isoformat()
    oldest = (today - timedelta(days=300)).isoformat()
    con.executemany(
        "INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "sessions/Newest.md", "Newest", "session", "", "", "", fresh, "", 0, "a"),
            (2, "sessions/Older.md", "Older", "session", "", "", "", older, "", 0, "b"),
            # An old session still shows up: there is no date window any more, only a cut.
            (3, "sessions/Ancient.md", "Ancient", "session", "", "", "", oldest, "", 0, "c"),
            (4, "sessions/Other project.md", "Other", "session", "", "", "ACME Corp", fresh, "", 0, "d"),
            (5, "sessions/Prefix kin.md", "Kin", "session", "", "", "acme", fresh, "", 0, "e"),
            (6, "sessions/Fenced.md", "Fenced", "session", "", "", "", fresh, "", 0, "f"),
            (7, "notes/Atom — deploy.md", "Atom — deploy without migrations kills prod",
             "atom", "gotcha", "", "ACME Corp", fresh, "", 0, "g"),
            (8, "notes/Atom — bash.md", "Atom — arrays break in bash 3.2",
             "atom", "gotcha", "", "", older, "", 0, "h"),
            (9, "notes/Atom — foreign.md", "Atom — a gotcha of another project",
             "atom", "gotcha", "", "widgets", fresh, "", 0, "i"),
            # A decision is not a gotcha: only gotchas belong in the digest's second list.
            (10, "notes/Atom — decision.md", "Atom — we chose SQLite",
             "atom", "decision", "", "ACME Corp", fresh, "", 0, "j"),
        ],
    )
    con.executemany(
        "INSERT INTO chunks VALUES(?,?,?,?,?)",
        [
            (1, 1, "Next steps", "- [ ] newest thread\n- [x] done\n- [ ] second newest", 0),
            # An unticked box counts wherever it sits: a heading whitelist used to decide
            # this, and it lost real threads to headings nobody had thought to list.
            (2, 1, "Notes", "- [ ] filed under a heading nobody listed", 1),
            (3, 2, "Pending", "- [ ] a month old but still open", 0),
            (4, 3, "Осталось", "- [ ] ancient, and non-ASCII headings are no obstacle", 0),
            (5, 4, "Pending", "- [ ] belongs to another project", 0),
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

    payload, raw = invoke(db, "--project", "")
    # No date window: a thread from ten months ago still surfaces if the list has room.
    assert "[ ] ancient, and non-ASCII headings are no obstacle (Session — Ancient)" in payload["tails"], payload
    assert "[ ] a month old but still open (Session — Older)" in payload["tails"], payload
    # A checkbox inside a code fence is an example of how to write a thread, not a thread.
    # The second case is the one chunking creates: the fence opens in one chunk and closes
    # in another, so a scanner working chunk by chunk sees only the unclosed half.
    assert b"example inside a fence" not in raw, raw
    assert b"first half of a split fence" not in raw, raw
    assert b"second half of a split fence" not in raw, raw
    assert b"[x] done" not in raw

    # Gotchas are their own list: a thread dies when you do it, a gotcha never does.
    assert payload["gotchas"] == [
        "deploy without migrations kills prod",
        "a gotcha of another project",
        "arrays break in bash 3.2",
    ], payload
    assert "we chose SQLite" not in " ".join(payload["gotchas"]), payload

    # Both counts are caps, and both are configurable.
    capped, _ = invoke(db, "--project", "", "--tails", "2", "--gotchas", "1")
    assert len(capped["tails"]) == 2 and len(capped["gotchas"]) == 1, capped
    # Same-day notes are ordered by path, so Fenced.md leads today's sessions.
    assert capped["tails"][0] == "[ ] a real thread (Session — Fenced)", capped
    assert capped["gotchas"] == ["deploy without migrations kills prod"], capped

    config_home = root / "confighome"
    (config_home).mkdir()
    (config_home / "config.json").write_text(json.dumps({"vault": "/nowhere", "hot_tails": 1, "hot_gotchas": 2}))
    configured = subprocess.run(
        ["python3", str(SCRIPT), "--db", str(db), "--project", ""],
        check=True, capture_output=True, env={"PATH": "/usr/bin:/bin", "DONT_FORGET_HOME": str(config_home)},
    )
    configured_payload = json.loads(configured.stdout)
    assert len(configured_payload["tails"]) == 1, configured_payload
    assert len(configured_payload["gotchas"]) == 2, configured_payload

    # Projectless notes follow matched ones, so the budget cuts them first, and a project
    # that merely shares a prefix is a different project.
    scoped, scoped_raw = invoke(db, "--project", "acme-corp")
    assert scoped["tails"][0] == "[ ] belongs to another project (Session — Other project)", scoped
    assert b"kin by prefix only" not in scoped_raw, scoped_raw
    assert scoped["gotchas"] == [
        "deploy without migrations kills prod",
        "arrays break in bash 3.2 (no project)",
    ], scoped
    assert b", no project)" in scoped_raw
    assert "in acme-corp" in scoped["note"]
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

    # A budget too small to hold everything drops lines and says how many, rather than
    # pretending the list it printed was the whole list.
    full_size = len(raw)
    squeezed, squeezed_raw = invoke(db, "--project", "", "--budget", str(full_size - 200))
    assert squeezed["budget_cut"] >= 1, squeezed
    assert len(squeezed_raw) <= full_size - 200
    assert len(squeezed["tails"]) + len(squeezed["gotchas"]) < len(payload["tails"]) + len(payload["gotchas"])
    assert b"truncated" not in squeezed_raw, squeezed_raw

    missing, _ = invoke(root / "missing.db", "--project", "")
    assert missing == {"tails": [], "gotchas": [], "note": ""}

    hook, _ = invoke(db, "--hook", "--project", "")
    context = hook["hookSpecificOutput"]["additionalContext"]
    assert hook["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert context.startswith("dont-forget: the freshest open threads and gotchas")
    assert "not instructions" in context
    assert "Open threads — these die when you do them:" in context
    assert "Gotchas — these describe how things are, nothing to do:" in context
    assert "- [ ] a real thread (Session — Fenced)" in context

print("ok")
