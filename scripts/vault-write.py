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

from common import DEFAULT_DB, config, vault_from_config
from secret_scan import warning

try:
    import fcntl
except ImportError:
    fcntl = None  # Windows runs unlocked.


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


def validate_frontmatter(content: str) -> None:
    """Refuse a note whose frontmatter is opened but never closed.

    index.py's parser treats an unterminated `---` block as no frontmatter at all
    and silently drops every field (type, date, dies-when, reviewed). Catch it on
    the write path so the metadata loss surfaces as an error, not a quiet miss.
    """
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return
    if not any(line.strip() == "---" for line in lines[1:]):
        raise ValueError("frontmatter opened with '---' but never closed")


def validate_filename(filename: object) -> str:
    if not isinstance(filename, str) or not filename:
        raise ValueError("filename must be a non-empty string")
    path = Path(filename)
    if path.name != filename or "/" in filename or "\\" in filename:
        raise ValueError("filename must not contain a path")
    if path.suffix != ".md" or not path.stem or "#" in path.stem:
        raise ValueError("filename must be <non-empty stem>.md without '#'")
    return filename


def similar_notes(vault: Path, db_path: Path, filename: str, content: str,
                  limit: int = 3) -> list[dict]:
    """Notes the vault already holds that may be this same claim.

    This used to be a checklist line telling the agent to run the search and judge the
    result, and a checklist line is exactly what gets skipped in the middle of a task.
    The mechanics move into code; the judgement — same claim or not — stays with the
    person, because "one cause or two" is meaning, and code cannot see it.

    The bar for showing candidates is deliberately low. A candidate costs one glance;
    a duplicate costs a split memory that nobody notices for months.
    """
    from index import parse_frontmatter, refresh_index
    from search import search

    meta, body = parse_frontmatter(content)
    # A session note is a dated snapshot, not a claim, so it has no duplicates by
    # construction — and it always reads like every earlier session of the same project.
    # Without this the writer would answer "similar" to every session ever recorded.
    if str(meta.get("type", "")).strip().lower() == "session":
        return []

    query = f"{Path(filename).stem} {body[:400]}"
    try:
        refresh_index(vault, db_path)
        # A third of real chunks are larger than 1200 bytes, and apply_budget stops at
        # the first fragment that does not fit — so a small budget here returned nothing
        # and let the duplicate through.
        result = search(query, budget=4000, db_path=db_path)
    except Exception:
        # Dedup is a courtesy, not a gate: a broken index must not stop a note being
        # written. The write is the thing the user asked for.
        return []
    if result["coverage"].get("weak_match"):
        return []
    seen, out = set(), []
    for fragment in result["fragments"]:
        path = fragment.get("path")
        if path in seen or path == filename:
            continue
        seen.add(path)
        out.append({"path": path, "kind": fragment.get("kind") or "",
                    "date": fragment.get("date") or ""})
        if len(out) >= limit:
            break
    return out


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
    # The dedup search reads and refreshes an index, and an index belongs to one vault.
    # Pointing a test vault at the live index is how the live index gets overwritten with
    # test notes, so an explicit --vault without an explicit --db simply skips dedup.
    parser.add_argument("--db", type=Path)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        filename = validate_filename(payload.get("filename"))
        content = payload.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        validate_frontmatter(content)
        action = payload.get("action", "create")
        if action not in ("create", "replace"):
            raise ValueError("action must be create or replace")
        vault = args.vault or vault_from_config()
        if fcntl is not None:
            vault_fd = os.open(vault, os.O_RDONLY)
            fcntl.flock(vault_fd, fcntl.LOCK_EX)
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
            db_path = args.db or (DEFAULT_DB if args.vault is None else None)
            # `is True` on purpose: a JSON string "false" is truthy, and silently
            # skipping the check is the one failure mode this must not have.
            skip = payload.get("duplicates_checked") is True
            if not skip and db_path is None:
                print("dedup skipped: --vault was given without --db, and the check needs"
                      " an index that belongs to this vault.", file=sys.stderr)
            candidates = ([] if skip or db_path is None
                          else similar_notes(vault, db_path, filename, content))
            status = "similar" if candidates else write_note(vault, filename, content)
        # Every write, whatever the genre: "the RCON password leaked" arrived as an open
        # thread, not as a gotcha. The warning goes in the returned status as well as to
        # stderr, so it can be counted later instead of scrolling past.
        wrote = status in ("created", "replaced")
        secret_warning = (warning(content)
                          if wrote and config().get("scan_secrets", True) else "")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    result = {"status": status}
    if status == "similar":
        # Not written. The caller shows these, the user says new note or an update to an
        # existing one, and a repeat with duplicates_checked writes it.
        result["candidates"] = candidates
        print(f"similar notes already in the vault: {', '.join(c['path'] for c in candidates)}."
              " Repeat with duplicates_checked to write anyway.", file=sys.stderr)
    if secret_warning:
        print(secret_warning, file=sys.stderr)
        result["warning"] = secret_warning
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
