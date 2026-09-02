"""Human verdicts on the judgement calls.

Kept in the results directory beside the machine output, because a finding that
lives only in someone's memory is not a finding. Recorded per task and runner so
that re-running a suite does not quietly invalidate a verdict about a different
run's images.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["load_verdicts", "record_verdict", "VERDICTS"]

VERDICTS = ("pass", "fail", "partial")


def _path(out_dir: Path) -> Path:
    return out_dir / "verdicts.json"


def load_verdicts(out_dir: Path) -> dict[str, dict]:
    path = _path(out_dir)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def record_verdict(out_dir: Path, task_id: str, verdict: str, note: str = "",
                   runner: str = "") -> dict:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    data = load_verdicts(out_dir)
    entry = {
        "task_id": task_id,
        "verdict": verdict,
        "note": note,
        "runner": runner,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    data[task_id] = entry
    _path(out_dir).write_text(json.dumps(data, indent=2), encoding="utf-8")
    return entry
