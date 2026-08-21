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

DEFAULT_DB = Path.home() / ".dont-forget" / "index.db"
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
        # A single indivisible paragraph/sentence is kept whole: sentences are never cut.
        if current.strip():
            out.append(current.strip())
            current = ""
        out.append(unit.strip())
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
            end = len(body)
            for later in matches[i + 1 :]:
                if len(later.group(1)) <= level:
                    end = later.start()
                    break
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
 title TEXT NOT NULL, type TEXT, project TEXT, date TEXT, reviewed TEXT,
 mtime INTEGER NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
 heading_path TEXT NOT NULL, body TEXT NOT NULL, ord INTEGER NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(body, content='chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS links(src_note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
 dst_name TEXT NOT NULL, dst_note_id_or_null INTEGER REFERENCES notes(id) ON DELETE SET NULL);
CREATE INDEX IF NOT EXISTS links_src ON links(src_note_id);
CREATE INDEX IF NOT EXISTS links_dst ON links(dst_note_id_or_null);
"""


def _scalar(value) -> str:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, list) else str(value or "")


def build(vault: Path, db_path: Path = DEFAULT_DB, rebuild: bool = False) -> dict:
    started = time.perf_counter()
    vault = vault.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if rebuild and db_path.exists():
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
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            mtime = path.stat().st_mtime_ns
            if known.get(rel) == (mtime, digest):
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
                con.execute("UPDATE notes SET title=?,type=?,project=?,date=?,reviewed=?,mtime=?,sha256=? WHERE id=?",
                            (title, _scalar(meta.get("type")), _scalar(meta.get("project")), _scalar(meta.get("date")),
                             _scalar(meta.get("reviewed")), mtime, digest, note_id))
            else:
                cur = con.execute("INSERT INTO notes(path,title,type,project,date,reviewed,mtime,sha256) VALUES(?,?,?,?,?,?,?,?)",
                                  (rel, title, _scalar(meta.get("type")), _scalar(meta.get("project")),
                                   _scalar(meta.get("date")), _scalar(meta.get("reviewed")), mtime, digest))
                note_id = cur.lastrowid
            for heading, chunk_body, ordinal in make_chunks(title, body):
                cur = con.execute("INSERT INTO chunks(note_id,heading_path,body,ord) VALUES(?,?,?,?)",
                                  (note_id, heading, chunk_body, ordinal))
                con.execute("INSERT INTO chunks_fts(rowid,body) VALUES(?,?)", (cur.lastrowid, chunk_body))
            con.executemany("INSERT INTO links(src_note_id,dst_name,dst_note_id_or_null) VALUES(?,?,NULL)",
                            ((note_id, target) for target in extract_links(body)))
            reindexed += 1
        aliases: dict[str, int | None] = {}
        for note_id, path, title in con.execute("SELECT id,path,title FROM notes"):
            for name in (title, Path(path).stem, path.removesuffix(".md")):
                key = name.casefold()
                if key not in aliases:
                    aliases[key] = note_id
                elif aliases[key] != note_id:
                    aliases[key] = None
        for rowid, name in con.execute("SELECT rowid,dst_name FROM links"):
            target = aliases.get(name.casefold())
            con.execute("UPDATE links SET dst_note_id_or_null=? WHERE rowid=?", (target, rowid))
    counts = [con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("notes", "chunks", "links")]
    con.close()
    return {"notes": counts[0], "chunks": counts[1], "links": counts[2], "reindexed": reindexed,
            "deleted": len(set(known) - disk_paths), "seconds": round(time.perf_counter() - started, 3)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", type=Path)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    vault = args.vault
    if vault is None:
        config_path = Path.home() / ".dont-forget" / "config.json"
        try:
            vault = Path(json.loads(config_path.read_text())["vault"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            parser.error(f"cannot read vault from {config_path}: {error}")
    if not vault.is_dir():
        parser.error(f"vault is not a directory: {vault}")
    print(json.dumps(build(vault, rebuild=args.rebuild), ensure_ascii=False))


if __name__ == "__main__":
    main()
