"""Optional semantic lane. This module deliberately uses only the standard library."""

from __future__ import annotations

import glob
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from common import HOME_DIR

WORKER = Path(__file__).with_name("semantic_worker.py")
VENV_PYTHON = HOME_DIR / "venv" / "bin" / "python"
# A resident server keeps Granite loaded, so a search stops paying model load time.
WARM_SOCKET = HOME_DIR / "embed.sock"
WARM_CONNECT_SECONDS = 0.2
WARM_READ_SECONDS = 90
# A small bound keeps an edit-triggered search responsive even on slower CPUs.
SEARCH_EMBED_LIMIT = 8
MODEL_ID = "ibm-granite/granite-embedding-311m-multilingual-r2"
MODEL_REVISION = "44399559930365213510b1ee2eb15ded83374f0e"
VECTOR_SUFFIX = f"vectors-{MODEL_ID.rsplit('/', 1)[-1]}-{MODEL_REVISION[:12]}.db"


def vector_db_path(db_path: Path) -> Path:
    return db_path.with_name(f"{db_path.name}.{VECTOR_SUFFIX}")


def code_identity() -> str:
    """Model plus the code mtimes, so a server left over from an update cannot answer."""
    mtimes = [Path(__file__).with_name(name).stat().st_mtime_ns
              for name in ("semantic.py", "semantic_worker.py", "common.py")]
    return ":".join([MODEL_ID, MODEL_REVISION, *(str(value) for value in mtimes)])


def stub_enabled() -> bool:
    """A test encoder requires an explicitly isolated home and a test opt-in."""
    home = os.environ.get("DONT_FORGET_HOME")
    return (os.environ.get("DONT_FORGET_EMBED_STUB") == "1"
            and os.environ.get("DONT_FORGET_TEST") == "1" and bool(home)
            and Path(home).resolve() != (Path.home() / ".dont-forget").resolve())


def _warm(request: dict) -> tuple[dict | None, str | None]:
    """Ask the resident server; (None, None) means nobody is listening."""
    client = None
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(WARM_CONNECT_SECONDS)
        client.connect(str(WARM_SOCKET))
    except OSError:
        # Missing, refused or stale socket file all mean "no server running".
        if client is not None:
            client.close()
        return None, None
    try:
        client.settimeout(WARM_READ_SECONDS)
        client.sendall((json.dumps(request) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(1 << 16)
            if not chunk:
                break
            data += chunk
        reply = json.loads(data)
        if not isinstance(reply, dict) or "semantic" not in reply:
            return None, "bad reply"
        return reply, None
    except (OSError, ValueError) as error:
        return None, type(error).__name__
    finally:
        client.close()


def _start_server(python: Path) -> None:
    """Detach the server; this call answers cold and does not wait for it to load."""
    try:
        HOME_DIR.mkdir(parents=True, exist_ok=True)
        with (HOME_DIR / "embed-server.log").open("ab") as log:
            subprocess.Popen([str(python), str(WORKER), "--serve", str(WARM_SOCKET)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=log, start_new_session=True)
    except OSError:
        pass


def run(db_path: Path, query: str | None = None, full: bool = False) -> dict:
    """Return ranked chunk IDs, or a short reason why semantic search is off."""
    old_files = []

    def finish(result: dict, worker: str = "cold") -> dict:
        result["worker"] = worker
        if old_files:
            result["old_vector_files"] = old_files
        return result

    try:
        old_files = sorted(path.name for path in db_path.parent.glob(
            f"{glob.escape(db_path.name)}.vectors-*.db") if path != vector_db_path(db_path))
        if not db_path.is_file():
            return finish({"semantic": "off: no index"})
        stub = stub_enabled()
        python = Path(sys.executable) if stub else VENV_PYTHON
        if not python.is_file():
            return finish({"semantic": "off: no venv"})
        if not WORKER.is_file():
            return finish({"semantic": "off: no worker"})
        request = {"db": str(db_path.resolve()),
                   "vectors_db": str(vector_db_path(db_path).resolve()),
                   "query": query, "full": full,
                   "limit": SEARCH_EMBED_LIMIT, "code": code_identity()}
        worker = "cold"
        if not full and os.environ.get("DONT_FORGET_WARM") != "0":
            reply, reason = _warm(request)
            if reply is not None and not str(reply["semantic"]).startswith("off:"):
                return finish(reply, "warm")
            if reply is not None:
                # The server answered, but with a refusal: retry one-shot and say why.
                reason = str(reply["semantic"]).removeprefix("off: ")[:120]
                if reason == "stale server":
                    # It is about to exit anyway: answer cold and start a fresh one.
                    reason = None
            if reason is None:
                _start_server(python)
            else:
                worker = f"cold: {reason}"
        done = subprocess.run([str(python), str(WORKER)], input=json.dumps(request),
                              text=True, capture_output=True, timeout=None if full else 90)
        if done.returncode:
            # The worker gives a short controlled message for missing model/deps.
            reason = done.stderr.strip().splitlines()[-1:] or ["worker failed"]
            return finish({"semantic": f"off: {reason[0][:120]}"}, worker)
        return finish(json.loads(done.stdout), worker)
    except subprocess.TimeoutExpired:
        return finish({"semantic": "off: timeout"})
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return finish({"semantic": f"off: {type(error).__name__}"})
