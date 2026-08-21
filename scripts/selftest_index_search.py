#!/usr/bin/env python3
"""Small dependency-free self-check for index.py and search.py."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from index import extract_links, parse_frontmatter
from search import apply_budget, fts_query


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
assert fts_query("чанк") == '"чанк"*'
assert fts_query("чанк тест") == '"чанк"* OR "тест"*'
assert fts_query("chunks") == '"chunks"'
assert fts_query("кот") == '"кот"'

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

    # No chunk holds both words, so the answer would be built out of strangers.
    weak = run("alphaword gammaword")
    assert weak["coverage"]["weak_match"] is True, weak["coverage"]
    assert weak["coverage"]["best_terms_matched"] == 1, weak["coverage"]
    # A weak match must not also be amplified: no neighbours of an irrelevant seed.
    assert weak["coverage"]["returned_by_link"] == 0, weak["coverage"]
    assert all(f["found_by"] == "text" for f in weak["fragments"]), weak["fragments"]

    # Coverage must describe the whole match set, not the re-ranked pool.
    assert weak["coverage"]["matched_chunks"] >= 11, weak["coverage"]
    assert isinstance(covering["coverage"]["expanded_notes"], int), covering["coverage"]

print("ok")
