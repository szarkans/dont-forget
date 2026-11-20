#!/usr/bin/env python3
"""Installer: ownership, foreign config preservation, receipts, rollback and removal."""

import _selftest_env  # noqa: F401

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("install-hosts.py")
ROOT = Path(__file__).resolve().parent.parent


def run(home: Path, *args, path=None, expect=0):
    env = {key: value for key, value in os.environ.items() if key != "DONT_FORGET_HOME"}
    if path is not None:
        env["PATH"] = path
    done = subprocess.run([sys.executable, str(SCRIPT), "--home", str(home), *args],
                          capture_output=True, text=True, env=env)
    assert done.returncode == expect, (done.returncode, done.stderr, done.stdout)
    return done


def load(done):
    return json.loads(done.stdout)


with tempfile.TemporaryDirectory(prefix="dont-forget-install-") as directory:
    home = Path(directory)
    state = home / ".dont-forget"
    hooks = home / ".codex/hooks.json"
    hooks.parent.mkdir()
    foreign_start = {"matcher": "startup", "hooks": [{"type": "command", "command": "foreign-start"}]}
    original = {"hooks": {"SessionStart": [foreign_start],
                          "Stop": [{"hooks": [{"type": "command", "command": "keep-this"}]}]}}
    hooks.write_text(json.dumps(original))

    first = load(run(home, "--host", "codex"))
    assert first["backup"] and first["changed"], first
    assert first["trust_review_required"] == ["codex"], first
    receipt = json.loads((state / "hosts.json").read_text())
    assert {entry["host"] for entry in receipt["installations"]} == {"shared", "codex"}, receipt
    updated = json.loads(hooks.read_text())
    assert updated["hooks"]["Stop"] == original["hooks"]["Stop"]
    assert foreign_start in updated["hooks"]["SessionStart"]
    ours = [group for group in updated["hooks"]["SessionStart"]
            if any("hot-scan.py" in h.get("command", "") for h in group["hooks"])]
    assert len(ours) == 1 and ours[0]["matcher"] == "startup|resume|clear|compact|fork", ours
    handler = ours[0]["hooks"][0]
    assert handler["timeout"] == 15 and handler["additionalContextLimit"] == 0, handler
    assert handler["command"].startswith("python3 ") and "--harness codex" in handler["command"]
    assert any(group["matcher"] == "manual|auto" for group in updated["hooks"]["PreCompact"])
    shared = home / ".agents/skills/dont-forget"
    assert (shared / "scripts/transcript.py").is_file()
    assert (shared / "scripts").is_symlink()
    for path in (shared / "references").glob("*.md"):
        text = path.read_text()
        assert "${CLAUDE_PLUGIN_ROOT}" not in text, path
        assert "<skill-root>" not in text, path

    # One command skill per catalog entry, next to the shared skill.
    catalog = json.loads((ROOT / "integrations/commands.json").read_text())
    for entry in catalog:
        skill = home / ".agents/skills" / entry["command"] / "SKILL.md"
        text = skill.read_text()
        assert f'name: {entry["command"]}' in text.split("---")[1], skill
        assert "${CLAUDE_PLUGIN_ROOT}" not in text, skill
        assert "<skill-root>" not in text, skill
    assert "disable-model-invocation" in \
        (home / ".agents/skills/dont-forget-him-please/SKILL.md").read_text()

    # A repeat is a real no-op: no new backup, no byte changes.
    before = hooks.read_bytes()
    second = load(run(home, "--host", "codex"))
    assert second["changed"] == [] and second["backup"] is None, second
    assert hooks.read_bytes() == before

    # A foreign group added after ours must not push our exact group to the end: that
    # would rewrite the file on every run and invalidate Codex's trust hash.
    updated = json.loads(hooks.read_text())
    later_foreign = {"hooks": [{"type": "command", "command": "later-foreign"}]}
    updated["hooks"]["SessionStart"].append(later_foreign)
    hooks.write_text(json.dumps(updated))
    before_foreign = hooks.read_bytes()
    again = load(run(home, "--host", "codex"))
    assert again["changed"] == [] and again["backup"] is None, again
    assert hooks.read_bytes() == before_foreign
    groups = json.loads(hooks.read_text())["hooks"]["SessionStart"]
    ours_index = next(i for i, group in enumerate(groups)
                      if any("hot-scan.py" in h.get("command", "") for h in group["hooks"]))
    assert groups.index(later_foreign) > ours_index, groups

    # Updating an owned shared file is allowed; the old bytes are in the transaction.
    (shared / "SKILL.md").write_text("user edit")
    third = load(run(home, "--host", "codex"))
    assert third["backup"], third
    assert "Don't Forget" in (shared / "SKILL.md").read_text()
    assert json.loads((state / "host-backups" / third["backup"] / "manifest.json").read_text())["status"] == "complete"
    assert hooks.read_bytes() == before_foreign
    updated = json.loads(hooks.read_text())
    updated["hooks"]["SessionStart"].append({"hooks": [{"type": "command", "command": "later-foreign"}]})
    hooks.write_text(json.dumps(updated))
    removed = load(run(home, "--host", "codex", "--uninstall"))
    assert removed["backup"], removed
    after = json.loads(hooks.read_text())
    assert all("hot-scan.py" not in h.get("command", "")
               for group in after["hooks"].get("SessionStart", []) for h in group["hooks"])
    assert any(h.get("command") == "later-foreign"
               for group in after["hooks"]["SessionStart"] for h in group["hooks"])
    assert not (state / "hosts.json").read_text().count('"codex"')
    assert (shared / "SKILL.md").is_file()

    # Rollback restores that uninstall exactly: our handlers and receipt entry come back
    # while the later foreign handler is preserved.
    rolled = load(run(home, "--rollback", removed["backup"]))
    assert rolled["rolled_back"] == removed["backup"], rolled
    restored = json.loads(hooks.read_text())
    assert any("hot-scan.py" in h.get("command", "")
               for group in restored["hooks"]["SessionStart"] for h in group["hooks"])
    assert any(h.get("command") == "later-foreign"
               for group in restored["hooks"]["SessionStart"] for h in group["hooks"])
    receipt = json.loads((state / "hosts.json").read_text())
    assert {entry["host"] for entry in receipt["installations"]} == {"shared", "codex"}

    # Conflict: a file changed after its install cannot be rolled back over.
    (shared / "SKILL.md").write_text("edited after install")
    failed = run(home, "--rollback", third["backup"], expect=1)
    assert "refused" in failed.stderr and (shared / "SKILL.md").read_text() == "edited after install"
    (shared / "SKILL.md").write_text("user edit")  # match the installed record? no: reinstall first
    load(run(home, "--host", "codex"))

    # A changed owned file blocks uninstall of that host instead of deleting user content.
    current = (shared / "SKILL.md").read_text()
    (shared / "SKILL.md").write_text("changed by user")
    failed = run(home, "--host", "shared", "--uninstall", expect=1)
    assert "does not match" in failed.stderr
    assert (shared / "SKILL.md").read_text() == "changed by user"
    (shared / "SKILL.md").write_text(current)

    # An existing OMP extension that already injects the digest is a conflict, not two
    # copies of memory in the same context.
    omp_home = home / "ompconflict"
    (omp_home / ".omp/agent/extensions").mkdir(parents=True)
    (omp_home / ".omp/agent/extensions/startup-memory.ts").write_text(
        'spawn("python3", ["/x/hot-scan.py", "--hook"])\n')
    conflict = run(omp_home, "--host", "omp", expect=1)
    assert "already injects a dont-forget digest" in conflict.stderr, conflict.stderr
    assert not (omp_home / ".omp/agent/extensions/dont-forget.js").exists()

    # --host all installs shared and explicitly skips absent CLIs.
    all_home = home / "all"
    result = load(run(all_home, "--host", "all", path="/nonexistent"))
    assert result["hosts"] == ["shared"], result
    assert set(result["skipped"]) == {"codex", "opencode", "omp", "devin", "agy"}, result
    assert (all_home / ".dont-forget/hosts.json").is_file()

    # OpenCode also gets a file command per catalog entry.
    oc_home = home / "oc"
    oc = load(run(oc_home, "--host", "opencode", "--opencode-api", "2"))
    assert oc["backup"], oc
    command_file = oc_home / ".config/opencode/commands/dont-forget-about.md"
    text = command_file.read_text()
    assert "User request: $ARGUMENTS" in text and "Search the vault" in text, text
    removed_oc = load(run(oc_home, "--host", "opencode", "--uninstall"))
    assert removed_oc["backup"], removed_oc
    assert not command_file.exists()
    assert not (oc_home / ".config/opencode/plugins/dont-forget.js").exists()

    # A foreign file or directory under a command name is a conflict, never a
    # silent overwrite.
    foreign_oc = home / "oc-foreign"
    foreign_commands = foreign_oc / ".config/opencode/commands"
    foreign_commands.mkdir(parents=True)
    (foreign_commands / "dont-forget-about.md").write_text("mine")
    conflict = run(foreign_oc, "--host", "opencode", "--opencode-api", "2", expect=1)
    assert "not a dont-forget file" in conflict.stderr, conflict.stderr
    assert (foreign_commands / "dont-forget-about.md").read_text() == "mine"
    foreign_skill = home / "foreign-skill"
    (foreign_skill / ".agents/skills/dont-forget-about").mkdir(parents=True)
    (foreign_skill / ".agents/skills/dont-forget-about/notes.md").write_text("foreign")
    conflict = run(foreign_skill, "--host", "shared", expect=1)
    assert "not a dont-forget command skill" in conflict.stderr, conflict.stderr

    # all-uninstall reads the receipt, not PATH, and removes the shared entry too.
    removed_all = load(run(all_home, "--host", "all", "--uninstall", path="/nonexistent"))
    assert removed_all["backup"], removed_all
    shared_after = all_home / ".agents/skills/dont-forget"
    assert not (shared_after / "SKILL.md").exists() and not (shared_after / "scripts").exists()
    assert not (all_home / ".agents/skills/dont-forget-about/SKILL.md").exists()
    assert json.loads((all_home / ".dont-forget/hosts.json").read_text())["installations"] == []

    # An alternate home cannot borrow the caller's state or agent directory.
    outside = home / "outside"
    outside.mkdir()
    env = {key: value for key, value in os.environ.items() if key != "DONT_FORGET_HOME"}
    env["DONT_FORGET_HOME"] = str(outside)
    failed = subprocess.run([sys.executable, str(SCRIPT), "--home", str(all_home), "--host", "shared"],
                            capture_output=True, text=True, env=env)
    assert failed.returncode != 0 and "must be inside" in failed.stderr, failed.stderr
    inside_home = home / "inside-home"
    inside = inside_home / "state"
    env["DONT_FORGET_HOME"] = str(inside)
    ok = subprocess.run([sys.executable, str(SCRIPT), "--home", str(inside_home), "--host", "shared"],
                        capture_output=True, text=True, env=env)
    assert ok.returncode == 0, ok.stderr
    assert (inside / "hosts.json").is_file()
    failed = subprocess.run([sys.executable, str(SCRIPT), "--home", str(inside_home), "--host", "omp",
                             "--omp-agent-dir", str(outside)], capture_output=True, text=True, env=env)
    assert failed.returncode != 0 and "--omp-agent-dir must be inside" in failed.stderr, failed.stderr

    # Dry run writes nothing at all, not even the state directory.
    dry_home = home / "dry"
    plan = load(run(dry_home, "--host", "codex", "--dry-run"))
    assert plan["dry_run"] and plan["changed"], plan
    assert not (dry_home / ".dont-forget").exists()
    assert not (dry_home / ".codex/hooks.json").exists()

