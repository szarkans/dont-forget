#!/usr/bin/env python3
"""Search the disposable vault index without requiring another application."""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from common import DEFAULT_DB, connect_ro
from index import refresh_index

WORD = re.compile(r"[^\W_]+", re.UNICODE)
CYRILLIC = re.compile(r"^[Ѐ-ӿ]+$")
# How many top-bm25 chunks are re-ranked. The pool is reported, so a query that
# fills it is visibly a query whose full result set was never examined.
POOL = 500
# A word sitting in more than this share of the vault is not a search term.
COMMON_ABOVE = 0.5


def parse_terms(query: str) -> list[str]:
    """One FTS term per distinct query word, in the order the user typed them.

    Cyrillic gets an explicit prefix wildcard because the porter stemmer only
    knows English; English relies on the stemmer instead of a wildcard.
    """
    terms: list[str] = []
    for word in WORD.findall(query):
        escaped = word.replace('"', '""')
        term = f'"{escaped}"*' if len(word) >= 4 and CYRILLIC.fullmatch(word) else f'"{escaped}"'
        if term not in terms:
            terms.append(term)
    return terms


def fts_query(query: str) -> str:
    return " OR ".join(parse_terms(query))


def term_weights(con: sqlite3.Connection, terms: list[str]) -> tuple[dict[str, set[int]], dict[str, float]]:
    """Ask SQLite which chunks each term hits, and how rare that term is.

    Asking per term instead of reproducing the tokenizer in Python is the only way
    the weights cannot drift from what the index actually matched.
    """
    total = con.execute("SELECT count(*) FROM chunks").fetchone()[0] or 1
    hits = {term: {row[0] for row in con.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", (term,))} for term in terms}
    weights = {term: math.log(1 + total / (1 + len(ids))) for term, ids in hits.items()}
    return hits, weights


def apply_budget(items: list[dict], budget: int) -> tuple[list[dict], int]:
    """Take items in the order given until the next one does not fit.

    Stopping at the first overflow keeps the result a true top-N by rank. Skipping
    the oversized item and continuing would quietly bias the answer towards short
    fragments regardless of relevance.
    """
    kept, used = [], 0
    for item in items:
        size = len(item["text"].encode("utf-8"))
        if used + size > budget:
            break
        kept.append(item)
        used += size
    return kept, len(items) - len(kept)


def _size(items: list[dict]) -> int:
    return sum(len(item["text"].encode("utf-8")) for item in items)


def _neighbours(con: sqlite3.Connection, seeds: list[tuple[int, float]], hub_cap: int,
                seen_chunks: set[int]) -> tuple[list[dict], list[dict], int]:
    """Fragments one link away from the fragments actually being returned.

    Expanding the shown fragments rather than every bm25 hit is what stops a single
    irrelevant top hit from also filling the graph lane with its neighbours.
    """
    skipped, scores = [], {}
    for note_id, seed_score in seeds:
        note = con.execute("SELECT path,title FROM notes WHERE id=?", (note_id,)).fetchone()
        degree = con.execute("SELECT count(*) FROM links WHERE src_note_id=?", (note_id,)).fetchone()[0]
        if degree > hub_cap:
            skipped.append({"path": note["path"], "title": note["title"], "outgoing_links": degree})
            continue
        for query_sql in ("SELECT dst_note_id_or_null FROM links WHERE src_note_id=? AND dst_note_id_or_null IS NOT NULL",
                          "SELECT src_note_id FROM links WHERE dst_note_id_or_null=?"):
            for (neighbour,) in con.execute(query_sql, (note_id,)):
                # A neighbour is only as relevant as the seed that reached it, and less so.
                scores[neighbour] = max(scores.get(neighbour, -1e9), seed_score * 0.5)
    if not scores:
        return [], skipped, 0
    marks = ",".join("?" for _ in scores)
    rows = con.execute(f"""SELECT c.id,c.note_id,n.path,c.heading_path,c.body FROM chunks c
        JOIN notes n ON n.id=c.note_id WHERE c.note_id IN ({marks}) ORDER BY n.path,c.ord""",
        tuple(scores)).fetchall()
    items, taken = [], set()
    for row in rows:
        # One fragment per neighbour note: otherwise a single note eats the graph lane.
        if row["id"] in seen_chunks or row["note_id"] in taken:
            continue
        taken.add(row["note_id"])
        items.append({"path": row["path"], "heading": row["heading_path"], "text": row["body"],
                      "score": round(scores[row["note_id"]], 6), "terms_matched": 0, "found_by": "link"})
    items.sort(key=lambda item: -item["score"])
    return items, skipped, len(scores)


def search(query: str, budget: int = 8000, hub_cap: int = 30, db_path: Path = DEFAULT_DB,
           graph_share: float = 0.4) -> dict:
    terms = parse_terms(query)
    budget = max(0, budget)
    if not terms:
        return {"fragments": [], "coverage": {"matched_chunks": 0, "returned": 0,
                "dropped_by_budget": 0, "weak_match": False, "skipped_hubs": []}}
    con = connect_ro(db_path)
    con.row_factory = sqlite3.Row
    hits, weights = term_weights(con, terms)

    # Rank by how much of the query a chunk actually covers, not by bm25 alone: bm25
    # rewards a short chunk holding one rare word over a long one holding several,
    # which is how a backup note won a search about a rule.
    mass: Counter[int] = Counter()
    matched_terms: Counter[int] = Counter()
    matched_content: Counter[int] = Counter()
    total_chunks = con.execute("SELECT count(*) FROM chunks").fetchone()[0] or 1
    content = [term for term in terms if len(hits[term]) < total_chunks * COMMON_ABOVE] or terms
    for term, ids in hits.items():
        for chunk_id in ids:
            mass[chunk_id] += weights[term]
            matched_terms[chunk_id] += 1
            if term in content:
                matched_content[chunk_id] += 1
    # Weak means no single chunk holds even two of the query's meaningful words. That is
    # a fact about the vault, not a score: without it the top hit is whatever rare word
    # happened to appear, and the answer gets synthesised from strangers.
    weak = len(content) > 1 and max(matched_content.values(), default=0) < 2

    rows = con.execute("""SELECT c.id,c.note_id,n.path,c.heading_path,c.body,bm25(chunks_fts) AS rank
        FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid JOIN notes n ON n.id=c.note_id
        WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?""", (" OR ".join(terms), POOL)).fetchall()
    text = [{"path": row["path"], "heading": row["heading_path"], "text": row["body"],
             "score": round(mass[row["id"]], 6), "terms_matched": matched_terms[row["id"]],
             "found_by": "text", "_id": row["id"], "_note": row["note_id"], "_bm25": row["rank"]}
            for row in rows]
    text.sort(key=lambda item: (-item["score"], item["_bm25"]))

    # Text fills first, then neighbours take the remainder. Only if neighbours exist
    # and got nothing does the text tail give up the reserved slice — that is the
    # measured failure this reserve is for: on a live vault every neighbour used to be
    # crowded out by text. Reserving up front instead would evict a single large text
    # fragment that is the actual answer.
    kept_text, _ = apply_budget(text, budget)
    seeds: list[tuple[int, float]] = []
    for item in kept_text:
        if item["_note"] not in [note for note, _ in seeds]:
            seeds.append((item["_note"], item["score"]))
    links, skipped, neighbour_notes = ([], [], 0) if weak else _neighbours(
        con, seeds, hub_cap, {item["_id"] for item in text})
    con.close()

    kept_links, _ = apply_budget(links, budget - _size(kept_text))
    if links and not kept_links:
        kept_text, _ = apply_budget(text, budget - int(budget * graph_share))
        kept_links, _ = apply_budget(links, budget - _size(kept_text))
    kept = kept_text + kept_links
    for item in kept:
        for private in ("_id", "_note", "_bm25"):
            item.pop(private, None)
    return {"fragments": kept, "coverage": {
        "matched_chunks": len(mass), "pool_examined": len(rows), "returned": len(kept),
        "returned_by_link": len(kept_links),
        "dropped_by_budget": len(text) + len(links) - len(kept),
        "query_terms": len(terms), "content_terms": len(content),
        "best_terms_matched": max(matched_content.values(), default=0), "weak_match": weak,
        "bytes_used": _size(kept), "budget_bytes": budget,
        "skipped_hubs": skipped, "expanded_notes": len(seeds) - len(skipped),
        "graph_neighbor_notes": neighbour_notes}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?")
    parser.add_argument("--budget", type=int, default=8000)
    parser.add_argument("--hub-cap", type=int, default=30)
    parser.add_argument("--graph-share", type=float, default=0.4)
    parser.add_argument("--vault", type=Path, help="index this vault instead of the configured one")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="use this index file")
    args = parser.parse_args()
    payload = {}
    if args.query is None and not sys.stdin.isatty():
        payload = json.load(sys.stdin)
    query = args.query if args.query is not None else payload.get("query", "")
    budget = int(payload.get("budget", args.budget))
    hub_cap = int(payload.get("hub_cap", args.hub_cap))
    index_error = refresh_index(args.vault, args.db)
    if index_error and not args.db.exists():
        raise SystemExit(f"search.py: index refresh failed: {index_error}")
    result = search(query, budget, hub_cap, args.db, args.graph_share)
    if index_error:
        print(f"search.py: index refresh failed; searching stale index: {index_error}", file=sys.stderr)
        result["coverage"]["index_stale"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
