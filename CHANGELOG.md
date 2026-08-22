# Changelog

## 0.3.1 — 2026-08-22

- Маршрут установки: `.claude-plugin/marketplace.json`, так что плагин ставится
  через `/plugin marketplace add szarkans/dont-forget` + `/plugin install`.
  Проверено вживую, а не написано по памяти.
- README переписан по фактам: шесть команд вместо пяти, `checkup` про здоровье
  волта (а не плагина), `review` про разбор сессии (а не откат), поиск назван
  тем, чем является — полнотекстовый SQLite плюс обход ссылок, без эмбеддингов.
  Добавлен обязательный шаг с `~/.dont-forget/config.json`.
- Переводы README на русский и китайский.
- Файл `LICENSE` (MIT) — раньше лицензия была заявлена в двух местах и ни в одном
  не лежала.
- Из кода убраны имена личных проектов: докстринга `hot-scan.py` и фикстура
  самотеста стали безликими.
- `experiments/` больше не в git: бенчмарк содержит реальные вопросы и заголовки
  заметок волта, публиковать их нельзя.

## 0.3.0 — 2026-08-22

- `about` теперь составляет поисковый запрос из вопроса и контекста (проект, ветка, свежая память), а не передаёт сообщение слово в слово. Замер: +6 попаданий / 0 потерь на обеих половинах бенчмарка (experiments/RESULTS.md).
- Теневой лог: каждый вызов поиска пишет строку в `~/.dont-forget/queries.jsonl` (сырой вопрос через `--raw`, отправленный запрос, топ-5, weak_match). Это копилка проспективной фазы бенчмарка.
- experiments/: замороженный бенчмарк — протокол, 81 настоящий вопрос + 10 ловушек, слепая разметка, 9 методов, одноразовый финальный прогон. Итог: движки неразличимы, прирост даёт только переписывание запроса; эмбеддинги и Obsidian-поиск не дали ничего.

## 0.2.1 — 21.08.2026

Реколл провалился на живом вопросе: «какие у нас там гочи по текстурам кошкокрафта»
не нашёл ни одной из четырёх заметок про текстуры, которые в волте есть. Две причины,
обе в ранжировании.

- **Словоформа больше не решает, найдётся заметка или нет.** Кириллица ищется префиксом,
  а префикс — это «слово начинается с», поэтому `"текстурам"*` совпал с 1 чанком из 3132:
  заметки написаны «текстуры». Теперь префикс укорачивается, пока укорачивание заметно
  прибавляет чанков, и останавливается на корне — там, где прибавка иссякает. Словарь
  для этого не нужен: словарём работает сам индекс, поэтому правило не привязано ни к
  одному языку. Редкое точное слово не размывается: если короче ничего не прибавляет,
  слово остаётся как есть.
- **Служебные слова больше не двигают выдачу.** Дело было не в частоте: idf душит частое,
  но «какие» сидит в 10 чанках из 3132 и по формуле оказалось самым информативным словом
  запроса — хаб набрал 12,4 балла на «какие» + «у» + «по». Теперь в счёт идут только
  слова, которые сидят не больше чем в 5% волта, и укорачивание само загоняет вопросные
  слова в этот разряд («какие» → «как», четверть волта). Списка стоп-слов нет — он был бы
  свой на каждый язык. У порога есть абсолютный пол в 20 чанков: в маленьком волте 5% —
  это две заметки, и без пола любое настоящее слово выглядело бы служебным.

Побочный эффект: `weak_match` стал срабатывать чаще и честнее — он считает совпавшие
содержательные слова, а раньше в этот счёт попадали «какие», «у» и «по», из-за чего флаг
молчал на вопросе, ответа на который в волте нет.

Проверка: два новых самотеста в `selftest_index_search.py` — заметку, написанную одной
словоформой, находит запрос в другой; запрос из шести служебных слов и одного
содержательного выдаёт заметку по содержательному. Цена: поиск 0,16 с → 0,23 с.

## 0.2.0 — 21.08.2026

Ревью качества: поиск перестал уверенно отвечать на то, чего в волте нет.

