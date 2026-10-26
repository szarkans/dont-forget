#!/usr/bin/env python3
"""audit_apply.py: links land where readers asked, page marks become the agent's heavy list."""

import _selftest_env  # noqa: F401

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import audit_apply
import audit_common as ac
from audit_apply import with_link

SCRIPT = Path(__file__).with_name("audit_apply.py")
GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

# One key for one set of notes, whatever order the members come in.
assert ac.finding_key("cross", ["Beta", "Alpha"]) == "l-f4b1049431ff9288"
assert ac.finding_key("cross", ["Alpha", "Beta"]) == "l-f4b1049431ff9288"
assert ac.finding_key("group", ["Gamma", "Alpha", "Beta"]) == ac.finding_key("group", ["Beta", "Gamma", "Alpha"]) \
    == "g-c8ad339f25f3f302"
assert ac.finding_key("same", ["Alpha", "Beta"]) == "d-f4b1049431ff9288"

# The link goes to the end of the Links list, before the next section.
assert with_link("# A\n\n## Links\n- [[Z]]\n\n## Notes\ntext\n", "B") == \
    "# A\n\n## Links\n- [[Z]]\n- [[B]]\n\n## Notes\ntext\n"
assert with_link("# A\n\n## Links\n\n- [[Z]]\n", "B") == "# A\n\n## Links\n\n- [[Z]]\n- [[B]]\n"
assert with_link("# A\n\n## Связи\n- [[Z]] — почему\n", "B") == "# A\n\n## Связи\n- [[Z]] — почему\n- [[B]]\n"
assert with_link("# A\nbody\n", "B") == "# A\nbody\n\n## Links\n- [[B]]\n"
assert with_link("# A\nbody", "B") == "# A\nbody\n\n## Links\n- [[B]]\n"
assert with_link("# A\n\n## Links\n- [[Z]]", "B") == "# A\n\n## Links\n- [[Z]]\n- [[B]]\n"
assert with_link("# A\nsee [[B|the other]]\n", "B") is None
assert with_link("# A\n\n## Links\n- [[B]]\n", "B") is None
assert with_link("# A\n\n## Links\n- [[B — longer]]\n", "B") == "# A\n\n## Links\n- [[B — longer]]\n- [[B]]\n"
# A CRLF note keeps CRLF, and its "## Links\r\n" is the section, not a reason for a second one.
assert with_link("# A\r\n\r\n## Links\r\n- [[Z]]\r\n\r\n## Notes\r\ntext\r\n", "B") == \
    "# A\r\n\r\n## Links\r\n- [[Z]]\r\n- [[B]]\r\n\r\n## Notes\r\ntext\r\n"
assert with_link("# A\r\nbody\r\n", "B") == "# A\r\nbody\r\n\r\n## Links\r\n- [[B]]\r\n"


def note(path, title, project="", sha=None):
    return {"title": title, "project": project, "date": "2026-01-01", "path": path, "file": "/elsewhere/" + path,
            "sha256": sha}


# A verdict holds while its notes are unchanged; a user's decision holds whatever happened to them since.
P, Q = note("P.md", "P", sha="p1"), note("Q.md", "Q", sha="q1")
pair = {"kind": "same", "key": "d-" + "1" * 16, "a": P, "b": Q}
assert ac.standing(pair, {"verdict": "мимо", "sha": {"P.md": "p1", "Q.md": "q1"}}) is not None
assert ac.standing(pair, {"verdict": "мимо", "sha": {"P.md": "p0", "Q.md": "q1"}}) is None
assert ac.standing(pair, {"verdict": "мимо"}) == {"verdict": "мимо"}
assert ac.standing(pair, {"verdict": "дубль", "sha": {"P.md": "p0"}, "decided": {"v": "yes"}}) is not None
assert ac.sort_findings({"same": [pair]}, {pair["key"]: {"verdict": "дубль", "sha": {"P.md": "p0", "Q.md": "q1"}}}) \
    == {"safe": [], "page": [], "decided": [], "dropped": {}, "unread": 1}

