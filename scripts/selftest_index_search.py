#!/usr/bin/env python3
"""Small dependency-free self-check for index.py and search.py."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from index import _split_large, extract_links, parse_frontmatter
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
    (vault / "note.md").write_text("# Note\n\nfreshnessalpha\n")
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
    assert json.loads(first.stdout)["coverage"]["matched_chunks"] == 1
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
    second = subprocess.run(
        [sys.executable, str(search_script), "freshnessbeta"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert json.loads(second.stdout)["coverage"]["matched_chunks"] == 1
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

print("ok")
