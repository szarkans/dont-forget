#!/usr/bin/env python3
"""Dependency-free self-check for vault discovery, config reading and setup.py."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("setup.py")
sys.path.insert(0, str(Path(__file__).parent))
import common  # noqa: E402

# Windows paths from a registry written on the Windows side of WSL.
assert common._from_windows(r"C:\Users\a\Vault") == "/mnt/c/Users/a/Vault"
assert common._from_windows("D:/notes") == "/mnt/d/notes"
assert common._from_windows("/home/a/vault") == "/home/a/vault"
assert common._from_windows("C:") == "C:"

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)

    # --- known_vaults: reads Obsidian's registry, open beats recent, dead paths drop ---
    live, recent, dead = root / "live", root / "recent", root / "gone"
    for path in (live, recent):
        (path / ".obsidian").mkdir(parents=True)
    registry = root / "obsidian.json"
    registry.write_text(json.dumps({"vaults": {
        "a": {"path": str(recent), "ts": 9_000_000, "open": False},
        "b": {"path": str(live), "ts": 1, "open": True},
        "c": {"path": str(dead), "ts": 9_999_999, "open": True},
        "d": {"path": None},
        "e": "not a dict",
    }}), encoding="utf-8")
    original = common._registries
    common._registries = lambda: [registry, root / "absent.json"]
    try:
        assert common.known_vaults() == [live, recent], common.known_vaults()
        registry.write_text("{ not json", encoding="utf-8")
        assert common.known_vaults() == []
    finally:
        common._registries = original

    # --- scan_for_vaults: bounded walk, and a vault never nests inside another ---
    deep = root / "one" / "two" / "three" / "notes"
    (deep / ".obsidian").mkdir(parents=True)
    (deep / "inner" / ".obsidian").mkdir(parents=True)
    too_deep = root / "a" / "b" / "c" / "d" / "e"
    (too_deep / ".obsidian").mkdir(parents=True)
    found = common.scan_for_vaults(root, depth=4)
    assert deep in found and (deep / "inner") not in found, found
    assert too_deep not in found, found

    # --- vault_from_config: every failure names the fix instead of raising a traceback ---
    config = root / "config.json"
    for broken, expected in (
        (None, "not set up"),
        ("{ not json", "not valid JSON"),
        ('{"nothing": 1}', 'no "vault" key'),
        (json.dumps({"vault": str(root / "missing")}), "is gone"),
    ):
        if broken is None:
            config.unlink(missing_ok=True)
        else:
            config.write_text(broken, encoding="utf-8")
        try:
            common.vault_from_config(config)
            raise AssertionError(f"expected failure for {broken!r}")
        except common.NotConfigured as error:
            assert expected in str(error), (broken, str(error))
            assert "/dont-forget:setup" in str(error), str(error)
    # NotConfigured stays a ValueError so callers that already catch ValueError still do.
    assert issubclass(common.NotConfigured, ValueError)

    config.write_text(json.dumps({"vault": str(live)}), encoding="utf-8")
    assert common.vault_from_config(config) == live


def run(home: Path, *args, expect_ok=True):
    env = {**os.environ, "DONT_FORGET_HOME": str(home)}
    done = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)
    assert (done.returncode == 0) == expect_ok, done.stderr
    return json.loads(done.stdout) if done.returncode == 0 else done.stderr


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    home, vault = root / "home", root / "vault"
    vault.mkdir()
    (vault / "Note.md").write_text("---\ntype: atom\n---\n# Note\n\nbody text\n", encoding="utf-8")

    detected = run(home, "--detect")
    assert detected["configured"] is None and "not set up" in detected["problem"]

    # --set writes the config and builds the index in the same step.
    applied = run(home, "--set", str(vault))
    assert applied["configured"] == str(vault)
    assert applied["index"]["notes"] == 1 and applied["index"]["chunks"] >= 1, applied
    assert (home / "index.db").is_file()

    # An unrelated key a user added by hand survives being pointed at a new vault.
    config = home / "config.json"
    config.write_text(json.dumps({"vault": str(vault), "mine": "keep me"}), encoding="utf-8")
    second = vault.parent / "second"
    second.mkdir()
    run(home, "--set", str(second))
    saved = json.loads(config.read_text(encoding="utf-8"))
    assert saved == {"vault": str(second), "mine": "keep me"}, saved

    assert "not a directory" in run(home, "--set", str(root / "nope"), expect_ok=False)
    assert "--detect or --set" in run(home, expect_ok=False)

print("ok")
