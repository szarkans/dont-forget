---
name: this
description: "Persist durable knowledge when it would change how a future session behaves. Capture atomic, deduplicated vault notes or route actionable code rules to the appropriate rules file."
model: inherit
---

# dont-forget:this — Persist Durable Knowledge

- [ ] Treat persistence as a behavior change, not a transcript archive. Save only
  knowledge that would make a future session act differently; otherwise say "nothing
  worth persisting" and create nothing.

- [ ] Redact passwords, tokens, keys, and other secrets as `<REDACTED>` before any
  content is written or shown in a write payload.
- [ ] Separate two or more independent claims into two or more notes. Never combine
  unrelated claims. If a claim cannot fit in a one-phrase title, split it further.
- [ ] Route an actionable code-bound "never X" or "always Y" rule away from the
  vault and into `.claude/rules/<domain>.md` in the relevant project. Give that file
  frontmatter with `paths:` globs controlling when it loads and a one-line human
  `description:`. Search existing rule files by `paths:` before choosing a file.
  Use `~/.claude/rules/` only when the rule genuinely applies across projects;
  choose the most specific scope.

## Shape the note around the claim

- [ ] Use the note role that best represents its function: `atom`, `molecule`,
  `source`, `session`, or `moc`. The filename is `Atom — <complete claim>.md`; keep
  `#`, `.`, and `/` out of its stem.

- [ ] Express a decision as: "in context X, facing Y, chose Z and rejected W",
  followed by `Because:` and `Fails-when:`.
- [ ] Express a gotcha as `GIVEN / WHEN / THEN`, followed by `Because:` and a dated
  precedent.
- [ ] Give a principle, pain, or stance the slots `Job`, `Pain`, `Done-well`, and
  `Anti-goal`.
- [ ] Give a fact or insight a claim-title, a BLUF, and supporting evidence.
- [ ] Add `kind:` only for `decision`, `gotcha`, `principle`, `pain`, or `stance`;
  facts and insights do not receive it.

- [ ] Every vault note needs YAML frontmatter. Include `type` from the five roles,
  creation `date`, `tags` with the type repeated as the first tag, and `source`
  identifying where the knowledge came from. Add `aliases` only when useful and
  `project` when the claim is project-bound.

## Preserve the knowledge graph

- [ ] Run `python3 scripts/search.py "<the substance of the claim>"` for every
  proposed claim and inspect its JSON fragments and paths. If an existing note
  already covers the claim, offer to update that note instead of creating another;
  do not silently choose for the user.
- [ ] End each note with `## Links`, or the vault's established equivalent, and at
  least one link to an appropriate MOC so no note is orphaned. Put links in prose,
  never inside code blocks.
- [ ] Write vault notes only by sending the complete filename and Markdown content
  JSON to `python3 scripts/vault-write.py`. On `exists-same`, report that the note
  was already present. On `conflict`, stop and tell the user; never replace or
  reconcile the content on your own.
