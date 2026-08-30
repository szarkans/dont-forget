# Changelog

## 0.10.0 — 2026-08-30

### Breaking
- Two commands are renamed and two are gone, so that each name finishes the plugin's own
  sentence: `this` is now `that` ("don't forget **that**…"), and `checkup` is now `health`.
  `review` no longer exists on its own — session close audits by default and writes only
  when you say so, which is what `review` was for. `feedback` is gone as a command; the
  journal it wrote is unchanged, and recall now writes to it where the evidence appears.
  Update any of your own aliases or notes that call the old names.

### Added
- The index keeps each note's `kind`, `source` and `project`, and search returns all
  three. They were written in every note and invisible to the code, so nothing outside the
  Markdown could tell a gotcha from a decision. The index rebuilds itself once on upgrade —
  measured at half a second for 915 notes.
- The session-start digest is a hot list: the freshest open threads and the freshest
  gotchas, both filtered by the project you are in, both capped by a config key
  (`hot_tails`, `hot_gotchas`, fifteen each). Gotchas get their own list because they are
  not chores — a standing warning used to sink down a list of tasks until it fell off.
- The writer warns when a note carries something that looks like a secret: private keys,
  cloud and service tokens, JWTs, and passwords written next to a name that says so. It
  warns and never blocks, on every write, and the warning comes back in the returned
  status as well as on stderr. `scan_secrets: false` in the config turns it off.
- The writer looks for near-duplicates before writing and answers `similar` with the notes
  it thinks already say this, instead of writing silently. Whether two notes are the same
  claim is a judgement, so it stays yours: repeat the call with `duplicates_checked` to
  write anyway.
- `audit` — a new command, run by hand every couple of months. It reads what the vault
  says rather than whether its machinery works: which notes carry a death condition that
  may have arrived, which notes keep answering the same questions and may be one claim,
  and which names the vault keeps pointing at and never answers. It proposes and changes
  nothing.
- A confirmed death condition is recorded as `died: YYYY-MM-DD`, and search carries it, so
  a note that has gone false is returned with a warning rather than quietly believed.
- Session close is audited by fresh sub-agents that read the session transcript as a file,
  rather than by the agent that lived through it and can no longer see what became
  routine. A long session is split at its compaction boundaries, one piece per reader.
- A finished thread can be closed without editing the note that recorded it. Closure is
  kept outside the vault, so a session note stays the dated snapshot it was. Threads
  naming a closed issue, a merged pull request or an existing commit close on that
  evidence alone; the rest need your word, and nothing closes when you are not there.
- A fixture runner (`scripts/behaviour-check.py`) checks what the agent does with search
  results — that a stale note is dated rather than stated as current, that an empty vault
  produces an admission rather than an invention, and that an instruction written inside a
  note is not obeyed. Run it before a release.

### Fixed
- Code fences are stripped before the digest looks for open threads, so a note that
  documents how threads are written no longer hands its example over as a live task. A
  fence now closes only on a bare marker at least as long as the one that opened it, which
  also fixes wikilinks being extracted out of nested examples.
- Project kinship no longer matches on a shared prefix. On a vault of 894 notes every
  prefix pair it matched was two genuinely different projects, and the widest of them put
  259 foreign notes into one project's reach; spelling drift is folded by normalising case
  and separators instead.
- The search log records how each note was found — by matching text or by following a
  link — which is what makes it possible to tell notes that answer the same questions from
  notes the graph walk always drags along together.
- The digest's seven-day window is gone rather than fixed. It was really eight days, it
  was a number nobody chose, and the top of a list answers the question better.
- An index built by an older version is refused in words instead of a raw traceback.

## 0.9.0 — 2026-08-28

### Changed
- `this` no longer asks which note type to use. Everything it writes is `type: atom`:
  session notes come from `session`, MOC pages are born from demand, generalisations
  across several atoms are proposed by a later vault audit, and where the knowledge came
  from is the `source:` field rather than a type. The choice cost thought on every save
  and changed the behaviour of no script.
- Linking a MOC is now conditional on a fitting one already existing. The old rule
  demanded every note reach a MOC, which pushed the agent into inventing hubs on empty
  ground — the disconnected page the rule existed to prevent.
