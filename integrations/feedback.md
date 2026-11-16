# Memory search evidence

Log a mechanically proven search win or failure when it happens, using
`python3 scripts/feedback-log.py`. Send one JSON object with `verdict`, non-empty
`query`, `notes` (returned note paths), and `note` (what proved the verdict).

Use `saved-work` for avoided repeated work, `noise` for irrelevant results,
`false-note` for demonstrably false content, or `proven-miss` when the same session
later finds a note the search missed. Suspicion is not proof. Show any returned
`trigger` to the user. Feedback stays outside the vault in `$DONT_FORGET_HOME/feedback.jsonl` (default `~/.dont-forget/feedback.jsonl`).
