#!/usr/bin/env python3
"""The resident embedding server: first call cold, next one warm, and an off switch."""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import _selftest_env  # noqa: F401
from index import build

SCRIPT = Path(__file__).with_name("search.py")


def run(home, vault, db, query, warm=True):
    env = {**os.environ, "DONT_FORGET_HOME": str(home),
           "DONT_FORGET_EMBED_STUB": "1", "DONT_FORGET_TEST": "1"}
    env.pop("DONT_FORGET_WARM", None)
    if not warm:
        env["DONT_FORGET_WARM"] = "0"
    done = subprocess.run([sys.executable, str(SCRIPT), query, "--vault", str(vault),
                           "--db", str(db)], env=env, capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def wait_for(path, seconds=10):
    """The socket file appears at bind; only a successful connect means it listens."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_UNIX) as probe:
                probe.settimeout(1)
                probe.connect(str(path))
            return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"server socket never came up: {path}")


def ask(path, payload):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(10)
        client.connect(str(path))
        client.sendall(payload)
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(1 << 16)
            if not chunk:
                break
            data += chunk
    return json.loads(data)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    home, vault, db = root / "home", root / "vault", root / "index.db"
    home.mkdir()
    vault.mkdir()
    (vault / "answer.md").write_text("# Answer\n\nптица хруст и соус\n")
    (vault / "other.md").write_text("# Other\n\nother content\n")
    build(vault, db)
    sock = home / "embed.sock"

    cold = run(home, vault, db, "bird crisp")
    assert cold["coverage"]["semantic_worker"] == "cold", cold["coverage"]
    assert cold["coverage"]["semantic"] == "ready", cold["coverage"]
    wait_for(sock)

    warm = run(home, vault, db, "bird crisp")
    assert warm["coverage"]["semantic_worker"] == "warm", warm["coverage"]
    assert warm["fragments"] == cold["fragments"], (cold["fragments"], warm["fragments"])

    # A garbled request is answered, not fatal: the server keeps serving.
    assert ask(sock, b"not json\n")["semantic"].startswith("off:"), "bad request should be refused"
    survived = run(home, vault, db, "bird crisp")
    assert survived["coverage"]["semantic_worker"] == "warm", survived["coverage"]

    # The off switch never touches the socket, so nothing is spawned.
    off_home = root / "home-off"
    off_home.mkdir()
    off = run(off_home, vault, db, "bird crisp", warm=False)
    assert off["coverage"]["semantic_worker"] == "cold", off["coverage"]
    assert off["fragments"] == cold["fragments"], off["fragments"]
    time.sleep(0.5)
    assert not (off_home / "embed.sock").exists() and not (off_home / "embed.sock.lock").exists(), \
        "DONT_FORGET_WARM=0 spawned a server"

    # A stale socket file left by a killed server is refused, then replaced.
    stale_home = root / "home-stale"
    stale_home.mkdir()
    (stale_home / "embed.sock").touch()
    stale = run(stale_home, vault, db, "bird crisp")
    assert stale["coverage"]["semantic_worker"] == "cold", stale["coverage"]
    wait_for(stale_home / "embed.sock")
    assert run(stale_home, vault, db, "bird crisp")["coverage"]["semantic_worker"] == "warm"

    # A plugin update must not leave the old code answering: the client answers this
    # call cold once, and the server built from the older files exits.
    code_home = root / "home-code"
    code_home.mkdir()
    assert run(code_home, vault, db, "bird crisp")["coverage"]["semantic_worker"] == "cold"
    wait_for(code_home / "embed.sock")
    old_pid = int((code_home / "embed.sock.lock").read_text())
    worker = Path(__file__).with_name("semantic_worker.py")
    before = worker.stat()
    try:
        os.utime(worker, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        updated = run(code_home, vault, db, "bird crisp")
    finally:
        os.utime(worker, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert updated["coverage"]["semantic_worker"] == "cold", updated["coverage"]
    assert updated["fragments"] == cold["fragments"], updated["fragments"]
    deadline = time.monotonic() + 10
    while alive(old_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(old_pid), "the server built from older code kept running"

    for name in ("home", "home-stale"):
        os.kill(int((root / name / "embed.sock.lock").read_text()), signal.SIGTERM)
    code_lock = code_home / "embed.sock.lock"
    if code_lock.exists():
        code_pid = int(code_lock.read_text())
        if code_pid != old_pid and alive(code_pid):
            os.kill(code_pid, signal.SIGTERM)
    for name in ("home", "home-stale", "home-code"):
        path = root / name / "embed.sock"
        deadline = time.monotonic() + 10
        while path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not path.exists(), f"{name} server left its socket behind"

print("ok")
