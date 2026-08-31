<h1 align="center">don't forget</h1>

<p align="center"><i>...i'm with you in the dark.</i></p>

<p align="center"><a href="README.ru.md">[🇷🇺 →]</a> · <a href="README.zh.md">[🇨🇳 →]</a> </p>

yet another long-term memory for AI-agents - Zettelkasten notes in plain Markdown, Obsidian-style.  
highly inspired by [mnemo](https://github.com/jojoprison/mnemo).

<h2 align="center">what's this about?</h2>

a folder of `.md` files for every gotcha / session-handoff / idea / fact you hit while
working on your project and decided (or your agent decided) to save.

then you can recall "oh, we did that two years ago!" or "wait no, that messed up our
database two months ago". Search runs locally over SQLite full-text search plus a walk
across the `[[wiki-links]]` between your notes — you may find cross-references from
other projects you forgot existed.

<h3>> Why not [insert other memory plugin]?</h3> 

no reason. every plugin there is helping one particular dev with their problem. my problem is i can't remember every mistake or descision i made after a month.  
also - `don't forget` is just simple. no daemons, no node - one folder, `.md` files and couple of python scripts.

<h2 align="center">install</h2>

```
/plugin marketplace add szarkans/dont-forget
/plugin install dont-forget@dont-forget
```
or
```
claude plugin marketplace add szarkans/dont-forget
claude plugin install dont-forget@dont-forget
```

then

```
/dont-forget:setup
```

then

use Claude Code as usual! After some time you'll have your second-brain-ish.

requirements: `python3`. No pip installs, no dependencies — standard library only.

<h2 align="center">commands</h2>

`/dont-forget:that` — "hey, let's save this fact/gotcha/decision so we don't forget it".

`/dont-forget:about` — "hey why did we switch to postgres?", "what exactly was bad in
here?".

`/dont-forget:session` — end-of-session skill. Fresh sub-agents read the session
transcript and report what never made it into memory; nothing is written until you say
so. Then it records the session note, closes the threads that are demonstrably finished,
and checks the vault.

`/dont-forget:health` — health of your **vault**: commits it to git and reports what the
index actually sees.

`/dont-forget:audit` — the slow read, once in a while: notes whose stated expiry may have
arrived, notes that keep answering the same questions and may be one claim, and names the
vault keeps pointing at and never answers. It proposes; you decide.

`/dont-forget:setup` — points the plugin at your notes, or moves it to a different
folder later. Finds your vaults for you instead of asking you to type a path.

<h2 align="center">also</h2>

- after each `/dont-forget:session`, your agent will keep list of hanging threads - something you did not do in this session but should've. next session will get first 15 of those hanging threads per-project. can be configured.  
  - same goes with gotchas - new session will get 15 freshiest gotchas you had recently injected. can be configured.  
- your agent will get notification when close to autocompact to do `/dont-forget:session` as after compact it will have summary of your conversation with details lost. can be turned off or forced on.  

<h2 align="center">why your README written like that?</h2>

because its written by me, human. *mostly*.  
no buzzwords, no ai-b2b-saas readmes, just straight to the point.  
its breathtaking, isnt it?
