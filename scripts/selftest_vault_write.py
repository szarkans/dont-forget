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

print("ok")
