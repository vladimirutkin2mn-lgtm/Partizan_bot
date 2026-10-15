import json
import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import BaseModel

from app.llm import LLMMessage, OpenAIResponsesProvider
from app.openai_usage import openai_usage_context
from app.search import DiscoveryQuery, OpenAIWebSearchProvider, SourceClass


class _CollectingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def usage_log():
    # The usage logger does not propagate to root, so caplog cannot see it.
    handler = _CollectingHandler()
    logger = logging.getLogger("app.openai_usage")
    logger.addHandler(handler)
    yield handler.messages
    logger.removeHandler(handler)


def _install_client(monkeypatch, responses) -> None:
    import openai

    monkeypatch.setattr(openai, "OpenAI", lambda api_key: SimpleNamespace(responses=responses))


class _WebSearchResponses:
    def create(self, **kwargs):
        del kwargs
        return SimpleNamespace(
            id="resp_search",
            usage=SimpleNamespace(
                input_tokens=1200,
                input_tokens_details=SimpleNamespace(cached_tokens=200),
                output_tokens=300,
                output_tokens_details=SimpleNamespace(reasoning_tokens=120),
            ),
            output=[
                SimpleNamespace(type="web_search_call"),
                SimpleNamespace(type="web_search_call"),
                SimpleNamespace(type="message", content=[]),
            ],
            output_text="",
        )


class _Answer(BaseModel):
    value: str


class _BrokenUsageResponse:
    id = "resp_broken"
    output_parsed = _Answer(value="ok")

    @property
    def usage(self):
        raise RuntimeError("usage unavailable")


class _BrokenUsageResponses:
    def parse(self, **kwargs):
        del kwargs
        return _BrokenUsageResponse()


@pytest.mark.asyncio
async def test_search_usage_is_logged_with_task_label(monkeypatch, usage_log) -> None:
    _install_client(monkeypatch, _WebSearchResponses())
    provider = OpenAIWebSearchProvider(api_key="test-key", model="test-search-model")
    query = DiscoveryQuery(source_class=SourceClass.COMMUNITY, query="founder communities")
    product_id = uuid4()

    with openai_usage_context("preview_scan", product_id):
        await provider.search(query)
    await provider.search(query)

    labelled, unlabelled = (json.loads(message) for message in usage_log)
    assert labelled.pop("duration_ms") >= 0
    assert labelled == {
        "event": "openai_usage",
        "task": "preview_scan",
        "model": "test-search-model",
        "product_id": str(product_id),
        "response_id": "resp_search",
        "input_tokens": 1200,
        "cached_input_tokens": 200,
        "output_tokens": 300,
        "reasoning_tokens": 120,
        "web_search_calls": 2,
    }
    assert unlabelled["task"] == "unknown"
    assert unlabelled["product_id"] is None


@pytest.mark.asyncio
async def test_usage_logging_failure_does_not_break_llm_call(monkeypatch, usage_log) -> None:
    _install_client(monkeypatch, _BrokenUsageResponses())
    provider = OpenAIResponsesProvider(api_key="test-key", model="test-llm-model")

    with openai_usage_context("icp_generation"):
        parsed = await provider.parse(
            [LLMMessage(role="user", content="hello")],
            _Answer,
        )

    assert parsed == _Answer(value="ok")
    assert len(usage_log) == 1
    assert "openai_usage logging failed" in usage_log[0]
    assert "task=icp_generation" in usage_log[0]
    assert "response_id=resp_broken" in usage_log[0]
