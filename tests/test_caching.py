"""Caching is a cost control, so it needs a test that fails when it regresses."""

from first_try.runners import ClaudeRunner


def _runner(cache=True):
    r = ClaudeRunner.__new__(ClaudeRunner)
    r.cache, r.model, r.max_tokens = cache, "m", 10
    r.usage = {}
    return r


def test_tools_and_system_are_marked_for_caching():
    r = _runner()
    tools = [{"name": "a", "description": "", "input_schema": {}},
             {"name": "b", "description": "", "input_schema": {}}]
    assert r._system()[0]["cache_control"] == {"type": "ephemeral"}
    defs = r._tools(tools)
    assert "cache_control" not in defs[0]
    assert defs[-1]["cache_control"] == {"type": "ephemeral"}


def test_caching_off_marks_nothing():
    r = _runner(cache=False)
    assert "cache_control" not in r._system()[0]
    assert "cache_control" not in r._tools([{"name": "a", "description": "", "input_schema": {}}])[0]


def test_only_the_latest_conversation_block_carries_the_marker():
    """Stale markers waste breakpoints and stop the prefix growing."""
    r = _runner()
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "one"}]},
        {"role": "user", "content": [{"type": "text", "text": "two"}]},
    ]
    r._mark_conversation(messages)
    assert "cache_control" not in messages[0]["content"][0]
    assert messages[1]["content"][-1]["cache_control"] == {"type": "ephemeral"}

    messages.append({"role": "user", "content": [{"type": "text", "text": "three"}]})
    r._mark_conversation(messages)
    assert "cache_control" not in messages[1]["content"][0]
    assert messages[2]["content"][-1]["cache_control"] == {"type": "ephemeral"}


def test_a_plain_string_message_is_left_alone():
    r = _runner()
    messages = [{"role": "user", "content": "plain"}]
    r._mark_conversation(messages)
    assert messages[0]["content"] == "plain"
