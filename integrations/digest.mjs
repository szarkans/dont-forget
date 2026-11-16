// Shared subprocess/cache path for the JavaScript host adapters.
//
// The Python core owns the digest, the cache and the error text. This module only runs
// it with the right identity, bounds what reaches the model, and keeps a host's own
// session variables out of the child: the identity is passed explicitly.

import { execFile } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const exec = promisify(execFile);
const root = fileURLToPath(new URL("../", import.meta.url));
const script = path.join(root, "scripts", "hot-scan.py");
const marker = "[dont-forget local memory]";
const TTL = 300_000;
const MAX_ENTRIES = 64;
const CORE_BUDGET = 11_264;
const MAX_CONTEXT = 12_288;
const TIMEOUT = 15_000;
const IDENTITY_VARS = [
  "CLAUDE_CODE_SESSION_ID",
  "CODEX_THREAD_ID",
  "CODEX_SESSION_ID",
  "OPENCODE_SESSION_ID",
  "PI_SESSION_ID",
  "PI_SESSION_FILE",
];

/** At most `limit` UTF-8 bytes, never a half-written codepoint. */
export function clipUtf8(text, limit) {
  if (Buffer.byteLength(text, "utf8") <= limit) return text;
  const cut = Buffer.from(text, "utf8").subarray(0, limit).toString("utf8");
  return cut.endsWith("\uFFFD") ? cut.slice(0, -1) : cut;
}

function childEnv() {
  const env = { ...process.env };
  for (const name of IDENTITY_VARS) delete env[name];
  return env;
}

function bounded(text, extra = "") {
  return clipUtf8(`${marker}\n${text}${extra}`, MAX_CONTEXT);
}

export function createDigestReader() {
  const cache = new Map();
  const generations = new Map();

  const keyOf = (harness, sessionId, directory) =>
    `${harness}\u0000${sessionId}\u0000${path.resolve(directory || process.cwd())}`;

  return async function read({
    harness,
    sessionId,
    directory,
    refresh = false,
    automatic = true,
    signal,
  }) {
    if (!harness || !sessionId) return null;
    if (automatic && process.env.DONT_FORGET_HOOKS === "0") return null;
    const cwd = path.resolve(directory || process.cwd());
    const key = keyOf(harness, sessionId, cwd);
    if (refresh) {
      generations.set(key, (generations.get(key) || 0) + 1);
      cache.delete(key);
    } else {
      if (!generations.has(key)) generations.set(key, 0);
      const old = cache.get(key);
      if (old && Date.now() - old.createdAt < TTL) {
        return { text: old.text, createdAt: old.createdAt };
      }
    }
    const generation = generations.get(key);
    const args = [script, "--text", "--harness", harness, "--session", sessionId,
      "--budget", String(CORE_BUDGET)];
    let stdout = "";
    let stderr = "";
    try {
      ({ stdout, stderr } = await exec("python3", args, {
        cwd,
        env: childEnv(),
        timeout: TIMEOUT,
        maxBuffer: 256 * 1024,
        encoding: "utf8",
        signal,
      }));
    } catch (error) {
      if (signal?.aborted || error?.name === "AbortError") return null;
      if (generations.get(key) !== generation) return null;
      const detail = String(error?.stderr || "").trim() || error?.message || String(error);
      return { text: bounded(`Memory digest unavailable: ${detail}`), createdAt: Date.now() };
    }
    if (signal?.aborted || generations.get(key) !== generation) return null;
    const warning = String(stderr || "").trim();
    const text = bounded(stdout.trimEnd(), warning ? `\nMemory diagnostics: ${warning}` : "");
    const createdAt = Date.now();
    const old = cache.get(key);
    if (!old || old.createdAt < createdAt) {
      if (cache.size >= MAX_ENTRIES && !cache.has(key)) cache.delete(cache.keys().next().value);
      cache.set(key, { text, createdAt });
    }
    return { text, createdAt };
  };
}

export { marker };
