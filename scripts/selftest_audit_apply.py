#!/usr/bin/env python3
"""audit_apply.py: links land where readers asked, page marks become the agent's heavy list."""

import _selftest_env  # noqa: F401

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import audit_common as ac
from audit_apply import with_link

SCRIPT = Path(__file__).with_name("audit_apply.py")

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


def note(path, title, project=""):
    return {"title": title, "project": project, "date": "2026-01-01", "path": path, "file": "/elsewhere/" + path}


A, B, C, D = note("p1/A.md", "Gotcha — A", "p1"), note("p2/B.md", "Gotcha — B", "p2"), \
    note("p1/C.md", "Fact — C", "p1"), note("p1/D.md", "Fact — D", "p1")
M1, M2, G = note("M1.md", "Gotcha — M1"), note("M2.md", "Gotcha — M2"), note("G.md", "Godnote — G")
X, Y, S = note("X.md", "Gotcha — X"), note("Y.md", "Gotcha — Y"), note("S.md", "Fact — S")
keys = {name: f"{ac.LETTER[kind]}-{n:016x}" for n, (name, kind) in enumerate(
    [("ab", "cross"), ("ac", "cross"), ("cd", "same"), ("m", "group"), ("xy", "group"), ("dub", "same"),
     ("s", "stale"), ("unread", "same")])}
candidates = {
    "date": "2026-01-01", "vault": "/elsewhere",
    "group": [{"kind": "group", "key": keys["m"], "members": [M1, M2], "nearest_godnotes": [{**G, "links_to_group": 0}]},
              {"kind": "group", "key": keys["xy"], "members": [X, Y], "nearest_godnotes": []}],
    "cross": [{"kind": "cross", "key": keys["ab"], "sim": 0.7, "a": A, "b": B},
              {"kind": "cross", "key": keys["ac"], "sim": 0.7, "a": A, "b": C}],
    "same": [{"kind": "same", "key": keys["cd"], "sim": 0.7, "a": C, "b": D},
             {"kind": "same", "key": keys["dub"], "sim": 0.9, "a": X, "b": Y},
             {"kind": "same", "key": keys["unread"], "sim": 0.6, "a": D, "b": S}],
    "stale": [{"kind": "stale", "key": keys["s"], "how": "поправка", "old": S, "new": None, "correction": "Поправка 2026-01-02"}],
}
verdicts = {
    keys["ab"]: {"key": keys["ab"], "kind": "cross", "keep": True, "common": "одно"},
    keys["ac"]: {"key": keys["ac"], "kind": "cross", "keep": False},
    keys["cd"]: {"key": keys["cd"], "kind": "same", "verdict": "уточняет"},
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

with tempfile.TemporaryDirectory() as temp:
    vault = Path(temp) / "vault"
    files = {
        "p1/A.md": "# A\n\n## Links\n- [[Z]]\n\n## Notes\ntext\n", "p2/B.md": "# B\nbody\n", "p1/C.md": "# C\nbody",
        "p1/D.md": "# D\n\n## Links\n- [[C]]\n", "M1.md": "# M1\n", "M2.md": "# M2\n\n## Links\n- [[G]]\n",
        "G.md": "# G\n", "X.md": "# X\n", "Y.md": "# Y\n", "S.md": "# S\n",
    }
    for rel, text in files.items():
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_text(text, encoding="utf-8")

    def apply(*args):
        done = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(vault), *args],
                              env=os.environ, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)

    expected_added = [
        {"file": "M1.md", "link": "G", "why": "в godnote"},
        {"file": "p1/A.md", "link": "B", "why": "между проектами"},
        {"file": "p2/B.md", "link": "A", "why": "между проектами"},
        {"file": "p1/C.md", "link": "D", "why": "уточняет"},
    ]
    dry = apply("--safe", "--dry")
    assert dry["added"] == expected_added, dry
    assert all((vault / rel).read_text(encoding="utf-8") == text for rel, text in files.items()), "dry run wrote"

    done = apply("--safe")
    assert done["added"] == expected_added, done
    assert done["already_there"] == 2 and done["godnote_not_found"] == [] and done["file_gone"] == [], done
    after = {rel: (vault / rel).read_text(encoding="utf-8") for rel in files}
    assert after == {**files,
                     "p1/A.md": "# A\n\n## Links\n- [[Z]]\n- [[B]]\n\n## Notes\ntext\n",
                     "p2/B.md": "# B\nbody\n\n## Links\n- [[A]]\n",
                     "p1/C.md": "# C\nbody\n\n## Links\n- [[D]]\n",
                     "M1.md": "# M1\n\n## Links\n- [[G]]\n"}, after
    again = apply("--safe")
    assert again["links_added"] == 0 and again["already_there"] == 6, again

    marks = Path(temp) / "export" / "decisions"
    marks.mkdir(parents=True)
    for key, body in ((keys["xy"], {"v": "yes", "c": "в правила\n"}), (keys["dub"], {"v": "no", "c": "мимо"}),
                      (keys["s"], {"v": None, "c": "разобраться"}), ("l-" + "0" * 16, {"v": "yes"})):
        (marks / f"{key}.json").write_text(json.dumps(body), encoding="utf-8")
    summary = apply("--decisions", str(marks.parent))
    assert summary == {"heavy": str(run / "heavy.json"), "pairs": 0, "godnotes": 1, "tasks": 1, "rejected": 1,
                       "unmarked": 0, "marks_not_on_page": ["l-" + "0" * 16]}, summary
    heavy = json.loads((run / "heavy.json").read_text(encoding="utf-8"))
    assert heavy == {
        "pairs": [],
        "godnotes": [{"key": keys["xy"], "name": "XY", "thesis": "одна мысль", "ripe": True, "rule_file": None,
                      "members": [str(vault / "X.md"), str(vault / "Y.md")], "comment": "в правила"}],
        "tasks": [{"key": keys["s"], "about": "эс", "comment": "разобраться", "files": [str(vault / "S.md")]}],
    }, heavy

    (marks / f"{keys['dub']}.json").write_text(json.dumps({"v": "yes", "c": ""}), encoding="utf-8")
    (marks / f"{keys['s']}.json").write_text(json.dumps({"v": "yes", "c": ""}), encoding="utf-8")
    apply("--decisions", str(marks))
    pairs = json.loads((run / "heavy.json").read_text(encoding="utf-8"))["pairs"]
    assert pairs == [
        {"key": keys["dub"], "verdict": "дубль", "topic": "икс", "fix": "слить", "new_title": None,
         "old": {"file": str(vault / "Y.md"), "says": "y says"}, "new": {"file": str(vault / "X.md"), "says": "x says"},
         "comment": ""},
        {"key": keys["s"], "verdict": "заголовок устарел", "topic": "эс", "fix": "переименовать",
         "new_title": "Fact — S наоборот", "old": {"file": str(vault / "S.md"), "says": "заголовок"}, "new": None,
         "comment": ""},
    ], pairs

print("selftest_audit_apply: ok")
