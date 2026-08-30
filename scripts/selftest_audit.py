#!/usr/bin/env python3
"""Dependency-free self-check for audit.py."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from audit import audit  # noqa: E402
from index import build  # noqa: E402

SCRIPT = Path(__file__).with_name("audit.py")

with tempfile.TemporaryDirectory(prefix="dont-forget-audit-") as tmp:
    home = Path(tmp)
    vault = home / "vault"
    vault.mkdir()
    (vault / "Atom — bridge.md").write_text(
        "---\ntype: atom\nkind: gotcha\ndate: 2026-08-01\n"
        "dies-when: the bridge DNS is repointed\n---\n\n# Atom — the bridge answers on 10.0.0.1\n"
        "\nThe deploy script hardcodes it.\n\n## Links\n\n- [[Bitrix24]]\n- [[bitrix 24]]\n- [[Битрикс24]]\n",
        encoding="utf-8")
    (vault / "Atom — deploy.md").write_text(
        "---\ntype: atom\nkind: gotcha\ndate: 2026-08-02\ndied: 2026-08-30\n---\n"
        "\n# Atom — deploying without migrations kills prod\n\nIt did, twice.\n"
        "\n## Links\n\n- [[Bitrix24]]\n", encoding="utf-8")
    (vault / "Atom — quiet.md").write_text(
        "---\ntype: atom\ndate: 2026-08-03\n---\n\n# Atom — nothing expires here\n\nA standing rule.\n",
        encoding="utf-8")
    db = home / "index.db"
    build(vault, db)

    log = home / "queries.jsonl"
    log.write_text("\n".join([
        # Old-format lines carry bare paths and cannot say how a note arrived.
        json.dumps({"query": "old", "top": ["Atom — bridge.md", "Atom — deploy.md"]}),
        json.dumps({"query": "deploy prod", "top": [
            {"path": "Atom — bridge.md", "found_by": "text"},
            {"path": "Atom — deploy.md", "found_by": "text"}]}),
        json.dumps({"query": "bridge deploy", "top": [
            {"path": "Atom — bridge.md", "found_by": "text"},
            {"path": "Atom — deploy.md", "found_by": "text"}]}),
        # A pair the graph walk carried is not a pair of ideas: it must not count.
        json.dumps({"query": "quiet", "top": [
            {"path": "Atom — quiet.md", "found_by": "text"},
            {"path": "Atom — bridge.md", "found_by": "link"}]}),
    ]) + "\n", encoding="utf-8")

    report = audit(db, log)
    assert report["notes"] == 3, report

    # Only the note that carries a death condition is asked about — the one already marked
    # keeps its mark, and a note without the field is never raised at all.
    dying = {note["path"]: note for note in report["dying"]}
    assert set(dying) == {"Atom — bridge.md"}, dying
    assert dying["Atom — bridge.md"]["dies_when"] == "the bridge DNS is repointed"

    # Both notes matched by text in two searches, so they are one candidate pair. The
    # link-carried pair is not, and the old-format line is not usable evidence either way.
    pairs = report["molecule_candidates"]
    assert len(pairs) == 1, pairs
    assert pairs[0]["notes"] == ["Atom — bridge.md", "Atom — deploy.md"], pairs
    assert pairs[0]["together"] == 2, pairs
    assert report["searches_logged"] == 4 and report["searches_usable"] == 3, report

    # Spellings that differ by case or separators are one demand, not three small ones.
    demand = {row["spellings"][0]: row for row in report["link_demand"]}
    assert len(demand) == 1, report["link_demand"]
    bitrix = report["link_demand"][0]
    assert bitrix["demand"] == 3, bitrix
    assert set(bitrix["spellings"]) == {"Bitrix24", "bitrix 24"}, bitrix
    # ...and the Cyrillic spelling of the same topic is NOT folded in. Pairing alphabets
    # is a guess, and this documents that the person is the one who spots that pair.
    assert "Битрикс24" not in bitrix["spellings"], bitrix

    # It is a reporter: it must never touch the vault.
    before = {path: path.read_bytes() for path in vault.iterdir()}
    subprocess.run([sys.executable, str(SCRIPT), "--db", str(db), "--log", str(log)],
                   capture_output=True, text=True, check=True)
    assert {path: path.read_bytes() for path in vault.iterdir()} == before

    empty = audit(home / "missing.db", log)
    assert "error" in empty, empty

print("ok")
