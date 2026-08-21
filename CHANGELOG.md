# Changelog

## 0.1.1 — 21.08.2026

Ребриф цели и три дефекта, найденные при разборе беты.

- Цель проекта в спеке приведена к фактической: личный инструмент, а не
  исследовательский проект. §11 «не строить новый mnemo, пока не доказано» снят
  сознательно, с датой и причиной. Роль проверки передана журналу `:feedback`;
  триггер возврата к векторам и замеру — 3 записи `proven-miss`, считает скрипт.
- `search.py`: обход графа перестал быть мёртвым кодом. Соседи находились и
  выбрасывались бюджетом — на живом волте 105 найденных соседей давали 0 фрагментов
  в выдаче. Починены сортировка перед бюджетом, отсечка вместо жадной набивки,
  резерв бюджета под графовую ветку. Добавлены `--vault` и `--db` (и `--db` в
  `index.py`), чтобы проверка на тестовом волте не трогала боевой индекс.
- Удалён handoff-индекс: писатель без читателя, скопированный из mnemo вместе
  с константами. −157 строк.
- `SessionStart`-хук: обновляет индекс перед чтением, отдаёт хвосты только текущего
  проекта, помечает поданную память как цитату. Убран двойной префикс в именах сессий.

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
