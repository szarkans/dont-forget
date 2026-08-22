#!/usr/bin/env python3
"""Create or replace a vault note atomically."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from common import vault_from_config


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(target: Path, data: bytes) -> None:
    """Replace the target in one step so a reader never sees a half-written note."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".vault-write-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def validate_filename(filename: object) -> str:
    if not isinstance(filename, str) or not filename:
        raise ValueError("filename must be a non-empty string")
    path = Path(filename)
    if path.name != filename or "/" in filename or "\\" in filename:
        raise ValueError("filename must not contain a path")
    if path.suffix != ".md" or not path.stem or "#" in path.stem:
        raise ValueError("filename must be <non-empty stem>.md without '#'")
    return filename


def write_note(vault: Path, filename: str, content: str) -> str:
    target = vault / filename
    incoming = content.encode("utf-8")
    if target.exists():
        if digest(target.read_bytes()) == digest(incoming):
            return "exists-same"
        print(f"conflict: {filename} already exists with different content", file=sys.stderr)
        return "conflict"
    atomic_write(target, incoming)
    return "created"


def replace_note(vault: Path, filename: str, content: str, expected_sha: str) -> str:
    target = vault / filename
    if not target.exists():
        print(f"conflict: {filename} does not exist", file=sys.stderr)
        return "conflict"
    if digest(target.read_bytes()) != expected_sha:
        print(f"conflict: {filename} content changed", file=sys.stderr)
        return "conflict"
    atomic_write(target, content.encode("utf-8"))
    return "replaced"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", type=Path)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        filename = validate_filename(payload.get("filename"))
        content = payload.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        action = payload.get("action", "create")
        if action not in ("create", "replace"):
            raise ValueError("action must be create or replace")
        vault = args.vault or vault_from_config()
        if action == "replace":
            expected_sha = payload.get("expected_sha")
            if not isinstance(expected_sha, str) or len(expected_sha) != 64:
                raise ValueError("expected_sha must be a sha256 hex digest")
            try:
                bytes.fromhex(expected_sha)
            except ValueError as error:
                raise ValueError("expected_sha must be a sha256 hex digest") from error
            status = replace_note(vault, filename, content, expected_sha.lower())
        else:
            status = write_note(vault, filename, content)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    print(json.dumps({"status": status}, separators=(",", ":")))


if __name__ == "__main__":
    main()
