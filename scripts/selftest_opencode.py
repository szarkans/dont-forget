#!/usr/bin/env python3
"""Both OpenCode hook shapes against the real Python digest and a fake native client."""

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
    raise SystemExit("node is required for the OpenCode adapter self-test")

with tempfile.TemporaryDirectory(prefix="dont-forget-opencode-") as directory:
    root = Path(directory)
    vault = root / "vault"
    vault.mkdir()
    (vault / "Adapter nebulaquartz.md").write_text(
        "---\ntype: atom\nkind: gotcha\nproject: adapter-smoke\ndate: 2026-10-30\n---\n"
        "# Adapter nebulaquartz\n[[hub]]\n")
    (HOME_DIR / "config.json").write_text(json.dumps({"vault": str(vault)}))
    build(vault, HOME_DIR / "index.db")
    work = root / "adapter-smoke"
    work.mkdir()
    elsewhere = root / "plugin-cwd"
    elsewhere.mkdir()
    adapter = (Path(__file__).resolve().parent.parent / "integrations/opencode.mjs").as_uri()
    code = '''
import assert from "node:assert/strict";
import adapter, { legacy } from ADAPTER;

const directory = process.env.PROJECT_DIR;
const elsewhere = process.env.PLUGIN_CWD;
const marker = "[dont-forget local memory]";
const foreign = { type: "text", text: "foreign system part" };
const metadata = (parentID = null) => ({ data: { location: { directory }, parentID } });

// --- v2 registration: one digest, own marker replaced, foreign parts untouched ---
{
  const hooks = new Map();
  let disposed = 0;
  const cleanup = await adapter.setup({
    location: { directory: elsewhere },
    session: {
      get: async ({ sessionID }) => {
        assert.equal(sessionID, "synthetic-v2");
        return metadata();
      },
      hook: async (name, fn) => {
        hooks.set(name, fn);
        return { dispose: async () => disposed++ };
      },
    },
  });
  for (const name of ["context", "compaction"]) {
    const event = { sessionID: "synthetic-v2", system: [foreign, { type: "text", text: marker + "\\nold" }] };
    await hooks.get(name)(event);
    const own = event.system.filter((part) => part.text?.startsWith(marker));
    assert.equal(own.length, 1, event.system);
    assert(own[0].text.includes("Adapter nebulaquartz"), own[0].text);
    assert(event.system[0] === foreign, "foreign system part changed");
    assert(!event.system.some((part) => part.text === marker + "\\nold"), "old marker kept");
  }
  await cleanup();
  assert.equal(disposed, 2);
}

// --- v2 child session gets no separate digest ---
{
  const hooks = new Map();
  const cleanup = await adapter.setup({
    session: {
      get: async () => metadata("parent-1"),
      hook: async (name, fn) => { hooks.set(name, fn); return { dispose: async () => {} }; },
    },
  });
  const event = { sessionID: "child", system: [foreign] };
  await hooks.get("context")(event);
  assert.deepEqual(event.system, [foreign]);
  await cleanup();
}

// --- v2 loader refusal and partial-registration cleanup ---
await assert.rejects(
  adapter.setup({ session: {} }),
  /unsupported OpenCode plugin API/,
);
{
  let disposed = 0;
  await assert.rejects(adapter.setup({
    session: {
      get: async () => metadata(),
      hook: async (name) => {
        if (name === "compaction") throw new Error("registration failed");
        return { dispose: async () => disposed++ };
      },
    },
  }), /registration failed/);
  assert.equal(disposed, 1);
}

// --- v1 legacy shape: metadata directory, child skip, own marker replacement ---
{
  let child = false;
  const v1 = await legacy({
    directory: elsewhere,
    client: { session: { get: async ({ path }) => ({ data: {
      directory,
      parentID: child ? "parent-1" : null,
    } }) } },
  });
  const foreignText = "foreign system part";
  const output = { system: [foreignText, marker + "\\nold"] };
  await v1["experimental.chat.system.transform"]({ sessionID: "synthetic-v1" }, output);
  const own = output.system.filter((part) => part.startsWith(marker));
  assert.equal(own.length, 1, output.system);
  assert(own[0].includes("Adapter nebulaquartz"));
  assert.equal(output.system[0], foreignText);
  const compact = { context: [] };
  await v1["experimental.session.compacting"]({ sessionID: "synthetic-v1" }, compact);
  assert(compact.context[0].includes("Adapter nebulaquartz"));
  assert(!("prompt" in compact));
  const env = { env: {} };
  await v1["shell.env"]({ sessionID: "synthetic-v1" }, env);
  assert.equal(env.env.OPENCODE_SESSION_ID, "synthetic-v1");
  child = true;
  const childOut = { system: [foreignText] };
  await v1["experimental.chat.system.transform"]({ sessionID: "child" }, childOut);
  assert.deepEqual(childOut.system, [foreignText]);
}

// --- metadata failure is model-visible, not a scan of the plugin cwd ---
{
  const v1 = await legacy({
    directory: elsewhere,
    client: { session: { get: async () => { throw new Error("store unavailable"); } } },
  });
  const output = { system: [] };
  await v1["experimental.chat.system.transform"]({ sessionID: "broken" }, output);
  assert(output.system[0].startsWith(marker));
  assert(output.system[0].includes("Memory digest unavailable"));
  assert(output.system[0].includes("store unavailable"));
}

// --- the switch disables automatic digests, never the manual core ---
process.env.DONT_FORGET_HOOKS = "0";
{
  const v1 = await legacy({
    directory: elsewhere,
    client: { session: { get: async () => ({ data: { directory, parentID: null } }) } },
  });
  const output = { system: [] };
  await v1["experimental.chat.system.transform"]({ sessionID: "synthetic-v1" }, output);
  assert.deepEqual(output.system, []);
}
'''.replace("ADAPTER", json.dumps(adapter))
    result = subprocess.run(
        ["node", "--input-type=module", "-e", code], cwd=elsewhere,
        env={**os.environ, "PROJECT_DIR": str(work), "PLUGIN_CWD": str(elsewhere)},
        text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, (result.stdout, result.stderr)

print("ok")
