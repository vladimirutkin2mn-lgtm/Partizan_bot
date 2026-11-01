"""Product Intake probe must distinguish real structured output from fallback."""
import pytest

from app.llm import LLMProvider
from app.product_agent import ProductAnalysis
from tools.product_intake_live_probe import FEMDOM_BRIEF, probe


class CapturingProvider(LLMProvider):
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def parse(self, messages, response_model):
        self.calls += 1
        assert response_model is ProductAnalysis
        assert "Original founder brief:" in messages[1].content
        assert "femdom" in messages[1].content.lower()
        if self.fail:
            raise ConnectionError("Simulated OpenAI outage")
        return ProductAnalysis(
            name="FemDom",
            description="A Telegram channel about FemDom",
            product_type="Telegram channel",
            value_proposition=None,
            known_audience=[],
            missing_information=["Actual member content is not visible"],
        )


@pytest.mark.asyncio
async def test_probe_dry_run_makes_no_provider_call():
    provider = CapturingProvider()
    report = await probe(provider=provider, live=False)
    assert report["status"] == "DRY_RUN_NO_LLM"
    assert report["llm_calls_attempted"] == 0
    assert provider.calls == 0
    assert report["runtime_writes"] == report["telegram_calls"] == 0
    assert "raw_llm_analysis" not in report
    assert report["request_messages"][1]["role"] == "user"


@pytest.mark.asyncio
async def test_live_probe_returns_true_raw_llm_without_fabricating_product_facts():
    provider = CapturingProvider()
    report = await probe(provider=provider, live=True)
    assert report["status"] == "LLM_PARSED"
    assert report["llm_success"] is True
    assert report["llm_calls_attempted"] == provider.calls == 1
    raw = report["raw_llm_analysis"]
    assert raw["name"] == "FemDom"
    assert raw["value_proposition"] is None
    assert raw["known_audience"] == []
    assert raw["missing_information"] == [
        "Actual member content is not visible",
    ]
    assert raw != report["fallback_comparison"]
    assert report["normalized_analysis"]["name"] == "FemDom"
    assert report["runtime_writes"] == 0
    assert report["telegram_calls"] == 0


@pytest.mark.asyncio
async def test_live_probe_fails_closed_instead_of_silently_returning_fallback():
    provider = CapturingProvider(fail=True)
    report = await probe(provider=provider, live=True)
    assert report["status"] == "LLM_FAILED_NO_FALLBACK"
    assert report["error_type"] == "ConnectionError"
    assert report["llm_calls_attempted"] == provider.calls == 1
    assert report["llm_success"] is False
    assert "raw_llm_analysis" not in report
    assert report["runtime_writes"] == 0


@pytest.mark.asyncio
async def test_unconfigured_provider_is_not_misreported_as_llm_success():
    report = await probe(provider=None, live=True, brief=FEMDOM_BRIEF)
    assert report["status"] == "LLM_NOT_CONFIGURED"
    assert report["llm_calls_attempted"] == 0
    assert report["runtime_writes"] == 0
