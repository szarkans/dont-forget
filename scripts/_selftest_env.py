"""Isolate every self-test before modules capture the plugin's paths."""

import atexit
import os
import tempfile

_home = tempfile.TemporaryDirectory(prefix="dont-forget-selftest-home-")
atexit.register(_home.cleanup)
os.environ["DONT_FORGET_HOME"] = _home.name
os.environ.pop("DONT_FORGET_EMBED_STUB", None)
os.environ.pop("DONT_FORGET_TEST", None)
