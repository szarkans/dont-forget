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
  The exception is the user's own position: a rule, principle, or lesson they state
  in their own words is theirs even when the world has said something like it before
  ("progression should grow sideways, not just further"). Record it, and keep their
  sentence verbatim as a `> ` quote under the title with the date it was said; your
  rewording, if any, goes below the quote, never in place of it.
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

- [ ] The vault's rules live in its own `README.md` at the vault root, in Russian, and
  outrank this file wherever they differ: the vault is the data, this plugin is the
  engine. Read it once per session before the first write.
- [ ] Write `type: atom` for a card. `session` notes come from the `session` skill, MOC
  pages are born from demand rather than written by hand, and where the knowledge came
  from is the `source:` field rather than a type. The filename is
  `<Kind> — <complete claim>.md`, where Kind is the capitalised `kind:` of the note —
  `Gotcha`, `Decision`, `Principle`, `Stance`, `Pain` — and `Fact` for a fact or insight
  that carries no `kind:` (renamed from `Atom — ` on 13.09.2026); keep `#` and `/` out
  of its stem. The writer files the note by its `project:` into that project's folder
  (`gamedev/`, `gym/`, `bts/`) and into `_unsorted/` when there is none: send only the
  filename, never a path. `_inbox/` is different: it is the user's own drop folder, never
  written by an agent, and sorted only when the user asks. A `.` is fine — a
  claim like `bash 3.2` or `search.py` keeps it.

- [ ] Express a decision as: "in context X, facing Y, chose Z and rejected W",
  followed by `Because:` and `Fails-when:`.
- [ ] Express a gotcha as `GIVEN / WHEN / THEN`, followed by `Because:`, `Fails-when:`
  (the provoking input and the wrong outcome, so the note is found by its symptom) and
  a dated precedent.
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
  is project-bound. A project is any domain the user works in, not only a repository:
  a game they design, their training, their health, their own habits all count, and
  each gets a `project:` of its own (`gamedev`, `gym`, `self`) so recall on "load" or
  "recovery" does not mix the body with the backend. Nothing about the domain makes a
  claim ineligible; the only test is whether it changes a future session's behaviour.
- [ ] The vault is a git repository. A note about the user's health, mood, money, or
  other people is still theirs to keep, but say once, before writing it, that it will
  be committed with the rest, and ask whether it belongs in the vault's untracked
  folder instead. Do not decide that for them, and do not ask again for the same domain
  in the same session.

## Say where it lands before it lands

- [ ] Before any write, search the vault for the claim's key words and its synonyms
  (`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/search.py" "<words>"`), read what comes back,
  and tell the user in one or two lines where the new claim lands — one of four:
  **new** (nothing close), **continues [[X]]** (same cause, another case: a merge
  candidate), **contradicts [[Y]] (date)** (say which is newer and that the older may
  be superseded; never silently drop either), or **already there** (nothing to write).
  Only then write. This line is the product, not a courtesy: it is the one moment
  the user sees the vault working, and a repeated cause found here is a root the
  pile alone will never show. Skipping it because the claim "looks new" is how the
  vault grew 570 typed notes and fewer than ten generalisations.
- [ ] Count what the search returned. When the claim **continues** a third card on the
  same cause and no `Godnote —` page covers it yet, say so and offer to build one; on
  the user's word, write it. This is the only trigger the synthesis layer has besides a
  direct request; there is no scheduler. Measured before it existed: 570 cards, fewer
  than ten generalisations.
- [ ] A contradiction is not resolved by the write. Name it in the new note's
  `## Links` with the date of the older claim, and offer — never do unasked — to
  add a `Disputed-by:` line to the older note so a reader of either sees both.

## Godnote — a page built from cards

- [ ] A godnote is one conclusion drawn from several cards: the root under a pile of
  symptoms, "what we know about X". Filename `Godnote — <conclusion>.md`, frontmatter
  `type: godnote`, `project:`, `date`, `tags: [godnote]`; the body is a BLUF, then the
  argument, then `## Cards` listing every card it was built from as a `[[link]]` with one
  line each. A godnote with no cards behind it is a claim, not a page.
- [ ] Cards outrank godnotes. A card was written by the script, at the time, in the
  user's words; a godnote is a rewrite. When they disagree, the card is right and the
  godnote is stale: rebuild it wholesale from its cards, do not patch a paragraph.
- [ ] `legacy: true` marks the pages written before this rule, with no cards behind
  them. They are not evidence. Rebuild one when the work touches it, never in bulk.

## Preserve the knowledge graph

- [ ] Expect the writer to answer `rejected` when the note has no `[[link]]` outside a
  code block, or a decision or gotcha lacks `Because:` or `Fails-when:`. That is the
  shape the graph and the reader need, and the writer holds it because a checklist line
  did not: three weeks after it became prose, atoms carrying `Fails-when` fell from 73%
  to 41%. Fix the note and repeat the call; there is no flag around it.
- [ ] Expect `neighbours` beside a `created` status: the nearest notes the vault already
  holds. Offer them to the user as links with one line each on why they touch this
  claim — a bare link is noise — and, on their word, add the accepted ones under
  `## Links` with `action: "replace"` and `expected_sha`. Never add them unasked.
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
  page the rule was meant to prevent. When the claim clearly belongs to a broader topic
  that has no note yet, link that topic by its **bare name** (`[[OAuth]]`), never by a
  hub name (`[[MOC — OAuth]]`): a bare name linked often enough is the demand signal
  that earns a MOC, whereas a hand-written `MOC — ` link is the invented hub this rule
  forbids — and the demand counter, which only sees bare names, never records it, so the
  hub it was reaching for can never be born. Put links in prose, never inside code blocks.
- [ ] Write vault notes only by sending the complete filename and Markdown content
  JSON to `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault-write.py"`. On `exists-same`, report that the note
  was already present. On `conflict`, stop and tell the user; never replace or
  reconcile the content on your own.
