#!/usr/bin/env python3
"""The expensive read of what the vault says, as opposed to whether its machinery works.

`health` is mechanics — index freshness, the vault commit, islands, broken links — and it
runs on every session close. This is the other half, run by hand every couple of months:
it reads content, and it only ever proposes. Nothing here writes, deletes or hides.

Two jobs of the four are here, on purpose. Checking `dies-when` is not a new feature but
the switching-on of a field that has been written and read by nobody. Molecule candidates
are the reason the command exists. Broken links get one line of report and no automation,
because the count says a topic recurs and does not say what is needed — an alias, a plain
note, a hub, or nothing at all because a typo was copy-pasted around.

The traversal is deliberately two-level: a cheap pass over metadata for every note, and
the body read only for the notes that pass flags. The cost then grows with the number of
problems rather than with the size of the vault.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from common import DEFAULT_DB, DEFAULT_QUERY_LOG, connect_ro

# How often two notes must come back together, both matched by text, before the pair is
# worth a human's attention. Two is low on purpose: this proposes, and a person reads it.
PAIR_FLOOR = 2
# A missing link name has to be asked for this many times before it is worth reporting.
LINK_FLOOR = 3


def dying_notes(con: sqlite3.Connection) -> list[dict]:
    """Notes carrying a death condition, newest first, with nothing decided for them.

    Whether the condition has arrived is a question about the world, and no script can
    see the world. It is asked of the user, with the note's own words in front of them.
    """
    rows = con.execute("""SELECT path, title, kind, project, date, reviewed, dies_when
                          FROM notes WHERE dies_when <> ''
                          ORDER BY date(date) DESC, path""").fetchall()
    return [dict(row) for row in rows]


def _log_lines(log_path: Path) -> list[dict]:
    try:
        with log_path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def molecule_candidates(con: sqlite3.Connection, log_path: Path) -> tuple[list[dict], int]:
    """Notes that keep coming back to the same questions, and so may share one claim.

    Only pairs where BOTH notes matched the query text count. A pair that travels because
    one note links to the other says something about the graph, not about the ideas — and
    until the log recorded how each note arrived, the two were indistinguishable, which is
    why the earlier co-retrieval numbers proved nothing.
    """
    pairs: Counter = Counter()
    usable = 0
    queries: dict[tuple[str, str], list[str]] = defaultdict(list)
    for entry in _log_lines(log_path):
        top = entry.get("top") or []
        # Old log lines stored bare paths, with no way to tell text from link.
        if not all(isinstance(item, dict) for item in top):
            continue
        usable += 1
        by_text = sorted({item["path"] for item in top if item.get("found_by") == "text"})
        for pair in combinations(by_text, 2):
            pairs[pair] += 1
            if entry.get("query"):
                queries[pair].append(entry["query"])

    titles = {row["path"]: row["title"] for row in con.execute("SELECT path, title FROM notes")}
    out: list[dict] = []
    for (left, right), count in pairs.most_common():
        if count < PAIR_FLOOR or left not in titles or right not in titles:
            continue
        out.append({"notes": [left, right], "together": count,
                    "titles": [titles[left], titles[right]],
                    "queries": sorted(set(queries[(left, right)]))[:5]})
    return out, usable


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


def audit(db_path: Path = DEFAULT_DB, log_path: Path = DEFAULT_QUERY_LOG) -> dict:
    if not db_path.is_file():
        return {"error": f"no index at {db_path}"}
    con = connect_ro(db_path)
    con.row_factory = sqlite3.Row
    candidates, usable = molecule_candidates(con, log_path)
    report = {
        "notes": con.execute("SELECT count(*) FROM notes").fetchone()[0],
        "dying": dying_notes(con),
        "molecule_candidates": candidates,
        "link_demand": link_demand(con),
        "searches_logged": len(_log_lines(log_path)),
        # No candidates because nothing surfaced together is a finding. No candidates
        # because no search was ever logged in a readable form is an absence of data,
        # and the two must not look alike in the report.
        "searches_usable": usable,
    }
    con.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--log", type=Path, default=DEFAULT_QUERY_LOG)
    args = parser.parse_args()
    print(json.dumps(audit(args.db, args.log), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
