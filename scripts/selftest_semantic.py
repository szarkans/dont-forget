#!/usr/bin/env python3
"""Semantic lane contracts without torch or a downloaded model."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from index import build
import semantic

SCRIPT = Path(__file__).with_name("search.py")
INDEX = Path(__file__).with_name("index.py")


def run(script, home, vault, db, *args, stub=True):
    env = {**os.environ, "DONT_FORGET_HOME": str(home)}
    env.pop("DONT_FORGET_EMBED_STUB", None)
    if stub:
        env["DONT_FORGET_EMBED_STUB"] = "1"
    done = subprocess.run([sys.executable, str(script), *args, "--vault", str(vault),
                           "--db", str(db)], env=env, capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    home, vault, db = root / "home", root / "vault", root / "index.db"
    home.mkdir()
    vault.mkdir()
    answer = vault / "answer.md"
    answer.write_text("# Answer\n\nптица хруст и соус\n")
    (vault / "other.md").write_text("# Other\n\nother content\n")
    build(vault, db)

    # Missing venv preserves the word-only search and names the reason.
    fallback = run(SCRIPT, home, vault, db, "bird crisp", stub=False)
    assert fallback["coverage"]["semantic"] == "off: no venv", fallback["coverage"]
    assert fallback["coverage"]["matched_chunks"] == 0

    fake_python = home / "venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.symlink_to(sys.executable)
    missing_model = run(SCRIPT, home, vault, db, "bird crisp", stub=False)
    assert missing_model["coverage"]["semantic"].startswith("off: model unavailable:"), missing_model

    with patch.object(semantic, "VENV_PYTHON", Path(sys.executable)), \
            patch.object(semantic.subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 1)):
        assert semantic.run(db, "bird crisp")["semantic"] == "off: timeout"

    first = run(SCRIPT, home, vault, db, "bird crisp")
    assert first["coverage"]["semantic"] == "ready", first["coverage"]
    assert first["coverage"]["returned_by_meaning"] >= 1, first["coverage"]
    assert first["fragments"][0]["path"] == "answer.md", first["fragments"]
    assert first["fragments"][0]["found_by"] == "meaning", first["fragments"]
    assert not first["coverage"]["weak_match"], first["coverage"]
    with sqlite3.connect(db) as con:
        before = con.execute("SELECT count(*) FROM chunk_vectors").fetchone()[0]
    unchanged = run(INDEX, home, vault, db, "--embed")
    assert unchanged["embedding"]["new_vectors"] == 0, unchanged
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM chunk_vectors").fetchone()[0] == before

    answer.write_text("---\ntype: atom\n---\n# Answer\n\nптица хруст и соус\n")
    rebuilt_same_text = run(INDEX, home, vault, db, "--embed")
    assert rebuilt_same_text["reindexed"] == 1, rebuilt_same_text
    assert rebuilt_same_text["embedding"]["new_vectors"] == 0, rebuilt_same_text

    answer.write_text("---\ntype: atom\n---\n# Answer\n\nптица хруст и соус; added line\n")
    changed = run(INDEX, home, vault, db, "--embed")
    assert changed["embedding"]["new_vectors"] == 1, changed
    assert changed["embedding"]["semantic"] == "ready", changed
    with sqlite3.connect(db) as con:
        # The old text's orphaned vector is pruned before the new one is stored.
        assert con.execute("SELECT count(*) FROM chunk_vectors").fetchone()[0] == before

    (vault / "lead.md").write_text("---\ntype: lead\n---\n# Lead\n\nптица хруст\n")
    lead = run(SCRIPT, home, vault, db, "bird crisp", "--scope", "leads")
    assert [item["path"] for item in lead["fragments"]] == ["lead.md"], lead
    own = run(SCRIPT, home, vault, db, "bird crisp", "--scope", "own")
    assert own["fragments"][0]["path"] == "answer.md", own

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    home, vault, db = root / "home", root / "vault", root / "index.db"
    home.mkdir()
    vault.mkdir()
    for n in range(10):
        (vault / f"{n}.md").write_text(f"# Note {n}\n\nunique content {n}\n")
    backlog = run(SCRIPT, home, vault, db, "bird crisp")
    assert backlog["coverage"]["semantic"] == "partial: 0 of 10 chunks", backlog
    assert "--embed" in backlog["coverage"]["semantic_next_step"]
    (vault / "answer.md").write_text("# Answer\n\nптица хруст\n")
    filled = run(INDEX, home, vault, db, "--embed")
    assert filled["embedding"]["semantic"] == "ready", filled
    for n in range(10, 20):
        (vault / f"{n}.md").write_text(f"# Note {n}\n\nunique content {n}\n")
    partial = run(SCRIPT, home, vault, db, "bird crisp")
    assert partial["coverage"]["semantic"] == "partial: 11 of 21 chunks", partial
    assert partial["fragments"][0]["path"] == "answer.md", partial
    assert partial["fragments"][0]["found_by"] == "meaning", partial

print("ok")