# A confirmed stale finding on O hides a same-project "устарело" only when O is that pair's old side.
O, N, R = note("O.md", "O"), note("N.md", "N"), note("R.md", "R")
stale_o = {"kind": "stale", "key": "s-" + "2" * 16, "how": "заменяет", "old": O, "new": None}
o_old = {"kind": "same", "key": "d-" + "3" * 16, "a": N, "b": O}
o_new = {"kind": "same", "key": "d-" + "4" * 16, "a": O, "b": R}
found = ac.sort_findings({"stale": [stale_o], "same": [o_old, o_new]}, {
    stale_o["key"]: {"confirmed": True}, o_old["key"]: {"verdict": "устарело", "old": "b"},
    o_new["key"]: {"verdict": "устарело", "old": "b"}})
assert [c["key"] for c, _ in found["page"]] == [o_new["key"], stale_o["key"]], found["page"]
assert found["dropped"] == {"same": 1}, found


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


with tempfile.TemporaryDirectory() as temp:
    vault = Path(temp) / "vault"
    files = {
        "p1/A.md": "# A\r\n\r\n## Links\r\n- [[Z]]\r\n\r\n## Notes\r\ntext\r\n", "p2/B.md": "# B\nbody\n",
        "p1/C.md": "# C\nbody", "p1/D.md": "# D\n\n## Links\n- [[C]]\n", "M1.md": "# M1\n",
        "M2.md": "# M2\n\n## Links\n- [[G]]\n", "G.md": "# G\n", "X.md": "# X\n", "Y.md": "# Y\n", "S.md": "# S\n",
        "p1/E.md": "# E\n", "p4/W.md": "# W\n",
    }
    for rel, text in files.items():
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_bytes(text.encode("utf-8"))
    (vault / "p3").mkdir()
    (vault / "p3/U.md").write_bytes(b"# U\n\xff\xfe not utf-8\n")
    files_bytes = {rel: (vault / rel).read_bytes() for rel in [*files, "p3/U.md"]}

    A, B = note("p1/A.md", "Gotcha — A", "p1", sha(files["p1/A.md"])), note("p2/B.md", "Gotcha — B", "p2", sha(files["p2/B.md"]))
    C, D = note("p1/C.md", "Fact — C", "p1"), note("p1/D.md", "Fact — D", "p1")
    M1, M2, G = note("M1.md", "Gotcha — M1"), note("M2.md", "Gotcha — M2"), note("G.md", "Godnote — G")
    X, Y, S = note("X.md", "Gotcha — X"), note("Y.md", "Gotcha — Y"), note("S.md", "Fact — S")
    E, F = note("p1/E.md", "Fact — E", "p1"), note("p2/F.md", "Fact — F", "p2")
    U, W = note("p3/U.md", "Fact — U", "p3"), note("p4/W.md", "Fact — W", "p4")
    keys = {name: f"{ac.LETTER[kind]}-{n:016x}" for n, (name, kind) in enumerate(
        [("ab", "cross"), ("ac", "cross"), ("cd", "same"), ("m", "group"), ("xy", "group"), ("dub", "same"),
         ("s", "stale"), ("unread", "same"), ("ab2", "same"), ("ef", "cross"), ("uw", "cross")])}
    candidates = {
        "date": "2026-01-01", "vault": str(vault),
        "group": [{"kind": "group", "key": keys["m"], "members": [M1, M2], "nearest_godnotes": [{**G, "links_to_group": 0}]},
                  {"kind": "group", "key": keys["xy"], "members": [X, Y], "nearest_godnotes": []}],
        "cross": [{"kind": "cross", "key": keys["ab"], "sim": 0.7, "a": A, "b": B},
                  {"kind": "cross", "key": keys["ac"], "sim": 0.7, "a": A, "b": C},
                  {"kind": "cross", "key": keys["ef"], "sim": 0.7, "a": E, "b": F},
                  {"kind": "cross", "key": keys["uw"], "sim": 0.7, "a": U, "b": W}],
        "same": [{"kind": "same", "key": keys["cd"], "sim": 0.7, "a": C, "b": D},
                 {"kind": "same", "key": keys["dub"], "sim": 0.9, "a": X, "b": Y},
                 {"kind": "same", "key": keys["unread"], "sim": 0.6, "a": D, "b": S},
                 # The same two notes as the cross pair "ab": the link is asked twice and written once.
                 {"kind": "same", "key": keys["ab2"], "sim": 0.6, "a": A, "b": B}],
        "stale": [{"kind": "stale", "key": keys["s"], "how": "поправка", "old": S, "new": None, "correction": "Поправка 2026-01-02"}],
    }
    verdicts = {
        keys["ab"]: {"key": keys["ab"], "kind": "cross", "keep": True, "common": "одно",
                     "sha": {"p1/A.md": A["sha256"], "p2/B.md": B["sha256"]}},
        keys["ac"]: {"key": keys["ac"], "kind": "cross", "keep": False},
        keys["cd"]: {"key": keys["cd"], "kind": "same", "verdict": "уточняет"},
        keys["ab2"]: {"key": keys["ab2"], "kind": "same", "verdict": "уточняет"},
        keys["ef"]: {"key": keys["ef"], "kind": "cross", "keep": True},
        keys["uw"]: {"key": keys["uw"], "kind": "cross", "keep": True},
        keys["m"]: {"key": keys["m"], "kind": "group", "keep": True, "name": "M", "thesis": "t", "extend": "G", "ripe": False},
        keys["xy"]: {"key": keys["xy"], "kind": "group", "keep": True, "name": "XY", "thesis": "одна мысль", "extend": None,
                     "ripe": True, "rule_file": None},
        keys["dub"]: {"key": keys["dub"], "kind": "same", "verdict": "дубль", "old": "b", "topic": "икс", "old_say": "y says",
                      "new_say": "x says", "fix": "слить"},
        keys["s"]: {"key": keys["s"], "kind": "stale", "confirmed": True, "topic": "эс", "old_say": "заголовок",
                    "new_say": "поправка", "new_title": "Fact — S наоборот", "fix": "переименовать"},
    }
    ac.write_json(ac.VERDICTS, verdicts)
    run = ac.RUNS / "2026-01-01"
    ac.write_json(run / "candidates.json", candidates)

    def apply(*args, ok=True, vault_arg=True):
        done = subprocess.run([sys.executable, str(SCRIPT), *(["--vault", str(vault)] if vault_arg else []), *args],
                              env=GIT_ENV, capture_output=True, text=True)
        assert (done.returncode == 0) == ok, (done.returncode, done.stderr)
        return json.loads(done.stdout) if ok else done.stderr

    def git(*args):
        return subprocess.run(["git", "-C", str(vault), *args], env=GIT_ENV, capture_output=True, text=True,
                              check=True).stdout

    expected_added = [
        {"file": "M1.md", "link": "G", "why": "в godnote"},
        {"file": "p1/A.md", "link": "B", "why": "между проектами"},
        {"file": "p2/B.md", "link": "A", "why": "между проектами"},
        {"file": "p4/W.md", "link": "U", "why": "между проектами"},
        {"file": "p1/C.md", "link": "D", "why": "уточняет"},
    ]
    expected_skips = {"already_there": 2, "file_gone": ["p2/F.md"], "target_gone": ["p1/E.md -> p2/F.md"],
                      "unreadable": ["p3/U.md: UnicodeDecodeError"], "changed_while_running": []}
    dry = apply("--safe", "--dry")
    assert dry["added"] == expected_added and {k: dry[k] for k in expected_skips} == expected_skips, dry
    assert all((vault / rel).read_bytes() == data for rel, data in files_bytes.items()), "dry run wrote"

    # Undo by git is the promise: no repository, or one with the user's work uncommitted, and nothing is written.
    assert "not in a git repository" in apply("--safe", ok=False)
    git("init", "--quiet")
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian/workspace.json").write_text("{}", encoding="utf-8")
    git("add", "-A")
    git("commit", "--quiet", "-m", "vault")
    (vault / "p2/B.md").write_text("# B\nbody, edited\n", encoding="utf-8")
    error = apply("--safe", ok=False)
    assert "uncommitted changes (p2/B.md)" in error, error
    git("checkout", "--", "p2/B.md")
    (vault / ".obsidian/workspace.json").write_text('{"open": 1}', encoding="utf-8")
    assert all((vault / rel).read_bytes() == data for rel, data in files_bytes.items()), "a refused run wrote"

    done = apply("--safe")
    assert done["added"] == expected_added and {k: done[k] for k in expected_skips} == expected_skips, done
    assert done["godnote_not_found"] == [] and done["commit"], done
    after = {rel: (vault / rel).read_bytes().decode("utf-8") for rel in files}
    assert after == {**files,
                     "p1/A.md": "# A\r\n\r\n## Links\r\n- [[Z]]\r\n- [[B]]\r\n\r\n## Notes\r\ntext\r\n",
                     "p2/B.md": "# B\nbody\n\n## Links\n- [[A]]\n",
                     "p1/C.md": "# C\nbody\n\n## Links\n- [[D]]\n",
                     "p4/W.md": "# W\n\n## Links\n- [[U]]\n",
                     "M1.md": "# M1\n\n## Links\n- [[G]]\n"}, after
    assert git("log", "-1", "--format=%s").strip() == "audit 2026-01-01: 5 links"
    assert git("show", "--name-only", "--format=", "HEAD").split() == ["M1.md", "p1/A.md", "p1/C.md", "p2/B.md", "p4/W.md"]
    assert git("status", "--porcelain") == " M .obsidian/workspace.json\n", "the commit took more or less than its links"
    assert not list(vault.rglob("*.audit-tmp")), "a temporary file was left in the vault"
    # Our own link is not news: the verdict that read A and B still stands on the linked files.
    assert ac.load_verdicts()[keys["ab"]]["sha"] == {"p1/A.md": sha(after["p1/A.md"]), "p2/B.md": sha(after["p2/B.md"])}

    again = apply("--safe")
    assert again["links_added"] == 0 and again["already_there"] == 7 and again["commit"] is None, again

