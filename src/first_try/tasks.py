"""Task definitions, loaded from YAML.

A task is a user message, a spend policy, and a list of checks. Nothing about
the expected behaviour is expressed in Python, so the suite can be reviewed by
someone who never opens the harness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .interceptor import Policy

__all__ = ["Task", "load_tasks"]


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    prompt: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    axes: list[str] = field(default_factory=list)
    #: Conversation turns before the harness gives up.
    max_turns: int = 6
    #: Tasks that must never execute, whatever the run-level policy says.
    force_dry_run: bool = False
    per_call_cap_usd: float | None = None
    #: Free-text note carried into the report, for tasks where the interesting
    #: outcome is not pass or fail.
    note: str = ""
    #: Priming turns replayed before the prompt, for tasks that need history.
    setup: list[dict[str, str]] = field(default_factory=list)

    def policy(self, run_policy: Policy) -> Policy:
        """Narrow the run policy for this task. Tasks may restrict, never widen."""
        return Policy(
            dry_run=run_policy.dry_run or self.force_dry_run,
            budget_usd=run_policy.budget_usd,
            per_call_cap_usd=min(
                run_policy.per_call_cap_usd,
                self.per_call_cap_usd if self.per_call_cap_usd is not None else float("inf"),
            ),
            always_allow_free=run_policy.always_allow_free,
        )


def load_tasks(path: str | Path) -> list[Task]:
    """Load every task from a YAML file or a directory of them, sorted by id."""
    p = Path(path)
    files = sorted(p.glob("*.yaml")) + sorted(p.glob("*.yml")) if p.is_dir() else [p]
    tasks: list[Task] = []
    for file in files:
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        for entry in raw if isinstance(raw, list) else [raw]:
            tasks.append(Task(**entry))
    seen = {}
    for task in tasks:
        if task.id in seen:
            raise ValueError(f"duplicate task id: {task.id}")
        seen[task.id] = task
    return sorted(tasks, key=lambda t: t.id)
