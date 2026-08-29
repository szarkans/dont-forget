#!/usr/bin/env python3
"""Small dependency-free self-check for index.py and search.py."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from index import _split_large, build, extract_links, parse_frontmatter, schema_stale
from common import connect_ro
from search import apply_budget, fts_query, widen


meta, body = parse_frontmatter("""---
type: fact
tags: [one, two]
aliases:
  - Alpha
  - Beta
---
Body
""")
assert meta == {"type": "fact", "tags": ["one", "two"], "aliases": ["Alpha", "Beta"]}
assert body == "Body\n"

links = extract_links("""[[Да|подпись]] [[Нет#раздел]] ![[Вложение]]
```python
fake = "[[Код]]"
```
~~~
[[Тоже код]]
~~~
""")
assert links == ["Да", "Нет", "Вложение"], links
# The way to show a ``` example is to wrap it in ````, so a fence must close only on a
# marker at least as long as the one that opened it. Closing on the inner marker used to
# spill the rest of the outer block back out — as links here, as live threads in the
# digest, which is the note that documents how threads are written.
assert extract_links("````\n```\n[[Внутри]]\n```\n[[Тоже внутри]]\n````\n[[Снаружи]]") == ["Снаружи"]
# A marker carrying an info string opens a block, it never closes one.
assert extract_links("```\n[[Внутри]]\n```python\n[[Тоже внутри]]\n```\n[[Снаружи]]") == ["Снаружи"]
# Every word long enough to carry endings gets a prefix wildcard, whatever alphabet it
# is in. Handing it to Cyrillic alone left every other language with neither the porter
# stemmer (which only knows English) nor a prefix: a German vault answered "Textur" with
# nothing while "Texturen" sat in it.
assert fts_query("чанк") == '"чанк"*'
assert fts_query("чанк тест") == '"чанк"* OR "тест"*'
assert fts_query("chunks") == '"chunks"*'
assert fts_query("Texturen") == '"Texturen"*'
# Too short to be an inflected form; widening these only makes them vague.
assert fts_query("кот") == '"кот"'
assert fts_query("api") == '"api"'

# A MOC is a list of links with no sentence ends anywhere, so the sentence splitter
# used to hand it back whole: one chunk of ten kilobytes, larger than the entire search
# budget, which made every query whose best match was that MOC return nothing at all.
moc = "\n".join(f"- [[Заметка номер {n}]] — почему она тут" for n in range(400))
parts = _split_large(moc)
assert parts, "a link list must still produce chunks"
assert max(len(part.encode()) for part in parts) <= 1200, max(len(p.encode()) for p in parts)
assert "".join(parts).count("[[") == 400, "no link may be lost while splitting"

items = [{"text": "я" * 3}, {"text": "x"}]
kept, dropped = apply_budget(items, 6)
assert kept == [items[0]] and dropped == 1

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    vault = home / "vault"
    vault.mkdir()
    (vault / "note.md").write_text(
        "---\ntype: atom\nkind: gotcha\nsource: созвон 2026-08-10\nproject: catcraft\n"
        "date: 2026-08-10\nreviewed: 2026-08-20\n"
        "dies-when: DNS record repointed\n---\n\n# Note\n\nfreshnessalpha\n")
    config_dir = home / ".dont-forget"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({"vault": str(vault)}))
    env = os.environ.copy()
    env["HOME"] = str(home)
    search_script = Path(__file__).with_name("search.py")
    first = subprocess.run(
        [sys.executable, str(search_script), "freshnessalpha"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert (config_dir / "index.db").is_file()
    first_result = json.loads(first.stdout)
    assert first_result["coverage"]["matched_chunks"] == 1
    # A fragment must carry its note's freshness fields so a reader can tell a fresh
    # observation from a standing rule, and see a named expiry that lives in
    # frontmatter rather than in the matched chunk.
    fragment = first_result["fragments"][0]
    assert fragment["type"] == "atom" and fragment["date"] == "2026-08-10", fragment
    assert fragment["reviewed"] == "2026-08-20", fragment
    assert fragment["dies_when"] == "DNS record repointed", fragment
    # Genre and provenance travel with the fragment. Stored and not returned is the state
    # this fixes: it is what stopped the digest from asking for gotchas by name.
    assert fragment["kind"] == "gotcha", fragment
    assert fragment["source"] == "созвон 2026-08-10", fragment
    assert fragment["project"] == "catcraft", fragment
    query_log = config_dir / "queries.jsonl"
    log_lines_before = 0
    if query_log.is_file():
        with query_log.open(encoding="utf-8") as handle:
            log_lines_before = sum(1 for _ in handle)
    subprocess.run(
        [sys.executable, str(search_script), "freshnessbeta", "--db", str(config_dir / "skip.db"), "--raw", "x"],
        env=env, capture_output=True, text=True, check=True,
    )
    with query_log.open(encoding="utf-8") as handle:
        log_lines_after = sum(1 for _ in handle)
    assert log_lines_after == log_lines_before
    (vault / "note.md").write_text("# Note\n\nfreshnessbeta\n")
    # A neighbour that shares no query word: it can only arrive by following the link.
    (vault / "neighbour.md").write_text("# Neighbour\n\nSee [[note]] for the details.\n")
    second = subprocess.run(
        [sys.executable, str(search_script), "freshnessbeta"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert json.loads(second.stdout)["coverage"]["matched_chunks"] == 1

    # The log has to say how each note arrived. Without that tag two notes the graph walk
    # always drags in behind each other look exactly like two notes that genuinely answer
    # the same questions, and every co-retrieval conclusion rests on telling them apart.
    with query_log.open(encoding="utf-8") as handle:
        logged = json.loads(handle.readlines()[-1])
    assert [entry["path"] for entry in logged["top"]] == ["note.md", "neighbour.md"], logged
    assert [entry["found_by"] for entry in logged["top"]] == ["text", "link"], logged

# An unchanged note must stop at stat: reading every note just to prove its digest is
# unchanged made a no-op refresh too slow for the SessionStart hook.
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    vault = home / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("# Note\n\nunchanged\n")
    db = home / "mtime-skip.db"
    build(vault, db)

    vault_reads = []
    original_read_bytes = Path.read_bytes

    def count_vault_reads(path: Path) -> bytes:
        if path == note:
            vault_reads.append(path)
        return original_read_bytes(path)

    Path.read_bytes = count_vault_reads
    try:
        unchanged = build(vault, db)
    finally:
        Path.read_bytes = original_read_bytes
    assert unchanged["reindexed"] == 0, unchanged
    assert vault_reads == [], vault_reads

# A metadata-only touch pays for one digest, then heals the stored mtime so later
# refreshes return to the read-free path.
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    vault = home / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("# Note\n\nsame content\n")
    db = home / "mtime-touch.db"
    build(vault, db)
    con = sqlite3.connect(db)
    stored_mtime = con.execute("SELECT mtime FROM notes WHERE path='note.md'").fetchone()[0]
    con.close()

    os.utime(note, ns=(note.stat().st_atime_ns, stored_mtime + 1_000_000_000))
    touched_mtime = note.stat().st_mtime_ns
    assert touched_mtime != stored_mtime
    touched = build(vault, db)
    con = sqlite3.connect(db)
    healed_mtime = con.execute("SELECT mtime FROM notes WHERE path='note.md'").fetchone()[0]
    con.close()
    assert touched["reindexed"] == 0, touched
    assert healed_mtime == touched_mtime, (healed_mtime, touched_mtime)

    vault_reads = []
    original_read_bytes = Path.read_bytes

    def count_vault_reads(path: Path) -> bytes:
        if path == note:
            vault_reads.append(path)
        return original_read_bytes(path)

    Path.read_bytes = count_vault_reads
    try:
        healed = build(vault, db)
    finally:
        Path.read_bytes = original_read_bytes
    assert healed["reindexed"] == 0, healed
    assert vault_reads == [], vault_reads

# Restoring mtime after changing content is the paid tradeoff of the mtime gate, not a
# bug: such changes are deliberately missed until --rebuild.
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    vault = home / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("# Note\n\nold content\n")
    db = home / "mtime-restored.db"
    build(vault, db)
    con = sqlite3.connect(db)
    stored_mtime, stored_digest = con.execute(
        "SELECT mtime,sha256 FROM notes WHERE path='note.md'").fetchone()
    con.close()

    note.write_text("# Note\n\nnew content\n")
    os.utime(note, ns=(note.stat().st_atime_ns, stored_mtime))
    restored = build(vault, db)
    con = sqlite3.connect(db)
    still_stored = con.execute("SELECT mtime,sha256 FROM notes WHERE path='note.md'").fetchone()
    con.close()
    assert restored["reindexed"] == 0, restored
    assert still_stored == (stored_mtime, stored_digest), still_stored

# A normal edit changes both content and mtime, so it still takes the full reindex path
# and records the new stat and digest.
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    vault = home / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("# Note\n\nold content\n")
    db = home / "mtime-edit.db"
    build(vault, db)
    con = sqlite3.connect(db)
    stored_mtime = con.execute("SELECT mtime FROM notes WHERE path='note.md'").fetchone()[0]
    con.close()

    edited = b"# Note\n\nnew content\n"
    note.write_bytes(edited)
    os.utime(note, ns=(note.stat().st_atime_ns, stored_mtime + 1_000_000_000))
    edited_mtime = note.stat().st_mtime_ns
    changed = build(vault, db)
    con = sqlite3.connect(db)
    stored = con.execute("SELECT mtime,sha256 FROM notes WHERE path='note.md'").fetchone()
    con.close()
    assert changed["reindexed"] == 1, changed
    assert stored == (edited_mtime, hashlib.sha256(edited).hexdigest()), stored

# Regression: text matches used to fill the whole budget, so link neighbours — the only
# thing this search does that plain FTS5 does not — never reached the output. Fragment
# sizes here are deliberately realistic: with tiny neighbours the old code passed too.
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    vault = home / "vault"
    vault.mkdir()
    for n in range(300):
        (vault / f"noise{n}.md").write_text(
            f"# Noise {n}\n\nworkers handle tasks in batch {n}. "
            + f"context line {n} about queues and jobs. " * 18
        )
    (vault / "seed.md").write_text(
        "# Seed\n\nworkers retry tasks here. "
        + "seed context about the same queues. " * 18 + "\n\n[[answer]]\n"
    )
    (vault / "answer.md").write_text(
        "# Answer\n\nexponential backoff caps at five attempts. "
        + "reason and consequences spelled out. " * 18
    )
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("search.py")), "workers retry tasks",
         "--vault", str(vault), "--db", str(home / "test.db")],
        capture_output=True, text=True, check=True,
    )
    coverage = json.loads(result.stdout)["coverage"]
    assert coverage["returned_by_link"] > 0, coverage
    assert coverage["weak_match"] is False, coverage

    # English used to be searched by exact form only: "retry" missed "retried".
    (vault / "english.md").write_text("# English\n\nthe scheduler retried every failing job\n")
    stemmed = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("search.py")), "scheduler retry",
         "--vault", str(vault), "--db", str(home / "test.db")],
        capture_output=True, text=True, check=True,
    )
    assert any("english.md" in f["path"] for f in json.loads(stemmed.stdout)["fragments"]), stemmed.stdout[:400]
    assert not (Path.home() / ".dont-forget" / "test.db").exists()


# Ranking used to be bm25 alone, which favours a short chunk holding one rare word over
# a long chunk holding every word of the query: a note about backups won a search about
# a rule. Covering more of the query must win now.
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    vault = home / "vault"
    vault.mkdir()
    for n in range(30):
        (vault / f"filler{n}.md").write_text(f"# Filler {n}\n\nunrelated words about weather {n}\n")
    for n in range(10):
        (vault / f"alphanote{n}.md").write_text(f"# Alphanote {n}\n\nalphaword appears here {n}\n")
    (vault / "short.md").write_text("# Short\n\nbetaword\n")
    (vault / "long.md").write_text("# Long\n\nalphaword and betaword together. "
                                   + "surrounding sentences that make this chunk long. " * 12)
    (vault / "gammanote.md").write_text("# Gammanote\n\ngammaword stands alone\n")

    def run(query: str, *extra: str) -> dict:
        done = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("search.py")), query,
             "--vault", str(vault), "--db", str(home / "rank.db"), *extra],
            capture_output=True, text=True, check=True)
        return json.loads(done.stdout)

    covering = run("alphaword betaword")
    assert covering["fragments"][0]["path"] == "long.md", covering["fragments"][0]["path"]
    assert covering["fragments"][0]["terms_matched"] == 2, covering["fragments"][0]
    assert covering["coverage"]["weak_match"] is False, covering["coverage"]

    # No chunk holds both words, and this used to be enough to call the result weak.
    # It is not: the chunk that matched holds gammaword, the rarer and more informative
    # of the two, and covers two thirds of the query's mass. Counting words called this
    # weak and called a chunk holding two common words an answer — exactly backwards.
    split = run("alphaword gammaword")
    assert split["coverage"]["best_terms_matched"] == 1, split["coverage"]
    assert split["coverage"]["best_mass_share"] > 0.6, split["coverage"]
    assert split["coverage"]["weak_match"] is False, split["coverage"]
    assert split["coverage"]["unmatched_terms"] == [], split["coverage"]

    # Coverage must describe the whole match set, not the re-ranked pool.
    assert split["coverage"]["matched_chunks"] >= 11, split["coverage"]
    assert isinstance(covering["coverage"]["expanded_notes"], int), covering["coverage"]

    # Two query words landing in one chunk is not an answer when the words the question
    # is actually about are absent. Here the best chunk holds 2 of 4 terms but only a
    # third of the query's idf mass, and the two it holds are the two the vault happens
    # to own. Counting terms answers "found"; counting how much of the query's mass the
    # best chunk covers answers "the vault does not have this". Measured on the search
    # benchmark: on questions with no answer in the vault the term count abstained 5
    # times out of 14, against a required 60%.
    thin = run("alphaword betaword zebrafish quasarword")
    assert thin["coverage"]["best_terms_matched"] == 2, thin["coverage"]
    assert thin["coverage"]["best_mass_share"] < 0.4, thin["coverage"]
    assert thin["coverage"]["weak_match"] is True, thin["coverage"]
    # A weak match must not also be amplified: no neighbours of an irrelevant seed.
    assert thin["coverage"]["returned_by_link"] == 0, thin["coverage"]
    assert all(f["found_by"] == "text" for f in thin["fragments"]), thin["fragments"]

    # A word the vault does not contain at all is invisible to the mass share: the two
    # words the vault does own carry the best chunk past the threshold on their own, and
    # the question comes back answered. Measured live: the best chunk covered 60% of the
    # mass while the word the question turned on had zero hits anywhere, and three of
    # three agent runs answered confidently, one of them inventing a pull request. The
    # word has to be reported by name — the numbers beside it were not enough — and it
    # must survive widening, which reports the word the user typed, not the stem it was
    # cut to. The flag stays off: refusing here was measured and cost more than it caught.
    qualifier = run("alphaword betaword quasarword")
    assert qualifier["coverage"]["best_mass_share"] > 0.4, qualifier["coverage"]
    assert qualifier["coverage"]["unmatched_terms"] == ["quasarword"], qualifier["coverage"]
    assert qualifier["coverage"]["weak_match"] is False, qualifier["coverage"]

    # A one-word question the vault has no word for is the plainest "not found" there is,
    # and it used to be the one case that never raised the flag: the rule only ran when
    # the query had more than one meaningful word, so a single miss returned no fragments
    # and an unset weak_match — an empty answer the skill was not told to distrust.
    lone = run("quasarword")
    assert lone["coverage"]["content_terms"] == 1, lone["coverage"]
    assert lone["coverage"]["returned"] == 0, lone["coverage"]
    assert lone["coverage"]["weak_match"] is True, lone["coverage"]

    # ...but a single word the vault does have is an answer, not a refusal.
    found = run("gammaword")
    assert found["coverage"]["returned"] > 0, found["coverage"]
    assert found["coverage"]["weak_match"] is False, found["coverage"]


# A word typed in one grammatical form does not prefix-match the same word written in
# another: on a live vault "текстурам"* held 1 chunk out of 3132 while the notes said
# "текстуры", so the subject of the question could not be ranked at all. The stem is
# taken from the index itself, which is why no per-language word list is needed.
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    vault = home / "vault"
    vault.mkdir()
    for n in range(25):
        (vault / f"noise{n}.md").write_text(f"# Noise {n}\n\nчто мы делали тут вчера {n}\n")
    (vault / "texture1.md").write_text("# Один\n\nтекстуры паков лежат рядом с моделями\n")
    (vault / "texture2.md").write_text("# Два\n\nтекстура блока грузится из пака\n")
    (vault / "texture3.md").write_text("# Три\n\nтекстурная сетка не совпадает\n")
    (vault / "rare.md").write_text("# Редкое\n\nщебетунчик встречается ровно однажды\n")
    db = home / "morph.db"

    def run(query: str) -> dict:
        done = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("search.py")), query,
             "--vault", str(vault), "--db", str(db)], capture_output=True, text=True, check=True)
        return json.loads(done.stdout)

    inflected = run("текстурам")
    assert inflected["fragments"][0]["path"].startswith("texture"), inflected["fragments"]

    con = connect_ro(db)
    assert list(widen(con, ['"текстурам"*'])) == ['"текстур"*'], widen(con, ['"текстурам"*'])
    # A rare precise word is never widened away: no shorter prefix buys anything, so
    # widening must not turn a term that pinpoints one note into a vague one.
    assert list(widen(con, ['"щебетунчик"*'])) == ['"щебетунчик"*'], widen(con, ['"щебетунчик"*'])
    # A misspelling has no forms in the vault, and shrinking it until something matches
    # is how a search for "ресруспаке" started answering with "рестарт": a guess at a
    # word form is allowed to give up half the word, no more.
    assert list(widen(con, ['"тексутрам"*'])) == ['"тексу"*'], widen(con, ['"тексутрам"*'])
    con.close()

    # Function words used to decide the ranking: each is worth little, but a question
    # carries six of them and the topic only two. They are recognised by how much of the
    # vault they sit in, not by a list, and they no longer add to a chunk's score.
    mixed = run("что мы тут делали текстурами")
    assert mixed["fragments"][0]["path"].startswith("texture"), mixed["fragments"]
    assert mixed["coverage"]["content_terms"] < mixed["coverage"]["query_terms"], mixed["coverage"]

# The graph lane once reserved its slice up front, so a text fragment larger than the
# remaining share left nothing to expand from and no neighbour ever appeared — the same
# dead lane the reserve exists to prevent, arriving from the other side.
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    vault = home / "vault"
    vault.mkdir()
    (vault / "seed.md").write_text(
        # No sentence ends and no blank line, so the chunker cannot split it: this is
        # how a single fragment grows past the reserved share in a real vault.
        "# Seed\n\nbulkyword heftyword " + "filler " * 800 + "\n\n[[answer]]\n")
    (vault / "answer.md").write_text("# Answer\n\nthe neighbour that explains the seed\n")
    done = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("search.py")), "bulkyword heftyword",
         "--vault", str(vault), "--db", str(home / "bulk.db")],
        capture_output=True, text=True, check=True)
    bulky = json.loads(done.stdout)
    assert bulky["fragments"], bulky["coverage"]
    assert len(bulky["fragments"][0]["text"].encode()) > 8000 * 0.6, "fixture is not large enough"
    assert bulky["coverage"]["returned_by_link"] > 0, bulky["coverage"]
    assert any(f["found_by"] == "text" for f in bulky["fragments"]), bulky["fragments"]

# A link written by alias opens in Obsidian, so an index that calls it broken is wrong
# twice: the health report cries wolf, and the graph lane never walks that edge.
def resolved_links(db_path: Path) -> dict[str, str | None]:
    con = connect_ro(db_path)
    paths = {row[0]: row[1] for row in con.execute("SELECT id,path FROM notes")}
    links = {name: paths.get(dst) for name, dst in
             con.execute("SELECT dst_name,dst_note_id_or_null FROM links")}
    con.close()
    return links


with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    vault = home / "vault"
    vault.mkdir()
    (vault / "widget-moc.md").write_text("---\naliases: [Widget, Gadget]\n---\n\n# Widgets\n\nbody\n")
    (vault / "twin-a.md").write_text("---\naliases: [Twin]\n---\n\nfirst\n")
    (vault / "twin-b.md").write_text("---\naliases: [Twin]\n---\n\nsecond\n")
    (vault / "overlap.md").write_text("---\ntitle: Overlap\n---\n\nthe real one\n")
    (vault / "rival.md").write_text("---\naliases: [Overlap]\n---\n\nthe pretender\n")
    (vault / "linker.md").write_text("[[Widget]] [[gadget]] [[widget-moc.md]] [[Twin]] [[Overlap]] [[widget-moc]] [[Nobody]]\n")
    db = home / "alias.db"
    build(vault, db)

    links = resolved_links(db)
    assert links["Widget"] == "widget-moc.md", links
    # Obsidian ignores case in a link, so the index must too.
    assert links["gadget"] == "widget-moc.md", links
    # Two notes answer to "Twin": inventing a link here is worse than leaving none.
    assert links["Twin"] is None, links
    # A title outranks another note's alias, whatever order the notes were walked in.
    assert links["Overlap"] == "overlap.md", links
    assert links["widget-moc"] == "widget-moc.md", links
    assert links["Nobody"] is None, links
    # Obsidian opens [[Note.md]] as readily as [[Note]], and a title that ends in a word
    # like CLAUDE gets linked to with the extension attached in real vaults.
    assert links["widget-moc.md"] == "widget-moc.md", links

    # Story 9: an alias taken out of a note must untie its links on the next pass, not
    # keep yesterday's picture.
    (vault / "widget-moc.md").write_text("---\naliases: [Gadget]\n---\n\n# Widgets\n\nbody\n")
    build(vault, db)
    links = resolved_links(db)
    assert links["Widget"] is None, links
    assert links["gadget"] == "widget-moc.md", links

    # An index built before this change has no aliases column, and a build against it
    # would either crash or quietly resolve nothing — same failure the tokenizer check
    # exists to prevent, so it is caught the same way.
    stale = home / "stale.db"
    con = sqlite3.connect(stale)
    con.executescript(
        "CREATE TABLE notes(id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,"
        " title TEXT NOT NULL, type TEXT, project TEXT, date TEXT, reviewed TEXT,"
        " mtime INTEGER NOT NULL, sha256 TEXT NOT NULL);")
    con.commit()
    con.close()
    assert schema_stale(stale), "an index without the aliases column must force a rebuild"
    build(vault, stale)
    assert resolved_links(stale)["gadget"] == "widget-moc.md"

    # Same for the genre columns: an index built before them would keep answering, with
    # every note's kind silently empty, and the digest would find no gotchas at all.
    genreless = home / "genreless.db"
    con = sqlite3.connect(genreless)
    con.executescript(
        "CREATE TABLE notes(id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,"
        " title TEXT NOT NULL, type TEXT, project TEXT, date TEXT, reviewed TEXT,"
        " dies_when TEXT, aliases TEXT, mtime INTEGER NOT NULL, sha256 TEXT NOT NULL);")
    con.commit()
    con.close()
    assert schema_stale(genreless), "an index without kind and source must force a rebuild"

# A vault with no aliases anywhere must resolve exactly as it did before.
with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    vault = home / "vault"
    vault.mkdir()
    (vault / "target.md").write_text("---\ntitle: Target\n---\n\nbody\n")
    (vault / "source.md").write_text("[[Target]] [[target]] [[Missing]]\n")
    db = home / "plain.db"
    build(vault, db)
    plain = resolved_links(db)
    assert plain["Target"] == "target.md" and plain["target"] == "target.md", plain
    assert plain["Missing"] is None, plain
    assert schema_stale(db) is False

print("ok")
