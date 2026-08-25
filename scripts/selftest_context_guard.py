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
    # The 80k default clears the quarter clamp (141k) here, so the margin is the flat 80k.
    assert named["compact-warn"] == point - guard.WARN_MARGIN, named
    assert named["compact-critical"] == point - 50_000, named
    assert named["compact-critical"] < point, "critical mark must sit before the compaction"
    assert named["compact-warn"] < named["compact-critical"]

    # The warn distance is configurable. A larger margin moves the mark earlier; a value at
    # or below the critical margin (or not a number) would invert the marks, so it is refused.
    wide = dict(guard.marks(600_000, True, 120_000))
    assert wide["compact-warn"] == point - 120_000, wide
    assert guard.warn_margin_from(120_000) == 120_000
    assert guard.warn_margin_from(50_000) == guard.WARN_MARGIN   # not above critical -> default
    assert guard.warn_margin_from(0) == guard.WARN_MARGIN
    assert guard.warn_margin_from("80000") == guard.WARN_MARGIN
    assert guard.warn_margin_from(True) == guard.WARN_MARGIN

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
    window = 600_000   # compact point 567k -> warn at 487k, critical at 517k, rot at 500k
    name, spent = guard.decide(490_000, window, [], True)
    assert name == "compact-warn", name
    # Crossing again says nothing new (still below the rot mark at 500k).
    quiet, spent = guard.decide(495_000, window, spent, True)
    assert quiet is None, quiet
    # Several marks crossed at once: the highest one speaks, the quieter are spent silently.
    name, spent = guard.decide(560_000, window, spent, True)
    assert name == "compact-critical", name
    assert "rot-500000" in spent, spent
    assert guard.decide(560_000, window, spent, True)[0] is None

    # A compaction drops usage; every mark re-arms, or the second compaction passes mute.
    name, spent = guard.decide(20_000, window, spent, True)
    assert name is None and spent == [], (name, spent)
    assert guard.decide(490_000, window, spent, True)[0] == "compact-warn"

    # With autocompact off only the rot scale can speak.
    name, _ = guard.decide(490_000, window, [], False)
    assert name is None, name
    assert guard.decide(520_000, window, [], False)[0] == "rot-500000"


def check_window(tmp: Path) -> None:
    """The window follows the configured autoCompactWindow, and falls back to the large
    default when nothing valid is set — no per-model guessing left."""
    config = tmp / "cc"
    config.mkdir(parents=True, exist_ok=True)
    settings = config / "settings.json"
    settings.write_text(json.dumps({"autoCompactWindow": 600_000}), encoding="utf-8")
    cwd = tmp / "project"
    cwd.mkdir(exist_ok=True)

    os.environ["CLAUDE_CONFIG_DIR"] = str(config)
    os.environ.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW", None)
    try:
        # A configured window is trusted as-is, whatever the model — this is the opus-4-8
        # fix: no model ceiling clamps 600k down to 200k any more.
        assert guard.resolve_window(cwd) == 600_000
        # Out of the range Claude Code accepts, so ignored, and the default takes over.
        settings.write_text(json.dumps({"autoCompactWindow": 9_000_000}), encoding="utf-8")
        assert guard.resolve_window(cwd) == guard.DEFAULT_WINDOW
        # Nothing configured at all: the large default, not a per-model guess.
        settings.write_text(json.dumps({}), encoding="utf-8")
        assert guard.resolve_window(cwd) == guard.DEFAULT_WINDOW
        # The env var wins over settings, the way Claude Code resolves it.
        os.environ["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "300000"
        assert guard.resolve_window(cwd) == 300_000
        os.environ.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW", None)
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
    path = transcript(tmp / "live.jsonl", [assistant(490_000)])
    payload = {"session_id": "s1", "transcript_path": str(path), "cwd": str(cwd),
               "stop_hook_active": False}

    # Observed usage lifts Opus to its 1M rung, so the 600k ceiling request takes effect.
    first = json.loads(run_hook(home, config_dir, payload))
    assert first["decision"] == "block", first
    assert "~600k window" in first["reason"], first
    assert "/dont-forget:" in first["reason"], first
    assert "ignore this message" in first["reason"], first
    # Default: the agent offers and waits. Saving writes notes and commits a vault, and
    # the hook fires on a schedule the user never chose — so the decision stays theirs.
    assert "offer to run" in first["reason"], first
    assert "Do not run it unless they agree" in first["reason"], first

    # Opted in: the same mark becomes an instruction instead of an offer.
    config.write_text(json.dumps({"vault": str(vault), "autocompact_autosave": True}),
                      encoding="utf-8")
    acting = json.loads(run_hook(home, config_dir, {**payload, "session_id": "s-act"}))
    assert "Run /dont-forget:" in acting["reason"] and " now" in acting["reason"], acting
    assert "offer to run" not in acting["reason"], acting
    assert "unless they agree" not in acting["reason"], acting
    config.write_text(json.dumps({"vault": str(vault)}), encoding="utf-8")

    # Same session, same mark: silence. This is what stops a stop-loop.
    assert run_hook(home, config_dir, payload) == ""

    # stop_hook_active is Claude Code saying it is already replying to our block.
    assert run_hook(home, config_dir, {**payload, "stop_hook_active": True}) == ""

    # The warn distance threads through from config: a smaller margin pushes the mark later,
    # so the same 490k that blocked by default now stays quiet (warn moves to 507k).
    config.write_text(json.dumps({"vault": str(vault), "autocompact_warn_margin": 60_000}),
                      encoding="utf-8")
    assert run_hook(home, config_dir, {**payload, "session_id": "s-margin"}) == ""
    config.write_text(json.dumps({"vault": str(vault)}), encoding="utf-8")

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
        check_window(tmp)
        check_usage(tmp)
        check_end_to_end(tmp)
    print("context-guard: 5 checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