- `this` now says what stays out: knowledge that ships with the tool — a documented flag,
  a framework default — belongs to its own manual. In the vault it is the worst kind of
  noise, written in common words so it matches many queries and answers none, while the
  byte budget drops real fragments to make room for it.
- Merging into an existing note requires a shared cause, not a shared symptom, and
  appends a dated case instead of rewriting the wording that is already there. Two
  deploys broken by skipped migrations are one note; one broken by migrations and one by
  an out-of-memory kill are two, because merged they become "prod sometimes falls over" —
  true and useless at the moment it would have to help.
- `review` and `session` stopped emitting `e2e not verified this session`. It was the
  single most repeated line in the reference vault (56 of 629 threads) and named neither
  what was unfinished nor who would finish it. A hanging thread now has to name both, so
  a later session can act on it.
- `review` and `session` separate work from knowledge. An item that dies when someone
  does it is a thread; a fact about how the world is goes to `this` as a note. Threads
  are shown by freshness and drop off the digest, so a gotcha parked among them was a
  gotcha thrown away.
- `checkup` commits the vault by naming the notes this session wrote rather than running
  `git add -A`. One vault is shared by parallel sessions, and staging everything committed
  a neighbour's half-written notes under this session's message.
- `about` reads a `type: session` fragment as a diary entry — how the work was thought
  about that day, not how things stand now — and prefers a later note when the two
  disagree. A decision reversed the next morning still sits unchanged in yesterday's diary.

### Added
- `volatility: hot | warm | cold` on new notes: how fast the claim goes out of date, which
  is a different question from how important it is. Nothing reads the field yet — it is
  being collected so that a later audit can ask "still true?" of `hot` notes only instead
  of the whole vault. The judgement itself gets reviewed at the end of September 2026.
- `checkup` reports `N uncommitted notes`. Now that it commits named paths, a session that
  dies mid-close leaves its notes outside Git indefinitely with nothing to sweep them up
  later; this line is what surfaces them.
- `about` answers a search that returned nothing with one fixed line —
  `Nothing in the vault about this.` — before anything else, then continues in the user's
  language. The wording is fixed so a fixture test can prove that an empty vault produces
  an admission rather than an invention.

## 0.8.3 — 2026-08-26

### Changed
- The autocompact Stop-hook speaks later now. Its warn nudge used to fire ~150k tokens
  before the compact point (or a quarter of the run-up, whichever was smaller) — on a
  600k window that landed around 425k, early enough to feel pushy. The default distance
  is now 80k, so the same window warns near 487k instead. The critical mark and the
  context-rot marks are unchanged.

### Added
- `autocompact_warn_margin` in `~/.dont-forget/config.json` sets that warn distance
  yourself — the token headroom you want before Claude Code auto-compacts. It must be
  above 50k (the critical margin) or it is ignored and the 80k default stands.

### Fixed
- `checkup` flags notes stranded on a small island cut off from the rest of the graph,
  not just notes with zero links. A cluster that links only itself used to read as
  healthy; it is surfaced as unreachable now.

## 0.8.2 — 2026-08-25

### Fixed
- `vault-write.py` now refuses a note whose frontmatter opens with `---` but is never
  closed. The indexer treated an unterminated block as no frontmatter at all and
  silently dropped every field (`type`, `date`, `dies-when`, `reviewed`) — the freshness
  signal shipped in 0.8.0 would just vanish, with no error anywhere. The write path
  catches it up front now. If a note already lost its metadata this way, add the closing
  `---` and reindex.

## 0.8.1 — 2026-08-24

### Fixed
- The `this` skill no longer tells you to keep `.` out of a note's filename stem. The
  write path stopped rejecting dots back in 0.6.1, so a claim like `bash 3.2` or
  `search.py` keeps its dot — the instruction had just lagged the code. `#` and `/` are
  still out (the writer rejects them). No action needed after upgrading.

## 0.8.0 — 2026-08-24

### What we did
Notes now travel with their freshness and search shows it: every fragment carries the
note's `type`, `date`, `reviewed`, and an optional `dies-when` expiry, and recall states
a stale-able observation as "valid as of that date, not re-verified" instead of quoting
it like a permanent rule.

### How
`search.py` returns the four fields on every fragment — `dies-when` rides in frontmatter,
so it reaches the reader even when the matched paragraph is not the one holding it;
`about` weighs them, reads age as the later of `date`/`reviewed`, and treats a newer note
linking back to an older one as a possible correction over the incoming-link walk search
already does; `this` invites a `dies-when:` field only when the author can name what ends
the claim.

