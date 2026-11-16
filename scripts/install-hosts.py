#!/usr/bin/env python3
"""Install shared instructions and thin host adapters, preserving existing config.

The shared Python core stays where it is: every host gets a skill that points at this
checkout and a small adapter that translates its native events. Nothing here edits a
host's models, providers, permissions or disabled extensions, and nothing grants hook
trust — a changed Codex hook has to be reviewed in the host's own `/hooks` screen.

Every real mutation is a transaction: before bytes are saved under
`DONT_FORGET_HOME/host-backups/<id>/`, each replacement re-checks the value it saw, and
an interrupted run reports its backup id instead of pretending to be complete.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from session_sources import omp_agent_dir

ROOT = Path(__file__).resolve().parent.parent
REFERENCES = {"about": "about", "this": "that", "session": "session", "review": "audit",
              "setup": "setup", "checkup": "health", "him-please": "him-please"}
DESIRED_HOOK = "python3 {path} --hook --harness codex --budget 12288"
LEGACY_HOOK = "python3 {path} --hook"
BACKUP_ID = re.compile(r"^[0-9]{8}T[0-9]{12}$")
HOOK_EVENTS = {"SessionStart": "startup|resume|clear|compact|fork",
               "PreCompact": "manual|auto"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".install-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_symlink(path: Path, target: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".install-link-{os.getpid()}")
    temporary.unlink(missing_ok=True)
    os.symlink(target, temporary, target_is_directory=True)
    os.replace(temporary, path)


def state_of(path: Path) -> dict:
    try:
        if path.is_symlink():
            return {"kind": "symlink", "target": os.readlink(path)}
        if path.is_file():
            return {"kind": "file", "sha256": digest(path.read_bytes())}
        if path.exists():
            return {"kind": "other"}
        return {"kind": "absent"}
    except OSError:
        return {"kind": "other"}


def file_entry(path: Path, data: bytes) -> dict:
    return {"path": str(path), "kind": "file", "sha256": digest(data)}


def symlink_entry(path: Path, target: Path) -> dict:
    return {"path": str(path), "kind": "symlink", "target": str(target)}


def read_receipt(state: Path) -> dict:
    path = state / "hosts.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "installations": []}
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read installation receipt {path}: {error}") from error
    if not isinstance(data, dict) or data.get("version") != 1 \
            or not isinstance(data.get("installations"), list):
        raise ValueError(f"unsupported installation receipt: {path}")
    return data


def write_receipt(state: Path, receipt: dict) -> None:
    atomic_write(state / "hosts.json",
                 (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def receipt_entry(receipt: dict, host: str, target: str) -> dict | None:
    for entry in receipt.get("installations", []):
        if entry.get("host") == host and entry.get("target") == target:
            return entry
    return None


def set_receipt_entry(state: Path, host: str, target: str, entry: dict | None) -> None:
    receipt = read_receipt(state)
    kept = [item for item in receipt["installations"]
            if not (item.get("host") == host and item.get("target") == target)]
    if entry is not None:
        kept.append(entry)
    kept.sort(key=lambda item: (item.get("host", ""), item.get("target", "")))
    receipt["installations"] = kept
    write_receipt(state, receipt)


# ---------------------------------------------------------------------------
# Operations and the transaction
# ---------------------------------------------------------------------------

def file_op(path: Path, data: bytes | None, before_data: bytes | None = None) -> dict:
    before = state_of(path)
    op = {"kind": "file", "path": str(path), "before": before,
          "after": {"kind": "absent"} if data is None else file_entry(path, data),
          "_data": data, "_before_data": before_data}
    return op


def link_op(path: Path, target: Path) -> dict:
    return {"kind": "file", "path": str(path), "before": state_of(path),
            "after": {"kind": "symlink", "target": str(target)},
            "_data": None, "_before_data": None}


def receipt_op(state: Path, host: str, target: str, after: dict | None) -> dict:
    before = receipt_entry(read_receipt(state), host, target)
    return {"kind": "receipt", "host": host, "target": target,
            "before_entry": before, "after_entry": after}


def _apply_file(op: dict) -> None:
    path = Path(op["path"])
    after = op["after"]
    if after["kind"] == "absent":
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    elif after["kind"] == "symlink":
        atomic_symlink(path, after["target"])
    else:
        atomic_write(path, op["_data"])


def _verify(op: dict) -> None:
    if op["kind"] == "receipt":
        current = receipt_entry(read_receipt(STATE), op["host"], op["target"])
        if canonical(current) != canonical(op["before_entry"]):
            raise ValueError(f"installation receipt changed while installing {op['host']} at {op['target']}")
        return
    if state_of(Path(op["path"])) != op["before"]:
        raise ValueError(f"{op['path']} changed before it could be written")


def _apply(op: dict) -> None:
    if op["kind"] == "receipt":
        set_receipt_entry(STATE, op["host"], op["target"], op["after_entry"])
    else:
        _apply_file(op)


def _state_matches(current: dict, expected: dict) -> bool:
    """Compare the observable part of a file state; `after` also carries its path."""
    fields = ("kind", "sha256", "target")
    return canonical({key: current.get(key) for key in fields if current.get(key) is not None}) \
        == canonical({key: expected.get(key) for key in fields if expected.get(key) is not None})


def _restore(op: dict, backup: Path | None) -> None:
    """Put one operation's before value back, refusing if the file moved under us."""
    if op["kind"] == "receipt":
        current = receipt_entry(read_receipt(STATE), op["host"], op["target"])
        if canonical(current) != canonical(op["after_entry"]):
            raise ValueError(f"installation receipt changed under {op['host']}: refusing to restore")
        set_receipt_entry(STATE, op["host"], op["target"], op["before_entry"])
        return
    path = Path(op["path"])
    before = op["before"]
    if not _state_matches(state_of(path), op["after"]):
        raise ValueError(f"{path} changed after install: refusing to restore")
    data = op.get("_before_data")
    saved = op.get("_saved") or before.get("saved")
    if before["kind"] == "file" and data is None and backup is not None and saved:
        data = (backup / saved).read_bytes()
    if before["kind"] == "absent":
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    elif before["kind"] == "symlink":
        atomic_symlink(path, before["target"])
    elif before["kind"] == "file":
        if data is None:
            raise ValueError(f"backup bytes for {path} are missing")
        atomic_write(path, data)


