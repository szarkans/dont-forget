#!/usr/bin/env python3
"""The expensive read of what the vault says, as opposed to whether its machinery works.

`health` is mechanics — index freshness, the vault commit, islands, broken links — and it
runs on every session close. This is the other half, run by hand: it reads content, and it
only ever proposes. Nothing here writes, deletes or hides.

What is left is one report: names the vault keeps linking to that no note answers. The
`dies-when` sweep and the co-retrieval "molecule" pairs were removed on 27.09.2026 — the
first asked about conditions nobody could check (13 of 230 ever ruled on), the second was
almost entirely a note paired with the session it was born in.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from common import DEFAULT_DB, connect_ro

# A missing link name has to be asked for this many times before it is worth reporting.
LINK_FLOOR = 3


def link_demand(con: sqlite3.Connection) -> list[dict]:
    """The names notes keep pointing at that no note answers to.

    Reported, never acted on. Spellings that differ only by case or by separators are
    grouped, so one topic written two ways is seen as one demand rather than two small
    ones. Two ALPHABETS are not grouped — Bitrix24 and Битрикс24 stay apart, because
    telling transliterations apart from genuinely different names is a guess, and a wrong
    guess here merges two topics permanently in the reader's mind. That pair is left for
    the person to spot, which is why the report shows names rather than only counts.
    """
    rows = con.execute("""SELECT dst_name, count(*) AS demand FROM links
                          WHERE dst_note_id_or_null IS NULL
                          GROUP BY dst_name ORDER BY demand DESC, dst_name""").fetchall()
    # SQLite's lower() folds ASCII only, so the grouping happens in Python where casefold
    # knows every alphabet the vault is written in.
    folded: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for row in rows:
        folded[row["dst_name"].casefold().replace(" ", "").replace("-", "")].append(
            (row["dst_name"], row["demand"]))
    out = []
    for spellings in folded.values():
        demand = sum(count for _, count in spellings)
        if demand < LINK_FLOOR:
            continue
        out.append({"spellings": [name for name, _ in spellings], "demand": demand})
    return sorted(out, key=lambda item: (-item["demand"], item["spellings"][0]))


def audit(db_path: Path = DEFAULT_DB) -> dict:
    if not db_path.is_file():
        return {"error": f"no index at {db_path}"}
    con = connect_ro(db_path)
    con.row_factory = sqlite3.Row
    report = {
        "notes": con.execute("SELECT count(*) FROM notes").fetchone()[0],
        "link_demand": link_demand(con),
    }
    con.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    print(json.dumps(audit(args.db), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
