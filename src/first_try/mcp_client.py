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
from concurrent.futures import TimeoutError as FuturesTimeout
from contextlib import AsyncExitStack
from typing import Any, Awaitable, Callable

__all__ = [
    "McpSession",
    "McpTimeout",
    "ResourceTools",
    "SessionWithResources",
    "to_anthropic_tools",
    "to_openai_tools",
]

Opener = Callable[[AsyncExitStack], Awaitable[Any]]

#: Generous by default. Image batches take a while, and video returns a
#: request id immediately rather than blocking for the render.
DEFAULT_CALL_TIMEOUT_S = 300.0


class McpTimeout(TimeoutError):
    """A call did not come back. Usually a transport that died quietly.

    A laptop going to sleep mid-run is the common cause: the HTTP connection
    behind the bridge is gone, nothing errors, and the future never resolves.
    Waiting forever is the worst available behaviour, so we stop.
    """



class McpSession:
    """Connect once, call many times, close cleanly, all on one task."""

    def __init__(self, call_timeout: float = DEFAULT_CALL_TIMEOUT_S) -> None:
        self.call_timeout = call_timeout
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
        try:
            return future.result(timeout=self.call_timeout)
        except FuturesTimeout as exc:
            raise McpTimeout(
                f"no response after {self.call_timeout:.0f}s. The connection to the "
                "server is probably dead; if the machine slept mid-run, that is why."
            ) from exc

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

    def read_resource(self, uri: str) -> Any:
        """Fetch one resource by URI."""

        async def _read():
            result = await self._session.read_resource(uri)
            out = []
            for item in result.contents or []:
                out.append(getattr(item, "text", None) or getattr(item, "blob", None) or str(item))
            return {"uri": uri, "contents": out}

        return self._submit(_read)

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


# --- exposing resources to a model that can only call tools -----------------


class ResourceTools:
    """Presents MCP resources as two ordinary tools.

    Resources are a first-class part of MCP, but most clients surface them to
    the *human* (as attachments or mentions) rather than to the model, and the
    model-facing tool APIs have no native notion of them. A benchmark that never
    exposes them is measuring a client that cannot read a server's own
    documentation, which for FLUX means the `bfl://models` catalogue is
    unreachable and any conclusion about capability discovery is unsupported.

    This is a methodological choice rather than a neutral one, so it is recorded
    on the transcript and named in the report. `--resources none` reproduces the
    stricter reading, where a server's resources simply are not available.
    """

    LIST = "list_resources"
    READ = "read_resource"

    def __init__(self, session: McpSession, taken: set[str] | None = None) -> None:
        self.session = session
        taken = taken or set()
        # Never shadow a real tool. If the server already owns the name, the
        # server wins and ours moves aside.
        self.list_name = self.LIST if self.LIST not in taken else f"mcp_{self.LIST}"
        self.read_name = self.READ if self.READ not in taken else f"mcp_{self.READ}"

    @property
    def names(self) -> set[str]:
        return {self.list_name, self.read_name}

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": self.list_name,
                "description": (
                    "List the resource URIs this server publishes. Resources hold "
                    "reference material such as model catalogues and capability "
                    "descriptions."
                ),
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": self.read_name,
                "description": "Read one resource by its URI, as returned by listing them.",
                "input_schema": {
                    "type": "object",
                    "properties": {"uri": {"type": "string", "description": "The resource URI"}},
                    "required": ["uri"],
                },
            },
        ]

    def handles(self, name: str) -> bool:
        return name in self.names

    def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        if name == self.list_name:
            return {"resources": self.session.list_resources()}
        if name == self.read_name:
            uri = (args or {}).get("uri")
            if not uri:
                raise ValueError("read_resource needs a uri; call list_resources first")
            return self.session.read_resource(uri)
        raise KeyError(name)


class SessionWithResources:
    """Routes the synthetic resource tools, passes everything else through."""

    def __init__(self, session: McpSession, resources: ResourceTools) -> None:
        self.session = session
        self.resources = resources

    def list_tools(self) -> list[dict[str, Any]]:
        return self.session.list_tools() + self.resources.definitions()

    def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        if self.resources.handles(name):
            return self.resources.call_tool(name, args)
        return self.session.call_tool(name, args)

    def close(self) -> None:
        self.session.close()
