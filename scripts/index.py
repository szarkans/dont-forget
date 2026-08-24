#!/usr/bin/env python3
"""Build a disposable SQLite search index for a Markdown vault."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

from common import DEFAULT_DB, NotConfigured, connect_ro, vault_from_config

FM_LINE = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
WIKILINK = re.compile(r"!?\[\[([^\]]+)\]\]")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


def _value(raw: str):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    if raw.startswith("[") and raw.endswith("]"):
        inside = raw[1:-1].strip()
        return [_value(x) for x in inside.split(",")] if inside else []
    return raw


def parse_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    data: dict = {}
    current = None
    for line in lines[1:end]:
        match = FM_LINE.match(line.rstrip("\r\n"))
        if match:
            current, raw = match.groups()
            data[current] = _value(raw)
        elif current and re.match(r"^\s+-\s+", line):
            if not isinstance(data[current], list):
                data[current] = []
            data[current].append(_value(re.sub(r"^\s+-\s+", "", line).strip()))
    return data, "".join(lines[end + 1 :])


def extract_links(body: str) -> list[str]:
    visible, fence = [], None
    for line in body.splitlines():
        marker = FENCE.match(line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token[0]
            elif token[0] == fence:
                fence = None
            continue
        if fence is None:
            visible.append(line)
    targets = []
    for match in WIKILINK.finditer("\n".join(visible)):
        target = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
        if target:
            targets.append(target)
    return targets


def _split_lines(unit: str, limit: int) -> list[str]:
    """Last resort for text the sentence splitter cannot divide.

    A MOC is a list of wikilinks with no sentence ends at all, so it used to survive
    as one chunk of ten kilobytes — larger than the whole search budget, which meant
    a query whose best match was that MOC returned nothing at all.
    """
    parts, current = [], ""
    for line in unit.splitlines(keepends=True):
        if current and len((current + line).encode()) > limit:
            parts.append(current.strip())
            current = ""
        current += line
    if current.strip():
        parts.append(current.strip())
    return [part for part in parts if part]


def _split_large(text: str, limit: int = 1200) -> list[str]:
    if len(text.encode()) <= limit:
        return [text]
    units = re.split(r"(?<=\n\n)|(?<=[.!?…])(?=\s+)", text)
    out, current = [], ""
    for unit in units:
        if current and len((current + unit).encode()) > limit:
            out.append(current.strip())
            current = ""
        if len(unit.encode()) <= limit:
            current += unit
            continue
        # Sentences are never cut, but a unit with no sentence ends still has lines.
        if current.strip():
            out.append(current.strip())
            current = ""
        out.extend(_split_lines(unit, limit))
    if current.strip():
        out.append(current.strip())
    return [part for part in out if part]


def make_chunks(title: str, body: str) -> list[tuple[str, str, int]]:
    matches = list(HEADING.finditer(body))
    sections: list[tuple[list[str], str]] = []
    if not matches:
        sections.append(([], body.strip()))
    else:
        preamble = body[: matches[0].start()].strip()
        if preamble:
            sections.append(([], preamble))
        stack: list[tuple[int, str]] = []
        for i, match in enumerate(matches):
            level, name = len(match.group(1)), match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, name))
            # A section ends at the NEXT heading of any level. Ending it at the next
            # heading of the same level made an H1 swallow the whole note, so every
            # subsection was indexed twice — once inside the H1, once on its own.
            end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
            sections.append(([x[1] for x in stack], body[match.start() : end].strip()))
    chunks, ordinal = [], 0
    for path, section in sections:
        heading_path = " > ".join(path)
        context = title + (" > " + heading_path if heading_path else "")
        for part in _split_large(section):
            chunks.append((heading_path, f"{context}\n\n{part}".strip(), ordinal))
            ordinal += 1
    return chunks


SCHEMA = """
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,
 title TEXT NOT NULL, type TEXT, project TEXT, date TEXT, reviewed TEXT, dies_when TEXT, aliases TEXT,
 mtime INTEGER NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
 heading_path TEXT NOT NULL, body TEXT NOT NULL, ord INTEGER NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(body, content='chunks', content_rowid='id', tokenize='porter unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS links(src_note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
 dst_name TEXT NOT NULL, dst_note_id_or_null INTEGER REFERENCES notes(id) ON DELETE SET NULL);
CREATE INDEX IF NOT EXISTS links_src ON links(src_note_id);
CREATE INDEX IF NOT EXISTS links_dst ON links(dst_note_id_or_null);
"""


def _scalar(value) -> str:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, list) else str(value or "")


def _unscalar(raw) -> list[str]:
    """Read back what _scalar wrote: a JSON list, or a lone value written as itself."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return [str(raw)]
    names = value if isinstance(value, list) else [raw]
    return [str(name).strip() for name in names if str(name).strip()]


TOKENIZER = "porter unicode61 remove_diacritics 2"


def schema_stale(db_path: Path) -> bool:
    """An existing index keeps its own schema, so a change here must force a rebuild.

    Without this an upgrade looks fine and quietly searches by the old rules — or
    resolves links without the aliases it was never given a column for.
    """
    if not db_path.exists():
        return False
    try:
        con = connect_ro(db_path)
        row = con.execute("SELECT sql FROM sqlite_master WHERE name='chunks_fts'").fetchone()
        columns = {info[1] for info in con.execute("PRAGMA table_info(notes)")}
        con.close()
    except sqlite3.Error:
        return True
    return not row or TOKENIZER not in row[0] or "aliases" not in columns or "dies_when" not in columns


