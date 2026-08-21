---
name: df-session
description: Record a verified session note and index its open threads.
---

# Record a Session

Capture the useful state of the conversation without turning the handoff index into a second task list.

- [ ] Analyze the dialogue for completed work, key decisions, commits or pull requests, discoveries, and unfinished threads.
- [ ] In the project repository, run `git log --oneline -15` and `git status --short`. Reconcile claims with this evidence. Never say work shipped when Git does not show it. If Git is unavailable, state that explicitly in the note.
- [ ] Choose a title `Session — YYYY-MM-DD <short topic>` that distinguishes this session from others on the same date. Prefer a pull request number, ticket, or branch when available. Use the same title as the filename stem.
- [ ] Draft frontmatter with `type: session`, `date`, and `tags`, with `session` as the first tag. Add `project` when known.
- [ ] Write `# Session — YYYY-MM-DD <short topic>` followed by `## Done`, `## Decisions`, and `## Next steps / pending`.
- [ ] Put every unfinished thread only in `## Next steps / pending`, as an unchecked `- [ ]` item. Describe the stopping point, why work stopped, and what was already tried; do not merely restate the task name.
- [ ] Add `## Links` with the most relevant MOC wikilink. Use `python3 scripts/search.py "<topic or project>"` when the right MOC is not evident from context.
- [ ] Write the note only through `python3 scripts/vault-write.py`. Send JSON with `filename` and `content`; do not write the vault file directly.
- [ ] If the same filename already exists, read it and offer an update through `vault-write.py` with `action: "replace"` and `expected_sha` equal to the SHA-256 of the current bytes. If replacement returns `conflict`, stop and tell the user; never resolve it silently.
- [ ] After a successful note write, call `python3 scripts/handoff-upsert.py` with the note title, date, and `open_count` equal to the number of unchecked items in `## Next steps / pending`. The handoff index receives only this pointer, never copies of pending items.
- [ ] Report the note name, open-thread count, exactly what entered the index, and anything that remains unsaved.

Keep statements factual and compact. Preserve useful commit identifiers, pull request numbers, branches, failed approaches, and constraints needed to resume the work.