STATE: Path  # the resolved installation state, set once in main()


@contextmanager
def installation_lock(state: Path):
    state.mkdir(parents=True, exist_ok=True)
    lock_path = state / ".host-install.lock"
    with lock_path.open("a+b") as lock:
        try:
            import fcntl
        except ImportError:  # Windows runs unlocked.
            yield
            return
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _pending_backup(state: Path) -> str | None:
    root = state / "host-backups"
    if not root.is_dir():
        return None
    for child in sorted(root.iterdir()):
        manifest = child / "manifest.json"
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("status") in ("pending", "partial"):
            return child.name
    return None


class Transaction:
    def __init__(self, state: Path, ops: list[dict], dry: bool, source: str):
        self.state = state
        self.ops = ops
        self.dry = dry
        self.source = source
        self.backup_id = datetime.datetime.now().strftime("%Y%m%dT%H%M%S%f")
        self.backup = state / "host-backups" / self.backup_id

    def describe(self) -> list[dict]:
        described = []
        for op in self.ops:
            if op["kind"] == "receipt":
                described.append({"kind": "receipt", "host": op["host"], "target": op["target"],
                                  "before": bool(op["before_entry"]),
                                  "after": bool(op["after_entry"])})
            else:
                described.append({"kind": "file", "path": op["path"],
                                  "before": op["before"], "after": op["after"]})
        return described

    def _manifest(self, status: str, applied: int) -> dict:
        operations = []
        for op in self.ops:
            clean = {key: value for key, value in op.items() if not key.startswith("_")}
            if op.get("_saved"):
                clean = {**clean, "before": {**clean["before"], "saved": op["_saved"]}}
            operations.append(clean)
        return {"version": 1, "status": status, "applied_count": applied,
                "source": self.source, "operations": operations}

    def _save_manifest(self, status: str, applied: int) -> None:
        self.backup.mkdir(parents=True, exist_ok=True)
        atomic_write(self.backup / "manifest.json",
                     (json.dumps(self._manifest(status, applied), ensure_ascii=False,
                                 indent=2) + "\n").encode("utf-8"))

    def _save_before(self) -> None:
        (self.backup / "files").mkdir(parents=True, exist_ok=True)
        for number, op in enumerate(self.ops):
            if op["kind"] != "file" or op["before"]["kind"] != "file":
                continue
            data = op.get("_before_data")
            if data is None:
                data = Path(op["path"]).read_bytes()
            saved = f"files/{number}"
            atomic_write(self.backup / saved, data)
            op["_saved"] = saved

    def run(self) -> dict:
        if not self.ops:
            return {"changed": [], "backup": None, "trust_review_required": []}
        if self.dry:
            return {"dry_run": True, "changed": [item for item in self.describe()],
                    "backup": None, "trust_review_required": []}
        pending = _pending_backup(self.state)
        if pending:
            raise ValueError(f"an unfinished installation exists: {pending}. Run --rollback {pending}")
        self._save_before()
        self._save_manifest("pending", 0)
        applied = 0
        try:
            for op in self.ops:
                _verify(op)
                _apply(op)
                applied += 1
                self._save_manifest("pending", applied)
            self._save_manifest("complete", applied)
        except Exception as error:
            remaining = []
            for op in reversed(self.ops[:applied]):
                try:
                    _restore(op, self.backup)
                except Exception as inverse_error:
                    remaining.append({"operation": op.get("path") or op.get("host"),
                                      "error": str(inverse_error)})
                    break
            self._save_manifest("partial", applied)
            raise ValueError(
                f"installation failed: {error}; backup {self.backup_id}"
                + (f"; inverse stopped at: {canonical(remaining)}" if remaining else "")) from error
        return {"changed": [item for item in self.describe()],
                "backup": self.backup_id, "trust_review_required": []}


