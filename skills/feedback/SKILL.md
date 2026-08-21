---
name: df-feedback
description: Log proven memory-search failures and wins when they occur.
---

# Memory search evidence log

Record only facts that are mechanically proven in the current session. This is an event log, not a retrospective opinion channel.

- [ ] Write at the moment the evidence appears; do not reconstruct an entry later merely because someone asks.
- [ ] Log `noise` when search results are irrelevant or noisy.
- [ ] Log `false-note` when a returned note is demonstrably false or outdated.
- [ ] Log `proven-miss` when search returned nothing and the same session later found the note through `this` deduplication or a differently worded query.
- [ ] Log `saved-work` when a search result demonstrably avoided repeated work.
- [ ] Do not log a suspicion that the vault probably contains something. The whole vault is not visible, so that is not proof.
- [ ] Send one JSON object to `python3 scripts/feedback-log.py` with `verdict`, non-empty `query`, `notes` as a list of returned-note strings, and `note` as one line describing what happened.
- [ ] Use only these verdicts: `saved-work`, `noise`, `false-note`, or `proven-miss`.
- [ ] The script answers with the running `proven_misses` count. When it also returns a
  `trigger` field, show that line to the user: it is the agreed signal that full-text
  search alone is no longer enough.
- [ ] Keep the journal outside the vault at `~/.dont-forget/feedback.jsonl`; never write feedback into vault notes.

Example input:

```json
{"verdict":"proven-miss","query":"deployment rule","notes":["rules/deploy.md"],"note":"A differently worded query found the note later in this session."}
```
