#!/usr/bin/env python3
"""Dependency-free self-check for vault-write.py."""

import json
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("vault-write.py")


def invoke(vault: Path, filename: str, content: str, **extra: str) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"filename": filename, "content": content, **extra})
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--vault", str(vault)],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )


with tempfile.TemporaryDirectory(prefix="dont-forget-test-") as directory:
    vault = Path(directory)
    filename = "Atom — A useful claim.md"
    original = "---\ntype: atom\n---\nA useful claim.\n"

    created = invoke(vault, filename, original)
    assert created.returncode == 0, created.stderr
    assert json.loads(created.stdout) == {"status": "created"}
    assert (vault / filename).read_text(encoding="utf-8") == original

    same = invoke(vault, filename, original)
    assert same.returncode == 0, same.stderr
    assert json.loads(same.stdout) == {"status": "exists-same"}

    conflict = invoke(vault, filename, "different")
    assert conflict.returncode == 0
    assert json.loads(conflict.stdout) == {"status": "conflict"}
    assert "conflict" in conflict.stderr
    assert (vault / filename).read_text(encoding="utf-8") == original

    replacement = "---\ntype: atom\n---\nA better claim.\n"
    original_sha = hashlib.sha256(original.encode()).hexdigest()
    replaced = invoke(vault, filename, replacement, action="replace", expected_sha=original_sha)
    assert replaced.returncode == 0, replaced.stderr
    assert json.loads(replaced.stdout) == {"status": "replaced"}
    assert (vault / filename).read_text(encoding="utf-8") == replacement

    stale = invoke(vault, filename, "must not land", action="replace", expected_sha=original_sha)
    assert stale.returncode == 0
    assert json.loads(stale.stdout) == {"status": "conflict"}
    assert "conflict" in stale.stderr
    assert (vault / filename).read_text(encoding="utf-8") == replacement

    invalid_sha = invoke(vault, filename, "must not land", action="replace", expected_sha="nope")
    assert invalid_sha.returncode != 0
    assert (vault / filename).read_text(encoding="utf-8") == replacement

    before = set(vault.iterdir())
    refused = invoke(vault, "Atom — bad#name.md", "must not land")
    assert refused.returncode != 0
    assert set(vault.iterdir()) == before

    dotted = invoke(vault, "Atom — v1.2 broke.md", "dotted name content")
    assert dotted.returncode == 0, dotted.stderr
    assert (vault / "Atom — v1.2 broke.md").read_text(encoding="utf-8") == "dotted name content"

    before = set(vault.iterdir())
    unclosed = invoke(vault, "Atom — unclosed.md", "---\ntype: atom\nbody with no closing fence\n")
    assert unclosed.returncode != 0
    assert "frontmatter" in unclosed.stderr
    assert set(vault.iterdir()) == before

    closed = invoke(vault, "Atom — closed.md", "---\ntype: atom\n---\nbody\n")
    assert closed.returncode == 0, closed.stderr

    # A secret warns and never blocks: the note lands, and the warning rides along in the
    # returned status so it can be counted rather than scrolled past.
    leaked = invoke(vault, "Atom — leaked.md", "---\ntype: atom\n---\npassword: hunter2-battery-x\n")
    assert leaked.returncode == 0, leaked.stderr
    body = json.loads(leaked.stdout)
    assert body["status"] == "created", body
    assert "possible secret" in body["warning"], body
    assert "possible secret" in leaked.stderr
    assert (vault / "Atom — leaked.md").exists()

    # Prose *about* a leak is not a leak. This is the shape the vault actually holds, and
    # a scanner that fires on it teaches the reader to ignore every warning it prints.
    innocent = invoke(vault, "Atom — incident.md",
                      "---\ntype: atom\n---\nRCON-пароль засветился в открытом чате, ключ ротирован.\n")
    assert innocent.returncode == 0, innocent.stderr
    assert "warning" not in json.loads(innocent.stdout), innocent.stdout

with tempfile.TemporaryDirectory(prefix="dont-forget-test-") as directory:
    # The scan is on by default and can be turned off without answering questions.
    home = Path(directory)
    vault = home / "vault"
    vault.mkdir()
    (home / "config.json").write_text(json.dumps({"vault": str(vault), "scan_secrets": False}))
    quiet = subprocess.run(
        [sys.executable, str(SCRIPT), "--vault", str(vault)],
        input=json.dumps({"filename": "Atom — quiet.md",
                          "content": "---\ntype: atom\n---\npassword: hunter2-battery-x\n"}),
        text=True, capture_output=True, check=False,
        env={"PATH": "/usr/bin:/bin", "DONT_FORGET_HOME": str(home)},
    )
    assert quiet.returncode == 0, quiet.stderr
    assert json.loads(quiet.stdout) == {"status": "created"}, quiet.stdout

print("ok")