# A note edited between our read and our write is left as the user made it, and named.
with tempfile.TemporaryDirectory() as temp:
    small = Path(temp)
    (small / "K.md").write_text("# K\n", encoding="utf-8")
    (small / "L.md").write_text("# L\n", encoding="utf-8")
    K, L = note("K.md", "K"), note("L.md", "L")
    ac.write_json(ac.VERDICTS, {"l-" + "5" * 16: {"keep": True}})
    run2 = ac.RUNS / "2026-01-02"
    ac.write_json(run2 / "candidates.json", {"date": "2026-01-02", "vault": str(small),
                                             "cross": [{"kind": "cross", "key": "l-" + "5" * 16, "a": K, "b": L}]})
    original = audit_apply.with_link

    def meddling(text, target):
        if target == "L":
            (small / "K.md").write_text("# K\nthe user typed this\n", encoding="utf-8")
        return original(text, target)

    audit_apply.with_link, audit_apply.vault_unclean, audit_apply.commit_links = meddling, lambda v: None, lambda *a: "c"
    done = audit_apply.cmd_safe(run2, small, dry=False)
    audit_apply.with_link = original
    assert done["changed_while_running"] == ["K.md"] and [a["file"] for a in done["added"]] == ["L.md"], done
    assert (small / "K.md").read_text(encoding="utf-8") == "# K\nthe user typed this\n"
    shutil.rmtree(run2)

