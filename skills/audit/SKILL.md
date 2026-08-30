---
name: audit
description: Read what the vault says, once in a while — dead conditions, notes that may be one claim, and names the vault keeps asking for. Proposes; the only thing it ever writes is a mark you approved.
model: inherit
---

# dont-forget:audit — read the content, propose, write only what is approved

`health` checks the machinery and runs on every session close. This reads the content and
runs by hand, every couple of months. It is expensive because it asks a person to think,
not because it is slow.

The rule that governs everything below: **the system proposes, the user decides.** Nothing
is deleted, hidden or reordered, ever. The single thing this command writes is the `died:`
mark on a note whose death the user has just confirmed — and that is a mark, not a
removal: the note stays, and search keeps returning it. Absence of information is
irreversible; dead weight is not.

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/audit.py"` and work through its JSON.

## Death conditions

`dying` lists every note carrying a `dies-when`, newest first. For each, the question is
one question: **has that condition happened?** You often know — a release shipped, a
server moved, a version bumped. Say what you know and what you are guessing.

- Ask the user only about the notes where the answer plausibly changed. Reading thirty-five
  conditions aloud is how a review gets abandoned halfway.
- When the user confirms one has arrived, mark it: read the note, add `died: YYYY-MM-DD`
  to its frontmatter, and write it back with `vault-write.py`, `action: "replace"` and
  `expected_sha`. Change nothing else in the note.
- **Marking is not deleting.** The note stays, search still returns it, and the mark rides
  along in every fragment so the next reader is warned instead of misled.
- A note whose condition arrived is not automatically wrong. It is unverified — say that
  to the user rather than declaring the claim false.

## Molecule candidates

`molecule_candidates` are pairs of notes that keep coming back to the same questions, both
matched by the query text rather than dragged in by a link. That is the whole signal: a
pair the graph always carries together says something about the graph, not about the ideas.

- Read the pair and ask whether one claim sits above both. If it does, propose it: the
  thesis in one sentence, the two notes as its evidence.
- **Propose, never write.** A hundred generated generalisations turn the vault into model
  output, irreversibly — six months on nobody can tell their own thought from something an
  agent stitched together. Ten strong candidates a person actually reads beat a hundred.
- `searches_usable` against `searches_logged` says how much evidence there was at all. Zero
  candidates out of two usable searches means *no data*, not *nothing to merge* — say which
  of the two it is, every time.
- Existing molecule notes are left alone and are never called dead.

## Names the vault keeps asking for

`link_demand` lists names that notes point at and no note answers to, with every spelling
of the same name grouped together.

- Report it, and stop there. A high count says the topic recurs; it does not say what is
  needed — an alias, a plain note, a hub, or nothing at all because one typo was
  copy-pasted around. Automation picks wrong here.
- Two spellings on one line (`CraftEngine` and `craft-engine`) are one topic written
  twice, and the fix is usually an `aliases:` entry rather than a second note. Offer it;
  do not do it.
- The grouping only folds case and separators. One topic written in two alphabets —
  `Bitrix24` and `Битрикс24` — arrives as two separate rows, each with its own count, and
  no code will pair them for you. Read the list for that yourself; it is the known hole,
  and it is why the report lists names rather than counts alone.

## Reporting

Show the whole list, uncut, with a proposed action per item and the evidence beside it.
The user is the only filter that works, and a trimmed list takes away their chance to
catch what you read wrongly. When you are unsure what an item needs, the default proposal
is "nothing".

Say plainly at the end what you changed — usually nothing at all — and what is waiting on
their decision.
