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
    assert json.loads(first.stdout)["coverage"]["text_matches"] == 1
    (vault / "note.md").write_text("# Note\n\nfreshnessbeta\n")
    second = subprocess.run(
        [sys.executable, str(search_script), "freshnessbeta"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert json.loads(second.stdout)["coverage"]["text_matches"] == 1
print("ok")
