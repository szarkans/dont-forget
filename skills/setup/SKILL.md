---
name: setup
description: "Point the plugin at a notes vault on first use, or move it to a different one. Also when recall reports that nothing is configured, or the configured folder is gone."
model: inherit
---

# dont-forget:setup — point the plugin at a vault

This plugin remembers by reading and writing plain Markdown files in one folder. Setup
is the single question of which folder that is. Everything else — the index, the logs —
is disposable and rebuilds itself, so nothing here is a decision the user has to live with.

Start with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" --detect`. It reports what is
configured now, what went wrong if anything, and every vault it found. Do not ask the
user to type a path before running it: the whole point is that they usually do not have to.

## Reading what came back

`candidates` is ordered best-guess first. `source: obsidian` means these came from
Obsidian's own registry — the same list its vault switcher shows, so the user will
recognise them. `source: disk-scan` means no registry existed and folders holding
`.obsidian` were found by walking the home directory; treat those as weaker guesses.

`notes` is there so the user can tell their vaults apart at a glance. `notes_capped`
means counting stopped early and the real number is higher.

A vault does not have to be an Obsidian vault at all. Any folder of Markdown files
works, and a user who keeps notes elsewhere should be offered the chance to name one.

## Choosing

Exactly one candidate and nothing configured yet: propose it and ask for a yes. One
question, not a menu of one.

Several candidates: show them with their note counts and let the user pick. Never
choose silently — the top of the list is a guess about which vault they meant, and
guessing wrong writes memory into the wrong place.

Nothing found: ask for a path. Say plainly that an empty folder is a fine answer if
they are starting fresh.

Already configured and the user is not asking to move: say what it points at and stop.
Re-running setup is not a reason to change anything.

## Applying

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/setup.py" --set <path>` writes the config and builds the first index in
one step. It reports the note, chunk and link counts it indexed — pass those on, because
they are the first evidence that the plugin can actually see the notes. Zero notes on a
folder the user believed was full means the wrong folder was chosen; say so rather than
reporting success.

Close by naming what the user can do next: recall with `/dont-forget:about`, save with
`/dont-forget:this`. A digest of the freshest open threads and gotchas appears on its own
at the start of the next session, so mention that it needs a restart to show up.
