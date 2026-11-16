"""Read local OMP, Claude, Codex, OpenCode and Devin conversations without changing their stores.

File exports and live stores converge here. Only conversation text and bounded tool
traces leave this module; reasoning, attachments and tool results stay in the source.

Identity is never guessed from a neighbouring host. The process that starts a session
knows which host and which session it belongs to; a parent's variables inherited by a
child are not evidence about the child, so every host is read through its own native
fields, and an ambiguous set is reported as unknown instead of resolved by priority.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


# ---------------------------------------------------------------------------
# Host identity and roots
# ---------------------------------------------------------------------------

def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _native_identity(harness: str) -> str | None:
    """The session id this host itself advertises, or None. Never another host's id."""
    if harness == "claude":
        return _env("CLAUDE_CODE_SESSION_ID") or None
    if harness == "omp":
        return _env("PI_SESSION_ID") or None
    if harness == "opencode":
        return _env("OPENCODE_SESSION_ID") or None
    if harness == "codex":
        thread, session = _env("CODEX_THREAD_ID"), _env("CODEX_SESSION_ID")
        # Two different non-empty ids name two sessions: the caller has to say which.
        if thread and session and thread != session:
            return None
        return thread or session or None
    if harness == "devin":
        return _devin_identity()
    return None


def _present_families() -> list[str]:
    """Host families whose native identity fields are present, even when empty.

    An empty PI_SESSION_FILE/PI_SESSION_ID still says "this process is OMP"; it must not
    fall through to a Claude id inherited from a parent process.
    """
    present = []
    if "PI_SESSION_FILE" in os.environ or "PI_SESSION_ID" in os.environ:
        present.append("omp")
    if _env("OPENCODE_SESSION_ID"):
        present.append("opencode")
    if _env("CODEX_THREAD_ID") or _env("CODEX_SESSION_ID"):
        present.append("codex")
    if _env("CLAUDE_CODE_SESSION_ID"):
        present.append("claude")
    if _env("AI_AGENT").startswith("devin") or "CHISEL_SESSION_DB" in os.environ \
            or "DEVIN_TRACEPARENT" in os.environ:
        present.append("devin")
    return present


def active_session_identity(harness: str = "auto") -> tuple[str, str | None]:
    """(host, id) for the running session, or ("unknown", None) when not unambiguous.

    Explicit hosts read only their own native fields. Auto accepts exactly one represented
    family: two families mean a foreign id was inherited and no answer is safe, while a
    single family with an empty id means the host is known but has no usable identity.
    """
    if harness in ("claude", "codex", "opencode", "omp", "devin"):
        return harness, _native_identity(harness)
    present = _present_families()
    if len(present) != 1:
        return "unknown", None
    family = present[0]
    return family, _native_identity(family)


def claude_projects() -> Path:
    """Claude Code's project transcripts; CLAUDE_CONFIG_DIR moves the whole config root."""
    configured = _env("CLAUDE_CONFIG_DIR")
    base = Path(configured).expanduser() if configured else Path.home() / ".claude"
    return base / "projects"


def omp_root() -> Path:
    profile = os.environ.get("OMP_PROFILE", os.environ.get("PI_PROFILE", "")).strip()
    if not profile or profile == "default":
        if os.environ.get("PI_CODING_AGENT_DIR"):
            return Path(os.environ["PI_CODING_AGENT_DIR"]).expanduser() / "sessions"
        profile = ""
    root = Path(os.environ.get("PI_CONFIG_DIR") or ".omp").expanduser()
    if not root.is_absolute():
        root = Path.home() / root
    agent = root / "profiles" / profile / "agent" if profile else root / "agent"
    return agent / "sessions"


def omp_agent_dir() -> Path:
    """The agent directory holding sessions/ and extensions/ for the active profile."""
    return omp_root().parent


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def codex_root() -> Path:
    return codex_home() / "sessions"


def codex_archived_root() -> Path:
    return codex_home() / "archived_sessions"


def codex_state_db() -> Path:
    """The SQLite file Codex uses to remember each thread's selected rollout.

    A non-default `sqlite_home` in Codex config is not emulated here: the caller passes
    --codex-state-db or an explicit transcript path instead of us guessing.
    """
    sqlite_home = _env("CODEX_SQLITE_HOME")
    return (Path(sqlite_home).expanduser() if sqlite_home else codex_home()) / "state_5.sqlite"


def opencode_db() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "opencode/opencode.db"


def validate_session_id(value: object) -> str:
    """A bookkeeping id is raw for Claude and namespaced for the other hosts."""
    problem = ("session_id must be a canonical id: raw Claude id or "
               "omp:<id>, codex:<id>, opencode:<id>, devin:<id>, without whitespace or paths")
    if (not isinstance(value, str) or not value
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127
                   or char in "/\\" for char in value)):
        raise ValueError(problem)
    if ":" in value:
        harness, session_id = value.split(":", 1)
        if harness not in ("omp", "codex", "opencode", "devin") \
                or not session_id or ":" in session_id:
            raise ValueError(problem)
    return value


