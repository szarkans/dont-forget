#!/usr/bin/env python3
"""OMP extension against a minimal native-event dispatcher and the real Python core."""

import _selftest_env  # noqa: F401

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from common import HOME_DIR
from index import build

if not shutil.which("node"):
    raise SystemExit("node is required for the OMP adapter self-test")

with tempfile.TemporaryDirectory(prefix="dont-forget-omp-adapter-") as directory:
    root = Path(directory)
    vault = root / "vault"
    vault.mkdir()
    work = root / "adapter-smoke"
    work.mkdir()
    (vault / "OMP cedar.md").write_text(
        "---\ntype: atom\nkind: gotcha\nproject: adapter-smoke\ndate: 2026-11-03\n---\n"
        "# OMP cedar\n[[hub]]\n")
    (HOME_DIR / "config.json").write_text(json.dumps({"vault": str(vault)}))
    build(vault, HOME_DIR / "index.db")

    session_file = root / "sessions/2026-11-03T10-00-00_omp-test.jsonl"
    session_file.parent.mkdir(parents=True)
    records = [
        {"type": "title", "title": "adapter"},
        {"type": "session", "version": 3, "id": "omp-test", "cwd": str(work)},
        {"type": "message", "id": "u1", "parentId": None,
         "message": {"role": "user", "content": [{"type": "text", "text": "remember cedar"}]}},
        {"type": "message", "id": "a1", "parentId": "u1",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "cedar noted"}]}},
    ]
    session_file.write_text("\n".join(json.dumps(row) for row in records) + "\n")
    adapter = (Path(__file__).resolve().parent.parent / "integrations/omp.mjs").as_uri()
    code = '''
import assert from "node:assert/strict";
import fs from "node:fs";
import dontForget from ADAPTER;

const directory = process.env.PROJECT_DIR;
const sessionFile = process.env.SESSION_FILE;
const markerText = "OMP cedar";
const notices = [];

function makePi() {
  const handlers = new Map(), tools = new Map(), commands = new Map(), entries = [], sent = [], notices = [];
  const pi = {
    zod: { object: () => ({ type: "object", properties: {} }) },
    on(name, fn) { if (!handlers.has(name)) handlers.set(name, []); handlers.get(name).push(fn); },
    appendEntry(customType, data) { entries.push({ type: "custom", customType, data }); },
    sendMessage(message, options) { sent.push({ message, options }); },
    registerTool(tool) { tools.set(tool.name, tool); },
    registerCommand(name, command) { commands.set(name, command); },
  };
  const emit = async (name, event, ctx) => {
    const results = [];
    for (const fn of handlers.get(name) || []) results.push(await fn(event, ctx));
    return results;
  };
  return { pi, handlers, tools, commands, entries, sent, notices, emit };
}

function ctxFor({ id = "omp-test", kind = "main", leaf = "a1", hasUI = true } = {}) {
  return {
    agent: { kind, id, name: kind },
    cwd: directory,
    hasUI,
    ui: { notify: (message, level) => notices.push({ message, level }) },
    sessionManager: {
      getSessionId: () => id,
      getSessionFile: () => sessionFile,
      getLeafId: () => leaf,
      getEntries: () => entries,
    },
  };
}

let entries = [];
{
  const { pi, entries: added, agents, emit } = (() => {
    const app = makePi();
    dontForget(app.pi);
    return { pi: app.pi, entries: app.entries, agents: app, emit: app.emit };
  })();
  entries = added;
  const main = ctxFor();
  await agents.emit("session_start", {}, main);
  assert.equal(entries.filter(e => e.customType === "dont-forget.session-role").length, 1, entries);
  const role = entries.find(e => e.customType === "dont-forget.session-role");
  assert.deepEqual(role.data, { v: 1, session_id: "omp-test", kind: "main" });
  // A repeated start does not append a second marker.
  await agents.emit("session_start", {}, main);
  assert.equal(entries.filter(e => e.customType === "dont-forget.session-role").length, 1, entries);

  const foreign = { role: "user", content: "foreign" };
  const old = { role: "custom", customType: "dont-forget.digest", content: "old memory" };
  const [out] = await agents.emit("context", { messages: [foreign, old] }, main);
  assert.equal(out.messages[0], foreign, "foreign message changed");
  const digests = out.messages.filter(m => m.role === "custom" && m.customType === "dont-forget.digest");
  assert.equal(digests.length, 1, out.messages);
  assert(digests[0].content.includes(markerText), digests[0].content);
  assert(digests[0].display === false && digests[0].attribution === "agent");

  const [compacted] = await agents.emit("session.compacting", { sessionId: "omp-test", messages: [] }, main);
  assert(compacted.context[0].includes(markerText), compacted.context[0]);
  assert(!("prompt" in compacted));

  // The tool pins the live leaf and registers the audit through the Python core.
  const tool = agents.tools.get("dont_forget_session");
  assert(tool && tool.approval === "exec", tool);
  const done = await tool.execute("call-1", {}, undefined, undefined, ctxFor());
  assert(!done.isError, done);
  assert.equal(done.details.leaf_id, "a1");
  assert.equal(done.details.audit_session_id, "omp:omp-test");
  assert(fs.existsSync(done.details.snapshot_file), done.details);
  const missing = await tool.execute("call-2", {}, undefined, undefined, ctxFor({ leaf: null }));
  assert(missing.isError && /no active leaf/.test(missing.content[0].text), missing);

  // A child gets a marker but no digest of its own.
  const child = ctxFor({ id: "omp-child", kind: "sub" });
  await agents.emit("session_start", {}, child);
  assert(entries.some(e => e.customType === "dont-forget.session-role" && e.data.session_id === "omp-child"));
  assert.equal((await agents.emit("context", { messages: [foreign] }, child))[0], undefined);

  // Manual command with a UI shows the digest without starting a model turn.
  const command = agents.commands.get("dont-forget-digest");
  assert(command, [...agents.commands.keys()]);
  await command.handler("", main);
  assert(notices.some(n => n.message.includes(markerText)), notices);
  const headless = ctxFor({ hasUI: false });
  await assert.rejects(command.handler("", headless), /requires a UI/);

  // One slash command per catalog entry sends the skill body as a fresh turn.
  const commands = ["dont-forget-about", "dont-forget-that", "dont-forget-session",
    "dont-forget-audit", "dont-forget-setup", "dont-forget-health", "dont-forget-him-please"];
  for (const name of commands) assert(agents.commands.get(name), `missing command ${name}`);
  const sentBefore = agents.sent.length;
  await agents.commands.get("dont-forget-about").handler("why did we switch", main);
  assert.equal(agents.sent.length, sentBefore + 1);
  const turn = agents.sent[agents.sent.length - 1];
  assert.equal(turn.message.customType, "dont-forget.command");
  assert.equal(turn.options.triggerTurn, true);
  assert(turn.message.content.includes("Search the vault"), turn.message.content);
  assert(turn.message.content.includes("User request: why did we switch"));

  // A session switch invalidates the old digest; the next context reloads it.
  await agents.emit("session_switch", {}, main);
  const [reloaded] = await agents.emit("context", { messages: [] }, main);
  assert(reloaded.messages[0].content.includes(markerText), reloaded.messages[0].content);
}

// The switch disables automatic markers/digests, not the manual command.
process.env.DONT_FORGET_HOOKS = "0";
{
  const app = makePi();
  dontForget(app.pi);
  const main = ctxFor();
  await app.emit("session_start", {}, main);
  assert.equal(app.entries.length, 0, app.entries);
  const [out] = await app.emit("context", { messages: [] }, main);
  assert.equal(out, undefined);
  const command = app.commands.get("dont-forget-digest");
  await command.handler("", main);
  assert(notices.some(n => n.message.includes(markerText)), notices);
}
'''.replace("ADAPTER", json.dumps(adapter))
    result = subprocess.run(
        ["node", "--input-type=module", "-e", code], cwd=work,
        env={**os.environ, "PROJECT_DIR": str(work), "SESSION_FILE": str(session_file)},
        text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, (result.stdout, result.stderr)

print("ok")
