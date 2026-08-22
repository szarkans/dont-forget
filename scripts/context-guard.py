#!/usr/bin/env python3
"""Stop hook: speak up before Claude Code's autocompact throws the raw conversation away.

Two independent scales share this hook, because they warn about different damage.

The compact scale. Claude Code does not compact when the window is full: it holds
room back for its own reply, and compacts at `window - 33k`. That reserve is measured,
not assumed — 44 auto-compacts recorded in the user's own transcripts, across three
different window settings (550k, 600k, 650k), put it at 29-33k every time. It matters
because a mark measured from the window instead of from that point sits past a
threshold no session ever reaches, and never fires at all.

The rot scale. A long context degrades answers whatever the window allows, and saving
does not cure it — only a fresh session does. So those marks are absolute token counts,
not offsets, and they stay live even when autocompact is switched off.

Claude Code only. The Stop payload carries no token count (checked: it holds
background_tasks, cwd, effort, hook_event_name, last_assistant_message, permission_mode,
prompt_id, session_crons, session_id, stop_hook_active, transcript_path and nothing
else), so usage is read from the transcript — from the end, since a long one reaches
tens of megabytes and only its last figure is wanted.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from common import CONFIG_PATH, HOME_DIR

# Room Claude Code reserves before compacting: min(max_output, 20k) + 13k safety.
# Measured against compactMetadata.preTokens on 44 auto-compacts; see the module docstring.
COMPACT_RESERVE = 33_000
# Distance from the compact point at which each compact mark speaks. Generous on purpose:
# `review --full` audits, saves facts, writes the note and commits, and the user wants
# room left over to keep working afterwards rather than to stop dead at the save.
WARN_MARGIN = 150_000
CRITICAL_MARGIN = 50_000
# Absolute marks for context rot. Unreachable on a 200k window, which is correct — that
# session ends long before quality drifts this far.
ROT_MARKS = (500_000, 900_000)
# Context windows a model may actually have. The transcript records a model id with its
# "[1m]" suffix stripped, so a 1M session and a 200k one are written identically; the
# suffix survives in ~/.claude.json, and where even that is unknown the ladder climbs by
# observation. Guessing high would silence the hook forever, so it always guesses low.
CEILING_LADDER = (200_000, 500_000, 1_000_000)
# The range Claude Code itself accepts for autoCompactWindow; a value outside it is ignored.
WINDOW_MIN, WINDOW_MAX = 100_000, 1_000_000

STATE_PATH = HOME_DIR / "context-guard.json"
LOG_PATH = HOME_DIR / "context-guard.log"
LOG_CAP = 200
# Sessions kept in the state file. Enough for every session open at once, and it stops
# the file growing forever on a machine that never reboots.
STATE_KEEP = 20


def read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def config_flags() -> tuple[bool, bool]:
    """(speak at all, act without asking).

    The first is opt-out: a nudge that ships switched off is a nudge nobody has. But a
    hook that blocks Stop is intrusive enough to deserve a documented off switch.

    The second is opt-in, and deliberately so. Saving a session writes files and commits
    a vault, and the hook fires on a schedule the user never asked for — so by default it
    hands them the decision instead of taking it. Whoever wants the close-out to just
    happen turns it on and stops being asked.

    A missing config means the plugin was never set up and there is nothing to save into.
    """
    config = read_json(CONFIG_PATH)
    if not isinstance(config, dict) or not config.get("vault"):
        return False, False
    return (config.get("autocompact_nudge", True) is not False,
            config.get("autocompact_autosave", False) is True)


def tail_lines(path: Path, chunk: int = 1 << 16):
    """Yield lines from the end of a file backwards, reading only the blocks needed.

    A transcript reaches tens of megabytes and the wanted record sits within the last
    few lines of it (measured: the seventh from the end). Reading forward from the start
    costs the whole file for one number.
    """
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        remainder = b""
        while position > 0:
            step = min(chunk, position)
            position -= step
            handle.seek(position)
            block = handle.read(step) + remainder
            parts = block.split(b"\n")
            remainder = parts[0]
            for line in reversed(parts[1:]):
                if line.strip():
                    yield line
        if remainder.strip():
            yield remainder


def last_usage(transcript: Path) -> tuple[int | None, str | None]:
    """Tokens in play and the model of the newest assistant turn, or (None, None).

    The sum is input + output + cache_creation + cache_read — the same one Claude Code's
    own status line reports. Output belongs in it: it says what the *next* request will
    carry, not what the last one did. Validated against compactMetadata.preTokens on 26
    compactions: median difference -726 tokens, worst case -33k, always low rather than
    high, so the hook errs towards speaking early.

    Sub-agent turns cannot pollute this: they are written to their own transcripts
    (checked — zero isSidechain assistant records in the three largest files here), and
    the guard below costs nothing if that ever changes.
    """
    try:
        for raw in tail_lines(transcript):
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("isSidechain"):
                continue
            message = entry.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            total = sum(usage.get(field) or 0 for field in
                        ("input_tokens", "output_tokens",
                         "cache_creation_input_tokens", "cache_read_input_tokens"))
            if total:
                model = message.get("model")
                return total, model if isinstance(model, str) else None
    except OSError:
        pass
    return None, None


def claude_config_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override).expanduser().resolve() if override else Path.home() / ".claude"


def settings_chain(cwd: Path):
    """Settings files in Claude Code's own precedence order, nearest first."""
    for path in (cwd / ".claude/settings.local.json", cwd / ".claude/settings.json",
                 claude_config_dir() / "settings.local.json",
                 claude_config_dir() / "settings.json"):
        data = read_json(path)
        if isinstance(data, dict):
            yield data


