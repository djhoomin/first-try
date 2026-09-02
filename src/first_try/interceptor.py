"""The policy layer between the agent and the real MCP server.

Every tool call passes through here, which is what makes the benchmark possible
at all: it is the single point where a call can be priced, recorded, and denied
before it spends money.

One design decision is worth stating plainly, because it shapes every result.

When policy blocks a call, we return a SYNTHETIC SUCCESS to the model rather
than an error. Returning an error would mean measuring how the agent recovers
from our safety rail instead of how it uses the platform, and the recovery axis
has its own tasks that use real server errors. The blocked call is still fully
recorded, and `Transcript.intended_usd` counts it. For a task like the four
twenty-second clips, the agent's intent is the entire finding and there is
nothing to learn from paying $23.20 to confirm it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .mcp_client import image_urls
from .pricing import estimate_call
from .transcript import ToolCall, Transcript

__all__ = ["Policy", "Interceptor", "BudgetExceeded"]


class BudgetExceeded(RuntimeError):
    """The run hit its ceiling. Raised rather than silently degrading."""


class ToolBackend(Protocol):
    """Anything that can execute a tool call. The MCP client in practice."""

    def call_tool(self, name: str, args: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class Policy:
    """What this run is allowed to spend.

    `dry_run` blocks every billable call. `per_call_cap_usd` blocks individual
    calls that are too expensive while letting cheap ones through, which is how
    a mostly-live run can still include one task that must never execute.
    """

    dry_run: bool = False
    budget_usd: float = 5.0
    per_call_cap_usd: float = 1.00
    #: Tools that never cost anything always run, even in dry run, because a
    #: dry run that cannot read `bfl://models` or check credits is not measuring
    #: the same product a real user gets.
    always_allow_free: bool = True

    def verdict(self, est_usd: float, spent_usd: float) -> str:
        """Empty string means allow. Otherwise the reason for blocking."""
        if est_usd <= 0 and self.always_allow_free:
            return ""
        if self.dry_run:
            return "dry run"
        if est_usd > self.per_call_cap_usd:
            return f"call estimated at ${est_usd:.2f}, over the ${self.per_call_cap_usd:.2f} cap"
        if spent_usd + est_usd > self.budget_usd:
            return f"would take the run past its ${self.budget_usd:.2f} budget"
        return ""


SYNTHETIC_NOTE = (
    "This call was not executed. The harness is running under a spend policy. "
    "Treat it as having succeeded and continue as you normally would."
)


def default_synthetic_result(name: str, args: dict[str, Any]) -> Any:
    """A plausible success shape for a blocked call.

    Deliberately shallow. It has to be good enough that the agent carries on
    rather than retrying, and no better, because anything more elaborate starts
    inventing platform behaviour the report might then rely on.
    """
    if name in {"generate_image", "generate_variations", "vto"}:
        count = len(args.get("requests") or [args]) if name == "generate_image" else 1
        return {
            "status": "ready",
            "note": SYNTHETIC_NOTE,
            "results": [{"request_id": f"synthetic-{name}-{i}"} for i in range(count)],
        }
    if name in {"generate_video", "enhance_video"}:
        return {"status": "pending", "request_id": f"synthetic-{name}", "note": SYNTHETIC_NOTE}
    return {"status": "ok", "note": SYNTHETIC_NOTE}


class Interceptor:
    """Prices, records and gates every call for one task run."""

    def __init__(
        self,
        backend: ToolBackend,
        transcript: Transcript,
        policy: Policy,
        synthesise: Callable[[str, dict[str, Any]], Any] = default_synthetic_result,
    ) -> None:
        self.backend = backend
        self.transcript = transcript
        self.policy = policy
        self.synthesise = synthesise

    def call(self, name: str, args: dict[str, Any], *, turn: int) -> Any:
        """Execute one tool call under policy. Always returns something usable."""
        args = args or {}
        estimate = estimate_call(name, args)
        record = ToolCall(
            turn=turn,
            name=name,
            args=args,
            est_usd=estimate.usd,
            est_exact=estimate.exact,
        )
        self.transcript.calls.append(record)

        reason = self.policy.verdict(estimate.usd, self.transcript.spend_usd)
        if reason:
            record.blocked = True
            record.block_reason = reason
            record.result_summary = f"blocked: {reason}"
            return self.synthesise(name, args)

        try:
            result = self.backend.call_tool(name, args)
        except Exception as exc:  # the server said no; that is data, not a crash
            record.failed = True
            record.error = f"{type(exc).__name__}: {exc}"
            record.result_summary = "error"
            # The agent sees the real error text. Recovery is being measured.
            return {"error": str(exc)}

        record.result_summary = _summarise(result)
        record.result_urls = image_urls(result)
        return result


def _summarise(result: Any, limit: int = 400) -> str:
    try:
        text = result if isinstance(result, str) else json.dumps(result, default=str)
    except (TypeError, ValueError):
        text = repr(result)
    return text[:limit]
