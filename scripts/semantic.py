"""Optional semantic lane. This module deliberately uses only the standard library."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from common import HOME_DIR

WORKER = Path(__file__).with_name("semantic_worker.py")
VENV_PYTHON = HOME_DIR / "venv" / "bin" / "python"
# A small bound keeps an edit-triggered search responsive even on slower CPUs.
SEARCH_EMBED_LIMIT = 8


def run(db_path: Path, query: str | None = None, full: bool = False) -> dict:
    """Return ranked chunk IDs, or a short reason why semantic search is off."""
    stub = os.environ.get("DONT_FORGET_EMBED_STUB") == "1"
    python = Path(sys.executable) if stub else VENV_PYTHON
    if not python.is_file():
        return {"semantic": "off: no venv"}
    if not WORKER.is_file():
        return {"semantic": "off: no worker"}
    request = {"db": str(db_path), "query": query, "full": full,
               "limit": SEARCH_EMBED_LIMIT}
    try:
        done = subprocess.run([str(python), str(WORKER)], input=json.dumps(request),
                              text=True, capture_output=True, timeout=None if full else 90)
        if done.returncode:
            # The worker gives a short controlled message for missing model/deps.
            reason = done.stderr.strip().splitlines()[-1:] or ["worker failed"]
            return {"semantic": f"off: {reason[0][:120]}"}
        return json.loads(done.stdout)
    except subprocess.TimeoutExpired:
        return {"semantic": "off: timeout"}
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return {"semantic": f"off: {type(error).__name__}"}