# A legacy installation is adopted only when every owned byte is the known old shape.
legacy = home / "legacy"
old_shared = legacy / ".agents/skills/dont-forget"
old_shared.mkdir(parents=True)
(old_shared / "SKILL.md").write_text("---\nname: dont-forget\ndescription: old\n---\n")
(old_shared / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
(old_shared / "unknown-user-file.txt").write_text("keep me")
old_hooks = legacy / ".codex/hooks.json"
old_hooks.parent.mkdir(parents=True)
old_command = "python3 " + shlex.quote(str(ROOT / "scripts/hot-scan.py")) + " --hook"
old_hooks.write_text(json.dumps({"hooks": {"SessionStart": [
    {"matcher": "startup|resume|clear|compact",
     "hooks": [{"type": "command", "command": old_command, "timeout": 15}]}]}}))
refused = run(legacy, "--host", "codex", expect=1)
assert "not a dont-forget installation" in refused.stderr, refused.stderr
adopted = load(run(legacy, "--host", "codex", "--adopt-existing"))
assert adopted["backup"] and "codex" in adopted["trust_review_required"], adopted
adopted_hooks = json.loads(old_hooks.read_text())
commands = [h["command"] for group in adopted_hooks["hooks"]["SessionStart"] for h in group["hooks"]]
assert old_command not in commands and any("--harness codex" in c for c in commands), commands
receipt = json.loads((legacy / ".dont-forget/hosts.json").read_text())
assert {entry["host"] for entry in receipt["installations"]} == {"shared", "codex"}, receipt
# Uninstall codex leaves the shared adoption; explicit shared uninstall removes only
# owned files and keeps the user's neighbour.
run(legacy, "--host", "codex", "--uninstall")
assert (old_shared / "SKILL.md").is_file() and (old_shared / "scripts").is_symlink()
run(legacy, "--host", "shared", "--uninstall")
assert not (old_shared / "SKILL.md").exists() and not (old_shared / "scripts").exists()
assert (old_shared / "unknown-user-file.txt").read_text() == "keep me"

# A checkout path with spaces, an apostrophe and a dollar sign stays one argv item.
spec = importlib.util.spec_from_file_location("install_hosts", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.ROOT = Path("/tmp/a b'c$d/dont-forget")
group = module._desired_codex_group("SessionStart", Path("/tmp/a b'c$d/home"))
argv = shlex.split(group["hooks"][0]["command"])
assert argv[0] == "python3" and \
    argv[1] == "/tmp/a b'c$d/home/.agents/skills/dont-forget/scripts/hot-scan.py", argv
assert "--harness" in argv and "codex" in argv and "--budget" in argv, argv

print("ok")