### Why
A note that died when an external fact changed under it — a repointed DNS record, a
bumped version — used to come back byte-identical and indistinguishable from a live one,
and a dead note read as current is worse than a missing one.

### Other info
A signal, not a lifecycle system: no typed expiry, no supersession graph, no `checkup`
condition-checking, by design. Upgrading rebuilds the index once to add the `dies_when`
column. Closes #7, #8, #9, #11.

## 0.7.2 — 2026-08-24

### Fixed
- The autocompact Stop hook (`context-guard.py`) no longer fires spurious "context is
  about to compact" warnings mid-session. It used to guess each model's window from a
  hardcoded family table and clamp your configured `autoCompactWindow` down to it — a
  guess that put current large-window models (e.g. opus-4-8) at 200k and warned from
  ~125k on a session whose real window was far larger. It now trusts the configured
  `autoCompactWindow`, falling back to 1M when unset (the default on current accounts).
  Known cost: a genuinely small-window model run under a larger setting loses the
  compaction nudge — the model table that used to catch that is gone by design.

## 0.7.1 — 2026-08-24

### Fixed
- Skills now invoke their scripts by an absolute `${CLAUDE_PLUGIN_ROOT}/scripts/…`
  path instead of a bare `scripts/…` relative to "the plugin root". A skill runs in
  the user's project directory, so the relative path did not exist there and the agent
  had to hunt for the script by hand. No action needed after upgrading.

## 0.7.0 — 2026-08-23

### Added
- `aliases` frontmatter support: notes can list alternate names, stored in a new
  `aliases` column, and `[[wiki-links]]` resolve through them too. A title still wins
  over an alias, and an alias that names more than one note is left unresolved rather
  than guessed. This is a schema change — the first run after upgrading does one full
  index rebuild to pick it up.

### Changed
- `weak_match` is now computed from the idf mass share of the best chunk instead of a
  raw matched-terms count, so a hit on a rare word counts for more than a hit on a
  common one. Search results also report `unmatched_terms` — query words that don't
  appear anywhere in the vault, in any form.

### Fixed
- Index refresh now stats files first and skips reading + hashing anything whose
  mtime hasn't moved, instead of hashing the whole vault on every run. Measured
  ~38% faster no-change refresh on drvfs vaults. Closes #6.
  Known limitation: a content edit that lands with its old mtime preserved (some sync
  tools do this) is invisible to the gate until the next `--rebuild`.
- The Stop hook's context nudge now picks its checkpoint from the documented window
  per model family (haiku 200k; fable/mythos 1M; sonnet-5 967k, its default compact
  point; others 200k then 1M with `"[1m]"` evidence skipping the 200k stop) instead of
  always starting from the 200k floor, which fired ~3x early on native big-window
  models. A configured window is still capped by the model ceiling.

## 0.6.2 — 2026-08-23