def build(vault: Path, db_path: Path = DEFAULT_DB, rebuild: bool = False) -> dict:
    started = time.perf_counter()
    vault = vault.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if (rebuild or schema_stale(db_path)) and db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    files = sorted(vault.rglob("*.md"))
    disk_paths = {p.relative_to(vault).as_posix() for p in files}
    known = {row[0]: (row[1], row[2]) for row in con.execute("SELECT path,mtime,sha256 FROM notes")}
    reindexed = 0
    with con:
        stale = set(known) - disk_paths
        for rel in stale:
            con.execute("DELETE FROM notes WHERE path=?", (rel,))
        for path in files:
            rel = path.relative_to(vault).as_posix()
            known_note = known.get(rel)
            if known_note is not None and known_note[0] == path.stat().st_mtime_ns:
                continue
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            mtime = path.stat().st_mtime_ns
            if known_note is not None and known_note[1] == digest:
                con.execute("UPDATE notes SET mtime=? WHERE path=?", (mtime, rel))
                continue
            text = raw.decode("utf-8", errors="replace")
            meta, body = parse_frontmatter(text)
            title = str(meta.get("title") or path.stem)
            old = con.execute("SELECT id FROM notes WHERE path=?", (rel,)).fetchone()
            if old:
                note_id = old[0]
                con.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE note_id=?)", (note_id,))
                con.execute("DELETE FROM chunks WHERE note_id=?", (note_id,))
                con.execute("DELETE FROM links WHERE src_note_id=?", (note_id,))
                con.execute("UPDATE notes SET title=?,type=?,project=?,date=?,reviewed=?,dies_when=?,aliases=?,mtime=?,sha256=? WHERE id=?",
                            (title, _scalar(meta.get("type")), _scalar(meta.get("project")), _scalar(meta.get("date")),
                             _scalar(meta.get("reviewed")), _scalar(meta.get("dies-when")), _scalar(meta.get("aliases")), mtime, digest, note_id))
            else:
                cur = con.execute("INSERT INTO notes(path,title,type,project,date,reviewed,dies_when,aliases,mtime,sha256) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                  (rel, title, _scalar(meta.get("type")), _scalar(meta.get("project")),
                                   _scalar(meta.get("date")), _scalar(meta.get("reviewed")),
                                   _scalar(meta.get("dies-when")), _scalar(meta.get("aliases")), mtime, digest))
                note_id = cur.lastrowid
            for heading, chunk_body, ordinal in make_chunks(title, body):
                cur = con.execute("INSERT INTO chunks(note_id,heading_path,body,ord) VALUES(?,?,?,?)",
                                  (note_id, heading, chunk_body, ordinal))
                con.execute("INSERT INTO chunks_fts(rowid,body) VALUES(?,?)", (cur.lastrowid, chunk_body))
            con.executemany("INSERT INTO links(src_note_id,dst_name,dst_note_id_or_null) VALUES(?,?,NULL)",
                            ((note_id, target) for target in extract_links(body)))
            reindexed += 1
        names: dict[str, int | None] = {}
        alias_names: dict[str, int | None] = {}

        def claim(bucket: dict[str, int | None], name: str, note_id: int) -> None:
            key = name.casefold()
            if key not in bucket:
                bucket[key] = note_id
            elif bucket[key] != note_id:
                # A name two notes answer to is a name this index refuses to guess at.
                bucket[key] = None

        for note_id, path, title, raw_aliases in con.execute("SELECT id,path,title,aliases FROM notes"):
            # The extension counts as part of a name: Obsidian opens [[Note.md]] too, and
            # a note whose own title ends in a word like CLAUDE is linked to exactly that way.
            for name in (title, Path(path).stem, path.removesuffix(".md"), Path(path).name, path):
                claim(names, name, note_id)
            for name in _unscalar(raw_aliases):
                claim(alias_names, name, note_id)
        # Aliases only fill gaps: a real title always outranks someone else's nickname.
        for key, target in alias_names.items():
            names.setdefault(key, target)
        for rowid, name in con.execute("SELECT rowid,dst_name FROM links"):
            target = names.get(name.casefold())
            con.execute("UPDATE links SET dst_note_id_or_null=? WHERE rowid=?", (target, rowid))
    counts = [con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("notes", "chunks", "links")]
    con.close()
    return {"notes": counts[0], "chunks": counts[1], "links": counts[2], "reindexed": reindexed,
            "deleted": len(set(known) - disk_paths), "seconds": round(time.perf_counter() - started, 3)}


def refresh_index(vault: Path | None = None, db_path: Path | None = None) -> str | None:
    """Bring the index up to date, returning a readable error instead of raising.

    Refreshing belongs to the indexer, not to its callers: search and the session
    digest both need it, and neither should know how the index is built.
    """
    try:
        build(vault if vault is not None else vault_from_config(), db_path or DEFAULT_DB)
    except NotConfigured as error:
        # This one already reads as instructions to the user, so it is passed through
        # whole. Wrapping it in "IndexError: ..." is how a fixable setup step ends up
        # looking like a crash.
        return str(error)
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as error:
        return f"dont-forget: index not refreshed ({type(error).__name__}: {error})"
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", type=Path)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    vault = args.vault
    if vault is None:
        try:
            vault = vault_from_config()
        except NotConfigured as error:
            raise SystemExit(str(error)) from None
    if not vault.is_dir():
        raise SystemExit(f"vault is not a directory: {vault}")
    print(json.dumps(build(vault, args.db, args.rebuild), ensure_ascii=False))


if __name__ == "__main__":
    main()
