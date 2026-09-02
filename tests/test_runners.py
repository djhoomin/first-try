"""The second runner, which is what stops the findings being about one vendor."""

import pytest

from first_try.runners import OpenAICompatRunner, normalise_base_url


def test_endpoint_paths_are_trimmed_off_the_base_url():
    """The SDK appends /chat/completions itself; pasting the full endpoint 404s."""
    for given in ("https://openrouter.ai/api/v1/chat/completions",
                  "https://openrouter.ai/api/v1/",
                  "https://openrouter.ai/api/v1"):
        assert normalise_base_url(given) == "https://openrouter.ai/api/v1"


def test_a_missing_key_names_the_variable_it_wanted(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY is not set"):
        OpenAICompatRunner(model="m", base_url="https://x.test/v1",
                           api_key_env="OPENROUTER_API_KEY")


def test_usage_is_reported_in_the_same_shape_as_the_claude_runner(monkeypatch):
    """runner_loop reads one usage dict; two runners must agree on its keys."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    runner = OpenAICompatRunner(model="m", base_url="https://x.test/v1",
                                api_key_env="OPENROUTER_API_KEY")

    class Details:
        cached_tokens = 400

    class Usage:
        prompt_tokens = 1000
        completion_tokens = 250
        prompt_tokens_details = Details()

    class Response:
        usage = Usage()

    runner._record(Response())
    assert runner.usage["input_tokens"] == 600        # fresh, cached subtracted
    assert runner.usage["cache_read_input_tokens"] == 400
    assert runner.usage["output_tokens"] == 250

    from first_try.runners import ClaudeRunner
    claude = ClaudeRunner.__new__(ClaudeRunner)
    claude.usage = {"input_tokens": 0, "output_tokens": 0,
                    "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    assert set(runner.usage) == set(claude.usage)


def test_a_response_without_usage_does_not_crash(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    runner = OpenAICompatRunner(model="m", base_url="https://x.test/v1",
                                api_key_env="OPENROUTER_API_KEY")
    runner._record(object())
    assert runner.usage["input_tokens"] == 0
