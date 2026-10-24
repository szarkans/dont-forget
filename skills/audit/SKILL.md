---
name: audit
description: Read what the vault says, once in a while — names the vault keeps asking for, and a meaning audit that finds topics without a godnote, notes that went stale, disagree or repeat. Links itself; everything that changes meaning waits for the user on a page.
model: inherit
---

# dont-forget:audit — read the content, the user decides what it means

`health` checks the machinery and runs on every session close. This reads the content and
runs by hand, rarely. The rule: **the system proposes, the user decides.** The one thing the
audit does by itself is add wiki-links between notes readers judged related — links are
reversible and the vault is in git. Nothing is renamed, merged, marked died or written as
a godnote without the user's mark on the page.

Two parts, independent: names the vault keeps asking for (`audit.py`), and the meaning audit
(`audit_semantic.py` + `audit_apply.py`). Run the first always; the second needs the
semantic lane (`setup.py --install-semantic` and `index.py --embed`).

## Names the vault keeps asking for

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/audit.py"`. `link_demand` lists names that
notes point at and no note answers to, with every spelling of the same name grouped together.

- Report it, and stop there. A high count says the topic recurs; it does not say what is
  needed — an alias, a plain note, a hub, or nothing at all because one typo was
  copy-pasted around. Automation picks wrong here.
- Two spellings on one line (`CraftEngine` and `craft-engine`) are one topic written
  twice, and the fix is usually an `aliases:` entry rather than a second note. Offer it;
  do not do it.
- The grouping only folds case and separators. `Bitrix24` and `Битрикс24` arrive as two
  separate rows and no code will pair them for you. Read the list for that yourself.

## The meaning audit

Vectors propose, readers judge, the user decides. Closeness by meaning is not sameness, so
every candidate is read in full by a reader agent before anyone acts on it.

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/audit_semantic.py" candidates` finds four kinds of
candidates — groups of cards no godnote links, close pairs across projects with no path
between them, close pairs inside one project, and notes the vault itself calls replaced,
outdated or corrected — and writes `batches/*.in.json` only for those no reader has judged
before. Verdicts live in `~/.dont-forget/audit/verdicts.json` and carry across runs; the
summary says how many were carried and how many need reading. It re-runs itself under the
plugin venv; the model is not loaded.

Give each batch's `prompt` file to one reader sub-agent, verbatim — the prompts in
`skills/audit/readers/` are tuned, do not paraphrase them. Readers only read the vault and
write the answer file. Then `audit_semantic.py merge`: it refuses the whole merge if any
answer names a key its batch did not ask about, so a wrong answer is never half-applied —
re-run that one reader. A batch with no answer is listed, not guessed.

Then `audit_apply.py --safe` (`--dry` first if the user wants to see the list). It links
cross-project pairs readers kept, pairs where one note refines the other, and the cards of
a group to the existing godnote that already carries its thought. Run `index.py` after.

`audit_semantic.py page` builds `runs/<date>/site/` with only what changes meaning: stale
notes, disputes, duplicates, titles a correction contradicts, groups that want a new godnote
or are ripe as a rule. Publish it as an artifact with the `db` capability; the page stores
each mark in the `decisions` collection under the finding's key (`{v: "yes"|"no", c}`).

When the user is done, export `decisions` with ArtifactData into a folder, one `<key>.json`
per document, and run `audit_apply.py --decisions <folder>`. It writes `heavy.json`: pairs
to fix, godnotes to write, and tasks — comments without a yes/no, which the user means as
work rather than an opinion. A "no" is dropped; its comment says why. The heavy work is
yours, with the user's mark as consent: read both notes before acting, because a verdict is
a reader's version, not a fact.

## Reporting

Lead with the few rows where you actually propose something, each with its evidence, in
plain words. Say how many links were added and that `git` in the vault undoes them; say
what is waiting on the user — the page link, or the heavy list.
