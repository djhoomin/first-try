"""A contact sheet for the judgement calls.

Roughly half the suite ends in a question no assertion can answer: did the style
actually transfer, is the composition intact, is the text spelled right, were
all five references used. Those are the findings with the most in them, and they
are also the ones most likely to go unmade if reviewing means opening fifteen
JSON files and pasting URLs into a browser.

So: one page, every outstanding question in order, with the prompt, the call the
agent made, and the images it produced, side by side.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

__all__ = ["render_review"]

_CSS = """
:root { --bg:#fff; --fg:#111; --muted:#666; --line:#e3e3e3; --card:#fafafa; --warn:#8a4b00; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151515; --fg:#eee; --muted:#9a9a9a; --line:#333; --card:#1e1e1e; --warn:#e0a260; }
}
* { box-sizing: border-box; }
body { background:var(--bg); color:var(--fg); font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
       margin:0 auto; padding:2rem 1.25rem 5rem; max-width:1100px; }
h1 { font-size:1.5rem; margin:0 0 .25rem; }
h2 { font-size:1.1rem; margin:2.5rem 0 .5rem; padding-top:1.5rem; border-top:1px solid var(--line); }
.sub { color:var(--muted); margin:0 0 2rem; }
.q { background:var(--card); border-left:3px solid var(--warn); padding:.7rem .9rem; margin:.75rem 0 1rem; }
.q b { color:var(--warn); }
.prompt { color:var(--muted); font-style:italic; margin:.5rem 0 1rem; }
.shots { display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:.75rem; }
.shots a { display:block; border:1px solid var(--line); border-radius:6px; overflow:hidden; }
.shots img { width:100%; height:auto; display:block; }
details { margin:1rem 0; }
summary { cursor:pointer; color:var(--muted); }
pre { background:var(--card); border:1px solid var(--line); border-radius:6px; padding:.75rem;
      overflow-x:auto; font-size:12.5px; }
.none { color:var(--muted); font-style:italic; }
.meta { color:var(--muted); font-size:13px; }
"""


def _calls_of(transcript: dict[str, Any], billable_only: bool = True) -> list[dict]:
    calls = transcript.get("calls", [])
    return [c for c in calls if c.get("est_usd", 0) > 0] if billable_only else calls


def render_review(rows: list[dict[str, Any]], transcripts: dict[str, dict],
                  verdicts: dict[str, dict] | None = None) -> str:
    """One page holding every outstanding judgement call."""
    verdicts = verdicts or {}
    pending = [r for r in rows if r.get("needs_review") and not r.get("errored")]

    out = [f"<style>{_CSS}</style>", "<h1>first-try: judgement calls</h1>"]
    if not pending:
        out.append('<p class="sub">Nothing outstanding.</p>')
        return "\n".join(out)

    out.append(
        f'<p class="sub">{len(pending)} task(s) end in a question no assertion can answer. '
        "Answer them here, then record the verdicts.</p>"
    )

    for row in pending:
        tid = row["task_id"]
        t = transcripts.get(tid, {})
        out.append(f'<h2>{html.escape(tid)} &mdash; {html.escape(row.get("title", ""))}</h2>')

        recorded = verdicts.get(tid)
        if recorded:
            note = f' &mdash; {html.escape(recorded["note"])}' if recorded.get("note") else ""
            out.append(f'<div class="q"><b>Recorded: {html.escape(recorded["verdict"])}.</b>{note}</div>')

        for check in row.get("checks", []):
            if check["kind"] == "manual" and not check.get("skipped"):
                detail = check["detail"].replace("needs review:", "").strip()
                out.append(f'<div class="q"><b>Question.</b> {html.escape(detail)}</div>')

        billable = _calls_of(t)
        urls: list[str] = []
        for call in billable:
            # Local copies first: delivery URLs are signed and expire.
            for u in (call.get("result_files") or call.get("result_urls") or []):
                if u not in urls:
                    urls.append(u)

        prompts = []
        for call in billable:
            for req in (call.get("args", {}).get("requests") or [call.get("args", {})]):
                if req.get("prompt"):
                    prompts.append(req["prompt"])
        if prompts:
            out.append(f'<div class="prompt">{html.escape(prompts[0][:600])}</div>')

        if urls:
            out.append('<div class="shots">')
            for u in urls:
                e = html.escape(u, quote=True)
                out.append(f'<a href="{e}" target="_blank"><img src="{e}" loading="lazy" alt=""></a>')
            out.append("</div>")
        elif row.get("dry_run") or all(c.get("blocked") for c in billable) and billable:
            out.append('<p class="none">No images: every billable call was blocked. '
                       "This task needs a live run before it can be judged.</p>")
        else:
            out.append('<p class="none">No output media found on the results.</p>')

        out.append(
            f'<p class="meta">{len(billable)} billable call(s), '
            f'intended ${row.get("intended_usd", 0):.3f}, {row.get("turns", 0)} turns</p>'
        )
        out.append("<details><summary>calls</summary><pre>"
                   + html.escape(json.dumps(
                       [{"tool": c["name"], "blocked": c.get("blocked"), "args": c.get("args")}
                        for c in _calls_of(t, billable_only=False)], indent=2)[:12000])
                   + "</pre></details>")

    return "\n".join(out)
