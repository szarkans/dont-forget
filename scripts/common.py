#!/usr/bin/env python3
"""Paths, config and read-only database access shared by every script here.

These four things used to be copy-pasted into each script, which is how the
indexer and the writer ended up disagreeing about what "~/vault" means.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

HOME_DIR = Path.home() / ".dont-forget"
DEFAULT_DB = HOME_DIR / "index.db"
DEFAULT_FEEDBACK = HOME_DIR / "feedback.jsonl"
DEFAULT_QUERY_LOG = HOME_DIR / "queries.jsonl"
CONFIG_PATH = HOME_DIR / "config.json"


def vault_from_config(config_path: Path = CONFIG_PATH) -> Path:
    """The one reader of config.json. Expands "~" so every caller agrees on the path."""
    with config_path.open(encoding="utf-8") as handle:
        value = json.load(handle).get("vault")
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing vault in {config_path}")
    return Path(value).expanduser()


def connect_ro(db_path: Path) -> sqlite3.Connection:
    """Open the index read-only. as_uri() escapes paths that plain f-strings break on."""
    return sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)
