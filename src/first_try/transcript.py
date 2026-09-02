"""The record of one task run.

Everything the report says has to be derivable from this object, so it holds the
raw calls rather than a summary of them. A benchmark whose findings cannot be
traced back to a specific recorded call is an opinion with a number attached.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = ["ToolCall", "Transcript"]


@dataclass
class ToolCall:
    """One attempted tool call, whether or not it reached the server."""

    turn: int
    name: str
    args: dict[str, Any]
    est_usd: float
    est_exact: bool
    blocked: bool = False          # stopped by policy, never reached the server
    block_reason: str = ""
    failed: bool = False           # the server rejected it
    error: str = ""
    result_summary: str = ""
    #: Output media the call produced, for review.
    result_urls: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    @property
    def billable(self) -> bool:
        """Did this call actually cost money?"""
        return not self.blocked and not self.failed and self.est_usd > 0


@dataclass
class Transcript:
    task_id: str
    runner: str
    dry_run: bool
    #: How MCP resources were exposed to the model: "tools" or "none".
    resource_mode: str = "tools"
    calls: list[ToolCall] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    final_text: str = ""
    turns: int = 0
    stopped_early: str = ""        # why the loop ended, if not naturally
    #: Tokens the runner spent on this task, including cache traffic.
    usage: dict = field(default_factory=dict)

    # --- derived views the checks and the report both read -----------------

    @property
    def tool_names(self) -> list[str]:
        return [c.name for c in self.calls]

    @property
    def first_tool(self) -> str | None:
        return self.calls[0].name if self.calls else None

    @property
    def spend_usd(self) -> float:
        return sum(c.est_usd for c in self.calls if c.billable)

    @property
    def intended_usd(self) -> float:
        """What it would have cost if nothing had been blocked.

        This is the number that matters in dry run: the agent's intent is the
        finding, and blocking it is our safety measure, not its restraint.
        """
        return sum(c.est_usd for c in self.calls if not c.failed)

    @property
    def generations(self) -> list[ToolCall]:
        return [c for c in self.calls if c.est_usd > 0]

    @property
    def failures(self) -> list[ToolCall]:
        return [c for c in self.calls if c.failed]

    def turns_to_first_success(self) -> int | None:
        """How many turns until a generating call went through cleanly.

        None means it never did. This is the recovery number: an error an LLM
        cannot act on shows up here as a large integer or a None.
        """
        for call in self.calls:
            if call.est_usd > 0 and not call.failed and not call.blocked:
                return call.turn
        return None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)
