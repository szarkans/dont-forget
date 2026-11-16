// OpenCode adapters: the v1 legacy hook shape and the v2 session-hook shape.
//
// Both call the same Python digest. The session's own directory comes from native
// metadata, not from the plugin's working directory: a session can be started anywhere,
// and scanning the wrong project's notes is worse than showing that memory is unavailable.

import { createDigestReader, marker } from "./digest.mjs";

function unwrap(response) {
  return response?.data ?? response;
}

function withMarkerError(message) {
  return `${marker}\nMemory digest unavailable: ${message}`;
}

function replaceOwn(system, text, asObject) {
  const kept = (system || []).filter((part) =>
    typeof part === "string" ? !part.startsWith(marker) : !String(part?.text || "").startsWith(marker));
  if (text) kept.push(asObject ? { type: "text", text } : text);
  return kept;
}

export async function legacy({ directory, client }) {
  const read = createDigestReader();

  const sessionDirectory = async (sessionID) => {
    const response = await client.session.get({ path: { id: sessionID } });
    const info = unwrap(response);
    return {
      directory: info?.directory || info?.location?.directory || null,
      parentID: info?.parentID || null,
    };
  };

  const digest = async (sessionID, refresh) => {
    if (!sessionID) return null;
    let meta;
    try {
      meta = await sessionDirectory(sessionID);
    } catch (error) {
      return withMarkerError(`session metadata: ${error?.message || error}`);
    }
    if (meta.parentID) return null;
    if (!meta.directory) return withMarkerError("session metadata has no project directory");
    const result = await read({
      harness: "opencode",
      sessionId: sessionID,
      directory: meta.directory,
      refresh,
      automatic: true,
    });
    return result?.text ?? null;
  };

  return {
    "experimental.chat.system.transform": async (input, output) => {
      const text = await digest(input.sessionID, false);
      if (!text) return;
      output.system = replaceOwn(output.system, text, false);
    },
    "experimental.session.compacting": async (input, output) => {
      const text = await digest(input.sessionID, true);
      if (!text) return;
      output.context = replaceOwn(output.context, text, false);
    },
    "shell.env": async (input, output) => {
      if (input.sessionID) output.env.OPENCODE_SESSION_ID = input.sessionID;
    },
  };
}

export default {
  id: "dont-forget",
  server: legacy,
  async setup(ctx) {
    if (typeof ctx.session?.hook !== "function") {
      throw new Error("dont-forget: unsupported OpenCode plugin API (ctx.session.hook is missing)");
    }
    const read = createDigestReader();

    const sessionInfo = async (sessionID) => {
      const info = unwrap(await ctx.session.get({ sessionID }));
      return {
        directory: info?.location?.directory || info?.directory || null,
        parentID: info?.parentID || null,
      };
    };

    const digest = async (sessionID, refresh) => {
      if (!sessionID) return null;
      let meta;
      try {
        meta = await sessionInfo(sessionID);
      } catch (error) {
        return withMarkerError(`session metadata: ${error?.message || error}`);
      }
      if (meta.parentID) return null;
      if (!meta.directory) return withMarkerError("session metadata has no project directory");
      const result = await read({
        harness: "opencode",
        sessionId: sessionID,
        directory: meta.directory,
        refresh,
        automatic: true,
      });
      return result?.text ?? null;
    };

    const registrations = [];
    try {
      for (const name of ["context", "compaction"]) {
        registrations.push(await ctx.session.hook(name, async (event) => {
          const text = await digest(event.sessionID, name === "compaction");
          if (!text) return;
          event.system = replaceOwn(event.system, text, true);
        }));
      }
    } catch (error) {
      for (const registration of registrations) await registration.dispose();
      throw error;
    }
    return async () => {
      for (const registration of registrations) await registration.dispose();
    };
  },
};
