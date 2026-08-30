---
name: session
description: Close a work session — audit what was not saved, then record the session note and check the vault.
model: inherit
---

# Close a session

Two things happen here, in this order: an audit of what the session leaves unsaved, and
— only with the user's word — the writing. Showing is the default; writing is the step
they ask for. There is no separate audit command, because an audit nobody acts on is a
report, and an audit that writes on its own is a vault filling up with paraphrase.

Use the conversation as evidence and git as the reality check. Never invent a memory
candidate, and never claim work the evidence does not support.

## Audit — always, and it writes nothing

- [ ] Check every user request and say whether it was completed.
- [ ] Identify decisions, and whether each was saved.
- [ ] Identify completed actions such as commits and pull requests.
- [ ] Find TODO, later, and FIXME items in the conversation or the code.
- [ ] Check whether every error encountered was resolved, and every question answered.
- [ ] Check whether relevant external systems were updated.
- [ ] Check whether code-bound "never X" or "always Y" rules were recorded in rules.
- [ ] Run `git log --oneline -15` and `git status --short`. Never report an action as done
  when git does not show it. If git is unavailable, say so rather than assuming.
- [ ] Report `Done`, `Missed` memory candidates, and `Hanging threads`.
- [ ] Include a memory candidate only when the conversation visibly supports it.
- [ ] Give every candidate a proposed destination: the vault, the repository rules, a
  runbook, or nowhere. Deciding should cost the user one movement, not a deliberation;
  when you are unsure, the default is nowhere.
- [ ] Show the whole list, uncut. The user is the only working filter, and a trimmed list
  takes away their chance to catch what you understood wrongly.
- [ ] Sort each loose end by whether doing something kills it. "Merge PR #41" dies the
  moment someone merges it — that is a hanging thread. "The RCON password went out in
  public chat" describes how the world is, and no action erases it — that is knowledge,
  and it belongs in a note through `that`. Threads are shown by freshness and fall out of
  the digest, so a fact parked among them is a fact thrown away.
- [ ] Make every hanging thread name what is unfinished and who finishes it. This vault's
  single most repeated line was `e2e not verified this session` — 56 of 629 threads — and
  it named neither, so no later session could act on it: it was a reflex, not a finding.
  Write the actionable version instead: "the release checklist in PR #41 has not been run
  against prod — user runs it".
- [ ] Never run or simulate tests or QA. Report plainly which checks ran and which did not.

## Writing — only on the user's word

`--full` is consent for the whole chain below. Without it, stop after the audit and offer.

- [ ] Capture an origin anchor first: the session's opening request, `git rev-parse HEAD`,
  and `git status --short`.
- [ ] Save the accepted knowledge candidates through `that`, one at a time.
- [ ] Log what the user turned down by piping to
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/feedback-log.py"`, one JSON object per verdict
  (`{"verdict": "noise", "query": "<the candidate>"}`). Every refusal is data: in a month
  it shows what actually gets rejected — public knowledge, trivia, duplicates — and the
  filter can then be built on a measurement instead of a guess.
- [ ] Then write the session note itself, as below.
- [ ] Then run `health` — the chain's last step. Its commit is local to the vault, and
  asking for it separately turns a one-command close into a conversation.
- [ ] Verify the writing actually happened: the new notes appear in `git status`, each has
  a type, an atomic scope, and a real link.
- [ ] Compare the end state with the origin anchor. If neither git nor the vault changed,
  say `already in order, nothing to redo`, and do not imply work happened.

## The session note

Open threads live only in this note's pending section; the digest reads them from the
index, so there is no second place to keep in sync.

- [ ] Title it `Session — YYYY-MM-DD <short topic>`, distinct from other sessions that
  day. Prefer a pull request number, ticket, or branch. The filename stem is the title.
- [ ] Frontmatter: `type: session`, `date`, `tags` with `session` first, and `project`
  when it is known.
- [ ] Body: `# Session — YYYY-MM-DD <short topic>`, then `## Done`, `## Decisions`, and
  `## Next steps / pending`.
- [ ] Put every unfinished thread only in `## Next steps / pending`, as an unchecked
  `- [ ]`. Each names what is unfinished and who finishes it, then the state someone needs
  to resume: work left uncommitted, the attempt that just failed, the plan half-executed,
  what was already tried.
- [ ] Say what is in flight right now, not only what was finished. The end of a context is
  the end of a session, and this note is the only handoff there is.
- [ ] Keep knowledge out of the pending section, by the dying test above.
- [ ] Add `## Links`. Link a MOC when a fitting one already exists — find it with
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/search.py" "<topic or project>"`. When none
  fits, link the notes this session actually touched instead of inventing a hub.
- [ ] Write only through `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault-write.py"`, sending
  JSON with `filename` and `content`. Never write the vault file directly.
- [ ] If the filename already exists, read it and offer an update with `action: "replace"`
  and `expected_sha` set to the SHA-256 of the current bytes. On `conflict`, stop and tell
  the user; never resolve it silently.
- [ ] Report the note name, the open-thread count, what entered the index, and anything
  that remains unsaved.

Keep statements factual and compact. Preserve commit ids, pull request numbers, branches,
failed approaches, and the constraints someone needs to pick the work back up.
