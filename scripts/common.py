#!/usr/bin/env python3
"""Paths, config, vault discovery and read-only database access, shared by every script.

These used to be copy-pasted into each script, which is how the indexer and the writer
ended up disagreeing about what "~/vault" means.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

# Everything this plugin owns lives here. The override exists so a second vault, or a
# test run, gets its own index and logs without having to fake $HOME.
HOME_DIR = Path(os.environ.get("DONT_FORGET_HOME") or Path.home() / ".dont-forget")
DEFAULT_DB = HOME_DIR / "index.db"
DEFAULT_FEEDBACK = HOME_DIR / "feedback.jsonl"
DEFAULT_QUERY_LOG = HOME_DIR / "queries.jsonl"
CONFIG_PATH = HOME_DIR / "config.json"

SETUP_HINT = "Run /dont-forget:setup — it finds your vault and writes the config for you."

# One list, because the writer and the reader of the journal are different scripts and
# drifted apart once: feedback-log.py accepted "rejected" while checkup.py counted four
# verdicts, so every refusal was written and then silently missing from the report.
VERDICTS = ("saved-work", "noise", "false-note", "proven-miss", "rejected")


class NotConfigured(ValueError):
    """No usable vault. Subclasses ValueError so existing callers still catch it.

    It carries instructions, not a stack trace: the first thing a new user ever sees
    from this plugin used to be a FileNotFoundError about a file they never made.
    """


def config(config_path: Path = CONFIG_PATH) -> dict:
    """The whole config, for settings that have a working default without it.

    A missing or broken file is not an error here: only the vault key is worth
    interrupting the user over, and vault_from_config already does that.
    """
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def config_count(key: str, default: int, config_path: Path = CONFIG_PATH) -> int:
    """A non-negative whole number from the config; anything else falls back silently."""
    value = config(config_path).get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else default


def vault_from_config(config_path: Path = CONFIG_PATH) -> Path:
    """The one reader of config.json. Expands "~" so every caller agrees on the path."""
    try:
        raw = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise NotConfigured(f"dont-forget is not set up: no {config_path}. {SETUP_HINT}") from None
    except OSError as error:
        raise NotConfigured(f"cannot read {config_path}: {error}. {SETUP_HINT}") from None
    try:
        value = json.loads(raw).get("vault")
    except (json.JSONDecodeError, AttributeError):
        raise NotConfigured(f"{config_path} is not valid JSON. Delete it and {SETUP_HINT[0].lower()}"
                            f"{SETUP_HINT[1:]}") from None
    if not isinstance(value, str) or not value:
        raise NotConfigured(f'{config_path} has no "vault" key. {SETUP_HINT}')
    vault = Path(value).expanduser()
    if not vault.is_dir():
        raise NotConfigured(f"the configured vault is gone: {vault}. "
                            f"It was moved or deleted. {SETUP_HINT}")
    return vault


def _from_windows(raw: str) -> str:
    r"""Translate C:\Users\... to /mnt/c/Users/... .

    Under WSL the Obsidian app runs on the Windows side and writes Windows paths into
    its registry, which a Linux process cannot open as written.
    """
    if len(raw) > 2 and raw[1] == ":" and raw[2] in "\\/":
        return f"/mnt/{raw[0].lower()}/" + raw[3:].replace("\\", "/")
    return raw


def _registries() -> list[Path]:
    """Every place Obsidian is known to keep its list of vaults."""
    home = Path.home()
    found = [
        home / ".config/obsidian/obsidian.json",                               # Linux
        home / "Library/Application Support/obsidian/obsidian.json",           # macOS
        home / ".var/app/md.obsidian.Obsidian/config/obsidian/obsidian.json",  # Flatpak
        home / "snap/obsidian/current/.config/obsidian/obsidian.json",         # Snap
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        found.append(Path(appdata) / "obsidian/obsidian.json")
    # WSL again: the shell is Linux, the app and its registry are on the Windows disk.
    found.extend(Path("/mnt/c/Users").glob("*/AppData/Roaming/obsidian/obsidian.json"))
    return found


def known_vaults() -> list[Path]:
    """Vaults Obsidian already knows about, currently-open first, then most recent.

    Reading Obsidian's own registry beats scanning the disk: it is the same list the
    app shows, so the user recognises the answer instead of auditing a find(1) dump.
    """
    ranked: dict[Path, float] = {}
    for registry in _registries():
        try:
            vaults = json.loads(registry.read_text(encoding="utf-8")).get("vaults")
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(vaults, dict):
            continue
        for entry in vaults.values():
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                continue
            path = Path(_from_windows(entry["path"]))
            if not path.is_dir():
                continue
            try:
                rank = float(entry.get("ts") or 0)
            except (TypeError, ValueError):
                rank = 0.0
            # An open vault is the one the user is looking at right now, so it outranks
            # any timestamp rather than competing with it.
            rank += 1e18 if entry.get("open") else 0
            ranked[path] = max(ranked.get(path, 0.0), rank)
    return sorted(ranked, key=lambda path: -ranked[path])


def scan_for_vaults(root: Path | None = None, depth: int = 4) -> list[Path]:
    """Folders holding a .obsidian directory, for when no registry exists.

    Bounded depth on purpose: an unbounded walk of $HOME takes minutes and mostly finds
    node_modules. A vault is never nested inside another, so the first hit wins.
    """
    root = root or Path.home()
    found: list[Path] = []
    for level in range(1, max(1, depth) + 1):
        for marker in root.glob("/".join(["*"] * level + [".obsidian"])):
            vault = marker.parent
            if marker.is_dir() and not any(vault.is_relative_to(seen) for seen in found):
                found.append(vault)
    return found


def connect_ro(db_path: Path) -> sqlite3.Connection:
    """Open the index read-only. as_uri() escapes paths that plain f-strings break on."""
    return sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)
