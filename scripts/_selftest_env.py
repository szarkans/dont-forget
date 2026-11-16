"""Isolate every self-test before modules capture the plugin's paths."""

import atexit
import os
import tempfile

_home = tempfile.TemporaryDirectory(prefix="dont-forget-selftest-home-")
atexit.register(_home.cleanup)
os.environ["DONT_FORGET_HOME"] = _home.name
os.environ["HOME"] = _home.name
os.environ["CODEX_HOME"] = _home.name + "/codex"
os.environ["XDG_DATA_HOME"] = _home.name + "/data"
os.environ["XDG_CONFIG_HOME"] = _home.name + "/config"
os.environ["OPENCODE_CONFIG_DIR"] = _home.name + "/config/opencode"
os.environ["PI_CONFIG_DIR"] = _home.name + "/.omp"
for _key in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "OPENCODE_SESSION_ID",
             "PI_SESSION_ID", "PI_SESSION_FILE", "OMP_PROFILE", "PI_PROFILE",
             "PI_CODING_AGENT_DIR", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CONFIG_DIR"):
    os.environ.pop(_key, None)
os.environ.pop("DONT_FORGET_EMBED_STUB", None)
os.environ.pop("DONT_FORGET_TEST", None)
