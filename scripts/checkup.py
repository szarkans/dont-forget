#!/usr/bin/env python3
"""Report health statistics from the disposable vault index."""

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from common import DEFAULT_DB, DEFAULT_FEEDBACK, connect_ro

VERDICTS = ("saved-work", "noise", "false-note", "proven-miss")
STALE_DAYS = 60
LIMIT = 50


def parsed_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def feedback_summary(path, now=None):
    totals = {verdict: 0 for verdict in VERDICTS}
    recent = totals.copy()
    if not path.is_file():
        return {"last_7_days": recent, "total": totals}
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
            timestamp = datetime.fromisoformat(item["ts"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            verdict = item["verdict"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if verdict not in totals:
            continue
        totals[verdict] += 1
        if timestamp >= cutoff:
            recent[verdict] += 1
    return {"last_7_days": recent, "total": totals}


def report(db_path, stale_days=STALE_DAYS, feedback_path=DEFAULT_FEEDBACK):
    con = connect_ro(db_path)
    con.row_factory = sqlite3.Row
    by_type = {row[0] or "": row[1] for row in con.execute(
        "SELECT type, count(*) FROM notes GROUP BY type ORDER BY type"
    )}
    totals = {
        "notes_by_type": by_type,
        "notes": sum(by_type.values()),
        "chunks": con.execute("SELECT count(*) FROM chunks").fetchone()[0],
        "links": con.execute("SELECT count(*) FROM links").fetchone()[0],
    }
    orphan_rows = con.execute("""
        SELECT title, path FROM notes n WHERE NOT EXISTS
        (SELECT 1 FROM links l WHERE l.src_note_id=n.id OR l.dst_note_id_or_null=n.id)
        ORDER BY title, path
    """).fetchall()
    broken_rows = con.execute("""
        SELECT n.title AS src_title, l.dst_name FROM links l
        JOIN notes n ON n.id=l.src_note_id WHERE l.dst_note_id_or_null IS NULL
        ORDER BY n.title, l.dst_name
    """).fetchall()
    stale = []
    today = date.today()
    for row in con.execute("""
        SELECT title, path, date, reviewed FROM notes
        WHERE type IN ('atom','molecule','source') ORDER BY title, path
    """):
        dates = [d for d in (parsed_date(row["date"]), parsed_date(row["reviewed"])) if d]
        if dates:
            age = (today - max(dates)).days
            if age > stale_days:
                stale.append({"title": row["title"], "path": row["path"], "age_days": age})
    result = {
        "totals": totals,
        "orphans": {"count": len(orphan_rows), "items": [dict(r) for r in orphan_rows[:LIMIT]]},
        "broken_links": {"count": len(broken_rows), "items": [dict(r) for r in broken_rows[:LIMIT]]},
        "stale": {"count": len(stale), "stale_days": stale_days, "items": stale[:LIMIT]},
        "feedback": feedback_summary(feedback_path),
    }
    con.close()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--feedback", type=Path, default=DEFAULT_FEEDBACK)
    args = parser.parse_args()
    if not args.db.is_file():
        raise SystemExit(f"no index at {args.db}. Run /dont-forget:setup if this plugin "
                         "has never been pointed at a vault, otherwise /dont-forget:about "
                         "rebuilds the index on its next search.")
    print(json.dumps(report(args.db, feedback_path=args.feedback), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
