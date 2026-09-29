---
name: audit
description: Read what the vault says, once in a while — names the vault keeps linking to that no note answers. Proposes; writes nothing.
model: inherit
---

# dont-forget:audit — read the content, propose, write nothing

`health` checks the machinery and runs on every session close. This reads the content and
runs by hand, rarely. The rule: **the system proposes, the user decides.** Nothing is
created, deleted, hidden or reordered by this command.

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/audit.py"` and work through its JSON.

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

Lead with the few rows where you actually propose something — an alias for two spellings
of one topic, a note or hub for a name that clearly recurs — each with its evidence, in
plain words. The rest of the list is "nothing"; say how many rows that is and where the
full JSON is, rather than reading hundreds of rows aloud.

Say plainly at the end that nothing was changed and what is waiting on the user.