def rollback(state: Path, backup_id: str) -> dict:
    if not BACKUP_ID.match(backup_id):
        raise ValueError(f"not a backup id: {backup_id}")
    backup = state / "host-backups" / backup_id
    manifest_path = backup / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read backup {backup_id}: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError(f"unsupported backup manifest: {backup_id}")
    ops = manifest.get("operations") or []
    applied_count = manifest.get("applied_count", 0)
    applied = []
    conflicts = []
    for number, op in enumerate(ops):
        current = (receipt_entry(read_receipt(state), op["host"], op["target"])
                   if op["kind"] == "receipt" else state_of(Path(op["path"])))
        after = (op["after_entry"] if op["kind"] == "receipt"
                 else {key: value for key, value in op["after"].items()
                       if key in ("kind", "sha256", "target") and value is not None})
        done = canonical(current) == canonical(after)
        if not done and canonical(current) != canonical(op["before_entry"] if op["kind"] == "receipt" else op["before"]):
            conflicts.append(op.get("path") or f"{op.get('host')}:{op.get('target')}")
        if done or number < applied_count:
            applied.append(op)
    if conflicts:
        raise ValueError("rollback refused, nothing was restored; these changed since the install: "
                         + ", ".join(conflicts))
    for op in reversed(applied):
        _restore(op, backup)
    manifest["status"] = "rolled-back"
    atomic_write(manifest_path,
                 (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return {"rolled_back": backup_id, "operations": len(applied)}


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def shared_dir(home: Path) -> Path:
    return home / ".agents/skills/dont-forget"


def _replace_root(text: str) -> str:
    # The generated references run `python3 "<root>/scripts/x.py"`. Replace the whole
    # quoted string with a shell-quoted concrete path; a raw root pasted inside double
    # quotes would break on spaces and `$`.
    text = re.sub(r'"\$\{CLAUDE_PLUGIN_ROOT\}/(scripts/[^"]+)"',
                  lambda match: shlex.quote(str(ROOT / match.group(1))), text)
    return text.replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT))


