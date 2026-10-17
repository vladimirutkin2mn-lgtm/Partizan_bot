from __future__ import annotations

import socket

from app.discovery_relevance_learning import discovery_relevance_learning_service
from app.discovery_trace_replay import replay_trace


def _trace_payload() -> dict:
    return {
        "product": {
            "id": "52d7e59e-5ac5-4363-b829-c93e33e48272",
            "name": "FemDom dating",
            "problem_or_desire": "find femdom relationships",
            "known_audience": ["people interested in femdom"],
        },
        "current_negative_memory": {
            "blocked_keys": [],
            "negative_terms": [],
        },
        "search": {
            "raw_hits": [
                {
                    "query": "femdom telegram community",
                    "rank": 1,
                    "url": "https://t.me/+InviteToken",
                    "normalized": False,
                    "normalization_reason": "TELEGRAM_INVITE_UNSUPPORTED",
                    "host": "t.me",
                }
            ]
        },
        "opportunities": [
            {
                "verdict": "REJECT",
                "canonical_key": "telegram:123:community",
                "title": "FemDom dating community",
                "metadata": {"community_fit_pass": True},
                "research_signals": {
                    "confidence": "MEDIUM",
                    "independent_evidence_count": 2,
                    "matched_terms": ["femdom", "dating"],
                    "fit_ratio": 0.5,
                    "pain_ratio": 0.5,
                    "trigger_ratio": 0.5,
                    "alternative_ratio": 0.0,
                    "commercial_intent_hits": 0,
                    "demand_intent_hits": 1,
                },
            },
            {
                "verdict": "REJECT",
                "canonical_key": "telegram:456:community",
                "title": "Another relevant community",
                "metadata": {"community_fit_pass": True},
                "research_signals": {},
            },
        ],
    }


class _BombStore:
    ephemeral = False

    def __getattr__(self, name):
        raise AssertionError(f"REPLAY touched runtime store via {name}")


def test_replay_is_offline_read_only_and_reuses_saved_decision_inputs(monkeypatch) -> None:
    monkeypatch.setattr(discovery_relevance_learning_service, "_store", _BombStore())

    def _network_forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError("REPLAY attempted network access")

    monkeypatch.setattr(socket, "socket", _network_forbidden)

    result = replay_trace(_trace_payload())

    assert result["status"] == "REPLAY_COMPLETE"
    assert result["execution_context"]["mode"] == "REPLAY"
    assert result["execution_context"]["effects"]["network"] is False
    assert result["execution_context"]["effects"]["state_writes"] is False
    assert result["effects"] == {
        "new_web_search_calls": 0,
        "new_llm_calls": 0,
        "new_native_telegram_calls": 0,
        "working_state_writes": 0,
    }
    assert result["summary"]["opportunity_count"] == 2
    assert result["summary"]["verdicts"] == {"PASS": 2}
    assert result["summary"]["verdict_change_count"] == 2


def test_replay_reprocesses_old_hit_normalization_without_search() -> None:
    result = replay_trace(_trace_payload())

    assert result["summary"]["raw_hit_count"] == 1
    assert result["summary"]["normalization_change_count"] == 1
    change = result["normalization_changes"][0]
    assert change["url"] == "https://t.me/+InviteToken"
    assert change["previous"]["normalization_reason"] == "TELEGRAM_INVITE_UNSUPPORTED"
    assert change["current"]["normalization_reason"] == "TELEGRAM_INVITE_ACCEPTED"
    assert change["current"]["access_state"] == "JOIN_REQUIRED"


def test_replay_is_deterministic_for_same_saved_trace() -> None:
    payload = _trace_payload()

    first = replay_trace(payload)
    second = replay_trace(payload)

    assert first["input_fingerprint"] == second["input_fingerprint"]
    assert first["opportunities"] == second["opportunities"]
    assert first["normalization"] == second["normalization"]
