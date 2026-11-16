// OMP extension: role markers, memory in the outgoing context, compaction help,
// the live session-audit tool, and a manual digest command.
//
// OMP rebinds extension factories for child sessions while module globals stay shared,
// so every piece of state lives inside this factory. The session file's last physical
// entry is not the live leaf: the tool takes the leaf the host holds right now and the
// Python reader pins that branch into a frozen snapshot.

import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createDigestReader, marker } from "./digest.mjs";

const ROLE = "dont-forget.session-role";
const DIGEST = "dont-forget.digest";
const root = fileURLToPath(new URL("../", import.meta.url));
const transcript = path.join(root, "scripts", "transcript.py");
const TTL = 300_000;

function failure(message) {
  return { isError: true, content: [{ type: "text", text: message }], details: { error: message } };
}

function run(args, { cwd, signal }) {
  return new Promise((resolve, reject) => {
    execFile("python3", [transcript, ...args], {
      cwd,
      timeout: 120_000,
      maxBuffer: 1024 * 1024,
      encoding: "utf8",
      signal,
    }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(String(stderr || "").trim() || error.message));
        return;
      }
      resolve(String(stdout));
    });
  });
}

export default function dontForget(pi) {
  const read = createDigestReader();
  const state = {
    sessionId: null,
    cwd: null,
    generation: 0,
    text: null,
    createdAt: 0,
    controller: null,
    notified: new Set(),
  };

  const disabled = () => process.env.DONT_FORGET_HOOKS === "0";

  const invalidate = () => {
    state.generation += 1;
    if (state.controller) state.controller.abort();
    state.controller = null;
    state.text = null;
    state.createdAt = 0;
  };

  const markRole = (ctx) => {
    if (disabled()) return;
    const id = ctx.sessionManager.getSessionId();
    const kind = ctx.agent?.kind;
    if (!id || (kind !== "main" && kind !== "sub")) return;
    const has = ctx.sessionManager.getEntries().some((entry) =>
      entry.type === "custom" && entry.customType === ROLE
      && entry.data?.session_id === id && entry.data?.kind === kind);
    if (!has) pi.appendEntry(ROLE, { v: 1, session_id: id, kind });
  };

  const ensureDigest = async (ctx, { refresh = false, automatic = true } = {}) => {
    if (automatic && disabled()) return null;
    if (ctx.agent?.kind !== "main") return null;
    const id = ctx.sessionManager.getSessionId();
    const cwd = ctx.cwd;
    if (!id || !cwd) return null;
    if (!refresh && state.text && state.sessionId === id && state.cwd === cwd
        && Date.now() - state.createdAt < TTL) {
      return { text: state.text, createdAt: state.createdAt };
    }
    const generation = state.generation;
    if (refresh) {
      if (state.controller) state.controller.abort();
    }
    const controller = new AbortController();
    state.controller = controller;
    const result = await read({
      harness: "omp",
      sessionId: id,
      directory: cwd,
      refresh,
      automatic,
      signal: controller.signal,
    });
    // A result from a session that already switched must never land in the new one.
    if (state.generation !== generation || state.controller !== controller
        || ctx.sessionManager.getSessionId() !== id || ctx.cwd !== cwd) {
      return null;
    }
    state.controller = null;
    if (!result) return null;
    state.sessionId = id;
    state.cwd = cwd;
    state.text = result.text;
    state.createdAt = result.createdAt;
    return result;
  };

  const startup = async (ctx, { branch = false } = {}) => {
    // Take identity before the first await: the session may switch while the digest runs.
    const id = ctx.sessionManager.getSessionId();
    const cwd = ctx.cwd;
    const generation = state.generation + 1;
    state.generation = generation;
    if (state.controller) state.controller.abort();
    state.controller = null;
    state.text = null;
    state.createdAt = 0;
    if (!disabled()) markRole(ctx);
    if (disabled() || ctx.agent?.kind !== "main" || !id) return;
    await ensureDigest(ctx, { refresh: true, automatic: true });
    if (state.generation !== generation || state.sessionId !== id || ctx.cwd !== cwd) return;
    if (ctx.hasUI && !state.notified.has(id)) {
      state.notified.add(id);
      ctx.ui.notify("dont-forget: memory loaded; /dont-forget-digest shows it", "info");
    }
    if (branch && ctx.hasUI) {
      ctx.ui.notify("dont-forget: branch changed; session audit takes the live leaf", "info");
    }
  };

  pi.on("session_start", async (_event, ctx) => { await startup(ctx); });
  pi.on("session_switch", async (_event, ctx) => { await startup(ctx); });
  pi.on("session_branch", async (_event, ctx) => { await startup(ctx, { branch: true }); });
  pi.on("session_tree", async () => { invalidate(); });
  pi.on("session_compact", async () => { invalidate(); });
  pi.on("session_shutdown", async () => {
    invalidate();
    state.notified.clear();
  });

  pi.on("context", async (event, ctx) => {
    if (disabled() || ctx.agent?.kind !== "main") return;
    const digest = await ensureDigest(ctx, { refresh: false, automatic: true });
    if (!digest) return;
    const kept = (event.messages || []).filter((message) =>
      !(message?.role === "custom" && message?.customType === DIGEST));
    kept.push({
      role: "custom",
      customType: DIGEST,
      content: digest.text,
      display: false,
      attribution: "agent",
      timestamp: digest.createdAt,
    });
    return { messages: kept };
  });

  pi.on("session.compacting", async (_event, ctx) => {
    if (disabled() || ctx.agent?.kind !== "main") return;
    const digest = await ensureDigest(ctx, { refresh: true, automatic: true });
    if (!digest) return;
    return { context: [digest.text] };
  });

  pi.registerTool({
    name: "dont_forget_session",
    label: "Audit this session",
    description: "Capture this OMP session's current branch for a dont-forget audit. "
      + "Returns the frozen snapshot path and the exact session id. Call it before reading "
      + "pieces with transcript.py --snapshot FILE --piece N.",
    parameters: pi.zod.object({}),
    approval: "exec",
    async execute(_toolCallId, _params, signal, _onUpdate, ctx) {
      let file, id, leaf;
      try {
        file = ctx.sessionManager.getSessionFile();
        id = ctx.sessionManager.getSessionId();
        leaf = ctx.sessionManager.getLeafId();
      } catch (error) {
        return failure(`dont-forget: cannot read the OMP session identity: ${error?.message || error}`);
      }
      if (!file || !fs.existsSync(file)) {
        return failure("dont-forget: OMP session file is missing; no audit was registered");
      }
      if (leaf === null || leaf === undefined) {
        return failure("dont-forget: OMP session has no active leaf; no audit was registered");
      }
      let stdout;
      try {
        stdout = await run(["--harness", "omp", "--path", file, "--session", id, "--leaf", leaf],
          { cwd: ctx.cwd, signal });
      } catch (error) {
        return failure(`dont-forget: session audit failed: ${error?.message || error}`);
      }
      let details;
      try {
        details = JSON.parse(stdout);
      } catch (error) {
        return failure(`dont-forget: session audit returned unreadable output: ${error?.message || error}`);
      }
      return { content: [{ type: "text", text: stdout }], details };
    },
  });

  pi.registerCommand("dont-forget-digest", {
    description: "Show the fresh dont-forget memory digest",
    handler: async (_args, ctx) => {
      if (!ctx.hasUI) {
        throw new Error("dont-forget-digest requires a UI; use hot-scan.py --text");
      }
      const id = ctx.sessionManager.getSessionId();
      const result = await read({
        harness: "omp",
        sessionId: id,
        directory: ctx.cwd,
        refresh: true,
        automatic: false,
      });
      ctx.ui.notify(result?.text || "dont-forget: digest is empty (successful local scan)", "info");
    },
  });
}

export { DIGEST, ROLE, marker };
