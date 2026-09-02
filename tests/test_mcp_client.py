"""Regression tests for the sync-over-async boundary.

These reproduce the two failures the first live run hit, without needing a
server: the connection must open and close on the *same* task, and the SDK's
attribute names are snake_case rather than the wire aliases.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest

from first_try.mcp_client import McpSession, to_anthropic_tools


class Recorder:
    """Records which asyncio task entered and exited the context."""

    def __init__(self):
        self.entered_in = None
        self.exited_in = None
        self.calls = []


@asynccontextmanager
async def tracked(recorder: Recorder):
    recorder.entered_in = asyncio.current_task()
    try:
        yield recorder
    finally:
        recorder.exited_in = asyncio.current_task()


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = "does a thing"
        self.input_schema = {"type": "object", "properties": {"x": {"type": "string"}}}


class FakeResult:
    def __init__(self, tools):
        self.tools = tools


class FakeSession:
    def __init__(self, recorder):
        self.recorder = recorder

    async def list_tools(self):
        self.recorder.calls.append("list_tools")
        return FakeResult([FakeTool("generate_image"), FakeTool("get_credits")])


def _connected():
    recorder = Recorder()
    session = McpSession()

    async def opener(stack):
        await stack.enter_async_context(tracked(recorder))
        return FakeSession(recorder)

    session.connect_with(opener)
    return session, recorder


def test_connection_opens_and_closes_on_the_same_task():
    """anyio task groups forbid exiting a cancel scope in a different task.

    run_coroutine_threadsafe starts a new task per submission, so opening in one
    submission and closing in another raised 'attempted to exit cancel scope in
    a different task'. The whole session must live on one task.
    """
    session, recorder = _connected()
    session.list_tools()
    session.close()
    assert recorder.entered_in is not None
    assert recorder.entered_in is recorder.exited_in


def test_list_tools_uses_snake_case_sdk_attributes():
    """`inputSchema` is a wire alias; reading it off the model raises."""
    session, _ = _connected()
    try:
        tools = session.list_tools()
    finally:
        session.close()
    assert [t["name"] for t in tools] == ["generate_image", "get_credits"]
    assert tools[0]["input_schema"]["properties"] == {"x": {"type": "string"}}
    assert to_anthropic_tools(tools)[0]["input_schema"] == tools[0]["input_schema"]


def test_many_calls_all_run_on_the_connection_task():
    session, recorder = _connected()
    try:
        for _ in range(5):
            session.list_tools()
    finally:
        session.close()
    assert recorder.calls == ["list_tools"] * 5
    assert recorder.entered_in is recorder.exited_in


def test_a_failing_opener_raises_on_connect_not_later():
    session = McpSession()

    async def opener(stack):
        raise RuntimeError("server said no")

    with pytest.raises(RuntimeError, match="server said no"):
        session.connect_with(opener)
    session.close()


def test_close_is_safe_on_a_session_that_never_connected():
    """close() runs in a finally after a failure; it must not raise there."""
    McpSession().close()


# --- exposing resources ----------------------------------------------------


class FakeResourceSession(FakeSession):
    async def list_resources(self):
        class R:
            def __init__(self, uri):
                self.uri = uri
        return type("Res", (), {"resources": [R("bfl://models")]})()

    async def read_resource(self, uri):
        class C:
            text = '{"models": ["flux2_max"]}'
        return type("Read", (), {"contents": [C()]})()


def _resource_session():
    recorder = Recorder()
    session = McpSession()

    async def opener(stack):
        await stack.enter_async_context(tracked(recorder))
        return FakeResourceSession(recorder)

    session.connect_with(opener)
    return session, recorder


def test_resources_reach_the_model_as_tools():
    """Without this the agent cannot read bfl://models, and any claim about
    capability discovery is unsupported."""
    from first_try.mcp_client import ResourceTools, SessionWithResources

    session, _ = _resource_session()
    try:
        taken = {t["name"] for t in session.list_tools()}
        backend = SessionWithResources(session, ResourceTools(session, taken))
        names = [t["name"] for t in backend.list_tools()]
        assert "list_resources" in names and "read_resource" in names
        assert backend.call_tool("list_resources", {})["resources"] == ["bfl://models"]
        got = backend.call_tool("read_resource", {"uri": "bfl://models"})
        assert "flux2_max" in got["contents"][0]
    finally:
        session.close()


def test_synthetic_tools_never_shadow_a_real_one():
    from first_try.mcp_client import ResourceTools

    rt = ResourceTools(session=None, taken={"list_resources"})
    assert rt.list_name == "mcp_list_resources"
    assert rt.read_name == "read_resource"


def test_resource_reads_are_free():
    from first_try.pricing import estimate_call

    for tool in ("list_resources", "read_resource", "mcp_read_resource", "get_skill", "list_skills"):
        assert estimate_call(tool, {}).usd == 0