def shared_files() -> dict[Path, bytes]:
    files = {Path("SKILL.md"): (ROOT / "integrations/SKILL.md").read_bytes()}
    for name, skill in REFERENCES.items():
        raw = (ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        files[Path("references") / f"{name}.md"] = _replace_root(raw).encode("utf-8")
    files[Path("references/feedback.md")] = (ROOT / "integrations/feedback.md").read_bytes()
    return files


def plan_shared(home: Path, adopt: bool, receipt: dict | None) -> tuple[list[dict], list[str]]:
    target = shared_dir(home)
    files = shared_files()
    ops, conflicts = [], []
    owned = receipt is not None
    if target.exists() and not owned:
        if not adopt:
            conflicts.append(f"{target} exists and is not a dont-forget installation; pass --adopt-existing")
            return ops, conflicts
        link = target / "scripts"
        if not link.is_symlink() or not (link / "transcript.py").is_file():
            conflicts.append(f"{target} has no scripts symlink to an existing checkout")
            return ops, conflicts
        skill = target / "SKILL.md"
        if not skill.is_file() or "dont-forget" not in skill.read_text(encoding="utf-8", errors="replace"):
            conflicts.append(f"{target} does not look like a dont-forget skill")
            return ops, conflicts
    for relative, data in files.items():
        path = target / relative
        current = state_of(path)
        if current["kind"] in ("other", "symlink"):
            conflicts.append(f"{path} is not a regular file")
            continue
        if current["kind"] == "file" and current["sha256"] == digest(data):
            continue
        ops.append(file_op(path, data,
                           path.read_bytes() if current["kind"] == "file" else None))
    link = target / "scripts"
    if link.is_symlink():
        if link.resolve() != (ROOT / "scripts").resolve():
            conflicts.append(f"{link} points at another checkout: {link.resolve()}")
    elif link.exists():
        conflicts.append(f"{link} is not a symlink")
    else:
        ops.append(link_op(link, ROOT / "scripts"))
    return ops, conflicts


def codex_owned_commands() -> set[str]:
    script = shlex.quote(str(ROOT / "scripts/hot-scan.py"))
    return {DESIRED_HOOK.format(path=script), LEGACY_HOOK.format(path=script)}


def _desired_codex_group(event: str) -> dict:
    script = shlex.quote(str(ROOT / "scripts/hot-scan.py"))
    handler = {"type": "command", "command": DESIRED_HOOK.format(path=script), "timeout": 15}
    if event == "SessionStart":
        # Only a context-emitting event can use the threshold; PreCompact would warn.
        handler["additionalContextLimit"] = 0
    return {"matcher": HOOK_EVENTS[event], "hooks": [handler]}


def plan_codex(codex_home: Path) -> tuple[list[dict], list[str], list[dict]]:
    path = codex_home / "hooks.json"
    if path.is_symlink():
        return [], [f"{path} is a symlink; refusing to replace it"], []
    try:
        config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"hooks": {}}
    except (OSError, json.JSONDecodeError) as error:
        return [], [f"cannot read {path}: {error}"], []
    if not isinstance(config, dict):
        return [], [f"{path} is not a JSON object"], []
    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return [], [f"{path} has a non-object hooks value"], []
    owned = codex_owned_commands()
    before = json.loads(json.dumps(config))
    changed_hooks = []
    for event in HOOK_EVENTS:
        wanted = _desired_codex_group(event)
        groups = hooks.get(event)
        if groups is None:
            groups = []
        elif not isinstance(groups, list):
            continue
        kept_groups = []
        exact = False
        for group in groups:
            # Our exact group stays where it is: moving it to the end would rewrite the
            # file on every run and invalidate Codex's hook trust hash.
            if canonical(group) == canonical(wanted):
                exact = True
                kept_groups.append(group)
                continue
            if not isinstance(group, dict):
                kept_groups.append(group)
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                kept_groups.append(group)
                continue
            kept = [handler for handler in handlers
                    if not (isinstance(handler, dict) and handler.get("command") in owned)]
            if len(kept) != len(handlers):
                changed_hooks.append({"event": event, "matcher": group.get("matcher"),
                                      "removed": len(handlers) - len(kept)})
            if kept:
                kept_groups.append({**group, "hooks": kept})
        if not exact:
            kept_groups.append(wanted)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)
    records = [{"path": str(path), "event": event, "matcher": HOOK_EVENTS[event],
                "handler": _desired_codex_group(event)["hooks"][0]["command"]}
               for event in HOOK_EVENTS]
    if canonical(config) == canonical(before):
        return [], [], records
    data = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return [file_op(path, data, path.read_bytes() if path.is_file() else None)], [], records


def opencode_wrapper(api: int) -> str:
    uri = (ROOT / "integrations/opencode.mjs").as_uri()
    exported = "adapter" if api == 2 else "legacy"
    return (f'import adapter, {{ legacy }} from {json.dumps(uri)};\n'
            f'export default {exported};\n')


def omp_wrapper() -> str:
    uri = (ROOT / "integrations/omp.mjs").as_uri()
    return f'export {{ default }} from {json.dumps(uri)};\n'


