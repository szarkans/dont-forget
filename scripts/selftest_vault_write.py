#!/usr/bin/env python3
"""Dependency-free self-check for vault-write.py."""

import json
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("vault-write.py")


def invoke(vault: Path, filename: str, content: str, **extra: str) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"filename": filename, "content": content, **extra})
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--vault", str(vault)],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )


with tempfile.TemporaryDirectory(prefix="dont-forget-test-") as directory:
    vault = Path(directory)
    filename = "Atom — A useful claim.md"
    original = "---\ntype: atom\n---\nA useful claim. [[hub]]\n"

    created = invoke(vault, filename, original)
    assert created.returncode == 0, created.stderr
    assert json.loads(created.stdout) == {"status": "created"}
    assert (vault / filename).read_text(encoding="utf-8") == original

    same = invoke(vault, filename, original)
    assert same.returncode == 0, same.stderr
    assert json.loads(same.stdout) == {"status": "exists-same"}

    conflict = invoke(vault, filename, "different [[hub]]")
    assert conflict.returncode == 0
    assert json.loads(conflict.stdout) == {"status": "conflict"}
    assert "conflict" in conflict.stderr
    assert (vault / filename).read_text(encoding="utf-8") == original

    replacement = "---\ntype: atom\n---\nA better claim. [[hub]]\n"
    original_sha = hashlib.sha256(original.encode()).hexdigest()
    replaced = invoke(vault, filename, replacement, action="replace", expected_sha=original_sha)
    assert replaced.returncode == 0, replaced.stderr
    assert json.loads(replaced.stdout) == {"status": "replaced"}
    assert (vault / filename).read_text(encoding="utf-8") == replacement

    stale = invoke(vault, filename, "must not land", action="replace", expected_sha=original_sha)
    assert stale.returncode == 0
    assert json.loads(stale.stdout) == {"status": "conflict"}
    assert "conflict" in stale.stderr
    assert (vault / filename).read_text(encoding="utf-8") == replacement

    invalid_sha = invoke(vault, filename, "must not land", action="replace", expected_sha="nope")
    assert invalid_sha.returncode != 0
    assert (vault / filename).read_text(encoding="utf-8") == replacement

    before = set(vault.iterdir())
    refused = invoke(vault, "Atom — bad#name.md", "must not land [[hub]]")
    assert refused.returncode != 0
    assert set(vault.iterdir()) == before

    dotted = invoke(vault, "Atom — v1.2 broke.md", "dotted name content [[hub]]")
    assert dotted.returncode == 0, dotted.stderr
    assert (vault / "Atom — v1.2 broke.md").read_text(encoding="utf-8") == "dotted name content [[hub]]"

    before = set(vault.iterdir())
    unclosed = invoke(vault, "Atom — unclosed.md", "---\ntype: atom\nbody with no closing fence\n")
    assert unclosed.returncode != 0
    assert "frontmatter" in unclosed.stderr
    assert set(vault.iterdir()) == before

    closed = invoke(vault, "Atom — closed.md", "---\ntype: atom\n---\nbody [[hub]]\n")
    assert closed.returncode == 0, closed.stderr

    # A secret warns and never blocks: the note lands, and the warning rides along in the
    # returned status so it can be counted rather than scrolled past.
    leaked = invoke(vault, "Atom — leaked.md", "---\ntype: atom\n---\npassword: hunter2-battery-x [[hub]]\n")
    assert leaked.returncode == 0, leaked.stderr
    body = json.loads(leaked.stdout)
    assert body["status"] == "created", body
    assert "possible secret" in body["warning"], body
    assert "possible secret" in leaked.stderr
    assert (vault / "Atom — leaked.md").exists()

    # Prose *about* a leak is not a leak. This is the shape the vault actually holds, and
    # a scanner that fires on it teaches the reader to ignore every warning it prints.
    innocent = invoke(vault, "Atom — incident.md",
                      "---\ntype: atom\n---\nRCON-пароль засветился в открытом чате, ключ ротирован. [[hub]]\n")
    assert innocent.returncode == 0, innocent.stderr
    assert "warning" not in json.loads(innocent.stdout), innocent.stdout

    # The shape the graph needs is held by the writer, not by a checklist line: a note
    # with no [[link]] outside code fences, or a decision/gotcha without `Because:` and
    # `Fails-when:`, comes back `rejected` and nothing lands. Replace stays exempt.
    before = set(vault.iterdir())
    linkless = invoke(vault, "Atom — linkless.md", "---\ntype: atom\n---\nclaim [[#heading only]]\n\n~~~\n[[only in a fence]]\n~~~\n")
    assert linkless.returncode == 0, linkless.stderr
    verdict = json.loads(linkless.stdout)
    assert verdict["status"] == "rejected" and "[[link]]" in verdict["problems"][0], verdict
    assert set(vault.iterdir()) == before
    slotless = invoke(vault, "Atom — slotless.md", "---\ntype: atom\nkind: gotcha\n---\nGIVEN x WHEN y THEN z [[hub]]\n"
                      "because we forgot the Fails-when field\n```\n**Because:** in a fence\n**Fails-when:** in a fence\n```\n")
    verdict = json.loads(slotless.stdout)
    assert verdict["status"] == "rejected" and len(verdict["problems"]) == 2, verdict
    assert set(vault.iterdir()) == before
    shaped = invoke(vault, "Atom — shaped.md", "---\ntype: atom\nkind: gotcha\n---\nGIVEN x WHEN y THEN z\n**Because**: w\n**Fails-when:** v\n[[hub]]\n")
    assert json.loads(shaped.stdout)["status"] == "created", shaped.stdout
    sha = hashlib.sha256((vault / "Atom — shaped.md").read_bytes()).hexdigest()
    # A linkless note already on disk still answers exists-same: the gate is for what is
    # about to be written, and an idempotent replay writes nothing.
    (vault / "Atom — legacy.md").write_text("---\ntype: atom\n---\nold and linkless\n", encoding="utf-8")
    replay = invoke(vault, "Atom — legacy.md", "---\ntype: atom\n---\nold and linkless\n")
    assert json.loads(replay.stdout) == {"status": "exists-same"}, replay.stdout
    marked = invoke(vault, "Atom — shaped.md", "---\ntype: atom\nkind: gotcha\ndied: 2026-09-06\n---\nno links any more\n", action="replace", expected_sha=sha)
    assert json.loads(marked.stdout)["status"] == "replaced", marked.stdout

