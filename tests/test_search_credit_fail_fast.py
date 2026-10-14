import asyncio
from types import SimpleNamespace

import pytest

from app.search import DiscoveryQuery, OpenAIWebSearchProvider, SourceClass


class _CreditExhaustedResponses:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs):
        del kwargs
        self.calls += 1
        raise RuntimeError(
            "Error code: 429 - {'error': {'type': 'insufficient_quota', "
            "'code': 'credit_balance_exhausted', "
            "'message': 'You have no credits remaining.'}}"
        )


class _TransientThenHealthyResponses:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs):
        del kwargs
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("temporary timeout")
        return SimpleNamespace(output=[], output_text="")


def _query() -> DiscoveryQuery:
    return DiscoveryQuery(
        source_class=SourceClass.COMMUNITY,
        query="site:t.me FemDom discussion questions",
    )


@pytest.mark.asyncio
async def test_credit_exhaustion_is_cached_after_single_provider_probe(monkeypatch) -> None:
    responses = _CreditExhaustedResponses()
    client = SimpleNamespace(responses=responses)

    import openai

    monkeypatch.setattr(openai, "OpenAI", lambda api_key: client)
    provider = OpenAIWebSearchProvider(api_key="test-key", model="test-model")

    results = await asyncio.gather(
        *(provider.search(_query()) for _ in range(8)),
        return_exceptions=True,
    )

    assert responses.calls == 1
    assert all(isinstance(result, RuntimeError) for result in results)
    assert all("credit_balance_exhausted" in str(result) for result in results)


@pytest.mark.asyncio
async def test_transient_first_failure_is_not_cached(monkeypatch) -> None:
    responses = _TransientThenHealthyResponses()
    client = SimpleNamespace(responses=responses)

    import openai

    monkeypatch.setattr(openai, "OpenAI", lambda api_key: client)
    provider = OpenAIWebSearchProvider(api_key="test-key", model="test-model")

    with pytest.raises(TimeoutError, match="temporary timeout"):
        await provider.search(_query())

    assert await provider.search(_query()) == []
    assert responses.calls == 2
