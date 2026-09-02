"""A synchronous facade over an MCP server connection.

The MCP SDK is async and the rest of the harness is not. Rather than make the
whole benchmark async for the sake of one boundary, the event loop lives on a
background thread and every call is marshalled onto it. The messy part stays in
one file and the core stays synchronously testable.

Two transports:

- **stdio**, which runs a server as a subprocess. This is the practical path for
  benchmarking BFL, because `flux-mcp` is open source and running it locally
  with an API key avoids the interactive OAuth flow that `mcp.bfl.ai` requires.
- **http**, for hosted servers, where the caller supplies its own auth headers.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Any

__all__ = ["McpSession", "to_anthropic_tools", "to_openai_tools"]


class McpSession:
    """Connect once, call many times, close cleanly."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="mcp-loop")
        self._thread.start()
        self._session: Any = None
        self._stack: Any = None

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro) -> Any:
        future: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    # --- connection --------------------------------------------------------

    def connect_stdio(self, command: str, args: list[str], env: dict[str, str] | None = None) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def _open() -> None:
            self._stack = AsyncExitStack()
            params = StdioServerParameters(command=command, args=args, env=env)
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            await self._session.initialize()

        self._submit(_open())

    def connect_http(self, url: str, headers: dict[str, str] | None = None) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def _open() -> None:
            self._stack = AsyncExitStack()
            transport = await self._stack.enter_async_context(
                streamablehttp_client(url, headers=headers or {})
            )
            read, write = transport[0], transport[1]
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            await self._session.initialize()

        self._submit(_open())

    # --- use ---------------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        async def _list():
            result = await self._session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.inputSchema or {"type": "object", "properties": {}},
                }
                for t in result.tools
            ]

        return self._submit(_list())

    def list_resources(self) -> list[str]:
        async def _list():
            try:
                result = await self._session.list_resources()
            except Exception:
                return []
            return [str(r.uri) for r in result.resources]

        return self._submit(_list())

    def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        """Execute a tool. Raises on server error so the interceptor records it."""

        async def _call():
            result = await self._session.call_tool(name, args)
            content = [getattr(c, "text", str(c)) for c in (result.content or [])]
            if getattr(result, "isError", False):
                raise RuntimeError("\n".join(content) or "tool reported an error")
            return {"content": content}

        return self._submit(_call())

    def close(self) -> None:
        async def _close():
            if self._stack is not None:
                await self._stack.aclose()

        try:
            self._submit(_close())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)


def to_anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
        for t in tools
    ]


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]
