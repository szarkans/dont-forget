#!/usr/bin/env python3
"""Dependency-free self-check for audit.py."""

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

    report = audit(db)
    assert report["notes"] == 3, report

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
    subprocess.run([sys.executable, str(SCRIPT), "--db", str(db)],
                   capture_output=True, text=True, check=True)
    assert {path: path.read_bytes() for path in vault.iterdir()} == before

    empty = audit(home / "missing.db")
    assert "error" in empty, empty

print("ok")
