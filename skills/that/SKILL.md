---
name: that
description: "Persist durable knowledge when it would change how a future session behaves. Capture atomic, deduplicated vault notes or route actionable code rules to the appropriate rules file."
model: inherit
---

# dont-forget:that — Persist Durable Knowledge

- [ ] Treat persistence as a behavior change, not a transcript archive. Save only
  knowledge that would make a future session act differently; otherwise say "nothing
  worth persisting" and create nothing.

- [ ] Save what is true of this user's world and nowhere else: their projects, their
  machines, the decisions they made, the way their environment misbehaves. Knowledge
  that ships with the tool — how a documented flag behaves, what a framework does by
  default — is already in every model and every manual, and in the vault it is the
  worst kind of noise: written in common words, it matches many queries and answers
  none, while the byte budget drops real fragments to make room for it.
- [ ] Redact passwords, tokens, keys, and other secrets as `<REDACTED>` before any
  content is written or shown in a write payload. The writer scans for them too and
  returns a `warning` beside the status — it warns, it does not block, so a warning
  means the secret is now in the vault: say so and name the credential to rotate.
- [ ] Separate two or more independent claims into two or more notes. Never combine
  unrelated claims. If a claim cannot fit in a one-phrase title, split it further.
- [ ] Route an actionable code-bound "never X" or "always Y" rule away from the
  vault and into `.claude/rules/<domain>.md` in the relevant project. Give that file
  frontmatter with `paths:` globs controlling when it loads and a one-line human
  `description:`. Search existing rule files by `paths:` before choosing a file.
  Use `~/.claude/rules/` only when the rule genuinely applies across projects;
  choose the most specific scope. A fact about the code belongs next to the code,
  where the same pull request that changes the behavior changes the rule; a fact
  about the world around the code belongs in the vault.

## Shape the note around the claim

- [ ] Write `type: atom`. There is no role to pick here: `session` notes come from the
  `session` skill, MOC pages are born from demand rather than written by hand,
  generalisations across several atoms are proposed later by the vault audit, and where
  the knowledge came from is the `source:` field rather than a type. The filename is
  `Atom — <complete claim>.md`; keep `#` and `/` out of its stem. A `.` is fine — a
  claim like `bash 3.2` or `search.py` keeps it.

- [ ] Express a decision as: "in context X, facing Y, chose Z and rejected W",
  followed by `Because:` and `Fails-when:`.
- [ ] Express a gotcha as `GIVEN / WHEN / THEN`, followed by `Because:` and a dated
  precedent.
- [ ] Give a principle, pain, or stance the slots `Job`, `Pain`, `Done-well`, and
  `Anti-goal`.
- [ ] Give a fact or insight a claim-title, a BLUF, and supporting evidence.
- [ ] Add `kind:` only for `decision`, `gotcha`, `principle`, `pain`, or `stance`;
  facts and insights do not receive it.
- [ ] Ask once of every note: can this claim become false while the note stays
  unchanged, because some external fact shifts under it — a DNS record, a version, a
  price, a one-off measurement? If yes, add a `dies-when:` frontmatter field naming
  that event (`dies-when: DNS record for the bridge is repointed`). If no — a
  permanent rule — omit it; do not invent a condition to fill the slot. It rides in
  frontmatter, not the body, so search surfaces it on every fragment of the note
  rather than only when its paragraph happens to match.
- [ ] Add `volatility: hot | warm | cold` — how fast the claim goes out of date, which
  is a different question from how important it is. `hot` is weeks: a version, a
  running address, a plan in flight. `warm` is months: a project convention, a team
  habit, a tool's current quirk. `cold` is years: a principle, a physical constraint,
  a post-mortem. Judge it from those definitions rather than reaching for the middle;
  nothing reads the field yet, and it is being collected precisely to find out whether
  the judgement is real, so a reflex `warm` on everything makes the field worthless.

- [ ] Every vault note needs YAML frontmatter. Include `type: atom`, creation `date`,
  `tags` with `atom` as the first tag, `source` identifying where the knowledge came
  from, and `volatility`. Add `aliases` only when useful and `project` when the claim
  is project-bound.

## Preserve the knowledge graph

- [ ] Expect the writer to answer `similar` instead of writing, with the notes it thinks
  say this already. That check is no longer yours to remember to run — but the decision
  is yours to put to the user: update one of those notes, or write a new one and repeat
  the call with `duplicates_checked: true`. Never pick silently, and never repeat the
  call with the flag just to get past the answer.
- [ ] Merge into an existing note only when the two share a cause, not a symptom. Two
  deploys that broke because migrations were skipped are one note, gaining a second
  dated case. A deploy broken by migrations and one broken by an out-of-memory kill
  are two notes: merged, they become "prod sometimes falls over", which is true and
  useless at the moment it would have to help. When merging, append the new case with
  its project and date and leave the existing wording alone — search lives on rare
  words, and generalising "arrays break in bash 3.2" into "shell arrays behave oddly"
  costs the note the very query that finds it. Unsure whether the cause is the same:
  write the separate note. A wrong split shows up in the vault audit and is glued
  back; a wrong merge quietly loses a fact nobody misses until search fails them.
- [ ] End each note with `## Links`, or the vault's established equivalent. Link a MOC
  when a fitting one already exists; when none does, link the notes the claim actually
  touches and stop there. MOC pages arise from demand — a name linked often enough
  earns one — so inventing a hub to satisfy this line produces exactly the disconnected
  page the rule was meant to prevent. Put links in prose, never inside code blocks.
- [ ] Write vault notes only by sending the complete filename and Markdown content
  JSON to `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault-write.py"`. On `exists-same`, report that the note
  was already present. On `conflict`, stop and tell the user; never replace or
  reconcile the content on your own.
