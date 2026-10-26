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

DIM = 32


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
    # In a folder that sorts before it, so the index holds Y first and only sorting puts X on side a.
    "0/Gotcha — Y": ("atom", "p3", axis(2, 3, -0.1), "игрек"),
    "Fact — S": ("atom", "p5", axis(7), "не годится\n\n## Поправка 2026-01-02\n\nгодится"),
    "Fact — S2": ("atom", "p5", axis(21), "старое"),
    "Decision — R": ("atom", "p5", axis(22), "заменяет [[Fact — S2]]"),
    # A second note replacing the same one is a second piece of evidence, not a repeat.
    "Decision — R2": ("atom", "p5", axis(24), "тоже заменяет [[Fact — S2]]"),
    # Old notes named by an alias and by a path in a folder.
    "Fact — Al": ("atom", "p6", axis(25), "старое", ["старый алиас"]),
    "Decision — Q": ("atom", "p6", axis(26), "supersedes [[старый алиас]]"),
    "sub/Fact — P": ("atom", "p6", axis(27), "старое"),
    "Decision — Pn": ("atom", "p6", axis(28), "corrects [[sub/Fact — P]]"),
    "Lead — L": ("lead", "", axis(23), "наводка"),
}

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    vault, db = root / "vault", root / "index.db"
    vault.mkdir()
    def write(name, typ, project, body, aliases=()):
        meta = f"type: {typ}\n" + (f"project: {project}\n" if project else "") + "date: 2026-01-01\n" + \
            (f"aliases: [{', '.join(aliases)}]\n" if aliases else "")
        (vault / f"{name}.md").parent.mkdir(parents=True, exist_ok=True)
        (vault / f"{name}.md").write_text(f"---\n{meta}---\n\n{body}\n", encoding="utf-8")

    def embed():
        """Index the vault and give every chunk its note's hand-set vector."""
        build(vault, db)
        with sqlite3.connect(db) as con, sqlite3.connect(semantic.vector_db_path(db)) as vcon:
            vcon.execute("CREATE TABLE IF NOT EXISTS chunk_vectors (hash TEXT PRIMARY KEY, vector BLOB NOT NULL, dim INTEGER NOT NULL)")
            for body, path in con.execute("SELECT c.body, n.path FROM chunks c JOIN notes n ON n.id = c.note_id"):
                half = struct.pack(f"<{DIM}e", *NOTES[path.removesuffix(".md")][2])
                vcon.execute("INSERT OR REPLACE INTO chunk_vectors VALUES (?,?,?)",
                             (hashlib.sha256(body.encode()).hexdigest(), half, DIM))

    for name, (typ, project, _, body, *aliases) in NOTES.items():
        write(name, typ, project, body, *aliases)
    embed()

    def audit(*args, ok=True):
        done = subprocess.run([str(VENV), str(SCRIPT), *args, "--db", str(db), "--vault", str(vault)],
                              env=os.environ, capture_output=True, text=True)
        assert (done.returncode == 0) == ok, (done.returncode, done.stderr)
        return json.loads(done.stdout) if ok else done.stderr

    first = audit("candidates")
    assert first["total"] == {"group": 2, "cross": 1, "same": 18, "stale": 5}, first
    assert first["to_read"] == first["total"] and first["carried"] == {k: 0 for k in ac.KINDS}, first
    assert [(b["kind"], b["items"]) for b in first["batches"]] == \
        [("group", 2), ("cross", 1), ("same", 18), ("stale", 5)], first["batches"]
    run = Path(first["run"])
    cands = json.loads((run / "candidates.json").read_text(encoding="utf-8"))
    assert sorted(sorted(m["title"] for m in g["members"]) for g in cands["group"]) == \
        [[f"Gotcha — A{i}" for i in range(4)], [f"Gotcha — B{i}" for i in range(4)]], cands["group"]
    assert cands["group"][0]["nearest_godnotes"][0]["links_to_group"] == 0
    # Sides in key order: "a" is the title that sorts first, so a saved `old: "b"` names one note forever.
    assert (cands["cross"][0]["a"]["title"], cands["cross"][0]["b"]["title"]) == ("Gotcha — X", "Gotcha — Y")
    assert all(p["a"]["title"] < p["b"]["title"] for p in cands["same"]), cands["same"]
    assert cands["cross"][0]["key"] == ac.finding_key("cross", ["Gotcha — X", "Gotcha — Y"])
    assert {p["a"]["project"] for p in cands["same"]} == {"p1", "p2", "p4"}
    assert sorted((s["how"], s["old"]["title"], (s["new"] or {}).get("title")) for s in cands["stale"]) == [
        ("corrects", "Fact — P", "Decision — Pn"), ("supersedes", "Fact — Al", "Decision — Q"),
        ("заменяет", "Fact — S2", "Decision — R"), ("заменяет", "Fact — S2", "Decision — R2"),
        ("поправка", "Fact — S", None)], cands["stale"]
    prompt = Path(first["batches"][0]["prompt"]).read_text(encoding="utf-8")
    assert str(run / "batches" / "group0.in.json") in prompt and first["batches"][0]["answer"] in prompt

    SHAPE = {"group": {"keep": False}, "cross": {"keep": False}, "same": {"verdict": "мимо"}, "stale": {"confirmed": False}}

    def answer(batch, keys=None, shape=None):
        items = json.loads(Path(batch["answer"].replace(".ann.json", ".in.json")).read_text(encoding="utf-8"))
        Path(batch["answer"]).write_text(json.dumps(
            [{"key": k, **(shape or SHAPE[batch["kind"]])} for k in (keys or [c["key"] for c in items])]), encoding="utf-8")

    # A reader that answers about something it was not asked fails the whole merge, loudly.
    cross_batch, same_batch = first["batches"][1], first["batches"][2]
    answer(cross_batch, ["l-" + "0" * 16])
    error = audit("merge", ok=False)
    assert "cross0.ann.json" in error and "l-0000000000000000" in error, error
    assert not ac.VERDICTS.exists()
    # So does an answer of the wrong shape: sorting and the page would read garbage from it.
    answer(cross_batch, shape={"keep": "yes"})
    answer(same_batch, shape={"verdict": "похоже"})
    error = audit("merge", ok=False)
    assert "cross0.ann.json: answers of the wrong shape" in error and "keep must be true or false, got 'yes'" in error, error
    assert "verdict must be one of устарело, спор, дубль, уточняет, мимо, got 'похоже'" in error, error
    assert not ac.VERDICTS.exists()
    # Unmerged answers block a new candidates run instead of being wiped with the batches.
    answer(cross_batch)
    assert "cross0.ann.json" in audit("candidates", ok=False)

    for batch in first["batches"]:
        answer(batch)
    merged = audit("merge")
    assert merged == {"merged": 26, "not_answered": [], "verdicts": 26}, merged
    verdicts = ac.load_verdicts()
    x, y = cands["cross"][0]["a"], cands["cross"][0]["b"]
    assert verdicts[cands["cross"][0]["key"]] == {"key": cands["cross"][0]["key"], "keep": False, "kind": "cross",
                                                  "run": run.name, "sha": {x["path"]: x["sha256"], y["path"]: y["sha256"]}}
    assert x["sha256"] == hashlib.sha256((vault / x["path"]).read_bytes()).hexdigest()

    second = audit("candidates")
    assert second["to_read"] == {k: 0 for k in ac.KINDS} and second["carried"] == first["total"], second
    assert second["batches"] == [] and list((run / "batches").iterdir()) == [], second

    # A note edited since its verdict is read again; a verdict the user decided is not, edited or not.
    decided_key = next(c["key"] for c in cands["stale"] if c["how"] == "поправка")
    ac.write_json(ac.VERDICTS, {**verdicts, decided_key: {**verdicts[decided_key], "decided": {"v": "no", "c": "", "run": "x"}}})
    write("Gotcha — X", "atom", "p1", "икс, переписан")
    write("Fact — S", "atom", "p5", "не годится\n\n## Поправка 2026-01-02\n\nгодится, переписано")
    embed()
    third = audit("candidates")
    assert third["to_read"] == {"group": 0, "cross": 1, "same": 0, "stale": 0}, third
    batch = json.loads((run / "batches" / "cross0.in.json").read_text(encoding="utf-8"))
    assert [c["key"] for c in batch] == [cands["cross"][0]["key"]], batch

    # The venv's python is a symlink to a bare interpreter; started as that bare binary the script
    # still finds its way into the venv, which only sys.prefix can tell apart.
    (Path(os.environ["DONT_FORGET_HOME"]) / "venv").symlink_to(VENV.parent.parent)
    bare = subprocess.run([os.path.realpath(VENV), str(SCRIPT), "page", "--db", str(db), "--vault", str(vault)],
                          env=os.environ, capture_output=True, text=True)
    assert bare.returncode == 0 and json.loads(bare.stdout)["notes"] == len(NOTES) - 1, bare.stderr

# A vault with one note has nothing to compare: a plain refusal, not numpy's NaN or an empty stack.
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    vault, db = root / "vault", root / "index.db"
    vault.mkdir()
    NOTES = {"Gotcha — Alone": ("atom", "p1", axis(0), "одна")}
    write("Gotcha — Alone", "atom", "p1", "одна")
    embed()
    error = audit("candidates", ok=False)
    assert "1 live note(s) with a vector, the audit needs at least two" in error, error

print("selftest_audit_semantic: ok")
