#!/usr/bin/env python3
"""audit_semantic.py on a synthetic index with hand-set vectors: groups, pairs, stale, verdict carry-over.

The numbers need numpy and scikit-learn, which live only in the plugin venv, so the script runs
there as a subprocess. Without that venv the test says so on stderr and passes.
"""

import _selftest_env  # noqa: F401

import hashlib
import json
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import audit_common as ac
import semantic
from index import build

VENV = Path.home() / ".dont-forget" / "venv" / "bin" / "python"
SCRIPT = Path(__file__).with_name("audit_semantic.py")
if not VENV.is_file():
    print(f"SKIP: no plugin venv at {VENV}", file=sys.stderr)
    sys.exit(0)

DIM = 24


def axis(main, noise=None, amount=0.15):
    v = [0.0] * DIM
    v[main] = 1.0
    if noise is not None:
        v[noise] = amount
    return v


# title: (type, project, vector, body)
NOTES = {
    **{f"Gotcha — A{i}": ("atom", "p1", axis(0, 8 + i), "про A") for i in range(4)},
    **{f"Gotcha — B{i}": ("atom", "p2", axis(1, 12 + i), "про B") for i in range(4)},
    # Covered: the godnote GC links all four, so this group is not a candidate.
    **{f"Fact — C{i}": ("atom", "p4", axis(5, 16 + i), "про C") for i in range(4)},
    "Godnote — GC": ("godnote", "p4", axis(6), "".join(f"- [[Fact — C{i}]]\n" for i in range(4))),
    "Godnote — God": ("godnote", "", axis(4), "общее"),
    "Gotcha — X": ("atom", "p1", axis(2, 3, 0.1), "икс"),
    "Gotcha — Y": ("atom", "p3", axis(2, 3, -0.1), "игрек"),
    "Fact — S": ("atom", "p5", axis(7), "не годится\n\n## Поправка 2026-01-02\n\nгодится"),
    "Fact — S2": ("atom", "p5", axis(21), "старое"),
    "Decision — R": ("atom", "p5", axis(22), "заменяет [[Fact — S2]]"),
    "Lead — L": ("lead", "", axis(23), "наводка"),
}

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    vault, db = root / "vault", root / "index.db"
    vault.mkdir()
    for title, (typ, project, _, body) in NOTES.items():
        meta = f"type: {typ}\n" + (f"project: {project}\n" if project else "") + "date: 2026-01-01\n"
        (vault / f"{title}.md").write_text(f"---\n{meta}---\n\n{body}\n", encoding="utf-8")
    build(vault, db)

    with sqlite3.connect(db) as con, sqlite3.connect(semantic.vector_db_path(db)) as vcon:
        vcon.execute("CREATE TABLE chunk_vectors (hash TEXT PRIMARY KEY, vector BLOB NOT NULL, dim INTEGER NOT NULL)")
        for body, title in con.execute("SELECT c.body, n.title FROM chunks c JOIN notes n ON n.id = c.note_id"):
            half = struct.pack(f"<{DIM}e", *NOTES[title][2])
            vcon.execute("INSERT OR REPLACE INTO chunk_vectors VALUES (?,?,?)",
                         (hashlib.sha256(body.encode()).hexdigest(), half, DIM))

    def audit(*args, ok=True):
        done = subprocess.run([str(VENV), str(SCRIPT), *args, "--db", str(db), "--vault", str(vault)],
                              env=os.environ, capture_output=True, text=True)
        assert (done.returncode == 0) == ok, (done.returncode, done.stderr)
        return json.loads(done.stdout) if ok else done.stderr

    first = audit("candidates")
    assert first["total"] == {"group": 2, "cross": 1, "same": 18, "stale": 2}, first
    assert first["to_read"] == first["total"] and first["carried"] == {k: 0 for k in ac.KINDS}, first
    assert [(b["kind"], b["items"]) for b in first["batches"]] == \
        [("group", 2), ("cross", 1), ("same", 18), ("stale", 2)], first["batches"]
    run = Path(first["run"])
    cands = json.loads((run / "candidates.json").read_text(encoding="utf-8"))
    assert sorted(sorted(m["title"] for m in g["members"]) for g in cands["group"]) == \
        [[f"Gotcha — A{i}" for i in range(4)], [f"Gotcha — B{i}" for i in range(4)]], cands["group"]
    assert cands["group"][0]["nearest_godnotes"][0]["links_to_group"] == 0
    assert {cands["cross"][0]["a"]["title"], cands["cross"][0]["b"]["title"]} == {"Gotcha — X", "Gotcha — Y"}
    assert cands["cross"][0]["key"] == ac.finding_key("cross", ["Gotcha — X", "Gotcha — Y"])
    assert {p["a"]["project"] for p in cands["same"]} == {"p1", "p2", "p4"}
    assert sorted((s["how"], s["old"]["title"], (s["new"] or {}).get("title")) for s in cands["stale"]) == \
        [("заменяет", "Fact — S2", "Decision — R"), ("поправка", "Fact — S", None)], cands["stale"]
    prompt = Path(first["batches"][0]["prompt"]).read_text(encoding="utf-8")
    assert str(run / "batches" / "group0.in.json") in prompt and first["batches"][0]["answer"] in prompt

    def answer(batch, keys=None):
        items = json.loads(Path(batch["answer"].replace(".ann.json", ".in.json")).read_text(encoding="utf-8"))
        Path(batch["answer"]).write_text(json.dumps(
            [{"key": k, "keep": False} for k in (keys or [c["key"] for c in items])]), encoding="utf-8")

    # A reader that answers about something it was not asked fails the whole merge, loudly.
    cross_batch = first["batches"][1]
    answer(cross_batch, ["l-" + "0" * 16])
    error = audit("merge", ok=False)
    assert "cross0.ann.json" in error and "l-0000000000000000" in error, error
    assert not ac.VERDICTS.exists()
    # Unmerged answers block a new candidates run instead of being wiped with the batches.
    answer(cross_batch)
    assert "cross0.ann.json" in audit("candidates", ok=False)

    for batch in first["batches"]:
        answer(batch)
    merged = audit("merge")
    assert merged == {"merged": 23, "not_answered": [], "verdicts": 23}, merged
    verdicts = ac.load_verdicts()
    assert verdicts[cands["cross"][0]["key"]] == {"key": cands["cross"][0]["key"], "keep": False,
                                                  "kind": "cross", "run": run.name}

    second = audit("candidates")
    assert second["to_read"] == {k: 0 for k in ac.KINDS} and second["carried"] == first["total"], second
    assert second["batches"] == [] and list((run / "batches").iterdir()) == [], second

print("selftest_audit_semantic: ok")
