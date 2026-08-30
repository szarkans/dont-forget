#!/usr/bin/env python3
"""Report health statistics from the disposable vault index."""

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from common import DEFAULT_DB, DEFAULT_FEEDBACK, VERDICTS, connect_ro

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


def find_islands(con):
    """Note groups severed from the main graph body, biggest island first.

    A degree-0 note (today's "orphan") is just an island of one; a cluster like
    CatCraft MOC — notes that link to each other but to nothing in the main body —
    is an island of two or more. The old EXISTS check only saw the size-1 case,
    so it walked straight past every multi-note island. Union-find over resolved
    links finds every connected component; the largest is home, the rest are islands.
    """
    parent = {row[0]: row[0] for row in con.execute("SELECT id FROM notes")}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for src, dst in con.execute(
        "SELECT src_note_id, dst_note_id_or_null FROM links WHERE dst_note_id_or_null IS NOT NULL"
    ):
        if src in parent and dst in parent:
            parent[find(src)] = find(dst)
    members = {}
    for note_id, title, path in con.execute("SELECT id, title, path FROM notes"):
        members.setdefault(find(note_id), []).append({"title": title, "path": path})
    components = sorted(members.values(), key=len, reverse=True)
    islands = components[1:]  # drop the main body; everything else is cut off from it
    for island in islands:
        island.sort(key=lambda m: (m["title"], m["path"]))
    islands.sort(key=lambda i: (-len(i), i[0]["title"] if i else ""))
    return islands


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
    islands = find_islands(con)
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
        "islands": {"count": len(islands), "notes": sum(len(i) for i in islands),
                    "items": [{"size": len(i), "members": i} for i in islands[:LIMIT]]},
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
