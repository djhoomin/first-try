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

import re

from .mcp_client import image_urls, request_ids

__all__ = ["fetch_outputs", "harvest", "normalise", "download_media"]

#: Tools that can turn a job id into a finished result, best first. get_result
#: is referenced in BFL's docs but absent from their published tool table, so
#: it may not exist on every server.
RESOLVERS = ("get_result", "get_history")


def fetch_outputs(out_dir: Path, session: Any, log=print, force: bool = False) -> int:
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
            if call.get("blocked") or call.get("failed"):
                continue
            if call.get("result_urls") and not force:
                continue
            ids = call.get("result_request_ids") or request_ids(call.get("result_summary") or "")
            for rid in ids:
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
    if resolved == 0:
        log("no job ids on record; falling back to matching account history by prompt")
        resolved = backfill_from_history(out_dir, session, log=log, force=force)
    return resolved


# --- recovering a run whose ids were not captured --------------------------


def normalise(text: str, length: int = 90) -> str:
    """A stable key for matching a prompt across two representations."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()[:length]


def harvest(obj: Any, _out: list | None = None) -> list[dict]:
    """Pull (prompt, urls, request_id) triples out of an unknown JSON shape.

    Servers describe their history differently and this has to work without a
    per-server adapter, so it walks the whole structure looking for objects
    that carry both a prompt and something that looks like output media.
    """
    out = [] if _out is None else _out
    if isinstance(obj, dict):
        prompt = obj.get("prompt")
        urls = image_urls(obj)
        if isinstance(prompt, str) and urls:
            out.append({
                "prompt": prompt,
                "urls": urls,
                "request_id": obj.get("request_id") or obj.get("id") or "",
            })
        for value in obj.values():
            harvest(value, out)
    elif isinstance(obj, list):
        for item in obj:
            harvest(item, out)
    return out


def backfill_from_history(out_dir: Path, session: Any, log=print, force: bool = False) -> int:
    """Match saved calls to account history by prompt, for runs that predate
    request-id capture or whose ids were lost to a truncated summary."""
    pages, cursor = [], None
    for _ in range(10):                       # bounded; history is paginated
        args: dict[str, Any] = {"status": "all"}
        if cursor:
            args["cursor"] = cursor
        try:
            page = session.call_tool("get_history", args)
        except Exception as exc:
            log(f"get_history failed: {type(exc).__name__}: {exc}")
            break
        pages.append(page)
        blob = json.dumps(page, default=str)
        match = re.search(r'"next_cursor"\s*:\s*"([^"]+)"', blob)
        cursor = match.group(1) if match else None
        if not cursor:
            break

    (out_dir / "history-raw.json").write_text(
        json.dumps(pages, indent=2, default=str)[:2_000_000], encoding="utf-8"
    )

    items: list[dict] = []
    for page in pages:
        # Content comes back as JSON strings inside text blocks.
        for text in (page.get("content") or []) if isinstance(page, dict) else []:
            try:
                items += harvest(json.loads(text))
            except (TypeError, ValueError):
                pass
        items += harvest(page)

    by_prompt = {}
    for item in items:
        by_prompt.setdefault(normalise(item["prompt"]), item)
    log(f"history: {len(items)} item(s) with media, {len(by_prompt)} distinct prompt(s)")
    if not by_prompt:
        log(f"nothing matched; raw history written to {out_dir / 'history-raw.json'}")
        return 0

    filled = 0
    for path in sorted(out_dir.glob("transcript-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        for call in data.get("calls", []):
            if call.get("blocked") or call.get("failed"):
                continue
            if call.get("result_urls") and not force:
                continue
            if force:
                call["result_urls"] = []
            requests = call.get("args", {}).get("requests") or [call.get("args", {})]
            for req in requests:
                hit = by_prompt.get(normalise(req.get("prompt", "")))
                if not hit:
                    continue
                call.setdefault("result_urls", [])
                for url in hit["urls"]:
                    if url not in call["result_urls"]:
                        call["result_urls"].append(url)
                changed, filled = True, filled + 1
                log(f"  {data['task_id']}: matched {len(hit['urls'])} media by prompt")
        if changed:
            path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return filled


# --- keeping the images ----------------------------------------------------


def download_media(out_dir: Path, log=print, timeout: float = 60.0) -> int:
    """Save every referenced image beside the results.

    Delivery URLs are signed and expire, so a review page built from remote
    URLs stops working at some unannounced point. The findings outlive the
    signatures, so the files come local and the page points at those.
    """
    import hashlib
    import urllib.request

    media = out_dir / "media"
    media.mkdir(exist_ok=True)
    saved = 0
    for path in sorted(out_dir.glob("transcript-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        for call in data.get("calls", []):
            files = call.setdefault("result_files", [])
            for url in call.get("result_urls") or []:
                stem = hashlib.sha1(url.encode()).hexdigest()[:16]
                suffix = re.sub(r"\?.*$", "", url).rsplit(".", 1)[-1].lower()[:4] or "jpg"
                name = f"{stem}.{suffix}"
                target = media / name
                rel = f"media/{name}"
                if target.exists():
                    if rel not in files:
                        files.append(rel)
                        changed = True
                    continue
                try:
                    with urllib.request.urlopen(url, timeout=timeout) as response:
                        target.write_bytes(response.read())
                except Exception as exc:
                    log(f"  could not save {url[:70]}...: {type(exc).__name__}: {exc}")
                    continue
                files.append(rel)
                changed, saved = True, saved + 1
        if changed:
            path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return saved
