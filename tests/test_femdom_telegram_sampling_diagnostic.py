"""Diagnostic retries are bounded, isolated and never publish anything."""
from types import SimpleNamespace

import pytest

from app import femdom_telegram_sampling_diagnostic as diag


class APIError(Exception):
    def __init__(self, code):
        super().__init__("deliberately hidden provider response")
        self.status_code = code


def _success(override):
    return {
        "sampling_profile": {
            "model": "gpt-6-luna", "max_completion_tokens": 1000,
            **(diag._PROFILE if override is None else override),
        },
        "request_sha256": "same-prompt",
        "comment": "Костюм — уже половина настроения.",
        "input_tokens": 1500,
        "output_tokens": 64,
    }


@pytest.mark.asyncio
async def test_full_retry_success_stops_after_exactly_one_request(monkeypatch):
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs["sampling_override"])
        return _success(kwargs["sampling_override"])

    monkeypatch.setattr(diag, "run", fake_run)
    out = await diag.diagnose(store=object(), product=object(), client=object())
    assert calls == [None]
    assert out["status"] == "FULL_PROFILE_SUPPORTED_ONE_DRAFT"
    assert out["attempt_count"] == 1
    assert out["results"][0]["comment"].startswith("Костюм")
    assert out["telegram_sends"] == 0
    assert out["review_cache_writes"] == 0


@pytest.mark.asyncio
async def test_repeated_server_error_and_working_baseline_isolates_individuals(monkeypatch):
    calls = []

    async def fake_run(**kwargs):
        override = kwargs["sampling_override"]
        calls.append(override)
        if override is None:
            raise APIError(500)
        if override == {"temperature": 0.9}:
            raise APIError(400)
        return _success(override)

    monkeypatch.setattr(diag, "run", fake_run)
    out = await diag.diagnose(store=object(), product=object(), client=object())
    assert out["status"] == "INDIVIDUAL_PARAMETERS_TESTED"
    assert calls == [None, {}, {"temperature": 0.9},
                     {"presence_penalty": 0.7},
                     {"frequency_penalty": 0.4},
                     {"reasoning_effort": "none"}]
    assert out["attempt_count"] == 6
    assert out["results"][2]["http_status"] == 400
    assert out["results"][3]["status"] == "SUCCEEDED"
    assert out["maximum_attempts"] == 6
    assert out["sdk_retries"] == 0
    assert out["telegram_sends"] == 0


@pytest.mark.asyncio
async def test_baseline_fails_stops_without_blame_for_any_one_parameter(monkeypatch):
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs["sampling_override"])
        raise APIError(500)

    monkeypatch.setattr(diag, "run", fake_run)
    out = await diag.diagnose(store=object(), product=object(), client=object())
    assert out["status"] == "BASELINE_CHAT_FAILED_PARAMETERS_NOT_ISOLATED"
    assert calls == [None, {}]


@pytest.mark.asyncio
async def test_provider_rate_limiting_does_not_generate_upsell_calls(monkeypatch):
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs["sampling_override"])
        raise APIError(429)

    monkeypatch.setattr(diag, "run", fake_run)
    out = await diag.diagnose(store=object(), product=object(), client=object())
    assert out["status"] == "STOPPED_AUTH_RATE_LIMIT_OR_NETWORK_FAILURE"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_diagnostic_requires_exact_project_before_api(monkeypatch):
    async def must_not_call(**_kwargs):
        raise AssertionError("No call")

    monkeypatch.setattr(diag, "run", must_not_call)
    with pytest.raises(ValueError, match="outside pinned"):
        await diag.diagnose(
            store=object(), product=object(), client=object(),
            project_id=diag._PRODUCT,
        )