vault = Path(candidates["vault"])
with tempfile.TemporaryDirectory() as temp:
    ac.write_json(ac.VERDICTS, verdicts)
    marks = Path(temp) / "export" / "decisions"
    marks.mkdir(parents=True)
    for key, body in ((keys["xy"], {"v": "yes", "c": "в правила\n"}), (keys["dub"], {"v": "no", "c": "мимо"}),
                      (keys["s"], {"v": None, "c": "разобраться"}), ("l-" + "0" * 16, {"v": "yes"})):
        (marks / f"{key}.json").write_text(json.dumps(body), encoding="utf-8")
    (marks / ("d-" + "a" * 16 + ".json")).write_text("{not json", encoding="utf-8")
    (marks / ("d-" + "b" * 16 + ".json")).write_text("[1]", encoding="utf-8")
    # With --run and no --vault the vault is the one this run was found in, not whatever the config says.
    summary = apply("--decisions", str(marks.parent), "--run", str(run), vault_arg=False)
    assert summary == {"heavy": str(run / "heavy.json"), "pairs": 0, "godnotes": 1, "tasks": 1, "rejected": 1,
                       "unmarked": 0, "marks_not_on_page": ["l-" + "0" * 16],
                       "bad_marks": [f"{marks}/d-{'a' * 16}.json: JSONDecodeError",
                                     f"{marks}/d-{'b' * 16}.json: not an object"]}, summary
    heavy = json.loads((run / "heavy.json").read_text(encoding="utf-8"))
    expected_heavy = {
        "pairs": [],
        "godnotes": [{"key": keys["xy"], "name": "XY", "thesis": "одна мысль", "ripe": True, "rule_file": None,
                      "members": [str(vault / "X.md"), str(vault / "Y.md")], "comment": "в правила"}],
        "tasks": [{"key": keys["s"], "about": "эс", "comment": "разобраться", "files": [str(vault / "S.md")]}],
    }
    assert heavy == expected_heavy, heavy
    # The marks are remembered: decided findings leave the page, a comment alone stays a task.
    after = ac.load_verdicts()
    assert after[keys["xy"]]["decided"] == {"v": "yes", "c": "в правила", "run": "2026-01-01"}
    assert after[keys["dub"]]["decided"] == {"v": "no", "c": "мимо", "run": "2026-01-01"}
    assert "decided" not in after[keys["s"]]
    assert [c["key"] for c, _ in ac.sort_findings(candidates, after)["page"]] == [keys["s"]]
    # The same export read twice gives the same heavy list.
    assert apply("--decisions", str(marks.parent), "--run", str(run), vault_arg=False) == summary
    assert json.loads((run / "heavy.json").read_text(encoding="utf-8")) == expected_heavy

    (marks / f"{keys['dub']}.json").write_text(json.dumps({"v": "yes", "c": ""}), encoding="utf-8")
    (marks / f"{keys['s']}.json").write_text(json.dumps({"v": "yes", "c": ""}), encoding="utf-8")
    apply("--decisions", str(marks), "--run", str(run))
    pairs = json.loads((run / "heavy.json").read_text(encoding="utf-8"))["pairs"]
    assert pairs == [
        {"key": keys["dub"], "verdict": "дубль", "topic": "икс", "fix": "слить", "new_title": None,
         "old": {"file": str(vault / "Y.md"), "says": "y says"}, "new": {"file": str(vault / "X.md"), "says": "x says"},
         "comment": ""},
        {"key": keys["s"], "verdict": "заголовок устарел", "topic": "эс", "fix": "переименовать",
         "new_title": "Fact — S наоборот", "old": {"file": str(vault / "S.md"), "says": "заголовок"}, "new": None,
         "comment": ""},
    ], pairs
    # The fix the user agreed to writes `supersedes`/`## Поправка` or a new title; the stale findings
    # that would raise next run are decided already, so the settled pair does not come back.
    after = ac.load_verdicts()
    via = {k: v["decided"]["via"] for k, v in after.items() if v.get("decided", {}).get("via")}
    assert via == {"s-c6343afe9fe0fa1e": keys["dub"], "s-351f94b9fc6220ea": keys["dub"],
                   ac.finding_key("stale", ["Fact — S наоборот"]): keys["s"],
                   # The test's stale key is synthetic; a real one is this, and is not its own follow-up.
                   ac.finding_key("stale", ["Fact — S"]): keys["s"]}, via

print("selftest_audit_apply: ok")
