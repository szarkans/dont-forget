---
name: health
description: Commit the vault and report its indexed health.
model: inherit
---

# Check Vault Health

Create a local Git snapshot, then explain the health report from the disposable index. The vault stays read-only apart from Git metadata and the commit.

- [ ] Compare the index timestamp at `~/.dont-forget/index.db` with the newest vault note modification. If the index is missing or older, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/index.py"` before producing the report.
- [ ] Check whether the vault root is a Git repository. If it is not, run `git init` there, create `.gitignore` containing `.obsidian/workspace*.json`, stage everything, and make the first commit.
- [ ] If the repository already exists, stage the notes this session wrote by name — `git add -- "<path>" "<path>"` — and commit them with `checkup: YYYY-MM-DD`. For another commit on the same day, append ` #2`, then increment that suffix as needed. When this session wrote nothing, commit nothing and go straight to the report. Naming paths is what replaces `git add -A`: one vault is shared by parallel sessions, and staging everything commits a neighbour's half-written notes under this session's message. Do not push; pushing is the user's decision.
- [ ] Run `git status --porcelain -- "*.md"` in the vault and report the count as `N uncommitted notes`, naming the files while the list is short. Committing named paths means a session that dies mid-close leaves its notes outside Git indefinitely — nothing sweeps them up later — so this line is the only thing that surfaces them.
- [ ] Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/checkup.py"` and explain the result plainly: total notes, islands, unresolved-link candidates, and stale notes.
- [ ] An island is a group of notes cut off from the main graph body. Report each with its size: a size-1 island is a lone orphan note; a size-2+ island is a cluster that links only to itself (name its members). These are candidates to bridge into the graph, not errors to fix automatically.
- [ ] Treat unresolved links as candidates, because they may refer to attachments or headings. Show the list and offer to investigate; do not repair links automatically.
- [ ] Treat stale notes as a report only. Do not edit or delete them unless the user explicitly asks to update them.
- [ ] If Git is unavailable or the commit fails, say so honestly and still show the health report.

Keep the report concise. Mention when displayed lists are capped and distinguish their displayed length from their full count.
