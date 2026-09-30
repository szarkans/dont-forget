#!/usr/bin/env python3
"""Semantic lane contracts without torch or a downloaded model."""

import json
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import _selftest_env  # noqa: F401
import index
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
        env["DONT_FORGET_TEST"] = "1"
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
    fake_python.write_text("#!/bin/sh\nprintf 'model unavailable: test fixture\\n' >&2\nexit 1\n")
    fake_python.chmod(0o755)
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
    assert vectors_db.name.startswith("index.db.vectors-")
    assert semantic.vector_db_path(root / "index.sqlite") != vectors_db
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
    (root / "index.db.vectors-retired.db").touch()
    old = run(SCRIPT, home, vault, db, "bird crisp")
    assert old["coverage"]["semantic_old_vector_files"] == ["index.db.vectors-retired.db"], old

    # An index name containing glob metacharacters only lists its own old caches.
    bracket_db = root / "index[1].db"
    bracket_db.write_bytes(db.read_bytes())
    (root / "index[1].db.vectors-retired.db").touch()
    bracket = run(SCRIPT, home, vault, bracket_db, "bird crisp")
    assert bracket["coverage"]["semantic_old_vector_files"] == ["index[1].db.vectors-retired.db"], bracket
    with patch.object(Path, "glob", side_effect=OSError("cannot list cache")):
        unavailable = search.search("птица", db_path=db)
    assert unavailable["coverage"]["semantic"] == "off: OSError", unavailable
    assert unavailable["fragments"], unavailable

    semantic_keys = {key for key in first["coverage"] if key.startswith("semantic")}
    semantic_keys.add("returned_by_meaning")
    for query in ("", "... !?"):
        with patch.object(search, "semantic_run", side_effect=AssertionError("empty query started worker")):
            empty = search.search(query, db_path=db)
        assert semantic_keys <= empty["coverage"].keys(), empty
        assert empty["coverage"]["semantic"] == "off: empty query", empty
        assert empty["coverage"]["returned_by_meaning"] == 0, empty

    answer_id = sqlite3.connect(db).execute(
        "SELECT c.id FROM chunks c JOIN notes n ON n.id=c.note_id WHERE n.path='answer.md'").fetchone()[0]
    injected = {"semantic": "ready", "rankings": [[answer_id, 0.95]]}
    with patch.object(search, "semantic_run", return_value=injected):
        tiny = search.search("bird crisp", budget=1, db_path=db)
        assert tiny["fragments"] == [] and tiny["coverage"]["weak_match"], tiny
        roomy = search.search("bird crisp", db_path=db)
        assert not roomy["coverage"]["weak_match"], roomy
        assert roomy["coverage"]["returned_by_meaning"] >= 1, roomy
    injected["rankings"] = [[answer_id, 0.89]]
    with patch.object(search, "semantic_run", return_value=injected):
        below_floor = search.search("bird crisp", db_path=db)
        assert below_floor["coverage"]["returned_by_meaning"] == 0, below_floor
        assert below_floor["coverage"]["weak_match"], below_floor

    # The injectable encoder exercises the very same cosine path as the model.
    direct = semantic_worker.execute({"db": str(db), "vectors_db": str(vectors_db),
                                      "query": "bird crisp", "full": False, "limit": 8},
                                     encoder=semantic_worker.StubEncoder())
    assert direct["rankings"][0][1] > direct["rankings"][-1][1], direct

    # A rebuild preserves this index's content-hash cache.
    with sqlite3.connect(vectors_db) as con:
        old_count = con.execute("SELECT count(*) FROM chunk_vectors").fetchone()[0]
    rebuilt = run(INDEX, home, vault, db, "--rebuild")
    with sqlite3.connect(vectors_db) as con:
        assert con.execute("SELECT count(*) FROM chunk_vectors").fetchone()[0] == old_count

    # A neighbouring index owns a distinct cache and cannot prune this one.
    other_vault = root / "other-vault"
    other_vault.mkdir()
    (other_vault / "unrelated.md").write_text("# Other\n\nother\n")
    other_db = root / "other.db"
    run(INDEX, home, other_vault, other_db, "--embed")
    assert semantic.vector_db_path(other_db) != vectors_db
    with sqlite3.connect(vectors_db) as con:
        assert con.execute("SELECT count(*) FROM chunk_vectors").fetchone()[0] == old_count

    # Same-stem indexes with different extensions must not share or prune vectors.
    sqlite_db = root / "index.sqlite"
    run(INDEX, home, other_vault, sqlite_db, "--embed")
    assert semantic.vector_db_path(sqlite_db).is_file()
    with sqlite3.connect(vectors_db) as con:
        assert con.execute("SELECT count(*) FROM chunk_vectors").fetchone()[0] == old_count

    # Invalid blobs are missing data, both in backlog coverage and in a full pass.
    with sqlite3.connect(vectors_db) as con:
        digest = con.execute("SELECT hash FROM chunk_vectors LIMIT 1").fetchone()[0]
        con.execute("UPDATE chunk_vectors SET vector=? WHERE hash=?", (b"broken", digest))
    repaired = run(INDEX, home, vault, db, "--embed")
    assert repaired["embedding"]["new_vectors"] == 1, repaired
    assert repaired["embedding"]["semantic"] == "ready", repaired

    # Optional cache pruning tolerates absent tables, corrupt files and lock errors.
    other_vectors = semantic.vector_db_path(other_db)
    other_vectors.unlink()
    other_vectors.touch()
    build(other_vault, other_db, rebuild=True)
    other_vectors.write_bytes(b"not sqlite")
    build(other_vault, other_db, rebuild=True)
    real_connect = sqlite3.connect
    def locked_cache(path, *args, **kwargs):
        if Path(path) == other_vectors:
            assert kwargs["timeout"] == 30
            raise sqlite3.OperationalError("database is locked")
        return real_connect(path, *args, **kwargs)
    with patch.object(index.sqlite3, "connect", side_effect=locked_cache):
        build(other_vault, other_db, rebuild=True)
    other_db.write_bytes(b"corrupt index")
    assert build(other_vault, other_db)["notes"] == 1

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
    # More than the incremental limit stays partial when every blob is malformed.
    with sqlite3.connect(semantic.vector_db_path(db)) as con:
        con.execute("UPDATE chunk_vectors SET vector=?", (b"broken",))
    invalid = run(SCRIPT, home, vault, db, "bird crisp")
    assert invalid["coverage"]["semantic"] == "partial: 0 of 21 chunks", invalid
    assert invalid["coverage"]["semantic_embedded_chunks"] == 0, invalid
    assert "--vault" in invalid["coverage"]["semantic_next_step"]
    (home / "config.json").write_text(json.dumps({"vault": str(vault)}))
    configured = subprocess.run([sys.executable, str(SCRIPT), "bird crisp", "--db", str(db)],
                               env={**os.environ, "DONT_FORGET_HOME": str(home),
                                    "DONT_FORGET_EMBED_STUB": "1", "DONT_FORGET_TEST": "1"},
                               capture_output=True, text=True, check=True)
    command = shlex.split(json.loads(configured.stdout)["coverage"]["semantic_next_step"])
    assert command[command.index("--vault") + 1] == str(vault), command
    assert command[command.index("--db") + 1] == str(db), command

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    vault = root / "vault"
    vault.mkdir()
    db = root / "index.db"
    (vault / "answer.md").write_text("птица [[neighbour]] " + "x" * 780)
    (vault / "neighbour.md").write_text("other " + "n" * 279)
    build(vault, db)
    with sqlite3.connect(db) as con:
        answer_id = con.execute("SELECT c.id FROM chunks c JOIN notes n ON n.id=c.note_id "
                                "WHERE n.path='answer.md'").fetchone()[0]
    injected = {"semantic": "ready", "rankings": [[answer_id, 0.95]]}
    with patch.object(search, "semantic_run", return_value=injected):
        # A graph reserve must retain the 800-byte semantic seed at budget 1000.
        result = search.search("bird", budget=1000, db_path=db)
        assert result["fragments"][0]["path"] == "answer.md", result
        assert not result["coverage"]["weak_match"], result
        assert result["coverage"]["bytes_used"] <= 1000
        # A dual-lane fragment keeps its cosine and clears weak text coverage.
        dual = search.search("птица unknown unseen absent", scope="all", db_path=db)
        assert dual["fragments"][0]["found_by"] == "text", dual
        assert dual["fragments"][0]["cosine"] == 0.95, dual
        assert not dual["coverage"]["weak_match"], dual
        # Word-only callers never even launch the worker.
        with patch.object(search, "semantic_run", side_effect=AssertionError("model started")):
            assert search.search("bird", db_path=db, semantic=False)["coverage"]["weak_match"]
    # Below-floor text hits still expose their measured cosine, without clearing weak.
    injected["rankings"] = [[answer_id, search.COSINE_FLOOR]]
    with patch.object(search, "semantic_run", return_value=injected):
        below = search.search("птица unknown unseen absent", scope="all", db_path=db)
        assert below["fragments"][0]["cosine"] == search.COSINE_FLOOR, below
        assert below["coverage"]["weak_match"], below

    # Seed strength follows fused rank, independent of incomparable raw lane scores.
    (vault / "text.md").write_text("alpha beta gamma delta [[second-neighbour]]")
    (vault / "second-neighbour.md").write_text("unrelated")
    build(vault, db)
    seen_seeds = []
    real_neighbours = search._neighbours
    def capture(con, seeds, *args):
        seen_seeds.append(seeds)
        return real_neighbours(con, seeds, *args)
    injected["rankings"] = [[answer_id, 0.95]]
    with patch.object(search, "semantic_run", return_value=injected), \
            patch.object(search, "_neighbours", side_effect=capture):
        mixed = search.search("alpha beta gamma delta", db_path=db, scope="all")
    assert mixed["fragments"][0]["found_by"] == "meaning", mixed
    assert seen_seeds[0][0][1] > seen_seeds[0][1][1], seen_seeds

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    vault = root / "vault"
    vault.mkdir()
    db = root / "index.db"
    for n in range(6):
        (vault / f"own{n}.md").write_text("птица [[linked]] " + "x" * 60)
    (vault / "weak-lead.md").write_text("---\ntype: lead\n---\nalpha")
    (vault / "linked.md").write_text("---\ntype: Lead\n---\nother source")
    build(vault, db)
    with sqlite3.connect(db) as con:
        ids = [r[0] for r in con.execute("SELECT c.id FROM chunks c JOIN notes n ON n.id=c.note_id "
                                        "WHERE n.path LIKE 'own%'")]
    injected = {"semantic": "ready", "rankings": [[i, 0.95] for i in ids]}
    with patch.object(search, "semantic_run", return_value=injected):
        result = search.search("bird alpha gamma delta epsilon", budget=250, db_path=db)
        leads = [f for f in result["fragments"] if f["type"].strip().lower() == "lead"]
        assert all(f["found_by"] == "link" for f in leads), result
        assert all(f["path"] != "weak-lead.md" for f in result["fragments"]), result
        roomy = search.search("bird alpha gamma delta epsilon", db_path=db)
        assert any(f["path"] == "linked.md" for f in roomy["fragments"]), roomy

    # Missing indexes produce no index or vector file.
    missing = root / "missing.db"
    missing_vectors = semantic.vector_db_path(missing)
    result = semantic_worker.execute({"db": str(missing), "vectors_db": str(missing_vectors),
                                      "query": None, "full": False, "limit": 8})
    assert result["semantic"] == "off: no index", result
    assert not missing.exists() and not missing_vectors.exists()

    # SQLite failures are controlled worker results, including in subprocesses.
    broken_index = root / "no-chunks.db"
    broken_index.touch()
    bad_request = {"db": str(broken_index), "vectors_db": str(root / "broken.vectors.db"),
                   "query": None, "full": False, "limit": 8}
    result = semantic_worker.execute(bad_request, semantic_worker.StubEncoder())
    assert result["semantic"] == "off: sqlite: no such table: chunks", result
    Path(bad_request["vectors_db"]).write_bytes(b"not sqlite")
    bad_request["db"] = str(db)
    failed = subprocess.run([sys.executable, str(semantic.WORKER)], input=json.dumps(bad_request),
                            text=True, capture_output=True)
    assert failed.returncode == 0 and not failed.stderr, failed
    assert json.loads(failed.stdout)["semantic"].startswith("off: sqlite:"), failed.stdout

    # A stub switch alone cannot replace a real model in a user's home.
    with patch.dict(os.environ, {"DONT_FORGET_EMBED_STUB": "1"}, clear=True):
        assert not semantic.stub_enabled()
        os.environ["DONT_FORGET_HOME"] = str(root)
        assert not semantic.stub_enabled()
        os.environ["DONT_FORGET_TEST"] = "1"
        assert semantic.stub_enabled()
        os.environ["DONT_FORGET_HOME"] = str(Path.home() / ".dont-forget")
        assert not semantic.stub_enabled()

