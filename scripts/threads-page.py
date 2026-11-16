#!/usr/bin/env python3
"""Render every open thread across all projects as one self-contained HTML page.

The digest shows only the freshest few threads per project; everything older ages out
of sight while the box stays unticked in its session note. This page answers the other
question — "what is the whole backlog" — by scanning every session note, not a window.
The definition of a thread is borrowed from hot-scan.py verbatim so the page and the
digest can never disagree about what counts.
"""

import html
import importlib.util
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from common import DEFAULT_DB, HOME_DIR, connect_ro
from threads import CLOSED_LOG, closed_keys, key as thread_key

_spec = importlib.util.spec_from_file_location(
    "hot_scan", Path(__file__).with_name("hot-scan.py"))
hot_scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hot_scan)

OUT = HOME_DIR / "threads.html"


def open_threads(db_path):
    con = connect_ro(db_path)
    con.row_factory = __import__("sqlite3").Row
    sessions = con.execute(
        """SELECT id, path, project, date FROM notes
           WHERE lower(type) = 'session' ORDER BY date(date) DESC, path""").fetchall()
    if not sessions:
        con.close()
        return []
    marks = ",".join("?" for _ in sessions)
    bodies = con.execute(
        f"""SELECT note_id, body FROM chunks
            WHERE note_id IN ({marks}) ORDER BY note_id, ord""",
        tuple(row["id"] for row in sessions)).fetchall()
    con.close()
    by_note = {}
    for row in bodies:
        by_note.setdefault(row["note_id"], []).append(row["body"])
    closed = closed_keys()
    found = []
    for note in sessions:
        name = Path(note["path"]).stem.removeprefix("Session — ")
        text = hot_scan.strip_fences("\n".join(by_note.get(note["id"], [])))
        for match in hot_scan.OPEN_ITEM.finditer(text):
            item = " ".join(match.group(0).strip()[2:].split())
            if len(item) > hot_scan.MAX_ITEM_CHARS:
                item = item[: hot_scan.MAX_ITEM_CHARS - 1] + "…"
            line = f"{item} (Session — {name})"
            if thread_key(line) in closed:
                continue
            found.append({"text": item, "session": name,
                          "project": note["project"] or "—",
                          "date": note["date"] or ""})
    return found


