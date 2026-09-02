"""Scorecard rendering.

Ordered for a reader who will not read all of it: the headline number, then the
per-axis rollup, then the failures with enough detail to reproduce, then the
full table. Findings before evidence, never the other way round.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

__all__ = ["render_report"]


def render_report(rows: list[dict[str, Any]]) -> str:
    """Render results. One row per task per runner."""
    if not rows:
        return "no results\n"

    out: list[str] = ["# first-try results", ""]

    by_runner: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_runner[row["runner"]].append(row)

    out += ["## Headline", ""]
    for runner, runner_rows in sorted(by_runner.items()):
        total = len(runner_rows)
        passed = sum(1 for r in runner_rows if r["passed"])
        pending = sum(1 for r in runner_rows if r["passed"] and r["needs_review"])
        spend = sum(r["intended_usd"] for r in runner_rows)
        pct = 100.0 * passed / total if total else 0.0
        tail = f", {pending} still awaiting a judgement call" if pending else ""
        out.append(
            f"- **{runner}**: {passed}/{total} tasks right first try on the checks that "
            f"can be scored automatically ({pct:.0f}%), intended spend ${spend:.2f}{tail}"
        )
    out.append("")

    axis_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        for axis in row["axes"]:
            axis_totals[axis][1] += 1
            if row["passed"]:
                axis_totals[axis][0] += 1
    out += ["## By axis", "", "| Axis | Passed |", "| --- | --- |"]
    for axis, (ok, total) in sorted(axis_totals.items()):
        out.append(f"| {axis} | {ok}/{total} |")
    out.append("")

    unmeasured = sorted({k for r in rows for k in r.get("skipped", [])})
    if unmeasured:
        out += ["## Not measured in this run", "",
                "These checks could not be evaluated under this run's conditions and are",
                "excluded from every score above, rather than counted as failures:", ""]
        for kind in unmeasured:
            affected = [r["task_id"] for r in rows if kind in r.get("skipped", [])]
            detail = next(c["detail"] for r in rows for c in r["checks"]
                          if c["kind"] == kind and c.get("skipped"))
            out.append(f"- **{kind}** ({', '.join(sorted(set(affected)))}): {detail}")
        out.append("")

    failures = [r for r in rows if not r["passed"]]
    if failures:
        out += ["## What failed", ""]
        for row in failures:
            out.append(f"### {row['task_id']} - {row['title']}  ({row['runner']})")
            for check in row["checks"]:
                if check.get("skipped"):
                    continue
                if not check["passed"]:
                    out.append(f"- **{check['kind']}**: {check['detail']}")
            if row["intended_usd"] > row["spend_usd"]:
                out.append(
                    f"- intended ${row['intended_usd']:.2f}, of which "
                    f"${row['intended_usd'] - row['spend_usd']:.2f} was blocked by policy"
                )
            if row.get("note"):
                out.append(f"- note: {row['note']}")
            out.append("")

    out += ["## All tasks", "", "| Task | Runner | Result | Turns | Intended $ | First tool |",
            "| --- | --- | --- | --- | --- | --- |"]
    for row in sorted(rows, key=lambda r: (r["task_id"], r["runner"])):
        # Failure outranks review. A task whose scored checks failed has failed,
        # whether or not a judgement is still outstanding on top of it.
        if not row["passed"]:
            result = "fail"
        else:
            result = "review" if row["needs_review"] else "pass"
        out.append(
            f"| {row['task_id']} | {row['runner']} | {result} | {row['turns']} | "
            f"{row['intended_usd']:.3f} | {row['first_tool'] or '-'} |"
        )
    out.append("")
    out += [
        "## Reading these numbers",
        "",
        "Image costs are lower bounds. FLUX.2 bills by megapixel and the published table",
        "quotes floor prices, so real spend is at least what is shown here. Video figures are",
        "exact. Intended spend counts calls the harness blocked, because the agent still meant",
        "to make them.",
        "",
        _resource_note(rows),
        "",
    ]
    return "\n".join(out)


def _resource_note(rows: list[dict[str, Any]]) -> str:
    """State how resources were exposed. It changes what discoverability means."""
    modes = {r.get("resource_mode", "tools") for r in rows}
    if modes == {"none"}:
        return ("The server's MCP resources were NOT exposed to the model, so it could not read "
                "any catalogue the server publishes. Discoverability results reflect the tool "
                "surface alone.")
    if modes == {"tools"}:
        return ("The server's MCP resources were exposed to the model as two extra tools, so it "
                "could list and read them. Most real clients surface resources to the human "
                "instead, so this is the generous reading of discoverability.")
    return f"Mixed resource exposure across runs: {sorted(modes)}. Results are not comparable."
