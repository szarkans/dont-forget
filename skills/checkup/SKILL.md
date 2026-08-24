---
name: checkup
description: Commit the vault and report its indexed health.
model: inherit
---

# Check Vault Health

Create a local Git snapshot, then explain the health report from the disposable index. The vault stays read-only apart from Git metadata and the commit.

- [ ] Compare the index timestamp at `~/.dont-forget/index.db` with the newest vault note modification. If the index is missing or older, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/index.py"` before producing the report.
- [ ] Check whether the vault root is a Git repository. If it is not, run `git init` there, create `.gitignore` containing `.obsidian/workspace*.json`, stage everything, and make the first commit.
- [ ] If the repository already exists, run `git add -A` and commit with `checkup: YYYY-MM-DD`. For another commit on the same day, append ` #2`, then increment that suffix as needed. Do not push; pushing is the user's decision.
- [ ] Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/checkup.py"` and explain the result plainly: total notes, orphan notes, unresolved-link candidates, and stale notes.
- [ ] Treat unresolved links as candidates, because they may refer to attachments or headings. Show the list and offer to investigate; do not repair links automatically.
- [ ] Treat stale notes as a report only. Do not edit or delete them unless the user explicitly asks to update them.
- [ ] If Git is unavailable or the commit fails, say so honestly and still show the health report.

Keep the report concise. Mention when displayed lists are capped and distinguish their displayed length from their full count.
