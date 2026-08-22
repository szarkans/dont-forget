# CONTRIBUTING

Just... Create a PR? idk lol

If you want to fix something — fix the **root** of the problem, not a symptom.
If you want to add something — follow the
[ponytail](https://github.com/DietrichGebert/ponytail) ladder:

1. do we really need this?
2. can we reuse something inside this project?
3. can we reuse something that already exists?
4. only then build something entirely new

simple = better

## Stuff you'd otherwise waste an afternoon on

**Run the tests like this:**

```bash
for t in scripts/selftest_*.py; do python3 "$t"; done
```

Plain asserts, no framework. Don't reach for `pytest` — the files are called
`selftest_*.py`, not `test_*.py`, so pytest collects nothing and cheerfully reports
"no tests ran". That green nothing has fooled us once already.

Touching anything non-trivial? Leave one runnable check behind. The smallest thing
that fails if your logic breaks.

**Your skill edits won't show up until you restart Claude Code.** Same for the
SessionStart hook. Edit, restart, then judge.

**You need a vault to run against.** Any folder of `.md` files:

```bash
python3 scripts/setup.py --set ~/some/notes
```

Don't point it at notes you care about while testing writes. `DONT_FORGET_HOME` moves
the config, index and logs somewhere else, which is how you keep a throwaway vault
without disturbing your real one:

```bash
DONT_FORGET_HOME=/tmp/df-test python3 scripts/setup.py --set /tmp/scratch-vault
```

## What gets a PR sent back

**No measured problem, no feature.** "This would be nice" isn't enough — show the
case where the current behavior actually fails. Same bar we hold ourselves to; the
whole search rewrite in 0.2.1 exists because one real question came back empty.

**Skills are written in English, and they're faceless.** No names, no personal
habits, no "at my company we...". Whoever installs this plugin is "the user". The
same goes for the README and the CHANGELOG.

**A skill body is a briefing, not a procedure.** Write what the agent is doing, why
it works that way, and where the traps are — not "Step 1, Step 2, see table".
A procedure turns the agent into a robot that freezes on anything the table
doesn't cover. If a step grows past ~10 lines, it belongs in `scripts/`, not in
the skill.

**Don't bump the version or write the changelog.** Maintainer does that — every
release is cut from `CHANGELOG.md`, and PRs bumping it just collide with each other.
