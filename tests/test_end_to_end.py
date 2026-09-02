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


def test_failure_outranks_pending_review_in_the_report():
    """A task whose scored checks failed must never read as 'review'."""
    rows = _run({"T05"}, {"T05": [("generate_image", {"requests": [{"model": "flux2_max"}]})]})
    serialisable = [{k: v for k, v in r.items() if k != "transcript"} for r in rows.values()]
    text = render_report(serialisable)
    line = next(l for l in text.splitlines() if l.startswith("| T05 "))
    assert "fail" in line and "review" not in line


def test_connection_failure_explains_itself():
    """The harness must not emit a traceback when its own connection fails."""
    from argparse import Namespace

    from first_try.cli import connection_help

    text = connection_help(
        Namespace(stdio="npx -y nonexistent-mcp", http=""),
        RuntimeError("Connection closed"),
    )
    assert "Could not connect" in text
    assert "mcp-remote" in text          # points at the route that works
    assert "Traceback" not in text


def test_cheap_local_checks_run_before_the_expensive_connection():
    """A missing SDK must be caught before anything spawns a process.

    Guards the ordering rather than the message: connecting can open a browser
    for OAuth, so a preventable failure after that point costs real time.
    """
    import inspect

    from first_try import cli

    # Scoped to the run path. Other subcommands legitimately connect without
    # building a runner at all, so ordering across the whole of main() says
    # nothing.
    source = inspect.getsource(cli._run_command)
    assert "_make_runner(args)" in source and "McpSession(" in source
    assert source.index("_make_runner(args)") < source.index("McpSession(")


def test_report_states_how_resources_were_exposed():
    """A discoverability number means different things under each mode."""
    rows = _run({"T09"}, {"T09": [("get_credits", {})]})
    serialisable = [{k: v for k, v in r.items() if k != "transcript"} for r in rows.values()]
    assert "exposed to the model as two extra tools" in render_report(serialisable)
    for row in serialisable:
        row["resource_mode"] = "none"
    assert "were NOT exposed" in render_report(serialisable)


def test_results_are_written_after_every_task_not_at_the_end(tmp_path):
    """A run that dies at task 14 must keep tasks 1 to 13.

    Reproduces a real interruption: the machine slept mid-run, the transport
    died, and because output happened only after the whole suite finished,
    nothing at all was saved.
    """
    import json as _json

    from first_try.report import render_report

    out = tmp_path / "results"
    out.mkdir()
    accumulated = []

    def save(row):
        accumulated.append({k: v for k, v in row.items() if k != "transcript"})
        (out / "results.json").write_text(_json.dumps(accumulated, default=str))
        (out / "report.md").write_text(render_report(accumulated))

    rows = _run({"T09", "T12"}, {"T09": [("get_credits", {})], "T12": []})
    for row in rows.values():
        save(row)
        # After each task, what is on disk is complete and readable.
        on_disk = _json.loads((out / "results.json").read_text())
        assert len(on_disk) == len(accumulated)
        assert "T09" in (out / "report.md").read_text()


def test_a_dead_transport_stops_the_suite_instead_of_timing_out_each_task():
    from first_try.interceptor import Policy
    from first_try.runner_loop import run_suite
    from first_try.tasks import load_tasks

    class DeadRunner:
        name = "dead"

        def run(self, **kwargs):
            raise __import__("first_try.mcp_client", fromlist=["McpTimeout"]).McpTimeout("gone")

    tasks = [t for t in load_tasks("tasks") if t.id in {"T09", "T11", "T12"}]
    rows = run_suite(tasks, DeadRunner(), FakeSession(), Policy(dry_run=True))
    assert len(rows) == 1                       # stopped, did not grind through all three
    assert "aborted" in rows[0]["note"]


def test_unmeasurable_checks_are_skipped_not_failed():
    """Recovery cannot be observed in a dry run: every billable call is blocked.

    Reporting that as a failure manufactures a finding out of the harness's own
    safety rail, which is the worst thing a benchmark can do.
    """
    rows = _run({"T13"}, {"T13": [("vto", {"person": {}, "garment": {}})]})
    row = rows["T13"]
    assert "turns_to_success_at_most" in row["skipped"]
    serialisable = [{k: v for k, v in row.items() if k != "transcript"}]
    text = render_report(serialisable)
    assert "Not measured in this run" in text
    # and it must not appear among the failures
    failures_section = text.split("## Not measured")[1]
    assert "turns_to_success_at_most" in failures_section


def test_reconnaissance_before_generating_is_not_a_miss():
    """Reading a skill guide first is good behaviour; first_tool_is punished it."""
    rows = _run({"T01"}, {"T01": [
        ("get_skill", {"name": "flux-image-best-practices"}),
        ("generate_image", {"requests": [{
            "model": "flux2_max",
            "prompt": "a market stall in the reference style",
            "input_medias": [{"url": "https://example.test/style-reference.jpg"}],
        }]}),
    ]})
    scored = [c for c in rows["T01"]["checks"] if not c.get("skipped") and c["kind"] != "manual"]
    assert all(c["passed"] for c in scored), [c for c in scored if not c["passed"]]


def test_a_task_that_did_nothing_does_not_pass():
    """T15 scored as a pass after the API refused the request.

    Two holes. The negative assertion had no prompts to match against and
    passed vacuously, and nothing required the task to generate at all, so
    spend_at_most passed on its own. A non-event must never score.
    """
    rows = _run({"T15"}, {"T15": [("get_skill", {"name": "flux-image-best-practices"})]})
    row = rows["T15"]
    assert not row["passed"], row["checks"]
    assert "arg_not_matches" in row["skipped"]      # no evidence, so not scored
    assert any(c["kind"] == "called_tool" and not c["passed"] for c in row["checks"])


def test_an_errored_task_is_excluded_from_the_denominator():
    from first_try.interceptor import Policy
    from first_try.runner_loop import run_task
    from first_try.tasks import load_tasks

    class Broken:
        name = "broken"

        def run(self, **kwargs):
            raise RuntimeError("credit balance is too low")

    task = next(t for t in load_tasks("tasks") if t.id == "T15")
    row = run_task(task, Broken(), FakeSession(), BFL_TOOLS, Policy(dry_run=True))
    assert row["errored"] and not row["passed"]
    text = render_report([{k: v for k, v in row.items() if k != "transcript"}])
    assert "did not run (T15)" in text
    assert "| ERROR " in text


def test_review_page_lists_every_open_question_and_says_when_it_cannot_show_images():
    import json as _json

    from first_try.review import render_review

    rows = _run({"T01", "T05"}, {
        "T01": [("generate_image", {"requests": [{
            "model": "flux2_max", "prompt": "a market stall",
            "input_medias": [{"url": "https://x.test/style.jpg"}]}]})],
        "T05": [("generate_image", {"requests": [{"model": "flux2_flex", "prompt": "'NIGHT MARKET'"}]})],
    })
    serialisable = [{k: v for k, v in r.items() if k != "transcript"} for r in rows.values()]
    transcripts = {tid: _json.loads(r["transcript"].to_json()) for tid, r in rows.items()}
    page = render_review(serialisable, transcripts)

    assert "T01" in page and "T05" in page
    assert "does the output actually carry the reference style" in page
    # Dry run: nothing was generated, and the page must say so rather than
    # showing an empty grid that looks like a verdict.
    assert "needs a live run before it can be judged" in page


def test_review_page_is_empty_when_nothing_is_outstanding():
    from first_try.review import render_review

    assert "Nothing outstanding" in render_review([], {})
