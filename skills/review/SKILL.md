---
name: df-review
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
- [ ] Never run or simulate tests or QA. If none ran, add `e2e not verified this session` as a hanging thread.

## Full mode

Treat `--full` as consent to execute the save chain.

- [ ] Capture an origin anchor before saving: the session's first request, `git rev-parse HEAD`, and `git status --short`.
- [ ] Invoke `this` first to save the supported candidates.
- [ ] Invoke `session` second to create the session note.
- [ ] Keep that order; do not combine or substitute the two commands.
- [ ] Afterward, offer `checkup` for the git commit and health check, but do not run it automatically.
- [ ] Verify actual persistence: new notes appear in `git status`, and each has a type, atomic scope, and a meaningful link.
- [ ] Compare the final state with the origin anchor. If git and state did not change, say `already in order, nothing to redo`; do not imply work happened.
