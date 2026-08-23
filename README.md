# Don't forget

*I'm with you in the dark.*

Persistent memory for AI agents — Zettelkasten notes in plain Markdown, Obsidian-style.

Highly inspired by [mnemo](https://github.com/jojoprison/mnemo).

🌍 [Русский](README.ru.md) · [中文](README.zh.md)

## What is this about?

A folder of `.md` files for every gotcha / session-handoff / idea / fact you hit while
working on your project and decided (or your agent decided) to save.

Then you can recall "oh, we did that two years ago!" or "wait no, that messed up our
database two months ago". Search runs locally over SQLite full-text search plus a walk
across the `[[wiki-links]]` between your notes — you may find cross-references from
other projects you forgot existed.

> Why not claude-mem / \<insert other memory plugin\>?

claude-mem is fast memory (like RAM), the agent always sees it. dont-forget is
long-term memory (like an HDD) — for you and your agent both.

## Install

```
/plugin marketplace add szarkans/dont-forget
/plugin install dont-forget@dont-forget

or
claude plugin marketplace add szarkans/dont-forget
claude plugin install dont-forget@dont-forget
```

then

`/dont-forget:setup`

then

Use Claude Code as usual! After some time you'll have your second-brain-ish.

Requirements: `python3`. No pip installs, no dependencies — standard library only.

## Commands

`/dont-forget:this` — "hey, let's save this fact/gotcha/decision so we don't forget it".
Writes atomic notes and de-duplicates against what's already in the vault.

`/dont-forget:about` — "hey why did we switch to postgres?", "what exactly was bad in
here?". Searches the vault and answers with citations plus an honest coverage report —
including telling you straight when the vault has nothing on your question.

`/dont-forget:session` — end-of-session skill. Writes what you did into a session note
and indexes its open threads, so the next session picks them up.

`/dont-forget:review` — looks back over the session and audits it: what got done, what
you claimed was done, which facts and commitments never made it into memory.

`/dont-forget:checkup` — health of your **vault**: commits it to git and reports what
the index actually sees.

`/dont-forget:setup` — points the plugin at your notes, or moves it to a different
folder later. Finds your vaults for you instead of asking you to type a path.

`/dont-forget:feedback` — logs proven search failures and wins, so the search can be
fixed against evidence instead of vibes.

## What it does on its own

At the start of every session it injects your open threads from the last 7 days,
the unfinished checkboxes from your session notes. No command needed.

Also you will be notified when autocompact is close to run `/dont-forget:session` so you will not lose progress or to have good handoff for new session.

## Why is your readme written like that?

Because it's written by me, a human. I'm tired of b2b-ai-saas-agentic-loop-skills
descriptions, they're a pain in the ass to read.

Plain and to the point. For the rest of the details just ask your agent.

## License

MIT