def plan_file_target(path: Path, data: str, known: list[str], receipt: dict | None,
                     target_name: str) -> tuple[list[dict], list[str]]:
    ops, conflicts = [], []
    current = state_of(path)
    if current["kind"] == "absent":
        return [file_op(path, data.encode("utf-8"))], []
    if current["kind"] == "file":
        existing = path.read_bytes()
        if existing == data.encode("utf-8"):
            return [], []
        if receipt is not None or any(existing == candidate.encode("utf-8") for candidate in known):
            return [file_op(path, data.encode("utf-8"), existing)], []
        conflicts.append(f"{path} exists with different content and is not an owned {target_name}")
        return [], conflicts
    conflicts.append(f"{path} is not a regular file")
    return [], conflicts


def plan_host(host: str, home: Path, receipt: dict, api: int | None,
              adopt: bool, roots: dict) -> tuple[list[dict], list[str], list[dict], dict | None, str]:
    if host == "shared":
        target = shared_dir(home)
        entry = receipt_entry(receipt, "shared", str(target))
        ops, conflicts = plan_shared(home, adopt, entry)
        if conflicts:
            return ops, conflicts, [], None, str(target)
        files = [file_entry(shared_dir(home) / relative, data)
                 for relative, data in shared_files().items()]
        files.append(symlink_entry(shared_dir(home) / "scripts", ROOT / "scripts"))
        after = {"host": "shared", "target": str(target), "source": str(ROOT),
                 "files": files, "hooks": []}
        return ops, [], [], after, str(target)

    if host == "codex":
        target = roots["codex"]
        ops, conflicts, hook_ops = plan_codex(target)
        if conflicts:
            return ops, conflicts, [], None, str(target)
        files = []
        after = {"host": "codex", "target": str(target), "source": str(ROOT),
                 "files": files, "hooks": hook_ops}
        return ops, [], hook_ops, after, str(target)

    if host == "opencode":
        target = roots["opencode"]
        path = target / "plugins/dont-forget.js"
        data = opencode_wrapper(api or 2)
        known = [opencode_wrapper(1), opencode_wrapper(2)]
        ops, conflicts = plan_file_target(path, data, known,
                                          receipt_entry(receipt, "opencode", str(target)),
                                          "OpenCode plugin")
        if conflicts:
            return ops, conflicts, [], None, str(target)
        files = [file_entry(path, data.encode("utf-8"))]
        after = {"host": "opencode", "target": str(target), "source": str(ROOT),
                 "files": files, "hooks": []}
        return ops, [], [], after, str(target)

    if host == "omp":
        target = roots["omp"]
        path = target / "extensions/dont-forget.js"
        data = omp_wrapper()
        conflicts = []
        extension_dir = target / "extensions"
        if extension_dir.is_dir():
            for candidate in extension_dir.iterdir():
                if candidate == path or not candidate.is_file():
                    continue
                try:
                    existing = candidate.read_text(encoding="utf-8", errors="replace")
                except OSError as error:
                    conflicts.append(f"cannot read {candidate}: {error}")
                    continue
                if "hot-scan.py" in existing and "--hook" in existing:
                    conflicts.append(
                        f"{candidate} already injects a dont-forget digest; remove or disable it "
                        "before installing the OMP extension (two sources would duplicate memory)")
        if conflicts:
            return [], conflicts, [], None, str(target)
        ops, conflicts = plan_file_target(path, data, [data],
                                          receipt_entry(receipt, "omp", str(target)),
                                          "OMP extension")
        if conflicts:
            return ops, conflicts, [], None, str(target)
        files = [file_entry(path, data.encode("utf-8"))]
        after = {"host": "omp", "target": str(target), "source": str(ROOT),
                 "files": files, "hooks": []}
        return ops, [], [], after, str(target)

    raise ValueError(f"unknown host: {host}")


