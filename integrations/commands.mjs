// One catalog of the user-facing dont-forget-<name> commands and the loader
// that turns a catalog entry into prompt text. The installer generates the
// same bodies for .agents/skills and OpenCode command files, so a command
// reads identically on every host.
//
// Installed, this module sits next to references/<name>.md copies whose script
// paths were rewritten at install time. Loaded from the repository (selftests,
// a plain checkout) the same bodies come from skills/<name>/SKILL.md with
// ${CLAUDE_PLUGIN_ROOT} pointed at the checkout.

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));

export const CATALOG = JSON.parse(readFileSync(new URL("./commands.json", import.meta.url), "utf8"));

const FRONTMATTER = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/;

export function skillBody(entry) {
  const installed = path.join(root, "references", `${entry.reference}.md`);
  const raw = existsSync(installed)
    ? readFileSync(installed, "utf8")
    : readFileSync(path.join(root, "skills", entry.skill, "SKILL.md"), "utf8")
        .replaceAll("${CLAUDE_PLUGIN_ROOT}", root);
  return raw.replace(FRONTMATTER, "").trim();
}

export function commandPrompt(entry, args) {
  const tail = String(args ?? "").trim();
  return `The user invoked the dont-forget command "/${entry.command}".\n\n${skillBody(entry)}`
    + (tail ? `\n\nUser request: ${tail}\n` : "");
}
