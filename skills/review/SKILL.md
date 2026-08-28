---
name: review
description: Audit a work session for facts and commitments that were not saved.
---

# Session completeness audit

Use the conversation as evidence and git as the reality check. Do not invent memory candidates or claim work that the evidence does not support.

## Audit

- [ ] In normal mode, produce only the report and an offer to save; do not save anything.
- [ ] Check every user request and say whether it was completed.
- [ ] Identify decisions and whether they were saved.
- [ ] Identify completed actions such as commits and pull requests.
- [ ] Find TODO, later, and FIXME items in the conversation or code.
- [ ] Check whether every encountered error was resolved.
- [ ] Check whether every question was answered or lost.
- [ ] Check whether relevant external systems were updated.
- [ ] Check whether code-bound “never X” or “always Y” rules were recorded in rules.
- [ ] Run `git log --oneline -15` and `git status --short`; never report an action as done when git does not show it.
- [ ] Report four parts: `Done`, `Missed` memory candidates, `Hanging Threads`, and a score out of 10.
- [ ] Include a memory candidate only when it is visibly supported by the conversation.
- [ ] Sort each loose end by whether doing something kills it. "Merge PR #41" dies the moment someone merges it — that is a hanging thread. "The RCON password went out in public chat" describes how the world is and no action erases it — that is knowledge, and it belongs in a note through `this`. Threads are shown by freshness and drop off the digest, so a fact parked among them is a fact thrown away.
- [ ] Make every hanging thread name what is unfinished and who finishes it. This vault's single most repeated line was `e2e not verified this session` — 56 of 629 threads — and it named neither, so no later session could act on it: it was a reflex, not a finding. Write the actionable version instead: "the release checklist in PR #41 has not been run against prod — user runs it".
- [ ] Never run or simulate tests or QA. Report plainly which checks actually ran and which did not, and leave it at that unless something specific remains for someone to do.

## Full mode

Treat `--full` as consent to execute the save chain.

- [ ] Capture an origin anchor before saving: the session's first request, `git rev-parse HEAD`, and `git status --short`.
- [ ] Invoke `this` first to save the supported candidates.
- [ ] Invoke `session` second to create the session note.
- [ ] Keep that order; do not combine or substitute the commands.
- [ ] Invoke `checkup` third and last. `--full` is consent for the whole chain, and its commit is local to the vault; asking for it separately turns a one-command close-out back into a conversation.
- [ ] Verify actual persistence: new notes appear in `git status`, and each has a type, atomic scope, and a meaningful link.
- [ ] Compare the final state with the origin anchor. If git and state did not change, say `already in order, nothing to redo`; do not imply work happened.