def plan_uninstall(hosts: list[str], receipt: dict) -> tuple[list[dict], list[str], list[dict]]:
    ops, conflicts = [], []
    entries = [entry for entry in receipt.get("installations", [])
               if "all" in hosts or entry.get("host") in hosts]
    for entry in entries:
        for item in entry.get("files", []):
            path = Path(item["path"])
            current = state_of(path)
            wanted = ({"kind": "symlink", "target": item["target"]} if item["kind"] == "symlink"
                      else {"kind": "file", "sha256": item["sha256"]})
            if current["kind"] == "absent":
                continue
            if current != wanted:
                conflicts.append(f"{path} does not match the installed file; not removing it")
                continue
            ops.append(file_op(path, None, path.read_bytes() if current["kind"] == "file" else None))
        if entry.get("host") == "codex" and entry.get("hooks"):
            path = Path(entry["hooks"][0]["path"])
            if not path.exists():
                continue
            try:
                config = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                conflicts.append(f"cannot read {path}: {error}")
                continue
            before = json.loads(json.dumps(config))
            owned = {(item.get("event"), item.get("matcher"), item.get("handler"))
                     for item in entry.get("hooks", [])}
            hooks = config.get("hooks", {})
            for event in list(hooks):
                groups = hooks[event]
                kept_groups = []
                for group in groups if isinstance(groups, list) else []:
                    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                        kept_groups.append(group)
                        continue
                    kept = [handler for handler in group["hooks"]
                            if not (isinstance(handler, dict)
                                    and (event, group.get("matcher"), handler.get("command")) in owned)]
                    if kept:
                        kept_groups.append({**group, "hooks": kept})
                if kept_groups:
                    hooks[event] = kept_groups
                else:
                    hooks.pop(event, None)
            if canonical(config) != canonical(before):
                ops.append(file_op(path, (json.dumps(config, ensure_ascii=False, indent=2)
                                          + "\n").encode("utf-8"), path.read_bytes()))
        ops.append(receipt_op(state_of_path(), entry["host"], entry["target"], None))
    return ops, conflicts, []


def state_of_path() -> Path:
    return STATE


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("shared", "codex", "opencode", "omp", "all"))
    parser.add_argument("--home", type=Path, default=Path.home(),
                        help="override install destinations for isolated checks")
    parser.add_argument("--opencode-api", type=int, choices=(1, 2),
                        help="OpenCode plugin API; default: detect the installed CLI major version")
    parser.add_argument("--omp-agent-dir", type=Path,
                        help="OMP agent directory, for a non-default profile")
    parser.add_argument("--adopt-existing", action="store_true",
                        help="take over an existing dont-forget installation of the same checkout")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--uninstall", action="store_true")
    modes.add_argument("--rollback", metavar="BACKUP_ID")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _resolve_roots(args):
    home = args.home.expanduser().resolve()
    native = home == Path.home().resolve()
    explicit_state = os.environ.get("DONT_FORGET_HOME")
    if native:
        state = Path(explicit_state).expanduser().resolve() if explicit_state else home / ".dont-forget"
        codex = Path(os.environ["CODEX_HOME"]).expanduser().resolve() if os.environ.get("CODEX_HOME") \
            else home / ".codex"
        xdg = os.environ.get("XDG_CONFIG_HOME")
        opencode = (Path(os.environ["OPENCODE_CONFIG_DIR"]).expanduser().resolve()
                    if os.environ.get("OPENCODE_CONFIG_DIR")
                    else (Path(xdg).expanduser().resolve() if xdg else home / ".config") / "opencode")
        agent = args.omp_agent_dir.expanduser().resolve() if args.omp_agent_dir else omp_agent_dir()
    else:
        if explicit_state:
            state = Path(explicit_state).expanduser().resolve()
            if not state.is_relative_to(home):
                raise ValueError(f"DONT_FORGET_HOME must be inside {home} for --home {home}: {state}")
        else:
            state = home / ".dont-forget"
        codex = home / ".codex"
        opencode = home / ".config/opencode"
        agent = args.omp_agent_dir.expanduser().resolve() if args.omp_agent_dir else home / ".omp/agent"
        if not agent.is_relative_to(home):
            raise ValueError(f"--omp-agent-dir must be inside {home}: {agent}")
    return home, native, state, codex, opencode, agent


def _detect_opencode_major(args) -> int | None:
    if args.opencode_api:
        return args.opencode_api
    if not shutil.which("opencode"):
        return None
    result = subprocess.run(["opencode", "--version"], capture_output=True, text=True, timeout=15, check=True)
    match = re.search(r"(\d+)\.", result.stdout)
    major = int(match.group(1)) if match else None
    if major not in (1, 2):
        return _unknown_major(result.stdout)
    return major


def _unknown_major(version: str):
    print(f"dont-forget: unknown OpenCode major version: {version.strip()!r}; "
          "pass --opencode-api 1 or 2", file=sys.stderr)
    raise SystemExit(2)


