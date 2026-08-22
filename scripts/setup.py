#!/usr/bin/env python3
"""Find the vault and write the config, so nobody has to type a path by hand."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import (CONFIG_PATH, DEFAULT_DB, HOME_DIR, NotConfigured, known_vaults,
                    scan_for_vaults, vault_from_config)
from index import build

# Counting every note on a slow network mount is not worth the wait: the number is only
# here so the user recognises which folder is theirs.
COUNT_CAP = 5000


def count_notes(vault: Path) -> tuple[int, bool]:
    total = 0
    for _ in vault.rglob("*.md"):
        total += 1
        if total >= COUNT_CAP:
            return total, True
    return total, False


def detect() -> dict:
    """Everything the setup conversation needs, gathered in one call.

    Obsidian's own registry is asked first and the disk is only walked when it holds
    nothing: the registry is the same list the app shows, so the user recognises the
    answer instead of auditing a directory dump.
    """
    try:
        configured, problem = str(vault_from_config()), None
    except NotConfigured as error:
        configured, problem = None, str(error)
    candidates, source = known_vaults(), "obsidian"
    if not candidates:
        candidates, source = scan_for_vaults(), "disk-scan"
    listed = []
    for path in candidates:
        notes, capped = count_notes(path)
        listed.append({"path": str(path), "notes": notes, "notes_capped": capped})
    return {"configured": configured, "problem": problem, "source": source,
            "candidates": listed, "config_path": str(CONFIG_PATH), "home": str(HOME_DIR)}


def configure(raw: str) -> dict:
    """Point the plugin at a vault and build its index in the same step.

    Writing the key back into whatever config.json already holds, rather than replacing
    the file, keeps any hand-added key the user put there.
    """
    vault = Path(raw).expanduser()
    if not vault.is_dir():
        raise NotConfigured(f"not a directory: {vault}")
    existing = {}
    if CONFIG_PATH.is_file():
        try:
            existing = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except ValueError:
            existing = {}
    if not isinstance(existing, dict):
        existing = {}
    existing["vault"] = str(vault)
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"configured": str(vault), "config_path": str(CONFIG_PATH), "index": build(vault, DEFAULT_DB)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detect", action="store_true", help="report the current config and every vault found")
    parser.add_argument("--set", dest="vault", help="use this vault, write the config, build the index")
    args = parser.parse_args()
    if not args.detect and not args.vault:
        parser.error("pass --detect or --set <path>")
    try:
        result = configure(args.vault) if args.vault else detect()
    except (NotConfigured, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
