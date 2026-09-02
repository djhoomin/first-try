"""The whole loop, with no network and no API key.

This is the test that matters for anyone picking the repo up: it proves the
harness runs, scores, and reports without spending a cent, which is also how you
should develop against it.
"""

import json

from first_try.interceptor import Policy
from first_try.report import render_report
from first_try.runner_loop import run_suite
from first_try.tasks import load_tasks

BFL_TOOLS = [
    {"name": n, "description": "", "input_schema": {"type": "object", "properties": {}}}
    for n in ("generate_image", "vto", "generate_variations", "get_history",
              "get_credits", "generate_video", "enhance_video")
]


class FakeSession:
    """Stands in for a real MCP server. Fails the documented vto-as-model case."""

    def list_tools(self):
        return BFL_TOOLS

    def call_tool(self, name, args):
        if name == "generate_image":
            for req in args.get("requests") or [args]:
                if req.get("model") == "vto":
                    raise RuntimeError("vto is not a valid model")
        return {"status": "ready", "results": [{"request_id": "req-x"}]}


class ScriptedRunner:
    """Replays a fixed sequence of tool calls, keyed by task id."""

    name = "scripted"

    def __init__(self, scripts):
        self.scripts = scripts
        self.current = None

    def run(self, *, prompt, setup, tools, invoke, max_turns):
        script = self.scripts.get(self.current, [])
        for turn, (tool, args) in enumerate(script, start=1):
            invoke(tool, args, turn)
        return ("What did you have in mind?" if not script else "Done."), max(len(script), 1), []


def _run(task_ids, scripts, policy=Policy(dry_run=True, budget_usd=1.0)):
    tasks = [t for t in load_tasks("tasks") if t.id in task_ids]
    runner = ScriptedRunner(scripts)
    rows = []
    for task in tasks:
        runner.current = task.id
        rows += run_suite([task], runner, FakeSession(), policy)
    return {r["task_id"]: r for r in rows}


def test_a_good_agent_passes_typography():
    rows = _run({"T05"}, {"T05": [
        ("generate_image", {"requests": [{"model": "flux2_flex", "prompt": "poster, 'NIGHT MARKET'"}]}),
    ]})
    row = rows["T05"]
    assert row["passed"] and row["needs_review"]  # scored checks pass; one judge check remains


def test_a_careless_agent_fails_typography_with_a_readable_reason():
    rows = _run({"T05"}, {"T05": [
        ("generate_image", {"requests": [{"model": "flux2_max", "prompt": "night market poster"}]}),
    ]})
    failures = [c for c in rows["T05"]["checks"] if not c["passed"]]
    assert any("flux2_flex" in c["detail"] for c in failures)


def test_t11_is_forced_dry_even_when_the_run_is_live():
    """A task may restrict the run policy. It must never widen it."""
    rows = _run({"T11"}, {"T11": [
        ("generate_video", {"requests": [{"mode": "t2v", "duration": 20, "resolution": "fhd"}] * 4}),
    ]}, policy=Policy(dry_run=False, budget_usd=100.0, per_call_cap_usd=100.0))
    row = rows["T11"]
    assert row["spend_usd"] == 0
    assert round(row["intended_usd"], 2) == 23.20


def test_asking_instead_of_guessing_passes_t12():
    assert _run({"T12"}, {"T12": []})["T12"]["passed"]


def test_guessing_fails_t12():
    rows = _run({"T12"}, {"T12": [("generate_image", {"requests": [{}] * 8})]})
    assert not rows["T12"]["passed"]


def test_report_renders_and_names_the_failures():
    rows = _run({"T05", "T12"}, {
        "T05": [("generate_image", {"requests": [{"model": "flux2_max"}]})],
        "T12": [("generate_image", {"requests": [{}] * 8})],
    })
    serialisable = [{k: v for k, v in r.items() if k != "transcript"} for r in rows.values()]
    text = render_report(serialisable)
    assert "## Headline" in text and "## What failed" in text
    assert "T05" in text and "T12" in text
    json.dumps(serialisable, default=str)  # results.json must be serialisable
