---
name: session
description: Record a verified session note and index its open threads.
---

# Record a Session

Capture the useful state of the conversation. Open threads live only in the note's pending section; the session-start digest reads them from the index, so there is no second place to keep them in sync.

- [ ] Analyze the dialogue for completed work, key decisions, commits or pull requests, discoveries, and unfinished threads.
- [ ] In the project repository, run `git log --oneline -15` and `git status --short`. Reconcile claims with this evidence. Never say work shipped when Git does not show it. If Git is unavailable, state that explicitly in the note.
- [ ] Choose a title `Session — YYYY-MM-DD <short topic>` that distinguishes this session from others on the same date. Prefer a pull request number, ticket, or branch when available. Use the same title as the filename stem.
- [ ] Draft frontmatter with `type: session`, `date`, and `tags`, with `session` as the first tag. Add `project` when known.
- [ ] Write `# Session — YYYY-MM-DD <short topic>` followed by `## Done`, `## Decisions`, and `## Next steps / pending`.
- [ ] Put every unfinished thread only in `## Next steps / pending`, as an unchecked `- [ ]` item. Each thread names what is unfinished and who finishes it, then the state someone would need to resume: work left uncommitted, the attempt that just failed, the plan that was half-executed, what was already tried. A thread nobody can act on is dead weight — `e2e not verified this session` was written into this vault 56 times as a closing reflex and told no later session anything.
- [ ] Keep knowledge out of that section. A thread dies when someone does it; a fact about how the world is does not die from any action. "Merge PR #41" is a thread. "The RCON password went out in public chat" is a gotcha — route it through `this` so search can find it later, because threads are shown by freshness and fall out of the digest.
- [ ] Add `## Links`. Link the MOC that fits when one already exists; use `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/search.py" "<topic or project>"` when it is not evident from context. When no MOC fits, link the notes this session actually touched rather than creating a hub to fill the slot.
- [ ] Write the note only through `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault-write.py"`. Send JSON with `filename` and `content`; do not write the vault file directly.
- [ ] If the same filename already exists, read it and offer an update through `vault-write.py` with `action: "replace"` and `expected_sha` equal to the SHA-256 of the current bytes. If replacement returns `conflict`, stop and tell the user; never resolve it silently.
- [ ] Report the note name, open-thread count, exactly what entered the index, and anything that remains unsaved.

Keep statements factual and compact. Preserve useful commit identifiers, pull request numbers, branches, failed approaches, and constraints needed to resume the work.
