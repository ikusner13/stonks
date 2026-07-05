from types import SimpleNamespace

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from app.llm.usage import run_tracked


class _StubAgent:
    def __init__(self, failures: list[int]):
        self.failures = list(failures)
        self.calls: list[dict] = []

    async def run(self, prompt, **kwargs):
        self.calls.append(kwargs)
        if self.failures:
            status = self.failures.pop(0)
            raise ModelHTTPError(status_code=status, model_name="x", body=None)
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=1, output_tokens=2, cache_read_tokens=3),
            response=SimpleNamespace(model_name="x"),
        )


async def _no_sleep(_delay: float) -> None:
    return None


async def test_retryable_errors_retry_then_return_result(monkeypatch):
    monkeypatch.setattr("app.llm.usage.asyncio.sleep", _no_sleep)
    agent = _StubAgent([429, 429])

    result = await run_tracked("critique", agent, "prompt", {})

    assert result.response.model_name == "x"
    assert len(agent.calls) == 3


async def test_final_attempt_strips_allow_fallbacks_without_mutating_caller(monkeypatch):
    monkeypatch.setattr("app.llm.usage.asyncio.sleep", _no_sleep)
    settings = {
        "openrouter_provider": {"data_collection": "deny", "allow_fallbacks": False}
    }
    agent = _StubAgent([429, 429])

    await run_tracked("critique", agent, "prompt", settings)

    first = agent.calls[0]["model_settings"]["openrouter_provider"]
    second = agent.calls[1]["model_settings"]["openrouter_provider"]
    third = agent.calls[2]["model_settings"]["openrouter_provider"]
    assert first["allow_fallbacks"] is False
    assert second["allow_fallbacks"] is False
    assert "allow_fallbacks" not in third
    assert settings == {
        "openrouter_provider": {"data_collection": "deny", "allow_fallbacks": False}
    }


async def test_non_retryable_http_error_raises_immediately(monkeypatch):
    monkeypatch.setattr("app.llm.usage.asyncio.sleep", _no_sleep)
    agent = _StubAgent([400])

    with pytest.raises(ModelHTTPError):
        await run_tracked("critique", agent, "prompt", {})

    assert len(agent.calls) == 1


async def test_retryable_errors_re_raise_after_three_attempts(monkeypatch):
    monkeypatch.setattr("app.llm.usage.asyncio.sleep", _no_sleep)
    agent = _StubAgent([429, 429, 429])

    with pytest.raises(ModelHTTPError):
        await run_tracked("critique", agent, "prompt", {})

    assert len(agent.calls) == 3
