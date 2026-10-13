from types import SimpleNamespace

from app.distribution_types import DistributionPlatform
from app.manual_discovery_trace import _filters, _trace_decision


def _product():
    return SimpleNamespace(
        name="Promoted asset: FemDom",
        problem_or_desire=None,
        value_proposition=None,
        description=(
            "Telegram channel for people interested in FemDom. "
            "Business goal: get paying customers."
        ),
        known_audience=[],
    )


def _opportunity():
    return SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        canonical_key="telegram:2076691209:community",
        title="FEMDOM Знакомства🌶",
        metadata={
            "research_signals": {
                "confidence": "MEDIUM",
                "independent_evidence_count": 2,
                "matched_terms": [
                    "channel",
                    "femdom",
                    "goal",
                    "group",
                    "https",
                    "product",
                    "telegram",
                ],
                "fit_ratio": 0.583,
                "pain_ratio": 0.875,
                "trigger_ratio": 0.875,
                "alternative_ratio": 0.0,
                "commercial_intent_hits": 0,
                "demand_intent_hits": 1,
            }
        },
    )


def test_trace_uses_direct_product_anchor_path_like_production_gate() -> None:
    decision = _trace_decision(
        _opportunity(),
        {"blocked_keys": set(), "negative_terms": set()},
        _product(),
    )

    assert decision["accepted"] is True
    assert decision["reason"] == "MULTI_SIGNAL_RELEVANCE"
    assert decision["direct_product_anchor_count"] == 1


def test_trace_filter_description_documents_direct_product_anchor_exception() -> None:
    semantic_gate = _filters()["semantic_gate"]

    assert semantic_gate["minimum_matched_terms"] == 2
    assert semantic_gate["direct_product_anchor_minimum_matched_terms"] == 1
    assert "DIRECT_PRODUCT_ANCHOR" in semantic_gate["pass_paths"]