def closed_threads(log_path=CLOSED_LOG):
    rows = []
    if log_path.is_file():
        for raw in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    rows.reverse()  # newest closure first
    return rows


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Threads — dont-forget</title>
<style>
  :root {{ color-scheme: dark; --bg:#0d1117; --panel:#161b22; --line:#30363d;
          --fg:#e6edf3; --dim:#8b949e; --accent:#58a6ff; --open:#f0883e; --done:#3fb950; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.5 -apple-system, "Segoe UI", system-ui, sans-serif; }}
  header {{ padding:28px 32px 20px; border-bottom:1px solid var(--line);
           position:sticky; top:0; background:var(--bg); z-index:2; }}
  h1 {{ margin:0 0 4px; font-size:20px; font-weight:600; }}
  .stats {{ color:var(--dim); font-size:13px; }}
  .stats b {{ color:var(--open); }} .stats i {{ color:var(--done); font-style:normal; }}
  #q {{ margin-top:14px; width:100%; max-width:520px; padding:9px 14px;
       background:var(--panel); border:1px solid var(--line); border-radius:8px;
       color:var(--fg); font-size:14px; outline:none; }}
  #q:focus {{ border-color:var(--accent); }}
  main {{ padding:8px 32px 60px; max-width:1200px; }}
  section {{ margin-top:26px; }}
  h2 {{ font-size:14px; font-weight:600; color:var(--accent); margin:0 0 8px;
       text-transform:uppercase; letter-spacing:.04em; }}
  h2 .n {{ color:var(--dim); font-weight:400; margin-left:6px; }}
  table {{ width:100%; border-collapse:collapse; background:var(--panel);
          border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
  th {{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.05em;
       color:var(--dim); padding:8px 12px; border-bottom:1px solid var(--line);
       background:rgba(255,255,255,.02); }}
  td {{ padding:9px 12px; border-bottom:1px solid var(--line); vertical-align:top; }}
  tr:last-child td {{ border-bottom:none; }}
  tr:hover td {{ background:rgba(88,166,255,.04); }}
  .t {{ font-family:ui-monospace, "SF Mono", Menlo, monospace; font-size:13px; }}
  .s {{ color:var(--dim); white-space:nowrap; font-size:12px; }}
  .d {{ color:var(--dim); white-space:nowrap; font-size:12px; width:90px; }}
  details {{ margin-top:26px; }}
  summary {{ cursor:pointer; color:var(--dim); font-size:14px; }}
  summary:hover {{ color:var(--fg); }}
  .empty {{ color:var(--dim); padding:40px 0; text-align:center; }}
  mark {{ background:rgba(240,136,62,.35); color:inherit; border-radius:2px; }}
</style></head><body>
<header>
  <h1>Open threads</h1>
  <div class="stats"><b>{open_n}</b> open in <b>{proj_n}</b> projects ·
       <i>{closed_n}</i> closed · generated {when}</div>
  <input id="q" type="search" placeholder="filter threads…" autofocus>
</header>
<main>
{body}
{closed_section}
</main>
<script>
const q = document.getElementById('q');
q.addEventListener('input', () => {{
  const needle = q.value.trim().toLowerCase();
  document.querySelectorAll('tbody tr').forEach(tr => {{
    const hit = !needle || tr.textContent.toLowerCase().includes(needle);
    tr.style.display = hit ? '' : 'none';
  }});
  document.querySelectorAll('section').forEach(sec => {{
    const any = [...sec.querySelectorAll('tbody tr')].some(tr => tr.style.display !== 'none');
    sec.style.display = any ? '' : 'none';
  }});
}});
</script>
</body></html>"""


def render(opened, closed_rows):
    groups = defaultdict(list)
    for row in opened:
        groups[row["project"]].append(row)
    blocks = []
    for project in sorted(groups, key=lambda p: -len(groups[p])):
        rows = "".join(
            f"<tr><td class='t'>{html.escape(r['text'])}</td>"
            f"<td class='s'>{html.escape(r['session'])}</td>"
            f"<td class='d'>{html.escape(r['date'][:10])}</td></tr>"
            for r in groups[project])
        blocks.append(
            f"<section><h2>{html.escape(project)}"
            f"<span class='n'>{len(groups[project])}</span></h2>"
            f"<table><thead><tr><th>thread</th><th>session</th><th>date</th></tr>"
            f"</thead><tbody>{rows}</tbody></table></section>")
    body = "\n".join(blocks) or "<p class='empty'>No open threads. Clean.</p>"
    closed_rows_html = "".join(
        f"<tr><td class='t'>{html.escape(r.get('thread',''))}</td>"
        f"<td class='s'>{html.escape(r.get('reason',''))}</td>"
        f"<td class='d'>{html.escape(str(r.get('closed','')))}</td></tr>"
        for r in closed_rows)
    closed_section = (
        f"<details><summary>Closed threads ({len(closed_rows)})</summary>"
        f"<table style='margin-top:10px'><thead><tr><th>thread</th>"
        f"<th>reason</th><th>closed</th></tr></thead>"
        f"<tbody>{closed_rows_html}</tbody></table></details>") if closed_rows else ""
    projects = sorted(groups)
    return PAGE.format(
        open_n=len(opened), proj_n=len(projects), closed_n=len(closed_rows),
        when=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        body=body, closed_section=closed_section)


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    opened = open_threads(DEFAULT_DB)
    closed_rows = closed_threads()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(opened, closed_rows), encoding="utf-8")
    print(f"{len(opened)} open / {len(closed_rows)} closed → {out}")


if __name__ == "__main__":
    main()