# ---------------------------------------------------------------------------
# Streams: plain JSONL, and Codex compressed history
# ---------------------------------------------------------------------------

class _PipeReader:
    """Binary line reader that remembers whether the decompressor reached its end."""

    def __init__(self, process: subprocess.Popen, error: tempfile._TemporaryFileWrapper):
        self.process = process
        self.error = error
        self.stream = process.stdout
        self.exhausted = False

    def readline(self, *args):
        data = self.stream.readline(*args)
        if not data:
            self.exhausted = True
        return data

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def close(self) -> None:
        self.stream.close()


@contextmanager
def open_transcript_bytes(path: Path):
    """Raw bytes of a transcript, decompressing `.jsonl.zst` without touching the source.

    stdlib `compression.zstd` is used when present; otherwise the system `zstd` streams
    decompressed bytes through a pipe. Nothing is unpacked next to the source. An early
    close terminates the child instead of reporting a broken frame as corruption.
    """
    if not path.name.endswith(".zst"):
        with path.open("rb") as handle:
            yield handle
        return
    try:
        from compression import zstd  # Python 3.14+
    except ImportError:
        zstd = None
    if zstd is not None:
        try:
            with zstd.open(path, "rb") as handle:
                yield handle
        except zstd.ZstdError as error:
            raise ValueError(f"zstd failed to decompress {path}: {error}") from error
        return
    if shutil.which("zstd") is None:
        raise ValueError("compressed Codex history requires compression.zstd or the zstd executable")
    error = tempfile.TemporaryFile()
    process = subprocess.Popen(
        ["zstd", "--decompress", "--stdout", "--quiet", "--", str(path)],
        stdout=subprocess.PIPE, stderr=error)
    reader = _PipeReader(process, error)
    try:
        yield reader
    except BaseException:
        reader.close()
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        if not reader.stream.closed:
            reader.close()
        if reader.exhausted:
            # EOF means the decompressor has no more output; give it a moment to exit.
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        elif process.poll() is None:
            # The consumer stopped before EOF: this is an intentional prefix read.
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if reader.exhausted and process.returncode not in (0, None):
            error.seek(0)
            detail = error.read().decode("utf-8", "replace").strip()
            raise ValueError(f"zstd failed to decompress {path}: {detail or process.returncode}")
        error.close()


def json_lines(path: Path):
    with open_transcript_bytes(path) as stream:
        for number, raw in enumerate(stream, 1):
            line = raw.decode("utf-8", "replace")
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                print(f"dont-forget: malformed JSON at {path}:{number}; skipped", file=sys.stderr)
                continue
            if isinstance(entry, dict):
                yield entry


# ---------------------------------------------------------------------------
# Shared conversation extraction
# ---------------------------------------------------------------------------

