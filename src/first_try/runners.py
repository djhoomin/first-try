"""Agent runners.

The system prompt is deliberately plain. The benchmark asks whether an agent
gets the platform right from the tool definitions alone, so priming it with FLUX
knowledge would measure the prompt instead of the product. If a runner needs to
be told how the platform works, that is the finding.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Protocol

from .mcp_client import to_anthropic_tools, to_openai_tools

__all__ = ["Runner", "ClaudeRunner", "OpenAICompatRunner", "SYSTEM_PROMPT"]

SYSTEM_PROMPT = (
    "You are a helpful assistant with access to a set of tools. Use them to do "
    "what the user asks. Be efficient and do not ask for confirmation of things "
    "you can reasonably decide yourself."
)

#: What a runner hands back: the closing text and how many turns it took.
Invoke = Callable[[str, dict[str, Any], int], Any]


class Runner(Protocol):
    name: str

    def run(
        self, *, prompt: str, setup: list[dict[str, str]], tools: list[dict[str, Any]],
        invoke: Invoke, max_turns: int,
    ) -> tuple[str, int, list[dict[str, Any]]]: ...


class ClaudeRunner:
    """Anthropic SDK. Adaptive thinking on, because that is how it ships.

    Caches aggressively, for a reason that is not only thrift. The system prompt
    and the server's tool schemas are byte-identical on every turn of every task
    in a run, and a task's own conversation grows monotonically. Without caching,
    an agent that reads a long skill guide on turn one pays for that guide again
    on every later turn, and the suite's cost scales with the square of how
    thorough the agent is. Measuring cost discipline while being profligate
    about it would be a poor look.
    """

    def __init__(self, model: str = "claude-sonnet-5", max_tokens: int = 8192,
                 cache: bool = True) -> None:
        import anthropic

        self.name = f"claude:{model}"
        self.model = model
        self.max_tokens = max_tokens
        self.cache = cache
        self.client = anthropic.Anthropic()
        self.usage: dict[str, int] = {
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        }

    def _system(self):
        block: dict[str, Any] = {"type": "text", "text": SYSTEM_PROMPT}
        if self.cache:
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    def _tools(self, tools):
        defs = to_anthropic_tools(tools)
        if self.cache and defs:
            # Marking the last definition caches the whole tool block, which is
            # identical across every task in the run.
            defs[-1] = {**defs[-1], "cache_control": {"type": "ephemeral"}}
        return defs

    def _mark_conversation(self, messages: list[dict[str, Any]]) -> None:
        """Cache the conversation prefix, so each turn only pays for what is new."""
        if not self.cache or not messages:
            return
        for message in messages:
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        block.pop("cache_control", None)
        last = messages[-1]
        content = last.get("content")
        if isinstance(content, list) and content and isinstance(content[-1], dict):
            content[-1]["cache_control"] = {"type": "ephemeral"}

    def _record(self, response) -> None:
        usage = getattr(response, "usage", None)
        for key in self.usage:
            self.usage[key] += getattr(usage, key, 0) or 0

    def run(self, *, prompt, setup, tools, invoke, max_turns):
        messages: list[dict[str, Any]] = [
            {"role": m["role"], "content": m["content"]} for m in setup
        ]
        messages.append({"role": "user", "content": prompt})
        final_text, turn = "", 0

        for turn in range(1, max_turns + 1):
            self._mark_conversation(messages)
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self._system(),
                tools=self._tools(tools),
                messages=messages,
                thinking={"type": "adaptive"},
            )
            self._record(response)
            messages.append({"role": "assistant", "content": response.content})
            uses = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
            if not uses:
                final_text = "".join(
                    getattr(b, "text", "") for b in response.content
                    if getattr(b, "type", "") == "text"
                )
                break
            results = []
            for use in uses:
                output = invoke(use.name, dict(use.input or {}), turn)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": use.id,
                    "content": json.dumps(output, default=str),
                })
            messages.append({"role": "user", "content": results})

        return final_text, turn, _plain(messages)


class OpenAICompatRunner:
    """Any OpenAI-shaped chat-completions endpoint, for a second opinion."""

    def __init__(self, model: str, base_url: str | None = None, api_key_env: str = "OPENAI_API_KEY") -> None:
        import os

        from openai import OpenAI

        self.name = f"openai:{model}"
        self.model = model
        self.client = OpenAI(base_url=base_url, api_key=os.environ.get(api_key_env))

    def run(self, *, prompt, setup, tools, invoke, max_turns):
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += [{"role": m["role"], "content": m["content"]} for m in setup]
        messages.append({"role": "user", "content": prompt})
        final_text, turn = "", 0

        for turn in range(1, max_turns + 1):
            response = self.client.chat.completions.create(
                model=self.model, messages=messages, tools=to_openai_tools(tools),
            )
            message = response.choices[0].message
            messages.append(message.model_dump(exclude_none=True))
            calls = message.tool_calls or []
            if not calls:
                final_text = message.content or ""
                break
            for call in calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    # Malformed tool arguments are themselves a first-try
                    # failure, so record the attempt rather than dropping it.
                    args = {"__unparsed__": call.function.arguments}
                output = invoke(call.function.name, args, turn)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(output, default=str),
                })

        return final_text, turn, _plain(messages)


def _plain(messages: list[Any]) -> list[dict[str, Any]]:
    """Make a transcript JSON-serialisable without depending on SDK types."""
    return json.loads(json.dumps(messages, default=lambda o: getattr(o, "model_dump", lambda: str(o))()))
