"""Declarative assertions over a transcript.

Checks live in the task YAML rather than in Python so that the suite is
readable by someone who is not going to read the harness, which includes most of
the people the findings are aimed at. A benchmark nobody can audit is a blog
post with extra steps.

Every check answers one question with a boolean and an explanation. The
explanation is what lands in the report, so it is written for a reader who has
not seen the call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .transcript import Transcript

__all__ = ["CheckResult", "run_checks", "CHECKS"]

_INDEX = re.compile(r"^(?P<key>[^\[\]]+)(?:\[(?P<idx>\d+|\*)\])?$")


@dataclass(frozen=True)
class CheckResult:
    kind: str
    passed: bool
    detail: str
    #: Not measurable under this run's conditions. Neither a pass nor a
    #: failure, and excluded from the score. A benchmark that reports an
    #: unmeasurable thing as a failure is manufacturing findings.
    skipped: bool = False


def resolve(obj: Any, path: str) -> list[Any]:
    """Resolve a dotted path with optional indexing, e.g. requests[*].model.

    Returns every match, so `[*]` and a missing key both behave sensibly: a
    missing key yields an empty list rather than raising, because "the agent did
    not pass this argument at all" is a normal and interesting outcome.
    """
    current: list[Any] = [obj]
    for part in path.split("."):
        match = _INDEX.match(part)
        if not match:
            return []
        key, idx = match.group("key"), match.group("idx")
        nxt: list[Any] = []
        for item in current:
            if not isinstance(item, dict) or key not in item:
                continue
            value = item[key]
            if idx is None:
                nxt.append(value)
            elif idx == "*":
                nxt.extend(value if isinstance(value, list) else [value])
            else:
                seq = value if isinstance(value, list) else [value]
                position = int(idx)
                if position < len(seq):
                    nxt.append(seq[position])
        current = nxt
    return current


def _calls_named(t: Transcript, name: str | None) -> list[Any]:
    return [c for c in t.calls if name in (None, c.name)]


# --- individual checks -----------------------------------------------------


def first_tool_is(t: Transcript, spec: dict) -> CheckResult:
    want, got = spec["value"], t.first_tool
    return CheckResult(
        "first_tool_is", got == want,
        f"first tool called was {got or 'none'}, expected {want}",
    )


def first_generating_tool_is(t: Transcript, spec: dict) -> CheckResult:
    """Which tool it chose to do the work, ignoring free reconnaissance.

    Agents routinely read a server's skill guides or check credits before
    acting, which is good behaviour. `first_tool_is` punishes it. This asks the
    question that was actually meant: of the calls that cost money, which came
    first.
    """
    want = spec["value"]
    billable = [c for c in t.calls if c.est_usd > 0]
    got = billable[0].name if billable else None
    return CheckResult(
        "first_generating_tool_is", got == want,
        f"first billable call was {got or 'none'}, expected {want}",
    )


def called_tool_times(t: Transcript, spec: dict) -> CheckResult:
    want, times = spec["value"], int(spec["times"])
    got = t.tool_names.count(want)
    return CheckResult("called_tool_times", got == times, f"{want} called {got} times, expected {times}")


def called_tool(t: Transcript, spec: dict) -> CheckResult:
    want = spec["value"]
    ok = want in t.tool_names
    return CheckResult("called_tool", ok, f"{want} was {'' if ok else 'not '}called")


def never_called_tool(t: Transcript, spec: dict) -> CheckResult:
    want = spec["value"]
    ok = want not in t.tool_names
    return CheckResult("never_called_tool", ok, f"{want} was {'not ' if ok else ''}called")


def arg_equals(t: Transcript, spec: dict) -> CheckResult:
    tool, path, want = spec.get("tool"), spec["path"], spec["value"]
    values = [v for c in _calls_named(t, tool) for v in resolve(c.args, path)]
    ok = bool(values) and all(v == want for v in values)
    return CheckResult("arg_equals", ok, f"{path} was {values or 'never set'}, expected {want}")


def arg_in(t: Transcript, spec: dict) -> CheckResult:
    tool, path, allowed = spec.get("tool"), spec["path"], list(spec["value"])
    values = [v for c in _calls_named(t, tool) for v in resolve(c.args, path)]
    ok = bool(values) and all(v in allowed for v in values)
    return CheckResult("arg_in", ok, f"{path} was {values or 'never set'}, expected one of {allowed}")


def arg_present(t: Transcript, spec: dict) -> CheckResult:
    tool, path = spec.get("tool"), spec["path"]
    values = [v for c in _calls_named(t, tool) for v in resolve(c.args, path) if v]
    return CheckResult("arg_present", bool(values), f"{path} {'was set' if values else 'was never set'}")


def arg_absent(t: Transcript, spec: dict) -> CheckResult:
    tool, path = spec.get("tool"), spec["path"]
    values = [v for c in _calls_named(t, tool) for v in resolve(c.args, path) if v]
    return CheckResult("arg_absent", not values, f"{path} {'was set to ' + str(values) if values else 'was absent'}")


def arg_matches(t: Transcript, spec: dict) -> CheckResult:
    """Regex over a string argument. Used for quoted-text and negation checks."""
    tool, path, pattern = spec.get("tool"), spec["path"], spec["value"]
    rx = re.compile(pattern, re.IGNORECASE)
    values = [str(v) for c in _calls_named(t, tool) for v in resolve(c.args, path)]
    ok = bool(values) and all(rx.search(v) for v in values)
    return CheckResult("arg_matches", ok, f"{path} against /{pattern}/: {values or 'never set'}")


def arg_not_matches(t: Transcript, spec: dict) -> CheckResult:
    tool, path, pattern = spec.get("tool"), spec["path"], spec["value"]
    rx = re.compile(pattern, re.IGNORECASE)
    values = [str(v) for c in _calls_named(t, tool) for v in resolve(c.args, path)]
    hits = [v for v in values if rx.search(v)]
    return CheckResult("arg_not_matches", not hits, f"{path} should not match /{pattern}/: {hits or 'clean'}")


def spend_at_most(t: Transcript, spec: dict) -> CheckResult:
    """Scored on intent, so a dry run still scores.

    Using `intended_usd` rather than `spend_usd` is the point: if the harness
    blocked a call to protect the budget, the agent still meant to make it.
    """
    cap = float(spec["value"])
    got = t.intended_usd
    return CheckResult("spend_at_most", got <= cap + 1e-9, f"intended spend ${got:.3f}, cap ${cap:.3f}")


def no_generation(t: Transcript, spec: dict) -> CheckResult:
    gens = t.generations
    return CheckResult(
        "no_generation", not gens,
        "no billable call" if not gens else f"generated anyway: {[c.name for c in gens]}",
    )


def turns_to_success_at_most(t: Transcript, spec: dict) -> CheckResult:
    """Turns until a generating call went through cleanly.

    Unmeasurable in a dry run: every billable call is blocked by policy, so
    nothing ever "succeeds" and the check would fail for reasons that have
    nothing to do with the platform. Recovery needs a live run.
    """
    cap = int(spec["value"])
    if t.dry_run:
        return CheckResult(
            "turns_to_success_at_most", False,
            "not measurable in a dry run: billable calls never execute, so recovery "
            "cannot be observed. Run this task live.",
            skipped=True,
        )
    got = t.turns_to_first_success()
    ok = got is not None and got <= cap
    return CheckResult("turns_to_success_at_most", ok, f"first clean generation on turn {got}, cap {cap}")


def asks_a_question(t: Transcript, spec: dict) -> CheckResult:
    """Did the agent come back with a question instead of guessing?"""
    ok = "?" in t.final_text and not t.generations
    return CheckResult("asks_a_question", ok, f"final text {'contains' if '?' in t.final_text else 'lacks'} a question; {len(t.generations)} generations")


def manual(t: Transcript, spec: dict) -> CheckResult:
    """A judgement a human or a judge model has to make. Never auto-passes."""
    return CheckResult("manual", False, f"needs review: {spec.get('note', '')}")


CHECKS: dict[str, Callable[[Transcript, dict], CheckResult]] = {
    "first_tool_is": first_tool_is,
    "first_generating_tool_is": first_generating_tool_is,
    "called_tool_times": called_tool_times,
    "called_tool": called_tool,
    "never_called_tool": never_called_tool,
    "arg_equals": arg_equals,
    "arg_in": arg_in,
    "arg_present": arg_present,
    "arg_absent": arg_absent,
    "arg_matches": arg_matches,
    "arg_not_matches": arg_not_matches,
    "spend_at_most": spend_at_most,
    "no_generation": no_generation,
    "turns_to_success_at_most": turns_to_success_at_most,
    "asks_a_question": asks_a_question,
    "manual": manual,
}


def run_checks(transcript: Transcript, specs: list[dict]) -> list[CheckResult]:
    results = []
    for spec in specs:
        kind = spec.get("kind")
        fn = CHECKS.get(kind)
        if fn is None:
            results.append(CheckResult(str(kind), False, f"unknown check kind: {kind}"))
            continue
        results.append(fn(transcript, spec))
    return results