- **Ранжирование по покрытию запроса, а не по bm25.** bm25 награждает короткий чанк
  с одним редким словом сильнее, чем длинный со всеми словами запроса: на живом волте
  запрос «правило остановки» первым выдавал заметку про бэкапы другого проекта. Теперь чанки
  сортируются по сумме idf *различных* совпавших слов, bm25 — только тай-брейк. Веса
  и совпадения спрашиваются у самого FTS5 (по одному запросу на слово), чтобы они не
  разъезжались с тем, что индекс реально сматчил.
- **`weak_match` — признание «в волте этого нет».** Флаг поднимается, когда ни один
  чанк не содержит даже двух значимых слов запроса (значимое = встречается меньше чем
  в половине чанков). Проверено на восьми живых запросах: поднялся ровно на двух
  заведомо отсутствующих темах, на шести реальных — нет, и топ-1 у всех шести по делу.
  Скилл `about` обязан сказать это первой фразой и не синтезировать ответ.
- **Граф больше не усиливает мусор.** Соседи берутся только у фрагментов, которые
  реально попали в выдачу, а не у всех топ-20 bm25. При `weak_match` обход графа не
  запускается вовсе. Раньше плохой топ-1 гарантированно затаскивал в ответ ещё и двух
  своих соседей.
- **Бюджет считал 42% вывода.** `coverage.expanded_notes` вываливал двадцать полных
  заголовков — 6600 байт при бюджете 8000, которые скилл всё равно велел свернуть.
  Теперь это число. Тот же запрос: stdout 17105 → 11652 байта, coverage 6600 → 303.
- **`matched_total` был насыщенным счётчиком** — упирался в `LIMIT 200` и врал
  одинаково при 201 и при 5000 совпадений. Заменён на честный `matched_chunks` по
  всему волту плюс отдельный `pool_examined` (сколько переранжировано).
- `hot-scan.py`: список из шести заголовков «незаконченного» был скопирован в SQL
  руками, а константа `PENDING_HEADINGS` не использовалась нигде — седьмой заголовок
  молча бы не заработал. Теперь SQL строится из константы.
- `hot-scan.py`: ошибка обновления индекса больше не выбрасывается молча — тихо
  протухший индекс неотличим от пустого. Теперь строка про это едет в дайджест.
- `refresh_index` переехал из `search.py` в `index.py` и вызывает `build()` напрямую
  вместо запуска подпроцесса с разбором его stderr. Заодно ушёл хак с `sys.path`
  в `hot-scan.py` — каталог скрипта и так лежит в `sys.path`.
- Новый `scripts/common.py`: пути, чтение конфига и открытие базы на чтение. До этого
  `index.py` понимал `~/vault` буквально, а `vault-write.py` — как домашний каталог,
  то есть заметки писались в одно место, а искались в другом.
- `vault-write.py`: блок атомарной записи был скопирован дважды слово в слово.
- Самотесты переименованы `test_*.py` → `selftest_*.py`. Под старым именем `pytest`
  собирал ноль тестов и отвечал «no tests ran» — зелёное ничто в любом CI. Запуск:
  `for t in scripts/selftest_*.py; do python3 "$t"; done`.
- Найдено живой проверкой уже после правки: граф-полоса резервировала свою долю
  заранее, поэтому один текстовый фрагмент крупнее остатка (в живом волте такие есть —
  чанк без точек и пустых строк не режется на части) оставлял выдачу вообще пустой.
  Полосы поменяли порядок: текст заполняется первым, соседи берут остаток, и только
  если соседи есть, но не влезли, хвост текста отдаёт зарезервированную долю. Регрессия
  закреплена тестом, который со старым порядком падает.
- Найдено дедупликацией при сохранении заметок: чанкер не резал текст без точек и
  пустых строк, поэтому MOC — список викиссылок — оставался одним чанком в 10 КБ,
  крупнее всего бюджета выдачи. Запрос, у которого лучшее совпадение было таким MOC,
  возвращал **ноль фрагментов** при 153 совпадениях: фрагмент не влезал, seed'ов для
  графа не оставалось, соседей тоже. Теперь неделимая единица режется по строкам.
  После правки самый большой чанк волта — 1096 символов вместо 10595.
- Мелочи: `checkup.py` открывал базу иначе, чем остальные (ломалось на путях со
  спецсимволами), и держал русскую строку ошибки в англоязычном коде.

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
