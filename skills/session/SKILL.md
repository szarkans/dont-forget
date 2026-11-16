---
name: session
description: Close a work session — audit what was not saved, then record the session note and check the vault.
model: inherit
---

# Close a session

Two things happen here, in this order: an audit of what the session leaves unsaved, and —
only on the user's word — the writing. Showing is the default. An audit nobody acts on is
a report; an audit that writes on its own fills the vault with paraphrase.

Use the conversation as evidence and git as the reality check. Never claim work git does
not show, and never invent a candidate the conversation does not support.

## Let a fresh reader do the audit

You lived through this session, so you are its worst auditor: your context is full, and
whatever became routine along the way is now invisible to you.

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/transcript.py"`. It resolves this session
on whichever harness hosts it — Claude, Codex, OpenCode, OMP or Devin — freezes the
conversation into a snapshot file, and prints that file plus how many pieces it splits
into. Auditing a different session needs its coordinates instead:
`transcript.py --harness <host> --session <id>`, or `--path <file-or-uri>` for the
`opencode://` and `devin://` listings that `unclosed.py --list` prints.

Start one sub-agent per piece, in parallel, and give each the **snapshot file** plus its
own piece number — `transcript.py --snapshot <file> --piece N` — because a live store
keeps growing while they read, and a second session in the same project would otherwise
make a reader pick up someone else's conversation. Hand them the checklist below; never
paste the transcript into a prompt, since the whole point is that they read the file
rather than your recollection of it.

They return candidates and write nothing. You merge the lists, drop exact duplicates, and
keep every disagreement visible — two readers of two pieces see different things, which is
why it was split rather than duplicated. If no transcript is found, audit it yourself and
say plainly that this is the weaker check.

A session that ended before the skill ran shows up in the startup digest and in
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/unclosed.py" --list`. Audit it the same way: take
its path or URI from there and run `transcript.py --path <it>` — that only marks it as
closing; it is recorded as closed when the session note is written.

**The checklist for each reader:** which requests were completed and which were not; which
decisions were made and whether they were saved; commits, pull requests and anything
changed outside this repository; TODO/later/FIXME left in the conversation or the code;
errors that were never resolved; questions that were never answered; code-bound "never X"
/ "always Y" rules that belong in the repository rules. `<used name: args>` lines are
tool calls; `<image: path>` lines are attachments you may open when the picture itself
carries the evidence. Then `git log --oneline -15` and `git status --short`, and no
claim that outruns them.

## Present the candidates

The report has one shape on every harness: two tables, nothing else between the summary
line and them. Show the whole list, uncut — the user is the only filter that works, and
trimming it takes away their chance to catch what you read wrongly.

Findings — everything worth keeping, one row each:

| # | Finding | Kind | Destination |
|---|---------|------|-------------|
| 1 | <what was learned, with the evidence it rests on> | gotcha / decision / principle / pain / correction | vault / repo rules / runbook / nowhere |

Open threads — everything left unfinished:

| Thread | Who closes it | What kills it |
|--------|---------------|---------------|
| <the unfinished thing and its state> | user / next session / CI / nobody | <the event or condition that ends it> |

Give each candidate a destination: the vault, the repository rules, a runbook, or
nowhere; when you are unsure, nowhere. Disagreements between readers get their own rows —
two readers of two pieces see different things, which is why it was split.

Then log the whole table — accepted and rejected alike — so a wrongly judged `nowhere`
leaves a trail instead of dying silently: pipe the rows as JSON
(`[{"candidate": ..., "destination": ..., "kind": ...}]`) to
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/audit-log.py" --session <audit_session_id>`.
Logging is separate from writing: the journal records the proposal even when the user
says no to every write.

Sort every loose end by whether doing something kills it. "Merge PR #41" dies when someone
merges it — a thread. "The RCON password went out in public chat" describes how the world
is and no action erases it — knowledge, and it goes through `that`. Threads fall out of
the digest by freshness, so a fact parked among them is a fact thrown away.

Every thread must name what is unfinished and who finishes it. The most repeated kind of
thread in this vault was some wording of `e2e not verified this session` — 56 of 629
threads — and it named neither,
so no later session could act on it. Write "the release checklist in PR #41 has not been
run against prod — user runs it" instead.

Never run or simulate tests to fill a gap. Say which checks actually ran.

## Write, once they say so

`--full` is consent for the whole chain. Without it, stop here and offer.

1. Anchor first: the session's opening request, `git rev-parse HEAD`, `git status --short`.
2. Save accepted knowledge through `that`, one candidate at a time.
3. Log every refusal: pipe `{"verdict": "rejected", "query": "<the candidate>", "notes":
   [], "note": "<why they said no>"}` to
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/feedback-log.py"`. Each "no" is data; in a month
   it says what actually gets rejected, and the filter can be built on that instead of a
   guess.
4. Close the threads that are finished (below).
5. Write the session note (below).
6. Run `health` last — its commit is local to the vault, and asking for it separately turns
   a one-command close into a conversation.
7. Verify against the anchor: the notes exist in the vault and are indexed. Do not expect
   them in `git status` — `health` has just committed them. If nothing changed at all, say
   `already in order, nothing to redo`.

## Threads that are already finished

A digest offering work finished three weeks ago teaches the user to stop reading it. Pass
the threads it still shows to
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/threads.py" --check "<thread as the digest shows
it>"`, and read what comes back as evidence about **the thing named**, not a verdict on
the thread: "backport the merged PR #41" mentions something merged and is not itself done.

Close without asking only when the thread asked for exactly what the evidence shows —
`--close "<thread>" --reason "<the evidence>"`. Otherwise propose it, carrying that
evidence, and let the user decide. **Close nothing when the user is not there**: an open
thread costs a line in a digest, a wrongly closed one costs the work it was holding. Say
which threads you closed and on what. The note that recorded them is never edited.

## The session note

Threads live only in this note's pending section; the digest reads them from the index.

- [ ] Title `Session — YYYY-MM-DD <short topic>`, distinct from others that day — prefer a
  PR number, ticket or branch. The filename stem is the title.
- [ ] Frontmatter: `type: session`, `date`, `tags` with `session` first, `project` if known.
- [ ] Body: `# Session — …`, then `## Done`, `## Decisions`, `## Next steps / pending`.
- [ ] Unfinished threads go only in `## Next steps / pending`, as `- [ ]`, each carrying
  the state someone needs to resume: uncommitted work, the attempt that just failed, the
  plan half-executed. Say what is **in flight right now**, not only what was finished —
  the end of a context is the end of a session, and this note is the only handoff.
- [ ] `## Links`: a MOC if a fitting one exists (find it with `search.py`), otherwise the
  notes this session actually touched. Do not invent a hub to fill the slot.
- [ ] Write only through `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault-write.py"` with
  `filename` and `content`. If the name exists, offer `action: "replace"` with
  `expected_sha`; on `conflict`, stop and tell the user.
- [ ] A note with no `[[link]]` comes back `rejected` — the writer holds the graph's
  shape now. `neighbours` beside `created` are the nearest notes; offer them as links,
  add only the ones the user accepts.
- [ ] Report the note name, the thread count, what entered the index, what stayed unsaved.

Keep it factual and compact: commit ids, PR numbers, branches, failed approaches, and the
constraints someone needs to pick the work back up.
