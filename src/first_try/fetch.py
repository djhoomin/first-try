"""Resolve pending generations into finished images.

Generation is job-shaped. A call returns `{"status": "pending", "request_id": ...}`
and the render exists minutes later, so a run records receipts rather than
pictures. This walks the saved transcripts, asks the server what became of each
job, and writes the resulting media URLs back where the review page can find
them.

Safe to run repeatedly: it only asks about ids that have not resolved yet, and
every tool it uses is free.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .mcp_client import image_urls

__all__ = ["fetch_outputs"]

#: Tools that can turn a job id into a finished result, best first. get_result
#: is referenced in BFL's docs but absent from their published tool table, so
#: it may not exist on every server.
RESOLVERS = ("get_result", "get_history")


def fetch_outputs(out_dir: Path, session: Any, log=print) -> int:
    """Fill in result_urls across saved transcripts. Returns how many resolved."""
    available = {t["name"] for t in session.list_tools()}
    resolver = next((name for name in RESOLVERS if name in available), None)
    if resolver is None:
        log(f"no resolver tool available (looked for {', '.join(RESOLVERS)})")
        return 0

    resolved = 0
    for path in sorted(out_dir.glob("transcript-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        for call in data.get("calls", []):
            if call.get("result_urls") or call.get("blocked") or call.get("failed"):
                continue
            for rid in call.get("result_request_ids") or []:
                try:
                    if resolver == "get_result":
                        result = session.call_tool("get_result", {"request_id": rid})
                    else:
                        result = session.call_tool("get_history", {"status": "all"})
                except Exception as exc:
                    log(f"  {data['task_id']} {rid[:8]}: {type(exc).__name__}: {exc}")
                    continue
                urls = image_urls(result)
                if resolver == "get_history":
                    # History returns everything; keep only entries naming this job.
                    blob = json.dumps(result, default=str)
                    if rid not in blob:
                        urls = []
                if urls:
                    call.setdefault("result_urls", []).extend(
                        u for u in urls if u not in call["result_urls"]
                    )
                    changed = True
                    resolved += 1
                    log(f"  {data['task_id']} {rid[:8]}: {len(urls)} media")
                else:
                    log(f"  {data['task_id']} {rid[:8]}: still pending or no media")
        if changed:
            path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return resolved
