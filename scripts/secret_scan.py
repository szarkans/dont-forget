#!/usr/bin/env python3
"""Spot credentials in text on its way into the vault.

Why this is code and not a line in a skill: the writing skill has always said "do not
save secrets", and the writer would still store a password without blinking. It matters
now because session transcripts are about to be read and proposed for saving, and a
sub-agent reading one real transcript surfaced "the password leaked in plain text", "the
RCON password leaked into an open chat" and "the forwarding secret hit the log twice".

It warns and never blocks. That is a deliberate choice, not an oversight: the note is
written either way and the warning rides along with it.

The rules are borrowed, the runner is ours. There is no single-file secret scanner for a
plugin with zero dependencies, but gitleaks publishes its rules as data (MIT) written for
an engine that, like Python's re, has no backreferences — so the patterns port almost
verbatim. Only the precise ones are taken. A regex is good over code and bad over prose,
and notes are prose; the documented spiral is that a scanner fires on an example key in
documentation, someone adds an exception, the tenth exception covers everything, and a
real leak drives through. So: no entropy rule for the AWS secret key (no checksum, every
base64 string trips it) and no "long opaque string" rule without a hint word next to it.

GitHub tokens are matched by prefix and length, without verifying the base62 CRC32 the
format carries. The checksum would cut false positives, but getting it subtly wrong
rejects every real token instead — and a missed secret costs more here than a warning
about a string that turned out to be harmless.
"""

from __future__ import annotations

import re

# A value that is obviously a stand-in, not a credential. Notes are full of these.
PLACEHOLDER = re.compile(r"^(?:\*+|x+|<.*>|\$\{.*\}|\.{3}|…|changeme|password|secret|none|null|"
                         r"your[_-].*|my[_-].*|example.*|placeholder.*|redacted.*|"
                         r"dummy.*|fake.*|sample.*|test[_-].*|.*[_-]for[_-]tests?)$", re.IGNORECASE)

RULES: list[tuple[str, re.Pattern]] = [
    ("private key block", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{50,})\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("Slack webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/+]{20,}")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe key", re.compile(r"\b[sr]k_(?:live|test)_[0-9A-Za-z]{20,}\b")),
    ("npm token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
]

# The one rule that needs a hint word beside it, because the value alone says nothing.
# No leading \b: an underscore is a word character, so \bpassword would never match the
# name it is most often written under, DB_PASSWORD. The value may be quoted, and a quoted
# value may contain spaces — "correct horse battery staple" is a passphrase, and stopping
# at the first space missed it.
ASSIGNMENT = re.compile(
    r"(?i)(password|passwd|pwd|api[_-]?key|secret[_-]?key|secret[_-]?access[_-]?key|"
    r"client[_-]?secret|access[_-]?token|auth[_-]?token|пароль)\b"
    r"\s*[:=]\s*(?:\"([^\"\n]{8,})\"|'([^'\n]{8,})'|([^\s\"'`,;]{8,}))")


def find_secrets(text: str) -> list[str]:
    """Names of the rules that fired, in order, without ever echoing the value itself."""
    found = []
    for name, pattern in RULES:
        if pattern.search(text):
            found.append(name)
    for match in ASSIGNMENT.findall(text):
        value = next((group for group in match[1:] if group), "")
        if value and not PLACEHOLDER.match(value):
            found.append("password or key assignment")
            break
    return found


def warning(text: str) -> str:
    """One line naming what was spotted, or empty when nothing was."""
    found = find_secrets(text)
    if not found:
        return ""
    return ("possible secret in this note: " + ", ".join(found) +
            ". It was written anyway — check it and rotate the credential if it is real.")


if __name__ == "__main__":
    import sys
    print(warning(sys.stdin.read()) or "nothing that looks like a secret")