def autocompact_enabled(cwd: Path) -> bool:
    """False only when a setting says so. With autocompact off the compact marks would
    warn about something that cannot happen, and their wording would simply be untrue."""
    for data in settings_chain(cwd):
        if isinstance(data.get("autoCompactEnabled"), bool):
            return data["autoCompactEnabled"]
    return True


def valid_window(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    return number if WINDOW_MIN <= number <= WINDOW_MAX else None


def configured_window(cwd: Path) -> int | None:
    """env -> project settings -> user settings, the way Claude Code resolves it."""
    raw = os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW")
    if raw:
        try:
            window = valid_window(int(raw))
        except ValueError:
            window = None
        if window is not None:
            return window
    for data in settings_chain(cwd):
        window = valid_window(data.get("autoCompactWindow"))
        if window is not None:
            return window
    return None


def model_ceiling(cwd: Path, model: str | None, used: int, state_path: Path | None = None) -> int:
    """The model's context window: a guess from ~/.claude.json, corrected by observation.

    lastModelUsage keeps model ids *with* the "[1m]" suffix the transcript drops, keyed by
    working directory, and it is a record of what actually ran rather than what is
    configured — so it survives the user clearing `settings.model` back to the default.
    Both keys present means the directory has seen both variants; the smaller one is
    taken, because guessing high costs every future warning while guessing low costs one
    early nudge that the ladder then corrects.

    The ladder is also what covers everything this file cannot recognise — a model served
    over a custom API, an unfamiliar plan, a future id. Whatever the guess, a session that
    outgrew it proves the window is larger, and the next rung is taken.
    """
    ceiling = CEILING_LADDER[0]
    state = read_json(state_path or Path.home() / ".claude.json")
    if isinstance(state, dict) and model:
        projects = state.get("projects")
        entry = projects.get(str(cwd)) if isinstance(projects, dict) else None
        usage = entry.get("lastModelUsage") if isinstance(entry, dict) else None
        if isinstance(usage, dict) and f"{model}[1m]" in usage and model not in usage:
            ceiling = CEILING_LADDER[-1]
    for rung in CEILING_LADDER:
        if rung >= ceiling and used <= rung:
            return rung
    return CEILING_LADDER[-1]


def resolve_window(cwd: Path, model: str | None, used: int, state_path: Path | None = None) -> int:
    """A configured value is a ceiling *request*: the window in force is the smaller of
    it and the model's own, which is why a 600k setting on a 200k model compacts at 167k."""
    ceiling = model_ceiling(cwd, model, used, state_path)
    configured = configured_window(cwd)
    return min(configured, ceiling) if configured else ceiling


def marks(window: int, compact_marks: bool) -> list[tuple[str, int]]:
    """Every threshold this session can cross, as (name, tokens), lowest first.

    Compact margins are clamped by a share of the run-up so they cannot invert or swallow
    a small window: a flat 150k on a 200k window would fire at 17k of usage, before the
    session has said anything worth saving.
    """
    found = [(f"rot-{mark}", mark) for mark in ROT_MARKS]
    if compact_marks:
        point = max(1, window - COMPACT_RESERVE)
        critical = min(CRITICAL_MARGIN, max(1, point // 10))
        warn = min(WARN_MARGIN, max(critical + 1, point // 4))
        found += [("compact-warn", point - warn), ("compact-critical", point - critical)]
    return sorted(((name, level) for name, level in found if level > 0),
                  key=lambda mark: mark[1])


def message(name: str, used: int, window: int, autosave: bool) -> str:
    """What the agent is told. Whether that is an instruction or an offer is the user's
    setting, not this hook's opinion: the hook fires on a schedule nobody asked for, and
    running the close-out writes notes and commits a vault."""
    thousands = lambda value: f"~{round(value / 1000)}k"
    tail = (" If this session was already saved since the last compaction, ignore this "
            "message and stop again.")

    def call(command: str, what: str) -> str:
        if autosave:
            return f"Run {command} now — it {what}."
        return (f"Tell the user what is about to happen and offer to run {command} — it "
                f"{what}. Do not run it unless they agree.")

    if name.startswith("rot-"):
        return (f"Context has passed {thousands(used)} tokens. Answer quality degrades as a "
                f"context grows long, and saving does not undo that — only a fresh session "
                f"does. " + call("/dont-forget:review --full",
                                 "stores what this session learned") +
                f" Either way, tell the user a new session would now serve them better and "
                f"let them decide; never clear on your own." + tail)
    point = thousands(max(1, window - COMPACT_RESERVE))
    if name == "compact-critical":
        return (f"Context is {thousands(used)} and Claude Code auto-compacts at {point}, "
                f"which drops the raw conversation for a summary. Little room is left. " +
                call("/dont-forget:session",
                     "keeps the session note and its open threads, the cheapest thing "
                     "that survives") + tail)
    return (f"Context is {thousands(used)} of a {thousands(window)} window; Claude Code "
            f"auto-compacts at {point} and the raw conversation is replaced by a summary. "
            f"There is still room to do this properly. " +
            call("/dont-forget:review --full",
                 "audits what went unsaved, stores the facts, writes the session note") +
            tail)


def load_state() -> dict:
    state = read_json(STATE_PATH)
    return state if isinstance(state, dict) else {}


def save_state(state: dict) -> bool:
    """Write atomically. A half-written state file would re-arm every mark and turn the
    hook into a loop, so a failure here has to be visible to the caller."""
    try:
        HOME_DIR.mkdir(parents=True, exist_ok=True)
        if len(state) > STATE_KEEP:
            state = dict(list(state.items())[-STATE_KEEP:])
        temporary = STATE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        temporary.replace(STATE_PATH)
        return True
    except OSError:
        return False


def log(used, window, fired: str) -> None:
    """A capped trail, because the margins above are an estimate of what `review --full`
    costs and only real firings can turn that into a measurement."""
    try:
        HOME_DIR.mkdir(parents=True, exist_ok=True)
        lines = []
        if LOG_PATH.exists():
            lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
        lines.append(json.dumps({"used": used, "window": window, "fired": fired}))
        LOG_PATH.write_text("\n".join(lines[-LOG_CAP:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def decide(used: int, window: int, already: list, compact_marks: bool) -> tuple[str | None, list]:
    """The highest crossed mark that has not spoken yet, plus the marks now spent.

    Only one message per stop: a resumed session, or one very large turn, can cross two
    marks at once, and blocking three times in a row over the same context teaches the
    agent to ignore all of them. The quieter marks are recorded as spent regardless —
    having crossed them, saying so later would be stale news.

    Everything re-arms once usage falls below the lowest mark, which is how a compaction
    announces itself: it drops the context to a fraction of what it was. Without that the
    second compaction in a long session — exactly the event worth warning about — would
    pass in silence. The floor has to be the lowest mark rather than a share of the
    window: a share sits *above* the first mark on a wide window (450k against a warn at
    425k on a 600k one), so every stop in between would clear the record and speak again.
    """
    levels = marks(window, compact_marks)
    if not levels:
        return None, []
    if used < levels[0][1]:
        already = []
    crossed = [(level, name) for name, level in levels if used >= level and name not in already]
    if not crossed:
        return None, already
    name = max(crossed)[1]
    return name, sorted(set(already) | {found for _, found in crossed})


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if not isinstance(payload, dict) or payload.get("stop_hook_active"):
        return 0
    enabled, autosave = config_flags()
    if not enabled:
        return 0

    transcript = payload.get("transcript_path")
    if not transcript or not Path(transcript).is_file():
        return 0
    used, model = last_usage(Path(transcript))
    if used is None:
        # Not zero. A silent zero is what hid this whole mechanism being broken before.
        log("?", None, "no-usage")
        return 0

    cwd = Path(payload.get("cwd") or Path.cwd())
    window = resolve_window(cwd, model, used)
    compact_marks = autocompact_enabled(cwd)
    session = str(payload.get("session_id") or "unknown")

    state = load_state()
    already = state.get(session)
    already = already if isinstance(already, list) else []
    name, spent = decide(used, window, already, compact_marks)
    # Re-insert rather than assign: the trim in save_state keeps the last STATE_KEEP keys
    # by insertion order, and updating a key in place does not move it. Without this a
    # long session is evicted by twenty short ones and every mark speaks a second time.
    # ponytail: last writer wins between concurrent sessions — costs at most one repeated
    # message, so no lock; revisit if that ever costs more than the lock would.
    state.pop(session, None)
    state[session] = spent
    written = save_state(state)
    log(used, window, name or "none")

    # Blocking without a working anti-loop record could trap the session in a stop cycle,
    # so an unwritable state file means staying quiet.
    if name is None or not written:
        return 0
    print(json.dumps({"decision": "block", "reason": message(name, used, window, autosave)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
