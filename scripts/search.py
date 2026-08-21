#!/usr/bin/env python3
"""Search the disposable vault index without requiring another application."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

DEFAULT_DB = Path.home() / ".dont-forget" / "index.db"
WORD = re.compile(r"[^\W_]+", re.UNICODE)
CYRILLIC = re.compile(r"^[\u0400-\u04ff]+$")


def refresh_index() -> str | None:
    """Run the incremental indexer, returning a readable error on failure."""
    index_script = Path(__file__).with_name("index.py")
    try:
        completed = subprocess.run(
            [sys.executable, str(index_script)], capture_output=True, text=True, check=False
        )
    except OSError as error:
        return f"could not start {index_script}: {error}"
    if completed.returncode == 0:
        return None
    detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
    return f"{index_script} failed: {detail}"


def fts_query(query: str) -> str:
    terms = []
    for word in WORD.findall(query):
        escaped = word.replace('"', '""')
        terms.append(f'"{escaped}"*' if len(word) >= 4 and CYRILLIC.fullmatch(word) else f'"{escaped}"')
    return " OR ".join(terms)


def apply_budget(items: list[dict], budget: int) -> tuple[list[dict], int]:
    kept, used = [], 0
    for item in items:
        size = len(item["text"].encode("utf-8"))
        if used + size <= budget:
            kept.append(item)
            used += size
    return kept, len(items) - len(kept)


def search(query: str, budget: int = 8000, hub_cap: int = 30, db_path: Path = DEFAULT_DB) -> dict:
    expression = fts_query(query)
    if not expression:
        return {"fragments": [], "coverage": {"matched_total": 0, "returned": 0, "dropped_by_budget": 0, "skipped_hubs": []}}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("""SELECT c.id,c.note_id,n.path,c.heading_path,c.body,bm25(chunks_fts) AS rank
        FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid JOIN notes n ON n.id=c.note_id
        WHERE chunks_fts MATCH ? ORDER BY rank LIMIT 200""", (expression,)).fetchall()
    items, seen_chunks = [], set()
    seed_notes = []
    for row in rows:
        seen_chunks.add(row["id"])
        if row["note_id"] not in seed_notes:
            seed_notes.append(row["note_id"])
        items.append({"path": row["path"], "heading": row["heading_path"], "text": row["body"],
                      "score": round(float(-row["rank"]), 6), "found_by": "text"})
    skipped, neighbor_ids, expanded = [], set(), []
    for note_id in seed_notes[:20]:
        note = con.execute("SELECT path,title FROM notes WHERE id=?", (note_id,)).fetchone()
        degree = con.execute("SELECT count(*) FROM links WHERE src_note_id=?", (note_id,)).fetchone()[0]
        if degree > hub_cap:
            skipped.append({"path": note["path"], "title": note["title"], "outgoing_links": degree})
            continue
        if degree:
            expanded.append({"path": note["path"], "title": note["title"], "outgoing_links": degree})
        neighbor_ids.update(r[0] for r in con.execute(
            "SELECT dst_note_id_or_null FROM links WHERE src_note_id=? AND dst_note_id_or_null IS NOT NULL", (note_id,)))
        neighbor_ids.update(r[0] for r in con.execute("SELECT src_note_id FROM links WHERE dst_note_id_or_null=?", (note_id,)))
    if neighbor_ids:
        marks = ",".join("?" for _ in neighbor_ids)
        graph_rows = con.execute(f"""SELECT c.id,c.note_id,n.path,c.heading_path,c.body FROM chunks c
            JOIN notes n ON n.id=c.note_id WHERE c.note_id IN ({marks}) ORDER BY n.path,c.ord""", tuple(neighbor_ids)).fetchall()
        for row in graph_rows:
            if row["id"] in seen_chunks:
                continue
            items.append({"path": row["path"], "heading": row["heading_path"], "text": row["body"],
                          "score": -1.0, "found_by": "link"})
    con.close()
    kept, dropped = apply_budget(items, max(0, budget))
    return {"fragments": kept, "coverage": {"matched_total": len(items), "text_matches": len(rows),
            "returned": len(kept), "dropped_by_budget": dropped, "bytes_used": sum(len(x["text"].encode()) for x in kept),
            "budget_bytes": budget, "skipped_hubs": skipped, "expanded_notes": expanded,
            "graph_neighbor_notes": len(neighbor_ids)}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?")
    parser.add_argument("--budget", type=int, default=8000)
    parser.add_argument("--hub-cap", type=int, default=30)
    args = parser.parse_args()
    payload = {}
    if args.query is None and not sys.stdin.isatty():
        payload = json.load(sys.stdin)
    query = args.query if args.query is not None else payload.get("query", "")
    budget = int(payload.get("budget", args.budget))
    hub_cap = int(payload.get("hub_cap", args.hub_cap))
    index_error = refresh_index()
    if index_error and not DEFAULT_DB.exists():
        raise SystemExit(f"search.py: index refresh failed: {index_error}")
    result = search(query, budget, hub_cap)
    if index_error:
        print(f"search.py: index refresh failed; searching stale index: {index_error}", file=sys.stderr)
        result["coverage"]["index_stale"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