def main() -> None:
    args = parse_args()
    global STATE
    try:
        home, native, STATE, codex, opencode, agent = _resolve_roots(args)
        os.environ["_DF_OMP_AGENT_DIR"] = str(agent)
        os.environ["_DF_HOSTS_HOME"] = str(home) if not native else ""
        receipt = read_receipt(STATE)
    except ValueError as error:
        raise SystemExit(f"dont-forget installation failed: {error}") from error

    if args.rollback:
        if args.host or args.adopt_existing or args.dry_run:
            raise SystemExit("dont-forget: --rollback does not accept --host/--adopt-existing/--dry-run")
        try:
            with installation_lock(STATE):
                done = rollback(STATE, args.rollback)
        except (OSError, ValueError) as error:
            raise SystemExit(f"dont-forget: {error}") from error
        print(json.dumps(done, ensure_ascii=False, indent=2))
        return

    if not args.host:
        raise SystemExit("dont-forget: --host is required unless --rollback is used")
    if args.uninstall and args.adopt_existing:
        raise SystemExit("dont-forget: --adopt-existing belongs to install only")

    if args.uninstall:
        hosts = ["all"] if args.host == "all" else [args.host]
        try:
            ops, conflicts, _ = plan_uninstall(hosts, receipt)
        except (OSError, ValueError) as error:
            raise SystemExit(f"dont-forget installation failed: {error}") from error
        if conflicts:
            raise SystemExit("dont-forget uninstall refused: " + "; ".join(conflicts))
        if args.dry_run:
            print(json.dumps({"dry_run": True, "operations": Transaction(STATE, ops, True, str(ROOT)).describe()},
                             ensure_ascii=False, indent=2))
            return
        try:
            with installation_lock(STATE):
                done = Transaction(STATE, ops, False, str(ROOT)).run()
        except (OSError, ValueError) as error:
            raise SystemExit(f"dont-forget uninstall failed: {error}") from error
        print(json.dumps(done, ensure_ascii=False, indent=2))
        return

    # Install: one host also ensures shared; `all` follows detected CLIs.
    selected = ["shared"]
    skipped = []
    api = None
    if args.host == "all":
        if shutil.which("codex"):
            selected.append("codex")
        else:
            skipped.append("codex")
        if shutil.which("opencode"):
            selected.append("opencode")
            try:
                api = _detect_opencode_major(args)
            except (OSError, subprocess.SubprocessError) as error:
                raise SystemExit(f"dont-forget: cannot detect OpenCode version: {error}") from error
        else:
            skipped.append("opencode")
        if shutil.which("omp"):
            selected.append("omp")
        else:
            skipped.append("omp")
    else:
        if args.host != "shared":
            selected.append(args.host)
        if args.host == "opencode":
            try:
                api = _detect_opencode_major(args)
            except (OSError, subprocess.SubprocessError) as error:
                raise SystemExit(f"dont-forget: cannot detect OpenCode version: {error}") from error
            if api is None:
                raise SystemExit("dont-forget: --opencode-api 1|2 is required when OpenCode is not installed")

    roots = {"codex": codex, "opencode": opencode, "omp": agent}
    ops: list[dict] = []
    conflicts: list[str] = []
    entries: list[dict] = []
    trust: list[str] = []
    try:
        for host in selected:
            host_ops, host_conflicts, hook_ops, after, target = plan_host(
                host, home, receipt, api, args.adopt_existing, roots)
            conflicts.extend(host_conflicts)
            if host_conflicts or after is None:
                continue
            ops.extend(host_ops)
            entry_op = receipt_op(STATE, host, target, after)
            if canonical(entry_op["before_entry"]) != canonical(entry_op["after_entry"]):
                ops.append(entry_op)
            entries.append(after)
            if hook_ops and any(op.get("path") == hook_ops[0]["path"] for op in host_ops):
                trust.append(host)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(f"dont-forget installation failed: {error}") from error
    if conflicts:
        raise SystemExit("dont-forget installation refused: " + "; ".join(conflicts))

    result = Transaction(STATE, ops, args.dry_run, str(ROOT)).run()
    result["hosts"] = selected
    result["skipped"] = skipped
    result["trust_review_required"] = sorted(set(trust))
    if result.get("backup"):
        result["restart"] = "Start a new session in each installed host; reload OpenCode after an adapter change."
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
