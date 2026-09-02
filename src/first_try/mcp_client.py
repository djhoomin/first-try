"""A synchronous facade over an MCP server connection.

The MCP SDK is async and the rest of the harness is not. Rather than make the
whole benchmark async for the sake of one boundary, the connection lives on a
background thread and calls are marshalled onto it.

The subtlety, learned the hard way: `run_coroutine_threadsafe` starts a **new
task** for every submission, and anyio task groups (which the stdio transport
uses internally) must be entered and exited in the *same* task. Opening the
connection in one submission and closing it in another therefore blows up with
"attempted to exit cancel scope in a different task".

So the whole session lifetime runs inside one long-lived task: `_serve` opens
the connection, then pulls work off a queue and awaits it, and closes the
connection itself when it receives the shutdown sentinel. Every call runs in
that same task, which is the only arrangement the transports accept.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from contextlib import AsyncExitStack
from typing import Any, Awaitable, Callable

__all__ = ["McpSession", "to_anthropic_tools", "to_openai_tools"]

Opener = Callable[[AsyncExitStack], Awaitable[Any]]


class McpSession:
    """Connect once, call many times, close cleanly, all on one task."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="mcp-loop")
        self._thread.start()
        self._session: Any = None
        self._queue: asyncio.Queue | None = None
        self._ready: Future = Future()
        self._serving: Future | None = None

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # --- the single task that owns the connection --------------------------

    async def _serve(self, opener: Opener) -> None:
        self._queue = asyncio.Queue()
        try:
            async with AsyncExitStack() as stack:
                self._session = await opener(stack)
                self._ready.set_result(None)
                while True:
                    item = await self._queue.get()
                    if item is None:          # shutdown sentinel
                        return
                    factory, future = item
                    try:
                        future.set_result(await factory())
                    except Exception as exc:
                        future.set_exception(exc)
        except Exception as exc:
            if not self._ready.done():
                self._ready.set_exception(exc)
            else:
                raise

    def _start(self, opener: Opener) -> None:
        self._serving = asyncio.run_coroutine_threadsafe(self._serve(opener), self._loop)
        self._ready.result()   # re-raises whatever went wrong while connecting

    def _submit(self, factory: Callable[[], Awaitable[Any]]) -> Any:
        if self._queue is None:
            raise RuntimeError("not connected: call connect_stdio or connect_http first")
        future: Future = Future()
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (factory, future))
        return future.result()

    # --- connection --------------------------------------------------------

    def connect_stdio(self, command: str, args: list[str], env: dict[str, str] | None = None) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def opener(stack: AsyncExitStack) -> Any:
            params = StdioServerParameters(command=command, args=args, env=env)
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            return session

        self._start(opener)

    def connect_http(self, url: str, headers: dict[str, str] | None = None) -> None:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def opener(stack: AsyncExitStack) -> Any:
            transport = await stack.enter_async_context(
                streamablehttp_client(url, headers=headers or {})
            )
            session = await stack.enter_async_context(ClientSession(transport[0], transport[1]))
            await session.initialize()
            return session

        self._start(opener)

    def connect_with(self, opener: Opener) -> None:
        """Connect using a caller-supplied opener. Used by the tests."""
        self._start(opener)

    # --- use ---------------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        """Tool definitions, in the shape the runners hand to a model.

        The SDK exposes snake_case attributes; `inputSchema` is a wire alias
        only, and reaching for it raises.
        """

        async def _list():
            result = await self._session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.input_schema or {"type": "object", "properties": {}},
                }
                for t in result.tools
            ]

        return self._submit(_list)

    def list_resources(self) -> list[str]:
        async def _list():
            try:
                result = await self._session.list_resources()
            except Exception:
                return []            # a server without resources is not an error
            return [str(r.uri) for r in result.resources]

        return self._submit(_list)

    def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        """Execute a tool. Raises on server error so the interceptor records it."""

        async def _call():
            result = await self._session.call_tool(name, args)
            content = [getattr(c, "text", None) or str(c) for c in (result.content or [])]
            if result.is_error:
                raise RuntimeError("\n".join(content) or "tool reported an error")
            return {"content": content}

        return self._submit(_call)

    def close(self) -> None:
        """Shut down in the same task that opened the connection.

        Defensive throughout: close runs in a `finally` after a failure, and a
        close that raises would replace the real error with a less useful one.
        """
        try:
            if self._queue is not None:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
            if self._serving is not None:
                self._serving.result(timeout=15)
        except Exception:
            pass
        finally:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._thread.join(timeout=5)
            except Exception:
                pass


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