# Model loading overrides even an explicitly online environment, without loading deps here.
def fake_model(*args, **kwargs):
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert kwargs["revision"] == semantic.MODEL_REVISION
    assert "trust_remote_code" not in kwargs
    return SimpleNamespace()
with patch.dict(os.environ, {"HF_HUB_OFFLINE": "0"}), patch.dict(sys.modules, {
        "numpy": SimpleNamespace(),
        "torch": SimpleNamespace(set_num_threads=lambda n: None, float32="float32"),
        "sentence_transformers": SimpleNamespace(SentenceTransformer=fake_model)}):
    assert semantic_worker.ModelEncoder().model.max_seq_length == 512

with patch.dict(sys.modules, {"numpy": None}):
    try:
        semantic_worker.ModelEncoder()
        raise AssertionError("missing model dependencies should fail")
    except SystemExit as error:
        assert "setup.py --install-semantic" in str(error), str(error)

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    vault, db = root / "vault", root / "index.db"
    vault.mkdir()
    (vault / "own.md").write_text("# Own\nalpha")
    (vault / "lead.md").write_text("---\ntype: lead\n---\n# Lead\nalpha")
    build(vault, db)
    weak = search.search("alpha beta gamma delta epsilon", db_path=db, semantic=False)
    assert weak["coverage"]["weak_match"], weak
    assert any(f["path"] == "lead.md" and f["found_by"] == "text" for f in weak["fragments"]), weak

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    vault, home, db = root / "vault", root / "home", root / "index.db"
    vault.mkdir()
    home.mkdir()
    (vault / "z-answer.md").write_text("# Answer\nптица хруст")
    (vault / "catalog.md").write_text("---\ntype: LEAD\n---\n" +
        "\n".join(f"## Tip {n}\nптица хруст" for n in range(510)))
    run(INDEX, home, vault, db, "--embed")
    direct = semantic_worker.execute({"db": str(db), "vectors_db": str(semantic.vector_db_path(db)),
                                      "query": "bird crisp", "full": False, "limit": 8},
                                     semantic_worker.StubEncoder())
    assert len(direct["rankings"]) == 511, len(direct["rankings"])
    # Five hundred leads ahead of an own candidate cannot starve the own scope.
    result = run(SCRIPT, home, vault, db, "bird crisp")
    assert result["fragments"][0]["path"] == "z-answer.md", result["coverage"]

print("ok")
