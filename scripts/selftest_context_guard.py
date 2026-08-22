#!/usr/bin/env python3
"""Self-check for the autocompact/rot Stop hook.

The hook cannot be observed firing from inside a session — hooks.json is read at
startup — so the live check is this: feed it a synthetic payload and a fixture
transcript, and assert on what it prints.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

guard = importlib.import_module("context-guard")

SCRIPT = Path(__file__).resolve().parent / "context-guard.py"


def transcript(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    return path


def assistant(total: int, model: str = "claude-opus-5", sidechain: bool = False) -> dict:
    return {"isSidechain": sidechain, "message": {
        "role": "assistant", "model": model,
        "usage": {"input_tokens": total, "output_tokens": 0,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}


def check_marks() -> None:
    """Marks are measured from the compact point, and clamped so a small window keeps them
    apart. Measuring from the window is the bug this hook exists to avoid: on a 600k
    window the old personal hook put its critical mark at 585k, past the 567k where
    compaction actually happens, so it could never fire."""
    named = dict(guard.marks(600_000, True))
    point = 600_000 - guard.COMPACT_RESERVE
    # The quarter clamp bites even here: a 567k run-up caps the warn margin at 141.75k.
    assert named["compact-warn"] == point - min(150_000, point // 4), named
    assert named["compact-critical"] == point - 50_000, named
    assert named["compact-critical"] < point, "critical mark must sit before the compaction"
    assert named["compact-warn"] < named["compact-critical"]

    small = dict(guard.marks(200_000, True))
    point_small = 200_000 - guard.COMPACT_RESERVE
    assert small["compact-warn"] == point_small - point_small // 4, small
    assert small["compact-critical"] == point_small - point_small // 10, small
    assert 0 < small["compact-warn"] < small["compact-critical"] < point_small

    # Rot marks are absolute and survive autocompact being switched off.
    off = dict(guard.marks(600_000, False))
    assert set(off) == {"rot-500000", "rot-900000"}, off
    levels = [level for _, level in guard.marks(600_000, True)]
    assert levels == sorted(levels), "marks must be ordered"
    assert all(level > 0 for level in levels)


def check_decide() -> None:
    """One message per stop, each mark spends once, everything re-arms after a compaction."""
    window = 600_000
    name, spent = guard.decide(430_000, window, [], True)
    assert name == "compact-warn", name
    # Crossing again says nothing new.
    quiet, spent = guard.decide(440_000, window, spent, True)
    assert quiet is None, quiet
    # Two marks crossed at once: the higher one speaks, the quieter is spent silently.
    name, spent = guard.decide(560_000, window, spent, True)
    assert name == "compact-critical", name
    assert "rot-500000" in spent, spent
    assert guard.decide(560_000, window, spent, True)[0] is None

    # A compaction drops usage; every mark re-arms, or the second compaction passes mute.
    name, spent = guard.decide(20_000, window, spent, True)
    assert name is None and spent == [], (name, spent)
    assert guard.decide(430_000, window, spent, True)[0] == "compact-warn"

    # With autocompact off only the rot scale can speak.
    name, _ = guard.decide(430_000, window, [], False)
    assert name is None, name
    assert guard.decide(520_000, window, [], False)[0] == "rot-500000"


def check_ceiling(tmp: Path) -> None:
    """The suffix decides, observation overrules, and ambiguity resolves downwards."""
    cwd = Path("/work/thing")
    state = tmp / "claude.json"

    state.write_text(json.dumps({"projects": {str(cwd): {"lastModelUsage": {
        "claude-opus-5[1m]": {}}}}}), encoding="utf-8")
    assert guard.model_ceiling(cwd, "claude-opus-5", 50_000, state) == 1_000_000

    # Both variants seen here: take the smaller, an over-guess would silence every mark.
    state.write_text(json.dumps({"projects": {str(cwd): {"lastModelUsage": {
        "claude-opus-5": {}, "claude-opus-5[1m]": {}}}}}), encoding="utf-8")
    assert guard.model_ceiling(cwd, "claude-opus-5", 50_000, state) == 200_000

    # Nothing known — a custom API model, an unfamiliar plan — starts low and climbs by
    # observation, because a session cannot hold more than its own window.
    missing = tmp / "absent.json"
    assert guard.model_ceiling(cwd, "whatever", 50_000, missing) == 200_000
    assert guard.model_ceiling(cwd, "whatever", 260_000, missing) == 500_000
    assert guard.model_ceiling(cwd, "whatever", 700_000, missing) == 1_000_000
    # The ladder never climbs past its top rung.
    assert guard.model_ceiling(cwd, "whatever", 5_000_000, missing) == 1_000_000


def check_window(tmp: Path) -> None:
    """A configured window is a ceiling request, not the threshold."""
    config = tmp / "cc"
    config.mkdir(parents=True, exist_ok=True)
    settings = config / "settings.json"
    settings.write_text(json.dumps({"autoCompactWindow": 600_000}), encoding="utf-8")
    state = tmp / "claude-1m.json"
    cwd = tmp / "project"
    cwd.mkdir(exist_ok=True)
    state.write_text(json.dumps({"projects": {str(cwd): {"lastModelUsage": {
        "claude-opus-5[1m]": {}}}}}), encoding="utf-8")

    os.environ["CLAUDE_CONFIG_DIR"] = str(config)
    os.environ.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW", None)
    try:
        assert guard.resolve_window(cwd, "claude-opus-5", 50_000, state) == 600_000
        # A 600k setting on a 200k model is still a 200k session.
        assert guard.resolve_window(cwd, "claude-haiku-4-5", 50_000, state) == 200_000
        # Out of the range Claude Code accepts, so ignored rather than trusted.
        settings.write_text(json.dumps({"autoCompactWindow": 9_000_000}), encoding="utf-8")
        assert guard.resolve_window(cwd, "claude-opus-5", 50_000, state) == 1_000_000
        os.environ["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "300000"
        assert guard.resolve_window(cwd, "claude-opus-5", 50_000, state) == 300_000
        assert guard.autocompact_enabled(cwd) is True
        settings.write_text(json.dumps({"autoCompactEnabled": False}), encoding="utf-8")
        assert guard.autocompact_enabled(cwd) is False
    finally:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        os.environ.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW", None)


def check_usage(tmp: Path) -> None:
    """Read from the end, and never count a sub-agent's turn as this session's."""
    path = transcript(tmp / "t.jsonl", [
        assistant(10_000), assistant(90_000), {"type": "system", "subtype": "compact_boundary"},
        assistant(400_000), assistant(999_999, sidechain=True), {"noise": True}])
    used, model = guard.last_usage(path)
    assert used == 400_000, used
    assert model == "claude-opus-5", model
    # An empty or unreadable transcript is "unknown", never zero.
    assert guard.last_usage(tmp / "nothing.jsonl") == (None, None)
    assert guard.last_usage(transcript(tmp / "e.jsonl", [{"a": 1}])) == (None, None)
    # A record split across a read block is still found: force many small blocks.
    big = transcript(tmp / "big.jsonl", [assistant(1234 + i) for i in range(400)])
    assert guard.last_usage(big)[0] == 1234 + 399


def run_hook(home: Path, config_dir: Path, payload: dict) -> str:
    environment = {**os.environ, "DONT_FORGET_HOME": str(home),
                   "CLAUDE_CONFIG_DIR": str(config_dir)}
    environment.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW", None)
    result = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload),
                            capture_output=True, text=True, env=environment)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def check_end_to_end(tmp: Path) -> None:
    """The whole path: real stdin, real files, real output."""
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    vault = tmp / "vault"
    vault.mkdir(exist_ok=True)
    config = home / "config.json"
    config.write_text(json.dumps({"vault": str(vault)}), encoding="utf-8")
    config_dir = tmp / "ccdir"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "settings.json").write_text(json.dumps({"autoCompactWindow": 600_000}),
                                              encoding="utf-8")
    cwd = tmp / "proj"
    cwd.mkdir(exist_ok=True)
    path = transcript(tmp / "live.jsonl", [assistant(430_000)])
    payload = {"session_id": "s1", "transcript_path": str(path), "cwd": str(cwd),
               "stop_hook_active": False}

    # 430k with nothing known about the model: the ladder lifts the ceiling to 500k, so
    # the compact point is 467k and the warn mark, clamped to a quarter, is crossed.
    first = json.loads(run_hook(home, config_dir, payload))
    assert first["decision"] == "block", first
    assert "/dont-forget:" in first["reason"], first
    assert "ignore this message" in first["reason"], first

    # Same session, same mark: silence. This is what stops a stop-loop.
    assert run_hook(home, config_dir, payload) == ""

    # stop_hook_active is Claude Code saying it is already replying to our block.
    assert run_hook(home, config_dir, {**payload, "stop_hook_active": True}) == ""

    # Opted out in config.
    config.write_text(json.dumps({"vault": str(vault), "autocompact_nudge": False}),
                      encoding="utf-8")
    assert run_hook(home, config_dir, {**payload, "session_id": "s2"}) == ""
    config.write_text(json.dumps({"vault": str(vault)}), encoding="utf-8")

    # No vault configured at all: nothing to save into, so nothing to say.
    config.unlink()
    assert run_hook(home, config_dir, {**payload, "session_id": "s3"}) == ""
    config.write_text(json.dumps({"vault": str(vault)}), encoding="utf-8")

    # A fresh session far from every mark stays quiet, and a missing transcript never throws.
    calm = transcript(tmp / "calm.jsonl", [assistant(20_000)])
    assert run_hook(home, config_dir, {**payload, "session_id": "s4",
                                       "transcript_path": str(calm)}) == ""
    assert run_hook(home, config_dir, {**payload, "session_id": "s5",
                                       "transcript_path": str(tmp / "gone.jsonl")}) == ""

    # The trail exists, so the estimated margins can become measured ones.
    assert (home / "context-guard.log").is_file()
    assert (home / "context-guard.json").is_file()

    # A long session must survive the state trim. The file keeps the last STATE_KEEP keys
    # by insertion order, and a session that keeps stopping only ever *updates* its key —
    # which does not move it — so without a re-insert it is evicted by newer sessions and
    # every mark it already spent speaks again.
    for index in range(guard.STATE_KEEP + 5):
        run_hook(home, config_dir, {**payload, "session_id": f"short-{index}",
                                    "transcript_path": str(calm)})
        assert run_hook(home, config_dir, payload) == "", "session s1 lost its record"


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        check_marks()
        check_decide()
        check_ceiling(tmp)
        check_window(tmp)
        check_usage(tmp)
        check_end_to_end(tmp)
    print("context-guard: 6 checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
