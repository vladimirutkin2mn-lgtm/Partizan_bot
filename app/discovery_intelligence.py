from __future__ import annotations

from collections import defaultdict
from typing import Any

INTELLIGENCE_SCORE_VERSION = "v1"
EXPLORATION_BUDGET_SHARE = 0.15
EXPLORATION_MIN_AUDIENCE_FIT = 45.0
EXPLORATION_MIN_CONFIDENCE = 45.0
EXPLORATION_CONTEXT_GAP = 20.0


def annotate_discovery_intelligence(opportunities: list[Any]) -> list[Any]:
    """Attach explicit audience/context/actionability/confidence scores.

    The scores are descriptive discovery evidence only. They never imply write permission,
    publication permission, or a successful publisher preflight.
    """

    annotated = [_annotate_base(item) for item in opportunities]
    return propagate_surface_corroboration(annotated)


def annotate_base_intelligence(opportunities: list[Any]) -> list[Any]:
    """Attach per-observation scores without cross-opportunity propagation."""

    return [_annotate_base(item) for item in opportunities]


def propagate_surface_corroboration(opportunities: list[Any]) -> list[Any]:
    """Propagate only corroboration for the exact same platform/canonical surface.

    Audience/confidence evidence can become stronger when the same surface is independently
    rediscovered for several ICPs. Actionability is intentionally never propagated.
    """

    grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for item in opportunities:
        grouped[_surface_key(item)].append(item)

    result: list[Any] = []
    for item in opportunities:
        group = grouped[_surface_key(item)]
        metadata = _metadata(item)
        scores = dict(metadata.get("intelligence_scores") or {})
        if not scores:
            result.append(item)
            continue

        distinct_icps = {str(getattr(peer, "icp_id", "")) for peer in group}
        observation_count = len(group)
        scopes = {
            str(_metadata(peer).get("opportunity_scope") or "UNKNOWN").upper()
            for peer in group
        }
        cross_icp_bonus = min(10.0, max(0, len(distinct_icps) - 1) * 4.0)
        repeated_surface_bonus = min(6.0, max(0, observation_count - 1) * 2.0)
        confidence_bonus = min(14.0, cross_icp_bonus + repeated_surface_bonus)
        audience_bonus = min(8.0, cross_icp_bonus * 0.5 + repeated_surface_bonus * 0.5)

        scores["audience_fit"] = _clamp(scores.get("audience_fit", 0.0) + audience_bonus)
        scores["confidence"] = _clamp(scores.get("confidence", 0.0) + confidence_bonus)
        scores["ranking_score"] = _ranking_score(scores)
        scores["exploration_candidate"] = _is_exploration_candidate(scores)
        metadata["intelligence_scores"] = scores
        metadata["signal_propagation"] = {
            "scope": "EXACT_PLATFORM_CANONICAL_KEY",
            "surface_observations": observation_count,
            "distinct_icp_count": len(distinct_icps),
            "observed_scopes": sorted(scopes),
            "audience_fit_bonus": round(audience_bonus, 1),
            "confidence_bonus": round(confidence_bonus, 1),
            "actionability_propagated": False,
        }
        result.append(_copy_with_metadata(item, metadata))
    return result


def portfolio_score(item: Any) -> float:
    scores = _metadata(item).get("intelligence_scores")
    if isinstance(scores, dict):
        value = _as_float(scores.get("ranking_score"))
        if value > 0:
            return value
    return _as_float(getattr(item, "relevance_score", 0.0))


def is_exploration_candidate(item: Any) -> bool:
    scores = _metadata(item).get("intelligence_scores")
    return bool(isinstance(scores, dict) and scores.get("exploration_candidate") is True)


def exploration_slot_count(limit: int) -> int:
    if limit < 6:
        return 0
    return max(1, int(round(limit * EXPLORATION_BUDGET_SHARE)))