### Fixed
- `feedback-log.py` no longer crashes on a corrupted `feedback.jsonl` line — malformed lines are skipped, same contract as `checkup.py`. (#2)
- Session notes without a `project` field appear in the session-start digest again, marked `no project`, after project-matched tails. (#4)
- Project matching respects word boundaries: `cat` no longer pulls `catcraft` tails; `acme` still matches `acme-corp`.

## 0.6.1 — 2026-08-22

- Dots are now allowed in note filenames. The ban was inherited from mnemo's
  obsidian-CLI workaround, and the cause it worked around is gone. The `#` ban
  stays — it would break wikilink heading anchors. (#5)
- `vault-write.py` now takes one exclusive flock on the vault directory before
  dispatching, making the check-then-write paths of create and replace
  race-free against concurrent `vault-write.py` runs; a concurrent writer can
  no longer be silently overwritten. Windows (no `fcntl`) runs unlocked. (#3)

## 0.6.0 — 2026-08-22

- The autocompact nudge now *offers* by default instead of instructing. Saving a session
  writes notes and commits a vault, and the hook fires on a schedule the user never asked
  for, so the decision goes back to them: the agent is told to explain what is about to
  happen and to wait for an answer. Two keys govern it now — `autocompact_nudge` (speak at
  all, default true) and `autocompact_autosave` (act without asking, default false). Set
  the second one to true if you would rather the close-out simply happened.

## 0.5.0 — 2026-08-22

- A Stop hook now speaks up before Claude Code auto-compacts, while the raw
  conversation still exists to be saved. It measures from the point compaction
  actually happens — `window - 33k`, because Claude Code holds room back for its own
  reply — and not from the window itself. That reserve is measured, not assumed:
  `compactMetadata.preTokens` across 44 auto-compacts and three different window
  settings (550k, 600k, 650k) put it at 29-33k every time. A mark measured from the
  window instead sits past a threshold no session ever reaches, and never fires.
- The window is resolved rather than guessed: `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, then
  settings, clamped to the model's own context. A transcript strips the `[1m]` suffix
  from model ids, so the ceiling is read from `lastModelUsage` in `~/.claude.json`,
  which keeps it and records what actually ran. Where nothing is recognised — a model
  served over a custom API, an unfamiliar plan — a 200k guess climbs by observation,
  since a session that outgrew a window proves the window is larger. Guessing low
  costs one early nudge; guessing high would silence the hook forever.
- A second scale warns at 500k and 900k tokens whatever the window allows. A long
  context degrades answers on its own, and saving does not undo that — only a new
  session does, which the hook suggests and never performs itself.
- Usage is read from the end of the transcript (the figure sits in its last few lines;
  reading forward costs tens of megabytes for one number) as input + output + cache
  tokens. Validated against `preTokens` on 26 compactions: median difference -726
  tokens, and low rather than high, so the hook errs towards speaking early.
- One message per stop, each mark spends once, and everything re-arms once usage falls
  below the lowest mark — which is how a compaction announces itself, so a second one
  in the same session still warns.
- Opt out with `"autocompact_nudge": false` in `config.json`. A seventh self-check
  covers the marks, the ceiling ladder, the window chain and the hook end to end.

## 0.4.1 — 2026-08-22

- The marketplace manifest was left at 0.3.2 while `plugin.json` said 0.4.0, so
  anyone installing through the marketplace got a manifest one release behind.
  Both manifests now carry the version; they are the only two places that do.

## 0.4.0 — 2026-08-22

- `/dont-forget:setup`: the vault is detected, not typed. Reads Obsidian's own
  registry (Linux, macOS, Flatpak, Snap, Windows, WSL — `C:\...` becomes
  `/mnt/c/...`), falls back to a bounded scan for `.obsidian`, never picks when it
  finds more than one. A typed path still works.
- A missing config used to surface as a `FileNotFoundError` about a file the user
  never created — including in the session-start digest, the first thing a fresh
  install printed. Every entry point now names the command to run.
- Session digest reads unticked `- [ ]` boxes instead of matching heading names.
  The old list lost threads two ways: names nobody listed, and SQLite's `lower()`
  folding ASCII only, which made its Cyrillic entries dead from the day they were
  added. Measured on a live vault: 359 tails before, 375 after, all 16 genuine.
- Prefix wildcard goes to every word of 4+ characters, not Cyrillic alone. The
  index's porter stemmer covers English and nothing else, so every other language
  had neither: a German vault answered `Textur` with 0 fragments, now 3. Tune
  split before and after: hit@3 0.517 both times, zero hit→miss transitions, traps
  unchanged. Not a no-op — 7 of 39 queries changed which notes they return. The
  final split stays locked.
- `DONT_FORGET_HOME` moves config, index and logs off `~/.dont-forget`.
- Sixth self-check (`selftest_setup.py`): discovery, Windows paths, every config
  error, and that setup keeps hand-added keys in `config.json`.

## 0.3.2 — 2026-08-22

- Changelog is now written in English. The plugin is open source, so its history
  has to be readable by whoever installs it; only the maintainer's own files stay
  in Russian. Entries for 0.1.1 through 0.3.1 were translated, not rewritten — the
  facts and measurements are unchanged.
- One more personal project name removed from the 0.2.1 entry.
- Release dates normalized to ISO (`2026-08-21`); three entries carried a
  day-first format.

## 0.3.1 — 2026-08-22

- Install route: `.claude-plugin/marketplace.json`, so the plugin installs with
  `/plugin marketplace add szarkans/dont-forget` + `/plugin install`. Verified by
  actually running it, not written from memory.
- README rewritten against the facts: six commands instead of five, `checkup` is
  about **vault** health (not plugin health), `review` is a look back over the
  session (not a rollback), and search is called what it is — SQLite full-text
  plus link traversal, no embeddings. Added the required
  `~/.dont-forget/config.json` step.
- README translated to Russian and Chinese.
- `LICENSE` (MIT) — the license was claimed in two places and present in neither.
- Personal project names removed from the code: the `hot-scan.py` docstring and a
  selftest fixture are now anonymous.
- `experiments/` is out of git: the benchmark holds real questions and real vault
  note titles, which cannot be published.

## 0.3.0 — 2026-08-22

- `about` now composes the search query from the question plus context (project,
  branch, recent memory) instead of passing the message through word for word.
  Measured: +6 hits / 0 losses on both halves of the benchmark.
- Shadow log: every search call appends a line to `~/.dont-forget/queries.jsonl`
  (raw question via `--raw`, the query actually sent, top-5, `weak_match`). This
  is the collection jar for the prospective phase of the benchmark.
- Frozen search benchmark: a protocol, 81 real questions + 10 traps, blind
  labeling, 9 methods, one single final run. Result: the engines are
  indistinguishable; the only gain comes from rewriting the query. Embeddings and
  Obsidian's own search added nothing.

## 0.2.1 — 2026-08-21

Recall failed on a live question: "what are our gotchas with the textures on
project X" found none of the four texture notes that are in the vault. Two causes,
both in ranking.

- **Wordform no longer decides whether a note is found.** Cyrillic is searched by
  prefix, and a prefix means "the word starts with", so `"текстурам"*` matched 1
  chunk out of 3132: the notes are written "текстуры". Now the prefix is shortened
  while shortening keeps adding a meaningful number of chunks, and stops at the
  stem, where the gain dries up. No dictionary is needed for this: the index itself
  acts as the dictionary, so the rule is not tied to any one language. A rare exact
  word is not blurred: if shortening adds nothing, the word is left as it is.
- **Function words no longer move the results.** The problem was not frequency:
  idf suppresses the common, but "какие" ("which") sits in 10 chunks out of 3132
  and by the formula came out as the most informative word in the query — a hub
  scored 12.4 points on "какие" + "у" + "по". Now only words present in at most 5%
  of the vault count, and the shortening itself drives question words into that
  bracket ("какие" → "как", a quarter of the vault). There is no stop-word list —
  it would have to be written per language. The threshold has an absolute floor of
  20 chunks: in a small vault 5% is two notes, and without the floor every real
  word would look like a function word.

Side effect: `weak_match` now fires more often and more honestly — it counts
matched content words, and previously "какие", "у" and "по" were counted too, which
kept the flag silent on a question the vault has no answer to.

Verification: two new selftests in `selftest_index_search.py` — a note written in
one wordform is found by a query in another; a query of six function words and one
content word returns the note via the content word. Cost: search 0.16 s → 0.23 s.

## 0.2.0 — 2026-08-21

Quality review: search stopped answering confidently about things the vault does
not contain.

- **Ranking by query coverage, not by bm25.** bm25 rewards a short chunk holding
  one rare word more than a long chunk holding every word of the query: on the live
  vault, the query "правило остановки" ("stopping rule") returned a note about
  another project's backups first. Chunks are now sorted by the summed idf of
  *distinct* matched words, with bm25 as a tie-break only. Weights and matches are
  asked of FTS5 itself (one query per word) so they cannot drift from what the
  index actually matched.
- **`weak_match` — an admission that "the vault does not have this".** The flag is
  raised when no single chunk holds even two meaningful words of the query
  (meaningful = occurring in fewer than half of the chunks). Checked on eight live
  queries: raised on exactly the two topics known to be absent, not raised on the
  six real ones, and the top-1 was on point for all six. The `about` skill must say
  this in its first sentence and must not synthesize an answer.
- **The graph no longer amplifies garbage.** Neighbors are taken only from
  fragments that actually made it into the results, not from all bm25 top-20. On
  `weak_match` the graph walk does not run at all. Previously a bad top-1 was
  guaranteed to drag two of its neighbors into the answer as well.
- **The budget was spending 42% of the output on bookkeeping.**
  `coverage.expanded_notes` dumped twenty full titles — 6600 bytes against an 8000
  budget, which the skill was told to collapse anyway. It is now a number. Same
  query: stdout 17105 → 11652 bytes, coverage 6600 → 303.
- **`matched_total` was a saturating counter** — it hit `LIMIT 200` and lied
  identically at 201 and at 5000 matches. Replaced by an honest `matched_chunks`
  over the whole vault plus a separate `pool_examined` (how many were re-ranked).
- `hot-scan.py`: the list of six "pending" headings had been copied into the SQL by
  hand while the `PENDING_HEADINGS` constant was used nowhere — a seventh heading
  would have silently failed to work. The SQL is now built from the constant.
- `hot-scan.py`: an index-refresh error is no longer swallowed — a silently stale
  index is indistinguishable from an empty one. The digest now carries a line about
  it.
- `refresh_index` moved from `search.py` to `index.py` and calls `build()` directly
  instead of spawning a subprocess and parsing its stderr. The `sys.path` hack in
  `hot-scan.py` went away with it — the script's own directory is on `sys.path`
  anyway.
- New `scripts/common.py`: paths, config reading, and read-only database access.
  Before it, `index.py` read `~/vault` literally while `vault-write.py` expanded it
  to the home directory, so notes were written to one place and searched for in
  another.
- `vault-write.py`: the atomic-write block had been copied out twice, word for
  word.
- Selftests renamed `test_*.py` → `selftest_*.py`. Under the old name `pytest`
  collected zero tests and answered "no tests ran" — a green nothing in any CI.
  Run them with `for t in scripts/selftest_*.py; do python3 "$t"; done`.
- Found by a live check after the fix: the graph lane reserved its share up front,
  so a single text fragment larger than the remainder (the live vault has them — a
  chunk with no periods and no blank lines is never split) left the results
  completely empty. The lanes swapped order: text fills first, neighbors take the
  remainder, and only if neighbors exist but do not fit does the tail of the text
  give up the reserved share. The regression is pinned by a test that fails under
  the old order.
- Found while deduplicating notes on save: the chunker did not split text without
  periods or blank lines, so a MOC — a list of wiki-links — stayed a single 10 KB
  chunk, larger than the entire output budget. A query whose best match was such a
  MOC returned **zero fragments** against 153 matches: the fragment did not fit, no
  seeds were left for the graph, and so no neighbors either. An indivisible unit is
  now split by lines. After the fix the largest chunk in the vault is 1096
  characters instead of 10595.
- Small things: `checkup.py` opened the database differently from everything else
  (which broke on paths with special characters) and held a Russian error string in
  otherwise English code.

## 0.1.1 — 2026-08-21

A rebrief of the project's goal, and three defects found while reviewing the beta.

- The goal in the spec was brought in line with reality: a personal tool, not a
  research project. §11 ("do not build a new mnemo until it is proven") was lifted
  deliberately, with a date and a reason. The verification role passed to the
  `:feedback` journal; the trigger for returning to vectors and measurement is 3
  `proven-miss` entries, counted by a script.
- `search.py`: the graph walk stopped being dead code. Neighbors were being found
  and then thrown away by the budget — on the live vault, 105 neighbors found
  produced 0 fragments in the output. Fixed: sorting before the budget, a cut-off
  instead of greedy filling, and a budget reserve for the graph branch. Added
  `--vault` and `--db` (and `--db` in `index.py`) so a check against a test vault
  does not touch the real index.
- Removed the handoff index: a writer with no reader, copied from mnemo along with
  its constants. −157 lines.
- `SessionStart` hook: refreshes the index before reading, returns open threads for
  the current project only, and marks the injected memory as a quotation. Removed a
  doubled prefix in session names.

## 0.1.0-beta — 2026-08-21

First working build: a personal memory plugin for Claude Code that reimplements
mnemo's behavior over the same Markdown vault, with a self-owned search index.
The vault stays untouched as a data format; Obsidian remains the human interface.

### Added

- **Own search index** (`index.py`, `search.py`): SQLite FTS5 over vault notes,
  chunked by headings, lazy rebuild by mtime/sha256, no daemon, ~0.5 s full build.
  _In mnemo: search called `obsidian search` CLI once per keyword (2–4 calls per
  question) and required a running Obsidian app._
- **Prefix queries for Russian**: words of 4+ chars are searched as prefixes.
  _In mnemo: exact wordform match only — measured on the vault: "чанк" matched 5
  notes, "чанков" matched 19, with zero overlap between the two result sets._
- **Graph walk with a hub cap**: neighbors of text hits are included at lower
  rank; notes with more than 30 outgoing links are never expanded (default
  `--hub-cap 30`). _In mnemo: the link graph was built but never used in search;
  there was no expansion at all, and therefore no cap._
- **Honest coverage report** in every search result: total matches, returned,
  dropped by budget, hubs not expanded. _In mnemo: results were silently cut to
  7 notes._
- **Byte budget and chunks**: results are chunks of notes, not whole files.
  _In mnemo: up to 7 notes read in full._
- **Six commands**: `about`, `this`, `session`, `checkup`, `review`, `feedback`.
  _In mnemo there were seven: ask, save, session, review, connect, health, setup.
  `feedback` is new; `connect` and `setup` are dropped._
- **`this` writes via `vault-write.py`** (~80 lines: CAS by sha256, atomic
  `os.replace`, never overwrites; conflict is reported, not resolved). Code-bound
  never-X/always-Y rules are routed to `.claude/rules/`. _In mnemo:
  vault-write.py was 1294 lines (dir-fd, O_EXCL, hardlink-swap, openat
  containment), and every save cascaded into up to five backends: Obsidian,
  claude-mem (with an upstream-API-bug workaround in the hot path),
  Claude↔Codex runtime bridge (1744 lines), project rules, CLAUDE.md. We keep
  only the vault and project rules._
- **`session` keeps open threads in the note itself** (its own pending section);
  the handoff index receives one pointer line per session (idempotent upsert,
  200-byte lines, 56 KB cap, oldest dropped on overflow). _In mnemo: same
  pointer idea, but the live handoff file still carried a legacy block format
  (805 KB measured) plus five migration scripts and a resolver (~1100 lines)
  that existed only to clean up after the old format. We start clean: no legacy
  format, no migrations._
- **`checkup` puts the vault under git and reports health from our own index**:
  orphans, unresolved links (candidates, not verdicts), stale notes by type.
  _In mnemo: health had 12+ steps, several of them servicing other plugins
  (claude-mem version check, cross-runtime bridge status); orphan detection came
  from the Obsidian cache with a documented 1–5 s lag._
- **`review` keeps mnemo's full logic**: eight signal categories, git grounding
  (`git log`/`git status` as reality check), `--full` chain in fixed order
  (this → session → suggest checkup), origin anchor, idempotency check
  ("already in order, nothing to redo"). _Changed from mnemo: the chain omits
  `connect` (we don't have it) and `health` (we suggest `checkup` instead)._
- **`feedback`** (new, no mnemo analogue): a journal of proven-only memory
  outcomes — saved work, noise, false notes, proven misses — outside the vault
  (`~/.dont-forget/feedback.jsonl`). Suspected misses are never logged.
- **SessionStart hook**: digest of open threads from the last 7 days (8 KB
  budget) plus a one-line reminder. _In mnemo: the same hook existed, but hook
  registration was doubled — the manifest declared `claude-hooks.json` while
  Claude Code read `hooks/hooks.json`, so the manifest could not tell which hook
  was live. Ours has a single live file and the manifest points at it._
- **Vault under git**, initialized and committed by `checkup` (first snapshot:
  699 files). _In mnemo: not handled._

### Dropped (existed in mnemo, intentionally not reimplemented)

- Claude↔Codex cross-runtime memory bridge (`runtime-memory.py`, 1744 lines)
  and per-skill Codex branches — Codex support comes without a bridge.
- claude-mem cascade and its upstream-bug workaround.
- PARA taxonomy option, `setup` and `connect` commands.
- Handoff legacy format, five migration scripts, handoff resolver, handoff
  archiver.
- Stop/autocompact nudges and the invocation-echo hook (no measured need yet).
- Vector search / embeddings — postponed, see docs/BACKLOG.md.

### Known limits

- Skills and the SessionStart hook go live only after Claude Code is restarted.
- `review` audits the visible conversation; it does not parse session
  transcripts (mnemo used a 414-line JSONL scanner for that).
- Unresolved-link and stale lists are capped at 50 items per section.
