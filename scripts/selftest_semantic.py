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
import semantic_worker
import search

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
    vectors_db = semantic.vector_db_path(db)
    assert vectors_db.parent == db.parent
    assert semantic.MODEL_REVISION[:12] in vectors_db.name
    with sqlite3.connect(vectors_db) as con:
        before = con.execute("SELECT count(*) FROM chunk_vectors").fetchone()[0]
    unchanged = run(INDEX, home, vault, db, "--embed")
    assert unchanged["embedding"]["new_vectors"] == 0, unchanged
    with sqlite3.connect(vectors_db) as con:
        assert con.execute("SELECT count(*) FROM chunk_vectors").fetchone()[0] == before

    answer.write_text("---\ntype: atom\n---\n# Answer\n\nптица хруст и соус\n")
    rebuilt_same_text = run(INDEX, home, vault, db, "--embed")
    assert rebuilt_same_text["reindexed"] == 1, rebuilt_same_text
    assert rebuilt_same_text["embedding"]["new_vectors"] == 0, rebuilt_same_text

    answer.write_text("---\ntype: atom\n---\n# Answer\n\nптица хруст и соус; added line\n")
    changed = run(INDEX, home, vault, db, "--embed")
    assert changed["embedding"]["new_vectors"] == 1, changed
    assert changed["embedding"]["semantic"] == "ready", changed
    with sqlite3.connect(vectors_db) as con:
        # The old text's orphaned vector is pruned before the new one is stored.
        assert con.execute("SELECT count(*) FROM chunk_vectors").fetchone()[0] == before

    (vault / "lead.md").write_text("---\ntype: lead\n---\n# Lead\n\nптица хруст\n")
    lead = run(SCRIPT, home, vault, db, "bird crisp", "--scope", "leads")
    assert [item["path"] for item in lead["fragments"]] == ["lead.md"], lead
    own = run(SCRIPT, home, vault, db, "bird crisp", "--scope", "own")
    assert own["fragments"][0]["path"] == "answer.md", own
    (root / "vectors-retired.db").touch()
    old = run(SCRIPT, home, vault, db, "bird crisp")
    assert old["coverage"]["semantic_old_vector_files"] == ["vectors-retired.db"], old

    answer_id = sqlite3.connect(db).execute(
        "SELECT c.id FROM chunks c JOIN notes n ON n.id=c.note_id WHERE n.path='answer.md'").fetchone()[0]
    injected = {"semantic": "ready", "rankings": {
        "own": [[answer_id, 0.95]], "leads": [], "all": [[answer_id, 0.95]]},
        "best_cosine": {"own": 0.95, "leads": None, "all": 0.95}}
    with patch.object(search, "semantic_run", return_value=injected):
        tiny = search.search("bird crisp", budget=1, db_path=db)
        assert tiny["fragments"] == [] and tiny["coverage"]["weak_match"], tiny
        assert "_weak_without_semantic" not in tiny["coverage"], tiny
        roomy = search.search("bird crisp", db_path=db)
        assert not roomy["coverage"]["weak_match"], roomy
        assert roomy["coverage"]["returned_by_meaning"] >= 1, roomy
    injected["rankings"]["own"] = [[answer_id, 0.89]]
    with patch.object(search, "semantic_run", return_value=injected):
        below_floor = search.search("bird crisp", db_path=db)
        assert below_floor["coverage"]["returned_by_meaning"] == 0, below_floor
        assert below_floor["coverage"]["weak_match"], below_floor

    # The injectable encoder exercises the very same cosine path as the model.
    direct = semantic_worker.execute({"db": str(db), "vectors_db": str(vectors_db),
                                      "query": "bird crisp", "full": False, "limit": 8},
                                     encoder=semantic_worker.StubEncoder())
    assert direct["rankings"]["own"][0][1] > direct["rankings"]["own"][-1][1], direct

    # A legacy vector table is moved before a rebuild unlinks index.db.
    with sqlite3.connect(vectors_db) as con:
        old_count = con.execute("SELECT count(*) FROM chunk_vectors").fetchone()[0]
        digest, blob, dim = con.execute("SELECT hash,vector,dim FROM chunk_vectors LIMIT 1").fetchone()
        con.execute("DELETE FROM chunk_vectors WHERE hash=?", (digest,))
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE chunk_vectors (hash TEXT PRIMARY KEY, vector BLOB NOT NULL, dim INTEGER NOT NULL)")
        con.execute("INSERT INTO chunk_vectors VALUES (?,?,?)", (digest, blob, dim))
    rebuilt = run(INDEX, home, vault, db, "--rebuild")
    assert rebuilt["migrated_vectors"] == 1, rebuilt
    with sqlite3.connect(vectors_db) as con:
        assert con.execute("SELECT count(*) FROM chunk_vectors").fetchone()[0] == old_count
    with sqlite3.connect(db) as con:
        assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='chunk_vectors'").fetchone()

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