@contextmanager
def read_db(path: Path | None = None):
    connection = sqlite3.connect((path or opencode_db()).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def text_parts(parts) -> str:
    if isinstance(parts, str):
        return parts
    lines = []
    for part in parts if isinstance(parts, list) else []:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in ("text", "input_text", "output_text"):
            if not part.get("synthetic") and not part.get("ignored"):
                lines.append(part.get("text", ""))
        elif kind in ("tool", "tool_use", "tool-call", "toolCall"):
            state = part.get("state") or {}
            args = part.get("input", part.get("args", part.get("arguments", state.get("input", {}))))
            lines.append(tool_line(part.get("tool", part.get("name", part.get("toolName", "tool"))), args))
    return "\n".join(lines)


def tool_line(name: str, arguments) -> str:
    args = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return f"<used {name}: {args[:300]}>"


def add_turn(out: list, role: str, text: str) -> None:
    if role in ("user", "assistant", "compaction") and text.strip():
        out.append({"role": role, "text": text.strip()})


# ---------------------------------------------------------------------------
# OMP
# ---------------------------------------------------------------------------

ROLE_MARKER = "dont-forget.session-role"


def _omp_header(entry: dict) -> bool:
    return entry.get("type") == "session"


def omp_session(path: Path, metadata_only: bool = False, *,
                leaf_id: str | None = None) -> dict:
    """Read the chosen root-to-leaf conversation, not a neighbouring branch.

    The file has no persisted leaf: loading rebuilds it from the last physical entry, so
    that is the default and it is labelled `last-persisted`. A live audit passes the leaf
    the host holds right now; `""` selects the empty state before the first entry.
    """
    header = None
    entries: dict[str, dict] = {}
    last = None
    roles: set[str] = set()
    for entry in json_lines(path):
        kind = entry.get("type")
        if kind == "title":
            continue
        if kind == "session":
            if header is not None:
                raise ValueError(f"multiple OMP session headers: {path}")
            header = entry
            if not isinstance(header.get("id"), str) or not header["id"]:
                raise ValueError(f"OMP session header has no id: {path}")
            continue
        if header is None:
            raise ValueError(f"not an OMP transcript: {path}")
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError(f"OMP entry has no id: {path}")
        if entry_id in entries:
            raise ValueError(f"duplicate OMP entry id {entry_id}: {path}")
        entries[entry_id] = entry
        last = entry_id
        if entry.get("type") == "custom" and entry.get("customType") == ROLE_MARKER:
            data = entry.get("data")
            if isinstance(data, dict) and data.get("session_id") == header["id"]:
                kind_value = data.get("kind")
                if kind_value in ("main", "sub"):
                    roles.add(kind_value)
    if header is None:
        raise ValueError(f"not an OMP transcript: {path}")

    if leaf_id is None:
        selected, branch_source = last, "last-persisted"
    elif leaf_id == "":
        selected, branch_source = None, "explicit"
    else:
        if leaf_id not in entries:
            raise ValueError(f"OMP leaf {leaf_id} does not exist: {path}")
        selected, branch_source = leaf_id, "explicit"

    branch, visited = [], set()
    while selected is not None:
        if selected in visited:
            raise ValueError(f"cycle in OMP conversation at {selected}: {path}")
        if selected not in entries:
            raise ValueError(f"missing OMP parent {selected}: {path}")
        visited.add(selected)
        entry = entries[selected]
        branch.append(entry)
        selected = entry.get("parentId")
    turns = []
    for entry in reversed(branch):
        kind = entry.get("type")
        if kind == "message":
            message = entry.get("message") or {}
            if isinstance(message, dict):
                add_turn(turns, message.get("role"), text_parts(message.get("content")))
        elif kind in ("compaction", "branch_summary"):
            add_turn(turns, "compaction", entry.get("summary") or "--- context compacted ---")
        elif kind == "reset_boundary":
            add_turn(turns, "compaction", "--- session cleared here ---")

    role_conflict = len(roles) > 1
    role = roles.pop() if len(roles) == 1 else None
    entrypoint = {"main": "cli", "sub": "subagent"}.get(role, "unknown") if role else "unknown"
    session = {"harness": "omp", "session_id": header["id"], "cwd": header.get("cwd", ""),
               "entrypoint": entrypoint, "role": role, "role_conflict": role_conflict,
               "turns": turns, "path": str(path),
               "leaf_id": last if leaf_id is None else (None if leaf_id == "" else leaf_id),
               "branch_source": branch_source}
    if metadata_only:
        session["turns"] = []
    return session


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------

def _codex_warn(message: str) -> None:
    print(f"dont-forget: {message}", file=sys.stderr)


def _parse_rollout_name(name: str) -> tuple[str, str] | None:
    core = name
    if core.endswith(".zst"):
        core = core[:-4]
    if not core.endswith(".jsonl") or not core.startswith("rollout-"):
        return None
    core = core[len("rollout-"):-len(".jsonl")]
    if len(core) < 20 or core[19] != "-":
        return None
    ids = core[20:]
    if not ids:
        return None
    thread, _, rollout = ids.partition("_")
    if not thread:
        return None
    return thread, rollout or thread


def parse_rollout_name(name: str) -> tuple[str, str] | None:
    """(stable thread id, immutable rollout id) from a canonical rollout filename."""
    return _parse_rollout_name(name)


def _codex_candidates() -> list[dict]:
    """Every rollout file under sessions and archived_sessions, with parsed identities."""
    found = []
    seen = set()
    for root in (codex_root(), codex_archived_root()):
        if not root.is_dir():
            continue
        for path in root.rglob("rollout-*"):
            if path.suffix not in (".jsonl", ".zst") or not path.is_file():
                continue
            identity = _parse_rollout_name(path.name)
            if identity is None:
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            found.append({"path": path, "thread": identity[0], "rollout": identity[1],
                          "plain": not path.name.endswith(".zst")})
    return found


def _one_representation(candidates: list[dict]) -> Path | None:
    """Plain and `.jsonl.zst` of one basename are one selected source; plain wins."""
    by_base: dict[str, dict] = {}
    for candidate in sorted(candidates, key=lambda item: (item["plain"], str(item["path"])), reverse=True):
        name = candidate["path"].name
        base = str(candidate["path"].with_name(name[:-4] if name.endswith(".zst") else name))
        by_base.setdefault(base, candidate)
    if len(by_base) == 1:
        return next(iter(by_base.values()))["path"]
    return None


def _existing_rollout(path: Path) -> Path | None:
    if path.is_file():
        return path
    compressed = path.with_name(path.name + ".zst")
    if compressed.is_file():
        return compressed
    return None


def resolve_codex_rollout(rollout_id: str) -> Path:
    """The immutable rollout file named by `rollout_id`, plain or compressed.

    A history base names a rollout, not a stable thread: after a revert the filename
    carries a distinct rollout id, and matching on the thread would pick the old file.
    """
    matches = [item for item in _codex_candidates() if item["rollout"] == rollout_id]
    path = _one_representation(matches)
    if path is None:
        if matches:
            raise ValueError(f"multiple Codex rollouts named {rollout_id}: pass --path")
        raise ValueError(f"Codex rollout {rollout_id} was not found under {codex_home()}")
    return path


def _codex_header(path: Path) -> dict:
    """The first record: its payload and its own top-level ordinal."""
    for entry in json_lines(path):
        if entry.get("type") != "session_meta" or not isinstance(entry.get("payload"), dict):
            raise ValueError(f"Codex rollout does not start with session metadata: {path}")
        return entry
    raise ValueError(f"empty Codex rollout: {path}")


def _codex_header_id(path: Path) -> str:
    return _codex_header(path)["payload"].get("id")


def resolve_codex_thread(thread_id: str, *, state_db: Path | None = None) -> Path:
    """The rollout Codex currently uses for this thread.

    The state database is authoritative after a revert: it may name a different physical
    rollout than a filename scan would. Only a missing database or a missing thread row
    falls back to the filesystem; permission or schema errors are reported, not hidden by
    switching to a stale file.
    """
    db = state_db or codex_state_db()
    fallback_reason = None
    if db.is_file():
        try:
            connection = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                row = connection.execute(
                    "SELECT rollout_path, history_mode FROM threads WHERE id=?", (thread_id,)).fetchone()
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise ValueError(f"cannot read Codex state database {db}: {error}") from error
        if row is not None:
            selected = _existing_rollout(Path(row[0]))
            if selected is not None:
                if _codex_header_id(selected) != thread_id:
                    raise ValueError(f"Codex rollout belongs to another thread: {selected}")
                return selected
            if (row[1] or "legacy") == "paginated":
                raise ValueError(
                    f"the selected paginated Codex rollout for {thread_id} is missing: {row[0]}")
            fallback_reason = f"the selected rollout {row[0]} is gone"
    else:
        fallback_reason = f"no Codex state database at {db}"
    matches = [item for item in _codex_candidates() if item["thread"] == thread_id]
    path = _one_representation(matches)
    if path is None:
        if matches:
            raise ValueError(
                f"several Codex rollouts for thread {thread_id} ({fallback_reason}); pass --path")
        raise ValueError(f"Codex thread {thread_id} was not found under {codex_home()}")
    if _codex_header_id(path) != thread_id:
        raise ValueError(f"Codex rollout belongs to another thread: {path}")
    return path


def iter_codex_records(path: Path):
    """Decoded records of one physical rollout, in file order."""
    yield from json_lines(path)


def _codex_bytes(path: Path):
    with open_transcript_bytes(path) as stream:
        for line in stream:
            yield line


def _codex_byte_prefix(path: Path, limit: int) -> tuple[list[tuple[int, dict]], int]:
    """Records fully inside the first `limit` bytes, and the bytes actually consumed.

    A prefix must end on a JSONL boundary; a byte offset in the middle of a record is a
    corrupt lineage pointer, not something to round to the nearest record.
    """
    records, offset = [], 0
    for raw in _codex_bytes(path):
        if offset + len(raw) > limit:
            break
        if not raw.strip():
            offset += len(raw)
            continue
        try:
            entry = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as error:
            raise ValueError(f"malformed Codex record at {path}:{offset}: {error}") from error
        if not isinstance(entry, dict):
            raise ValueError(f"Codex record is not an object at {path}:{offset}")
        records.append((offset, entry))
        offset += len(raw)
    if offset != limit:
        raise ValueError(f"Codex history cutoff {limit} is not on a record boundary in {path}")
    return records, offset


def _codex_segments(path: Path) -> list[dict]:
    """Root-to-child segments of a paginated rollout, each bounded by its child's base."""
    segments, seen = [], set()
    current, cutoff = path, None
    while True:
        record = _codex_header(current)
        header = record["payload"]
        rollout = _parse_rollout_name(current.name)
        rollout_id = rollout[1] if rollout else header.get("id")
        if rollout_id in seen:
            raise ValueError(f"cycle in Codex history lineage at {rollout_id}: {path}")
        seen.add(rollout_id)
        if (header.get("history_mode") or "legacy") != "paginated":
            raise ValueError(f"Codex history base points at a non-paginated rollout: {current}")
        segments.append({"path": current, "record": record, "header": header, "cutoff": cutoff})
        base = header.get("history_base")
        if not base:
            break
        if not isinstance(base, dict):
            raise ValueError(f"malformed Codex history_base in {current}")
        thread, ordinal, byte_offset = base.get("thread_id"), base.get("end_ordinal_exclusive"), base.get("end_byte_offset")
        if not isinstance(thread, str) or not thread or not isinstance(ordinal, int) or ordinal <= 0 \
                or not isinstance(byte_offset, int) or byte_offset <= 0:
            raise ValueError(f"malformed Codex history_base in {current}: {base!r}")
        current, cutoff = resolve_codex_rollout(thread), base
    segments.reverse()
    return segments


def _codex_segment_entries(segment: dict) -> list[dict]:
    """Records of one segment inside its child's cutoff, with ordinals verified.

    Byte bound and ordinal bound must agree: a prefix that ends mid-record or a cutoff
    ordinal that names a different record is a corrupt lineage pointer, not an
    approximation to smooth over.
    """
    path, record = segment["path"], segment["record"]
    header, cutoff = segment["header"], segment["cutoff"]
    base = header.get("history_base")
    expected_header = base["end_ordinal_exclusive"] if base else 0
    if record.get("ordinal") != expected_header:
        raise ValueError(
            f"Codex segment {path} header ordinal {record.get('ordinal')!r} "
            f"does not match history base {expected_header}")
    if cutoff is None:
        records = _codex_byte_prefix_all(path)
    else:
        records, _ = _codex_byte_prefix(path, cutoff["end_byte_offset"])
    entries, next_ordinal = [], expected_header + 1
    for _, entry in records:
        ordinal = entry.get("ordinal")
        if ordinal == expected_header:
            continue
        if not isinstance(ordinal, int) or ordinal != next_ordinal:
            raise ValueError(
                f"Codex records in {path} are out of order or missing at ordinal "
                f"{next_ordinal} (got {ordinal!r})")
        next_ordinal += 1
        entries.append(entry)
    if cutoff is not None and next_ordinal != cutoff["end_ordinal_exclusive"]:
        raise ValueError(
            f"Codex history cutoff ordinal {cutoff['end_ordinal_exclusive']} does not match "
            f"the records in {path}")
    return entries


def _codex_turns_from(records: list[dict], out: list) -> None:
    for entry in records:
        kind, payload = entry.get("type"), entry.get("payload")
        if not isinstance(payload, dict):
            continue
        if kind == "session_meta":
            continue
        if kind == "response_item":
            metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
            if metadata.get("compaction_output") is True:
                # A provider compaction checkpoint is not an ordinary assistant reply.
                continue
            item_type = payload.get("type")
            if item_type == "message":
                add_turn(out, payload.get("role"), text_parts(payload.get("content")))
            elif item_type in ("function_call", "custom_tool_call"):
                add_turn(out, "assistant",
                         tool_line(payload.get("name", "tool"),
                                   payload.get("arguments", payload.get("input", ""))))
        elif kind == "compacted":
            message = payload.get("message")
            add_turn(out, "compaction",
                     message if isinstance(message, str) and message.strip()
                     else "--- context compacted (no plaintext summary) ---")
            # `replacement_history` restates the original conversation: adding it here
            # would duplicate every turn that the file already records.
        # event_msg mirrors response_item messages: reading both duplicates turns.


def codex_session(path: Path, metadata_only: bool = False) -> dict:
    """A Codex rollout, following `history_base` into its bounded ancestor prefixes."""
    record = _codex_header(path)
    header = record["payload"]
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError(f"Codex session metadata has no thread id: {path}")
    history_mode = header.get("history_mode") or "legacy"
    base = header.get("history_base")
    if history_mode == "legacy" and base:
        raise ValueError(f"legacy Codex rollout carries a history_base: {path}")
    if history_mode not in ("legacy", "paginated"):
        raise ValueError(f"unsupported Codex history mode {history_mode!r}: {path}")
    source = header.get("source")
    if isinstance(source, str):
        entrypoint = source
    elif isinstance(source, dict):
        entrypoint = "subagent"
    else:
        entrypoint = ""
    session = {"harness": "codex", "session_id": session_id, "cwd": header.get("cwd", ""),
               "entrypoint": entrypoint, "turns": [], "path": str(path),
               "history_mode": history_mode, "history_base": base,
               "source": source, "parent_thread_id": header.get("parent_thread_id")}
    if metadata_only:
        return session

    if history_mode == "legacy":
        _codex_turns_from(list(iter_codex_records(path)), session["turns"])
        return session

    for segment in _codex_segments(path):
        _codex_turns_from(_codex_segment_entries(segment), session["turns"])
    return session


def _codex_byte_prefix_all(path: Path) -> list[tuple[int, dict]]:
    records, offset = [], 0
    for raw in _codex_bytes(path):
        if not raw.strip():
            offset += len(raw)
            continue
        try:
            entry = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as error:
            raise ValueError(f"malformed Codex record at {path}:{offset}: {error}") from error
        if not isinstance(entry, dict):
            raise ValueError(f"Codex record is not an object at {path}:{offset}")
        records.append((offset, entry))
        offset += len(raw)
    return records


# ---------------------------------------------------------------------------
# OpenCode
# ---------------------------------------------------------------------------

def opencode_export(data: dict, source: str) -> dict:
    info = data.get("info") or {}
    location = info.get("location") if isinstance(info.get("location"), dict) else {}
    directory = info.get("directory") or location.get("directory") or ""
    parent = info.get("parentID") or info.get("parent_id")
    session = {"harness": "opencode", "session_id": info.get("id", source),
               "cwd": directory, "entrypoint": "subagent" if parent else "cli",
               "turns": [], "path": source}
    for message in data.get("messages", []):
        meta = message.get("info") or {}
        role = meta.get("role") or message.get("type")
        body = message.get("data") or message
        if role == "compaction":
            add_turn(session["turns"], "compaction",
                     body.get("summary") or body.get("message") or "--- context compacted ---")
        elif role in ("user", "assistant"):
            parts = message.get("parts", body.get("content", body.get("text", "")))
            add_turn(session["turns"], role, text_parts(parts))
    return session


def opencode_session(session_id: str, db_path: Path | None = None) -> dict:
    with read_db(db_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, messages in (("session_v2", "session_message"), ("session", "message")):
            if table not in tables:
                continue
            info = connection.execute(f"SELECT * FROM {table} WHERE id=?", (session_id,)).fetchone()
            if info is None:
                continue
            exported = {"info": {"id": session_id, "directory": info["directory"],
                                 "parentID": info["parent_id"]}, "messages": []}
            if table == "session_v2":
                parts_by_message: dict[str, list] = {}
                if "session_part" in tables:
                    for part in connection.execute(
                            "SELECT message_id, ordinal, type, data FROM session_part "
                            "WHERE session_id=? ORDER BY message_id, ordinal", (session_id,)):
                        payload = json.loads(part["data"])
                        if not isinstance(payload, dict):
                            raise ValueError(f"OpenCode session part is not an object: {session_id}")
                        payload = {**payload, "type": part["type"]}
                        parts_by_message.setdefault(part["message_id"], []).append(payload)
                for row in connection.execute(
                        "SELECT id, type, data FROM session_message WHERE session_id=? ORDER BY seq",
                        (session_id,)):
                    payload = json.loads(row["data"])
                    if not isinstance(payload, dict):
                        raise ValueError(f"OpenCode message is not an object: {session_id}")
                    parts = parts_by_message.get(row["id"])
                    if row["type"] == "assistant" and parts:
                        # Normalized parts replace the inline copy; both would repeat text.
                        payload = {**payload, "content": parts}
                    exported["messages"].append({"type": row["type"], "data": payload})
            else:
                rows = connection.execute(
                    "SELECT id,data FROM message WHERE session_id=? ORDER BY time_created,id",
                    (session_id,))
                for row in rows:
                    parts = [json.loads(p[0]) for p in connection.execute(
                        "SELECT data FROM part WHERE message_id=? ORDER BY time_created,id", (row["id"],))]
                    exported["messages"].append({"info": json.loads(row["data"]), "parts": parts})
            return opencode_export(exported, f"opencode://{session_id}")
    raise ValueError(f"OpenCode session not found: {session_id}")


def opencode_sessions(cutoff: float, before: float, db_path: Path | None = None) -> list[dict]:
    path = db_path or opencode_db()
    if not path.is_file():
        return []
    found = {}
    with read_db(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables.intersection(("session", "session_v2")):
            raise ValueError(f"unsupported OpenCode database schema: {path}")
        # A migrated id belongs to v2 even if its old v1 copy still exists.
        migrated = {row[0] for row in connection.execute("SELECT id FROM session_v2")} if "session_v2" in tables else set()
        for table in ("session_v2", "session"):
            if table not in tables:
                continue
            for row in connection.execute(
                    f"SELECT id,directory,parent_id,time_updated FROM {table} "
                    "WHERE time_updated>=? AND time_updated<=?", (cutoff * 1000, before * 1000)):
                if table == "session" and row["id"] in migrated:
                    continue
                if not row["parent_id"] and row["id"] not in found:
                    found[row["id"]] = {"session_id": row["id"], "cwd": row["directory"],
                                        "mtime": row["time_updated"] / 1000,
                                        "path": f"opencode://{row['id']}"}
    return list(found.values())


# ---------------------------------------------------------------------------
# Devin
# ---------------------------------------------------------------------------

def devin_root() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "devin/cli"


def devin_state_db() -> Path:
    """Devin's live session store; CHISEL_SESSION_DB is set inside every Devin session."""
    configured = _env("CHISEL_SESSION_DB")
    return Path(configured).expanduser() if configured else devin_root() / "sessions.db"


def devin_transcripts() -> Path:
    """ATIF transcript exports, one JSON per ended session."""
    return devin_root() / "transcripts"


def session_map_path(state: Path | None = None) -> Path:
    root = state or Path(os.environ.get("DONT_FORGET_HOME") or Path.home() / ".dont-forget")
    return root / "session-map.json"


def record_session_map(harness: str, session_id: str, cwd: str,
                       state: Path | None = None) -> None:
    """Remember which session id a host started in a directory.

    A hook learns its session id from stdin; the transcript reader later runs in a
    process that has no such field, so the hook writes it down keyed by host and cwd.
    Hosts with a native id variable never need this — it exists for Devin, which
    advertises no session env of its own.
    """
    if not session_id or not isinstance(session_id, str):
        return
    path = session_map_path(state)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data[f"{harness}:{cwd}"] = {"session_id": session_id, "harness": harness,
                               "cwd": cwd, "ts": time.time()}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".session-map-",
                                         delete=False, mode="w", encoding="utf-8") as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(data, ensure_ascii=False))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def session_map_lookup(harness: str, cwd: str, state: Path | None = None) -> str | None:
    try:
        data = json.loads(session_map_path(state).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entry = data.get(f"{harness}:{cwd}") if isinstance(data, dict) else None
    if isinstance(entry, dict) and isinstance(entry.get("session_id"), str) \
            and entry["session_id"]:
        return entry["session_id"]
    return None


def _devin_identity() -> str | None:
    """The running Devin session's id, from the hook's map or its own store's newest row."""
    mapped = session_map_lookup("devin", str(Path.cwd().resolve()))
    if mapped:
        return mapped
    cwd = str(Path.cwd().resolve())
    try:
        rows = devin_sessions(time.time() - 90 * 86400, time.time())
    except (OSError, sqlite3.Error, ValueError):
        return None
    for row in rows:  # newest first
        if row["cwd"] == cwd:
            print("dont-forget: Devin session id came from the newest row for this"
                  " directory, not the hook's session map", file=sys.stderr)
            return row["session_id"]
    return None


def _devin_tool_calls(calls) -> list[str]:
    """Bounded `<used name: args>` traces out of Devin's assistant tool_calls.

    The live store writes tool_calls as a Python repr rather than JSON, so both
    parsers are tried; a call that decodes as neither is dropped, not guessed at.
    """
    if isinstance(calls, str):
        try:
            calls = json.loads(calls)
        except json.JSONDecodeError:
            try:
                calls = ast.literal_eval(calls)
            except (ValueError, SyntaxError, MemoryError):
                calls = []
    lines = []
    for call in calls if isinstance(calls, list) else []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else call
        name = function.get("name") or call.get("name") or "tool"
        arguments = function.get("arguments", call.get("arguments", ""))
        lines.append(tool_line(name, arguments))
    return lines


def _devin_message_text(message: dict) -> str:
    """What one OpenAI-shaped chat row says, plus its tool-call traces.

    `thinking` and `metadata` never leave the store: reasoning is private to the
    session, and a tool result lives on its own `tool` row which is skipped too.
    """
    out = []
    content = message.get("content")
    if isinstance(content, str):
        if content.strip():
            out.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("text", "input_text", "output_text"):
                out.append(part.get("text", ""))
            elif isinstance(part, str):
                out.append(part)
    out.extend(_devin_tool_calls(message.get("tool_calls")))
    return "\n".join(out)


def devin_export(data: dict, source: str) -> dict:
    """An ATIF transcript export (`transcripts/<id>.json`) as a session dict."""
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
        raise ValueError(f"not a Devin ATIF transcript: {source}")
    session_id = data.get("session_id") or Path(str(source)).stem
    agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    session = {"harness": "devin", "session_id": session_id,
               "cwd": agent.get("cwd") or agent.get("working_directory") or "",
               "entrypoint": "cli", "turns": [], "path": str(source)}
    for step in data["steps"]:
        if not isinstance(step, dict):
            continue
        source_kind = step.get("source")
        message = step.get("message") if isinstance(step.get("message"), str) else ""
        if source_kind == "user":
            add_turn(session["turns"], "user", message)
        elif source_kind == "agent":
            parts = [message] if message.strip() else []
            parts.extend(_devin_tool_calls(step.get("tool_calls")))
            add_turn(session["turns"], "assistant", "\n".join(parts))
        # system prompts, observations and compaction machinery stay in the file.
    return session


def devin_session(source, *, db_path: Path | None = None) -> dict:
    """A Devin session: `devin://<id>`, a bare session id, or an ATIF file path.

    The live store (`sessions.db` + `message_nodes`) is authoritative: it holds the
    running session before any transcript is exported. A session id that has no row
    falls back to its transcript export; a `.json` path is read as an export.
    """
    text = str(source)
    if text.startswith("devin://"):
        text = text[len("devin://"):]
    candidate = Path(text)
    if candidate.is_file() and candidate.suffix == ".json":
        try:
            return devin_export(json.loads(candidate.read_text(encoding="utf-8")), text)
        except json.JSONDecodeError as error:
            raise ValueError(f"malformed Devin transcript {candidate}: {error}") from error
    db = db_path or devin_state_db()
    if not db.is_file():
        exported = devin_transcripts() / f"{text}.json"
        if exported.is_file():
            return devin_export(json.loads(exported.read_text(encoding="utf-8")),
                                str(exported))
        raise ValueError(f"no Devin session store at {db} and no transcript for {text}")
    with read_db(db) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"sessions", "message_nodes"} <= tables:
            raise ValueError(f"unsupported Devin session store schema: {db}")
        row = connection.execute(
            "SELECT working_directory, main_chain_id, hidden FROM sessions WHERE id=?",
            (text,)).fetchone()
        if row is None:
            exported = devin_transcripts() / f"{text}.json"
            if exported.is_file():
                return devin_export(json.loads(exported.read_text(encoding="utf-8")),
                                    str(exported))
            raise ValueError(f"Devin session not found: {text}")
        nodes = connection.execute(
            "SELECT node_id, parent_node_id, chat_message FROM message_nodes "
            "WHERE session_id=?", (text,)).fetchall()
    by_id: dict[int, tuple] = {}
    for node_id, parent_id, chat in nodes:
        by_id[node_id] = (parent_id, chat)
    leaf = row["main_chain_id"]
    if leaf not in by_id:
        leaf = max(by_id) if by_id else None
    chain, visited = [], set()
    while leaf is not None:
        if leaf in visited:
            raise ValueError(f"cycle in Devin conversation at node {leaf}: {text}")
        if leaf not in by_id:
            raise ValueError(f"missing Devin parent node {leaf}: {text}")
        visited.add(leaf)
        parent, chat = by_id[leaf]
        chain.append(chat)
        leaf = parent
    session = {"harness": "devin", "session_id": text,
               "cwd": row["working_directory"] or "", "entrypoint": "cli",
               "turns": [], "path": f"devin://{text}", "chain_head": row["main_chain_id"]}
    for chat in reversed(chain):
        try:
            message = json.loads(chat) if isinstance(chat, str) else chat
        except json.JSONDecodeError:
            print(f"dont-forget: malformed Devin message row skipped: {text}",
                  file=sys.stderr)
            continue
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role in ("user", "assistant"):
            add_turn(session["turns"], role, _devin_message_text(message))
        # system rows are prompt machinery; tool rows are results — neither is said text.
    return session


def devin_sessions(cutoff: float, before: float, db_path: Path | None = None) -> list[dict]:
    """Interactive Devin sessions active inside the window, newest first."""
    db = db_path or devin_state_db()
    if not db.is_file():
        return []
    found = []
    with read_db(db) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "sessions" not in tables:
            raise ValueError(f"unsupported Devin session store schema: {db}")
        for row in connection.execute(
                "SELECT id, working_directory, last_activity_at, hidden FROM sessions "
                "WHERE last_activity_at>=? AND last_activity_at<=?", (cutoff, before)):
            found.append({"session_id": row["id"], "cwd": row["working_directory"] or "",
                          "mtime": row["last_activity_at"], "hidden": bool(row["hidden"]),
                          "path": f"devin://{row['id']}"})
    found.sort(key=lambda item: item["mtime"], reverse=True)
    return found


def omp_active(leaf_id: str | None = None) -> dict:
    """The running OMP session, honouring both of its native fields.

    PI_SESSION_FILE names the file itself and beats an id lookup; an empty value or a
    missing file is a failure, not a hint to try neighbours. When both fields are set
    they must name the same session — a mismatch is exactly the inherited-variable
    case this layer refuses to guess on.
    """
    file_var = os.environ.get("PI_SESSION_FILE")
    session_id = (os.environ.get("PI_SESSION_ID") or "").strip()
    if file_var is not None:
        if not file_var.strip():
            raise ValueError("PI_SESSION_FILE is set but empty")
        path = Path(file_var).expanduser()
        if not path.is_file():
            raise ValueError(f"PI_SESSION_FILE does not exist: {path}")
        header = omp_session(path, metadata_only=True)
        if session_id and header["session_id"] != session_id:
            raise ValueError(f"PI_SESSION_ID {session_id} does not match {path} "
                             f"(session {header['session_id']})")
        return omp_session(path, leaf_id=leaf_id)
    if not session_id:
        raise ValueError("the omp session advertises no id; pass --session")
    return omp_session(omp_transcript_path(session_id), leaf_id=leaf_id)


def omp_transcript_path(session_id: str) -> Path:
    """The OMP file whose header names this id — the filename only embeds a prefix."""
    root = omp_root()
    try:
        paths = sorted(root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime,
                       reverse=True)
    except OSError as error:
        raise ValueError(f"cannot list OMP sessions under {root}: {error}") from error
    scanned = 0
    for path in paths:
        for entry in json_lines(path):
            kind = entry.get("type")
            if kind == "title":
                continue
            if kind != "session":
                break
            scanned += 1
            if entry.get("id") == session_id:
                return path
            break
    raise ValueError(f"OMP session {session_id} was not found under {root} "
                     f"({scanned} sessions scanned)")


# ---------------------------------------------------------------------------
# Facts for discovery
# ---------------------------------------------------------------------------

def user_texts(session: dict) -> list[str]:
    return [turn["text"] for turn in session["turns"] if turn["role"] == "user"
            and not turn["text"].startswith(("<", "# AGENTS.md instructions"))]


def session_facts(session: dict) -> dict:
    users = user_texts(session)
    return {"session_id": f"{session['harness']}:{session['session_id']}", "harness": session["harness"],
            "cwd": session["cwd"], "entrypoint": session["entrypoint"], "closed": False,
            "role": session.get("role"), "role_conflict": session.get("role_conflict", False),
            "user_turns": len(users), "first_message": " ".join(users[0].split())[:100] if users else ""}
