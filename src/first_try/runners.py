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
    """Anthropic SDK. Adaptive thinking on, because that is how it ships."""

    def __init__(self, model: str = "claude-opus-5", max_tokens: int = 8192) -> None:
        import anthropic

        self.name = f"claude:{model}"
        self.model = model
        self.max_tokens = max_tokens
        self.client = anthropic.Anthropic()

    def run(self, *, prompt, setup, tools, invoke, max_turns):
        messages: list[dict[str, Any]] = [
            {"role": m["role"], "content": m["content"]} for m in setup
        ]
        messages.append({"role": "user", "content": prompt})
        final_text, turn = "", 0

        for turn in range(1, max_turns + 1):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                tools=to_anthropic_tools(tools),
                messages=messages,
                thinking={"type": "adaptive"},
            )
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
