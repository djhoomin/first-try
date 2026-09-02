"""Run one task, then many.

Kept separate from the CLI so the whole benchmark is usable as a library, and
separate from the runners so that adding a model does not touch the loop.
"""

from __future__ import annotations

from typing import Any

from .checks import CheckResult, run_checks
from .mcp_client import McpTimeout
from .interceptor import Interceptor, Policy
from .tasks import Task
from .transcript import Transcript

__all__ = ["run_task", "run_suite"]


def run_task(task: Task, runner: Any, session: Any, tools: list[dict], policy: Policy,
             resource_mode: str = "tools") -> dict[str, Any]:
    """Execute one task and score it. Never raises for a task-level failure."""
    transcript = Transcript(task_id=task.id, runner=runner.name, dry_run=policy.dry_run,
                            resource_mode=resource_mode)
    effective = task.policy(policy)
    interceptor = Interceptor(session, transcript, effective)

    def invoke(name: str, args: dict[str, Any], turn: int) -> Any:
        return interceptor.call(name, args, turn=turn)

    before = dict(getattr(runner, "usage", {}) or {})
    try:
        final_text, turns, messages = runner.run(
            prompt=task.prompt, setup=task.setup, tools=tools,
            invoke=invoke, max_turns=task.max_turns,
        )
        transcript.final_text = final_text
        transcript.turns = turns
        transcript.messages = messages
    except Exception as exc:
        # A runner blowing up is a result, not a crash. Record and move on so
        # one bad task cannot cost you a whole paid run.
        transcript.stopped_early = f"{type(exc).__name__}: {exc}"

    after = dict(getattr(runner, "usage", {}) or {})
    transcript.usage = {k: after.get(k, 0) - before.get(k, 0) for k in after}

    results = run_checks(transcript, task.checks)
    if transcript.stopped_early:
        # The run did not happen. Scoring it would grade the harness, the API
        # key or the network, and several checks pass vacuously on an empty
        # record, so an errored task counts as neither pass nor failure.
        results = [
            CheckResult(r.kind, False, f"not run: {transcript.stopped_early[:120]}", skipped=True)
            for r in results
        ]
    needs_review = any(r.kind == "manual" and not r.skipped for r in results)
    scored = [r for r in results if r.kind != "manual" and not r.skipped]

    return {
        "task_id": task.id,
        "title": task.title,
        "runner": runner.name,
        "axes": task.axes,
        "errored": bool(transcript.stopped_early),
        "passed": bool(scored) and all(r.passed for r in scored),
        "needs_review": needs_review,
        "turns": transcript.turns,
        "spend_usd": transcript.spend_usd,
        "intended_usd": transcript.intended_usd,
        "first_tool": transcript.first_tool,
        "resource_mode": resource_mode,
        "usage": transcript.usage,
        "note": task.note,
        "stopped_early": transcript.stopped_early,
        "skipped": [r.kind for r in results if r.skipped],
        "checks": [
            {"kind": r.kind, "passed": r.passed, "detail": r.detail, "skipped": r.skipped}
            for r in results
        ],
        "transcript": transcript,
    }


def run_suite(tasks: list[Task], runner: Any, session: Any, policy: Policy,
              on_result=None, resource_mode: str = "tools") -> list[dict[str, Any]]:
    """Run every task in order, stopping if the run budget is exhausted."""
    tools = session.list_tools()
    rows: list[dict[str, Any]] = []
    spent = 0.0
    for task in tasks:
        remaining = Policy(
            dry_run=policy.dry_run,
            budget_usd=max(policy.budget_usd - spent, 0.0),
            per_call_cap_usd=policy.per_call_cap_usd,
            always_allow_free=policy.always_allow_free,
        )
        row = run_task(task, runner, session, tools, remaining, resource_mode)
        spent += row["spend_usd"]
        rows.append(row)
        if on_result:
            on_result(row)
        if "McpTimeout" in (row.get("stopped_early") or ""):
            # The transport is gone. Every remaining task would time out in
            # turn, so stop rather than burning the wall clock proving it.
            row["note"] = (row.get("note") or "") + " [suite aborted: transport timed out]"
            break
    return rows