def _annotate_base(item: Any) -> Any:
    metadata = _metadata(item)
    signals = metadata.get("research_signals")
    signals = signals if isinstance(signals, dict) else {}

    fit = _ratio(signals.get("fit_ratio"))
    pain = _ratio(signals.get("pain_ratio"))
    trigger = _ratio(signals.get("trigger_ratio"))
    alternative = _ratio(signals.get("alternative_ratio"))
    demand_hits = max(0, int(_as_float(signals.get("demand_intent_hits"))))
    commercial_hits = max(0, int(_as_float(signals.get("commercial_intent_hits"))))
    independent_evidence = max(0, int(_as_float(signals.get("independent_evidence_count"))))
    matched_terms = [str(value) for value in (signals.get("matched_terms") or []) if value]

    audience_fit = _clamp(
        fit * 55.0
        + pain * 20.0
        + trigger * 15.0
        + min(10.0, demand_hits * 3.0 + commercial_hits * 2.0)
    )

    # Context fit intentionally measures topical/problem overlap rather than permission or
    # commercial value. A low score is therefore not a hard rejection if audience evidence
    # is strong enough for an exploration slot.
    native_overlap = len(metadata.get("community_match_terms") or [])
    context_fit = _clamp(
        fit * 40.0
        + pain * 20.0
        + trigger * 15.0
        + alternative * 15.0
        + min(10.0, native_overlap * 2.5)
    )

    confidence = _confidence_score(
        str(signals.get("confidence") or "LOW"),
        independent_evidence,
    )
    actionability = _actionability_score(metadata)

    scores = {
        "version": INTELLIGENCE_SCORE_VERSION,
        "audience_fit": round(audience_fit, 1),
        "context_fit": round(context_fit, 1),
        "actionability": round(actionability, 1),
        "confidence": round(confidence, 1),
        "ranking_score": 0.0,
        "exploration_candidate": False,
        "matched_term_count": len(matched_terms),
        "execution_permission_inferred": False,
    }
    scores["ranking_score"] = _ranking_score(scores)
    scores["exploration_candidate"] = _is_exploration_candidate(scores)
    metadata["intelligence_scores"] = scores
    return _copy_with_metadata(item, metadata)


def _actionability_score(metadata: dict) -> float:
    state = str(metadata.get("actionability") or "").upper()
    if state == "NEEDS_PREFLIGHT":
        return 70.0
    if state == "RESEARCH_ONLY":
        reason = str(metadata.get("community_only_reason") or "").upper()
        if reason == "NO_INTERACTION_SURFACE":
            return 10.0
        if reason == "NO_FRESH_ACTION_TARGET":
            return 20.0
        return 15.0
    if metadata.get("action_target_specific") is True:
        return 65.0
    return 35.0


def _confidence_score(label: str, independent_evidence: int) -> float:
    base = {"LOW": 30.0, "MEDIUM": 60.0, "HIGH": 85.0}.get(label.upper(), 25.0)
    return _clamp(base + min(15.0, max(0, independent_evidence - 1) * 5.0))


def _ranking_score(scores: dict) -> float:
    # Audience fit dominates. Context fit is supporting evidence, not a gate. Actionability
    # influences prioritisation but cannot confer execution permission.
    return round(
        _clamp(
            _as_float(scores.get("audience_fit")) * 0.50
            + _as_float(scores.get("context_fit")) * 0.10
            + _as_float(scores.get("actionability")) * 0.20
            + _as_float(scores.get("confidence")) * 0.20
        ),
        1,
    )


def _is_exploration_candidate(scores: dict) -> bool:
    audience_fit = _as_float(scores.get("audience_fit"))
    context_fit = _as_float(scores.get("context_fit"))
    confidence = _as_float(scores.get("confidence"))
    return (
        audience_fit >= EXPLORATION_MIN_AUDIENCE_FIT
        and confidence >= EXPLORATION_MIN_CONFIDENCE
        and audience_fit - context_fit >= EXPLORATION_CONTEXT_GAP
    )


def _surface_key(item: Any) -> tuple[str, str]:
    platform = str(getattr(getattr(item, "platform", None), "value", getattr(item, "platform", "")))
    canonical_key = str(getattr(item, "canonical_key", "") or "")
    return platform.upper(), canonical_key.casefold()


def _metadata(item: Any) -> dict:
    value = getattr(item, "metadata", {})
    return dict(value) if isinstance(value, dict) else {}


def _copy_with_metadata(item: Any, metadata: dict) -> Any:
    if hasattr(item, "model_copy"):
        return item.model_copy(update={"metadata": metadata})
    try:
        item.metadata = metadata
    except Exception:
        pass
    return item


def _ratio(value: Any) -> float:
    return min(1.0, max(0.0, _as_float(value)))


def _clamp(value: Any) -> float:
    return min(100.0, max(0.0, _as_float(value)))


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
