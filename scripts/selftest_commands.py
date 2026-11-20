#!/usr/bin/env python3
"""The command catalog is the one list of `dont-forget-<name>` commands, and the
artifacts generated from it are well-formed."""

import _selftest_env  # noqa: F401

import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "install_hosts", ROOT / "scripts/install-hosts.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

catalog = json.loads((ROOT / "integrations/commands.json").read_text(encoding="utf-8"))
commands = [entry["command"] for entry in catalog]
assert len(commands) == len(set(commands)), commands
assert all(name.startswith("dont-forget-") for name in commands), commands
references = [entry["reference"] for entry in catalog]
assert len(references) == len(set(references)), references
for entry in catalog:
    assert (ROOT / "skills" / entry["skill"] / "SKILL.md").is_file(), entry

with tempfile.TemporaryDirectory(prefix="dont-forget-commands-") as directory:
    home = Path(directory)
    shared = module.shared_dir(home)
    files = module.command_skill_files(home)
    assert {path.parent.name for path in files} == set(commands)
    for path, data in files.items():
        text = data.decode("utf-8")
        assert text.startswith("---\n"), path
        frontmatter = text.split("---")[1]
        assert f'name: {path.parent.name}' in frontmatter, path
        assert "${CLAUDE_PLUGIN_ROOT}" not in text, path
        assert "<skill-root>" not in text, path
    about = next(entry for entry in catalog if entry["skill"] == "about")
    assert str(shared / "scripts") in \
        files[shared.parent / about["command"] / "SKILL.md"].decode()
    him_please = next(entry for entry in catalog if entry["skill"] == "him-please")
    assert "disable-model-invocation" in \
        files[shared.parent / him_please["command"] / "SKILL.md"].decode()

    for entry in catalog:
        command = module.opencode_command_text(entry, home)
        assert command.startswith('---\ndescription:'), entry
        assert "User request: $ARGUMENTS" in command, entry
        assert "${CLAUDE_PLUGIN_ROOT}" not in command, entry
        assert "<skill-root>" not in command, entry
    assert "Search the vault" in module.opencode_command_text(catalog[0], home)

print("ok")
