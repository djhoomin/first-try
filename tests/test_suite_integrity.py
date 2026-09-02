"""Guards against the harness accusing the platform of its own bugs.

Six of the failures in this suite's early runs were defects in the checks
rather than in the thing being checked, and every one of them looked like a
plausible finding. These tests encode the shapes that actually occurred.
"""

import json
from pathlib import Path

import pytest
import yaml

from first_try.checks import CHECKS, resolve
from first_try.tasks import load_tasks

SUITE = Path("tasks")


def test_every_check_kind_exists():
    for task in load_tasks(SUITE):
        for check in task.checks:
            assert check["kind"] in CHECKS, f"{task.id}: unknown check {check['kind']}"


def test_every_check_names_a_path_or_none_but_not_both_forms():
    for task in load_tasks(SUITE):
        for check in task.checks:
            assert not ("path" in check and "paths" in check), \
                f"{task.id}: {check['kind']} sets both path and paths"


def test_argument_paths_resolve_against_a_recorded_call():
    """The bug that cost a run: `draft` was read at the top level of the call
    while both models set it on each request, so a correct call scored as a
    failure across two models."""
    call = {"requests": [{"model": "flux2_pro_preview", "draft": True,
                          "prompt": "p", "input_medias": [{"url": "u"}]}]}
    assert resolve(call, "draft") == []                    # the wrong path
    assert resolve(call, "requests[*].draft") == [True]     # the right one


def test_draft_is_checked_at_both_levels():
    task = next(t for t in load_tasks(SUITE) if t.id == "T10")
    check = next(c for c in task.checks if c["kind"] == "arg_equals")
    assert set(check["paths"]) == {"draft", "requests[*].draft"}


def test_no_check_uses_the_prose_parameter_name():
    """The docs call reference images `input_image`; the schema takes
    `input_medias`. Writing checks from prose produced three false failures."""
    raw = (SUITE / "suite-v1.yaml").read_text()
    assert "input_image" not in raw


@pytest.mark.parametrize("task_id", ["T09", "T12"])
def test_tasks_that_should_not_generate_do_not_require_it(task_id):
    """A suite must not reward asking in one task and punish it in another."""
    task = next(t for t in load_tasks(SUITE) if t.id == task_id)
    kinds = [(c["kind"], c.get("value")) for c in task.checks]
    assert ("no_generation", None) in kinds
    assert ("called_tool", "generate_image") not in kinds