with tempfile.TemporaryDirectory(prefix="dont-forget-test-") as directory:
    # A near-duplicate is not written silently: the mechanics of finding one are code,
    # the judgement of whether it is the same claim stays with the person.
    home = Path(directory)
    vault = home / "vault"
    vault.mkdir()
    db = home / "index.db"
    (vault / "Atom — deploy.md").write_text(
        "---\ntype: atom\nkind: gotcha\n---\n\n# Atom — deploying without migrations kills prod\n"
        "\nOn catcraft a deploy without migrations took prod down. [[catcraft]]\n", encoding="utf-8")

    def write(filename: str, content: str, **extra) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--vault", str(vault), "--db", str(db)],
            input=json.dumps({"filename": filename, "content": content, **extra}),
            text=True, capture_output=True, check=False)

    duplicate = write("Atom — deploy again.md",
                      "---\ntype: atom\n---\n\n# Atom — deploying without migrations kills prod\n"
                      "\nA deploy without migrations took prod down on catcraft. [[catcraft]]\n")
    assert duplicate.returncode == 0, duplicate.stderr
    flagged = json.loads(duplicate.stdout)
    assert flagged["status"] == "similar", flagged
    assert flagged["candidates"][0]["path"] == "Atom — deploy.md", flagged
    assert not (vault / "Atom — deploy again.md").exists(), "a candidate is not a refusal to ever write"

    # ...and the same call with the judgement made writes it.
    confirmed = write("Atom — deploy again.md",
                      "---\ntype: atom\n---\n\n# Atom — deploying without migrations kills prod\n"
                      "\nA deploy without migrations took prod down on catcraft. [[catcraft]]\n",
                      duplicates_checked=True)
    assert json.loads(confirmed.stdout)["status"] == "created", confirmed.stdout
    assert (vault / "Atom — deploy again.md").exists()
    # A created note comes back with its nearest notes, so the links it should carry
    # arrive with the status instead of depending on a separate step.
    assert [n["path"] for n in json.loads(confirmed.stdout)["neighbours"]] == ["Atom — deploy.md"], confirmed.stdout

    # Nothing was written, so nothing can have leaked: the secret warning says "written
    # anyway" and must not appear on a path that wrote nothing.
    held = write("Atom — deploy leaked.md",
                 "---\ntype: atom\n---\n\n# Atom — deploying without migrations kills prod\n"
                 "\nA deploy without migrations took prod down. password: hunter2-battery-x [[catcraft]]\n")
    held_body = json.loads(held.stdout)
    assert held_body["status"] == "similar", held_body
    assert "warning" not in held_body, held_body

    # A JSON string is truthy, and skipping the duplicate check silently is the one
    # failure this flag must not have.
    stringy = write("Atom — deploy again 2.md",
                    "---\ntype: atom\n---\n\n# Atom — deploying without migrations kills prod\n"
                    "\nA deploy without migrations took prod down on catcraft. [[catcraft]]\n",
                    duplicates_checked="false")
    assert json.loads(stringy.stdout)["status"] == "similar", stringy.stdout

    # A session note is a dated snapshot, never a duplicate — and it reads like every
    # earlier session of the same project, so without an exemption the writer would
    # answer "similar" to every session ever recorded and session close would stall.
    (vault / "Session — 2026-08-29 dedup.md").write_text(
        "---\ntype: session\ndate: 2026-08-29\n---\n\n# Session — 2026-08-29 dedup\n\n"
        "## Done\n\nFixed dedup and secrets.\n", encoding="utf-8")
    session = write("Session — 2026-08-30 dedup.md",
                    "---\ntype: session\ndate: 2026-08-30\n---\n\n# Session — 2026-08-30 dedup\n\n"
                    "## Done\n\nFixed dedup and secrets again. [[dedup]]\n")
    assert json.loads(session.stdout)["status"] == "created", session.stdout

    # An unrelated claim is not held up by anything.
    fresh = write("Atom — slack.md",
                  "---\ntype: atom\n---\n\n# Atom — Slack webhooks carry a signature\n"
                  "\nThe signature travels in a header. [[Slack]]\n")
    assert json.loads(fresh.stdout)["status"] == "created", fresh.stdout

with tempfile.TemporaryDirectory(prefix="dont-forget-test-") as directory:
    # The scan is on by default and can be turned off without answering questions.
    home = Path(directory)
    vault = home / "vault"
    vault.mkdir()
    (home / "config.json").write_text(json.dumps({"vault": str(vault), "scan_secrets": False}))
    quiet = subprocess.run(
        [sys.executable, str(SCRIPT), "--vault", str(vault)],
        input=json.dumps({"filename": "Atom — quiet.md",
                          "content": "---\ntype: atom\n---\npassword: hunter2-battery-x [[hub]]\n"}),
        text=True, capture_output=True, check=False,
        env={"PATH": "/usr/bin:/bin", "DONT_FORGET_HOME": str(home)},
    )
    assert quiet.returncode == 0, quiet.stderr
    assert json.loads(quiet.stdout) == {"status": "created"}, quiet.stdout

print("ok")
