# Host integrations

The Markdown vault, SQLite index, search, writer and audit are shared Python tools.
Claude Code loads the plugin; Codex, OpenCode, OMP, Devin and Antigravity get a generated
shared skill and a thin native adapter. Memory and its index are shared, not copied per host.

## Install

Every supported host has a native plugin path — see the README table for the user-facing
commands. The repository ships a manifest per host: `.claude-plugin/` +
`.claude-plugin/marketplace.json`, `.codex-plugin/` + `.agents/plugins/marketplace.json`,
`.antigravity-plugin/` + the root `hooks.json` (Antigravity's named-group format), and the
root `package.json` that serves both OpenCode's plugin loader and OMP's `omp.extensions`.
Devin reads the plugin formats above without a manifest of its own; its root `hooks.json`
slot is taken by Antigravity's format, so a Devin plugin install carries skills only and
the Devin digest still comes from `install-hosts.py --host devin`.

`npx skills add szarkans/dont-forget` copies skill folders only — no shared `scripts/`
core, no hooks — and is documented as a fallback, not an install.

## Install locally

Run from this checkout:

```bash
python3 scripts/install-hosts.py --host all
```

`--host shared`, `--host codex`, `--host opencode`, `--host omp`, `--host devin`,
`--host agy` install a subset; a single host install also ensures the shared skill.
`--host all` installs shared plus the CLIs it finds on `PATH`, and lists the rest as
`skipped`. An explicit host installs without a CLI, except OpenCode, which needs
`--opencode-api 1|2` when no CLI is available. An unknown detected OpenCode major version
exits 2 before writing.

Real changes are transactions. Before bytes go under
`DONT_FORGET_HOME/host-backups/<id>/`; a manifest records each operation and its progress.
Repeat an unchanged installation to get `changed: []` and no backup. Undo with:

```bash
python3 scripts/install-hosts.py --rollback <backup-id>
```

A rollback verifies every current value first; if a file changed after the install it
refuses instead of restoring part of it. An interrupted transaction reports its backup id
and refuses to start another until rolled back. `--dry-run` lists the exact operations
without creating state, a lock or a backup.

Existing foreign files and hooks are preserved. Installer ownership is exact: a Codex
handler is ours only when its whole command matches the generated command. Changing a
Codex hook invalidates its trust hash; review it in `/hooks`. The installer never grants
trust, edits providers, models, permissions, `config.yml` or disabled extensions, and
never starts a host.

Use `--adopt-existing` once for an older dont-forget installation of this same checkout.
Adoption requires the exact old shared skill (`SKILL.md` naming dont-forget, and a
`scripts` symlink to a checkout) or the exact old adapter bytes. Unknown content is not
adopted. An adopted installation is owned: uninstall removes it; the saved baseline comes
back only through an explicit rollback.

An OMP install refuses to add `extensions/dont-forget.js` when another extension in the
same directory already runs `hot-scan.py --hook`: two sources would inject the digest
twice. Disable or remove the older extension first; the installer never edits it.

`--uninstall` removes only recorded owned files and handlers. `--host shared` is removed
only by naming `shared` or `all`. Vault, config, index, vectors, journals and the checkout
are never removed.

## Commands

The shared install also writes one `dont-forget-<name>` skill per entry in
`integrations/commands.json`, next to the shared `dont-forget` skill. That is
how a command reaches a host with no command API of its own — Codex invokes it
as `$dont-forget-<name>`, Devin and other `.agents/skills` readers as
`/dont-forget-<name>` or a mention. Claude keeps its plugin names
(`/dont-forget:<name>`).

Two hosts get a native command surface on top: the OMP extension registers each
`dont-forget-<name>` slash command (its handler sends the skill body as a fresh
turn), and the OpenCode install writes `commands/dont-forget-<name>.md` —
file commands with a `$ARGUMENTS` tail, valid on both plugin APIs. The command
body is always the canonical `skills/<name>/SKILL.md` text with script paths
pointed at the installed shared skill.

## Read a session

```bash
python3 scripts/transcript.py --harness codex --session THREAD_ID
python3 scripts/transcript.py --harness opencode --session SESSION_ID
python3 scripts/transcript.py --harness omp --session SESSION_ID [--leaf LEAF]
python3 scripts/transcript.py --harness devin --session SESSION_ID
python3 scripts/transcript.py --harness agy --session CONVERSATION_ID
python3 scripts/transcript.py --path /absolute/transcript.jsonl
python3 scripts/transcript.py --path opencode://SESSION_ID --piece 1
python3 scripts/transcript.py --snapshot FILE --piece N
python3 scripts/transcript.py --discard-snapshot FILE
python3 scripts/unclosed.py --list
```

Identity is never guessed by directory or file time. An explicit `--path` is used as given;
`--harness`/`--session` only constrain it. Native environment fields are read only when no
explicit source exists, and a process carrying two host families' variables is reported as
unknown rather than resolved by priority. Claude uses `CLAUDE_CONFIG_DIR` when set.

The first summary of an audit writes a frozen snapshot under
`DONT_FORGET_HOME/audit-transcripts/` and registers the audit. Auditors read that snapshot,
not the live file, so an append or a later rewrite cannot change the pieces or the branch.
Snapshots expire after 24 hours and are discarded with `--discard-snapshot`. They are
temporary filtered input, not vault notes and not an archive.

OMP adds a native `dont_forget_session` tool: it pins the leaf the host currently holds —
the last physical entry is not the live leaf — and captures that branch. A historical
`--path` without `--leaf` is labelled `last-persisted`.

Codex reads paginated rollouts through their bounded `history_base` lineage, including
compressed `.jsonl.zst` when `compression.zstd` or the `zstd` executable is available.
A missing selected paginated rollout is an error, not a reason to open an older sibling.
OpenCode reads v1 and v2 SQLite stores read-only; v2 assistant parts replace inline content
instead of being appended twice, and export directory comes from the session's own location.

The transcript summary includes `audit_session_id` and `snapshot_file`. Pass the id as
`session_id` in `vault-write.py` when saving a session note. Only that session is closed;
concurrent pending sessions are preserved. Missing or ambiguous identity saves the note but
closes nothing (`session_closure.status: "no-identity"`).

Audit decision marks are a downloaded JSON file imported back into the same generated page;
the page never stores them and never talks to a server. This file transport is used on every
host, including Claude. The old Claude-only ArtifactData collection is deliberately not
used, so a page opened outside Claude behaves the same way.

## Digest and hooks

Claude and Codex use native `SessionStart` hooks; Codex also gets a `PreCompact` notification
that saves nothing. OpenCode and OMP inject memory through their event hooks. Antigravity
hooks emit `injectSteps` instead of `additionalContext` and learn the conversation id from
stdin's `conversationId`; the local install writes one named `dont-forget` group into
`~/.gemini/config/hooks.json`. OMP's
`/dont-forget-digest` shows the fresh digest on demand; headless access is:

```bash
python3 scripts/hot-scan.py --text --harness omp --session ID
```

`DONT_FORGET_HOOKS=0` disables automatic injection and OMP role markers, never manual
`--text` or the audit tools. A failed digest is shown as `Memory digest unavailable`, not as
an empty vault. A host that can run Python can call the core manually; automatic injection
still requires that host's adapter.

## Verification

Synthetic adapter tests prove translation and core invocation; they do not prove a native
model request. Release acceptance requires a fresh CLI session on each host with the real
plugin/skill loader, the startup marker observed in that host's own request path, and a
model answer that uses it. Linux is the required target; existing WSL/macOS compatibility is
kept but not separately promised.

Smoke-testing a `dont-forget-<name>` command differs per host:

- OMP (18.22.0): only the TUI runs extension commands end to end — drive it through a
  PTY. `omp -p "/cmd"` dispatches to the handler, but `sendMessage` with `triggerTurn`
  never completes a turn and no session is written; `echo "/cmd" | omp` skips command
  dispatch entirely and the model gets raw text.
- Codex: `codex exec --skip-git-repo-check '$dont-forget-<name>'` resolves the generated
  skill in a headless turn.
- OpenCode: slash commands only exist in the TUI (register → `$ARGUMENTS` expansion →
  turn); `opencode run` never expands them. The generated `~/.agents/skills` copy is
  reachable in `run` as a skill.
- Devin and other `.agents/skills` readers: `/dont-forget-<name>` or `$dont-forget-<name>`
  per the host's own skill syntax.
- Antigravity: `agy -p "<prompt>"` runs a headless session — plugin skills resolve as
  `/<skill-name>` and hook context lands in the request. Command tools need
  `--dangerously-skip-permissions` headless, or a permissions allow-rule.

Integration references checked 2026-11-03:
[Claude hooks](https://code.claude.com/docs/en/hooks.md),
[Codex hooks](https://learn.chatgpt.com/docs/hooks.md),
[Codex skills](https://learn.chatgpt.com/docs/build-skills.md),
[OpenCode skills](https://opencode.ai/docs/skills/),
[OpenCode plugin hooks](https://github.com/anomalyco/opencode/blob/dev/services/www/src/docs/content/build/plugins/server.mdx),
[OMP extensions](https://omp.sh/docs/extension-authoring),
[Antigravity skills](https://antigravity.google/docs/skills/),
[Antigravity hooks](https://antigravity.google/docs/hooks),
[Antigravity plugins](https://antigravity.google/docs/plugins).
