#!/usr/bin/env python3
"""OMP transcript CLI: exact identity, parallel sessions and active-branch evidence."""

import _selftest_env  # noqa: F401

import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from common import HOME_DIR

SCRIPT = Path(__file__).with_name("transcript.py")


def invoke(env, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          env=env, capture_output=True, text=True)


def snapshot(path, session_id, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = [{"type": "title", "title": session_id},
              {"type": "session", "version": 3, "id": session_id, "cwd": "/same/project"}]
    path.write_text("\n".join(json.dumps(row) for row in header + records) + "\n")


with tempfile.TemporaryDirectory(prefix="dont-forget-omp-") as directory:
    root = Path(directory)
    agent = root / "agent"
    env = dict(os.environ, PI_CODING_AGENT_DIR=str(agent))
    for key in ("PI_SESSION_ID", "PI_SESSION_FILE", "OMP_PROFILE", "PI_PROFILE",
                "PI_CONFIG_DIR", "CLAUDE_CODE_SESSION_ID"):
        env.pop(key, None)
    alpha = agent / "sessions/same/2026-11-02_alpha.jsonl"
    beta = agent / "sessions/same/2026-11-02_beta.jsonl"
    snapshot(alpha, "alpha", [
        {"type": "message", "id": "u1", "parentId": None,
         "message": {"role": "user", "content": [{"type": "text", "text": "ALPHA request"}]}},
        {"type": "message", "id": "abandoned", "parentId": "u1",
         "message": {"role": "assistant", "content": "ABANDONED answer"}},
        {"type": "message", "id": "a1", "parentId": "u1",
         "message": {"role": "assistant", "content": [
             {"type": "thinking", "thinking": "PRIVATE reasoning"},
             {"type": "text", "text": "ALPHA answer"},
             {"type": "image", "data": "PRIVATE image"},
             {"type": "toolCall", "name": "bash", "arguments": {"command": "git status"}}]}},
        {"type": "message", "id": "tool", "parentId": "a1",
         "message": {"role": "toolResult", "content": [{"type": "text", "text": "PRIVATE result"}]}},
        {"type": "message", "id": "developer", "parentId": "tool",
         "message": {"role": "developer", "content": "PRIVATE instructions"}},
        {"type": "compaction", "id": "compact", "parentId": "developer",
         "summary": "ALPHA checkpoint"},
        {"type": "message", "id": "u2", "parentId": "compact",
         "message": {"role": "user", "content": "ALPHA follow-up"}},
        {"type": "message", "id": "a2", "parentId": "u2",
         "message": {"role": "assistant", "content": "ALPHA final"}},
        {"type": "model_usage", "id": "usage", "parentId": "a2", "purpose": "title"},
    ])
    snapshot(beta, "beta", [
        {"type": "message", "id": "u", "parentId": None,
         "message": {"role": "user", "content": "BETA request"}},
        {"type": "message", "id": "a", "parentId": "u",
         "message": {"role": "assistant", "content": "BETA answer"}},
    ])
    os.utime(alpha, (1_000_000_000, 1_000_000_000))
    os.utime(beta, (2_000_000_000, 2_000_000_000))
    env_alpha = dict(env, PI_SESSION_ID="alpha", PI_SESSION_FILE=str(alpha),
                     CODEX_THREAD_ID="inherited-codex", OPENCODE_SESSION_ID="inherited-opencode",
                     CLAUDE_CODE_SESSION_ID="inherited-claude")
    env_beta = dict(env_alpha, PI_SESSION_ID="beta", PI_SESSION_FILE=str(beta))

    # Each simultaneous caller gets its own conversation, regardless of file freshness.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda current: invoke(current, "--harness", "omp"), (env_alpha, env_beta)))
    for result, session_id, path in zip(results, ("alpha", "beta"), (alpha, beta)):
        assert result.returncode == 0, result.stderr
        summary = json.loads(result.stdout)
        assert summary["harness"] == "omp" and summary["session_id"] == session_id, summary
        assert summary["audit_session_id"] == f"omp:{session_id}", summary
        assert summary["path"] == str(path), summary
    pending = HOME_DIR / "closing-sessions.jsonl"
    registered = {json.loads(line)["session_id"] for line in pending.read_text().splitlines()}
    assert registered == {"omp:alpha", "omp:beta"}, registered

    piece = invoke(env_alpha, "--harness", "omp", "--piece", 1)
    assert piece.returncode == 0, piece.stderr
    assert "ALPHA request" in piece.stdout and "ALPHA final" in piece.stdout
    assert "ALPHA checkpoint" in piece.stdout and "git status" in piece.stdout
    assert "PRIVATE" not in piece.stdout and "ABANDONED" not in piece.stdout
    assert "BETA" not in piece.stdout
    beta_piece = invoke(env_beta, "--harness", "omp", "--piece", 1)
    assert beta_piece.returncode == 0 and "BETA answer" in beta_piece.stdout, beta_piece.stderr
    assert "ALPHA" not in beta_piece.stdout

    explicit = invoke(env, "--harness", "omp", "--session", "alpha")
    assert explicit.returncode == 0, explicit.stderr
    assert json.loads(explicit.stdout)["path"] == str(alpha)
    explicit_path = invoke(env_beta, "--path", alpha, "--piece", 1)
    assert explicit_path.returncode == 0 and "ALPHA answer" in explicit_path.stdout
    assert "BETA" not in explicit_path.stdout

    long_summary = invoke(env_alpha, "--harness", "omp", "--limit", 40)
    assert long_summary.returncode == 0, long_summary.stderr
    long_pieces = json.loads(long_summary.stdout)["pieces"]
    assert long_pieces > 1
    rendered_pieces = []
    for number in range(1, long_pieces + 1):
        part = invoke(env_alpha, "--harness", "omp", "--limit", 40, "--piece", number)
        assert part.returncode == 0, part.stderr
        rendered_pieces.append(part.stdout)
    assert "ALPHA checkpoint" in "\n".join(rendered_pieces)

    # A pinned leaf is the audit: a later sibling and entries after it stay out, and the
    # frozen snapshot is what every reader sees.
    pinned = invoke(env_alpha, "--harness", "omp", "--leaf", "u1")
    assert pinned.returncode == 0, pinned.stderr
    pinned_summary = json.loads(pinned.stdout)
    assert pinned_summary["leaf_id"] == "u1" and pinned_summary["branch_source"] == "explicit", pinned_summary
    pinned_piece = invoke(env_alpha, "--snapshot", pinned_summary["snapshot_file"], "--piece", "1")
    assert "ALPHA request" in pinned_piece.stdout and "ALPHA answer" not in pinned_piece.stdout
    empty_leaf = invoke(env_alpha, "--harness", "omp", "--leaf", "")
    assert empty_leaf.returncode != 0 and "no readable conversation" in empty_leaf.stderr, empty_leaf.stderr

    # An ephemeral session, mismatched header or missing id cannot select a neighbour
    # or register a failed audit. Empty native fields also block inherited host ids.
    before = pending.read_bytes()
    for bad_env, args in [
        (dict(env_alpha, PI_SESSION_FILE=""), ("--harness", "omp")),
        (dict(env_alpha, PI_SESSION_ID="beta"), ("--harness", "omp")),
        (dict(env_alpha, PI_SESSION_ID="beta"), ("--harness", "omp", "--session", "")),
        (dict(env_alpha, PI_SESSION_FILE=str(root / "missing.jsonl")), ("--harness", "omp")),
        (dict(env_alpha, PI_SESSION_FILE="", PI_SESSION_ID=""), ("--harness", "omp")),
        (env, ("--harness", "omp", "--session", "absent")),
        (env, ("--harness", "omp")),
    ]:
        failed = invoke(bad_env, *args)
        assert failed.returncode != 0 and failed.stderr, failed
        assert pending.read_bytes() == before

    for name, records in [
        ("empty", []),
        ("missing-parent", [{"type": "message", "id": "u", "parentId": "gone",
                             "message": {"role": "user", "content": "incomplete"}}]),
        ("cycle", [{"type": "message", "id": "u", "parentId": "u",
                    "message": {"role": "user", "content": "cyclic"}}]),
    ]:
        invalid = agent / f"sessions/same/2026-11-02_{name}.jsonl"
        snapshot(invalid, name, records)
        failed = invoke(env, "--path", invalid)
        assert failed.returncode != 0 and failed.stderr, failed
        assert pending.read_bytes() == before

    # Named profiles use their own native store instead of the default agent override.
    profiled = root / "profiles/work/agent/sessions/same/2026-11-02_profiled.jsonl"
    snapshot(profiled, "profiled", [
        {"type": "message", "id": "u", "parentId": None,
         "message": {"role": "user", "content": "PROFILE request"}},
    ])
    profiled_env = dict(env, PI_CONFIG_DIR=str(root), OMP_PROFILE="work")
    selected = invoke(profiled_env, "--harness", "omp", "--session", "profiled")
    assert selected.returncode == 0, selected.stderr
    assert json.loads(selected.stdout)["path"] == str(profiled)

print("ok")
