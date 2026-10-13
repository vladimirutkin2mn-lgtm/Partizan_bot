from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

INTELLIGENCE_SCORE_VERSION = "v2"
EXPLORATION_BUDGET_SHARE = 0.15
EXPLORATION_MIN_AUDIENCE_FIT = 45.0
EXPLORATION_MIN_CONFIDENCE = 45.0
EXPLORATION_CONTEXT_GAP = 20.0
POST_SIGNAL_MAX_AGE_DAYS = 14.0
COMMUNITY_SIGNAL_MAX_AGE_DAYS = 30.0
MIN_STRONG_POSTS_FOR_COMMUNITY_BOOST = 2
MAX_POST_TO_COMMUNITY_AUDIENCE_BONUS = 10.0
MAX_POST_TO_COMMUNITY_CONFIDENCE_BONUS = 12.0
MAX_COMMUNITY_TO_POST_AUDIENCE_BONUS = 4.0
MAX_COMMUNITY_TO_POST_CONFIDENCE_BONUS = 4.0


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
    """Propagate exact-surface corroboration, then bounded parent/child graph signals.

    Exact canonical-surface corroboration may strengthen audience/confidence when supported
    by multiple independent evidence URLs. Telegram community/post propagation is then
    applied only inside the same parent surface and ICP. Actionability is never propagated.
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
        evidence_urls = {
            str(row.get("url") or "").strip()
            for peer in group
            for row in (getattr(peer, "evidence", []) or [])
            if isinstance(row, dict) and str(row.get("url") or "").strip()
        }
        independent_evidence_urls = len(evidence_urls)
        if independent_evidence_urls >= 2:
            cross_icp_bonus = min(8.0, max(0, len(distinct_icps) - 1) * 4.0)
            evidence_bonus = min(6.0, max(0, independent_evidence_urls - 1) * 2.0)
        else:
            cross_icp_bonus = 0.0
            evidence_bonus = 0.0
        confidence_bonus = min(14.0, cross_icp_bonus + evidence_bonus)
        audience_bonus = min(8.0, cross_icp_bonus * 0.5 + evidence_bonus * 0.5)

        scores["audience_fit"] = _clamp(scores.get("audience_fit", 0.0) + audience_bonus)
        scores["confidence"] = _clamp(scores.get("confidence", 0.0) + confidence_bonus)
        scores["ranking_score"] = _ranking_score(scores)
        scores["exploration_candidate"] = _is_exploration_candidate(scores)
        metadata["intelligence_scores"] = scores
        metadata["signal_propagation"] = {
            "scope": "EXACT_PLATFORM_CANONICAL_KEY",
            "surface_observations": observation_count,
            "distinct_icp_count": len(distinct_icps),
            "independent_evidence_urls": independent_evidence_urls,
            "observed_scopes": sorted(scopes),
            "audience_fit_bonus": round(audience_bonus, 1),
            "confidence_bonus": round(confidence_bonus, 1),
            "actionability_propagated": False,
        }
        result.append(_copy_with_metadata(item, metadata))
    return _propagate_parent_child_signals(result)


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


def _propagate_parent_child_signals(opportunities: list[Any]) -> list[Any]:
    """Share bounded research priors between a Telegram community and its posts.

    Propagation is scoped to the same parent Telegram entity and the same ICP. Two recent,
    independently discovered strong post surfaces may increase the parent community's
    audience/confidence prior. A strong recently checked community may provide a small prior
    to child posts. Context fit and actionability are never propagated.
    """

    grouped: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for item in opportunities:
        metadata = _metadata(item)
        parent_key = str(metadata.get("surface_parent_key") or "").strip().casefold()
        if not parent_key:
            continue
        platform = str(
            getattr(getattr(item, "platform", None), "value", getattr(item, "platform", ""))
        ).upper()
        if platform != "TELEGRAM":
            continue
        grouped[(platform, parent_key, str(getattr(item, "icp_id", "")))].append(item)

    frozen_scores = {
        id(item): dict(_metadata(item).get("intelligence_scores") or {})
        for item in opportunities
    }
    now = datetime.now(UTC)
    result: list[Any] = []

    for item in opportunities:
        metadata = _metadata(item)
        parent_key = str(metadata.get("surface_parent_key") or "").strip().casefold()
        platform = str(
            getattr(getattr(item, "platform", None), "value", getattr(item, "platform", ""))
        ).upper()
        if not parent_key or platform != "TELEGRAM":
            result.append(item)
            continue

        group = grouped[(platform, parent_key, str(getattr(item, "icp_id", "")))]
        scope = str(metadata.get("opportunity_scope") or "").upper()
        scores = dict(frozen_scores.get(id(item)) or {})
        if not scores or scope not in {"COMMUNITY", "POST"}:
            result.append(item)
            continue

        communities = [
            peer
            for peer in group
            if str(_metadata(peer).get("opportunity_scope") or "").upper() == "COMMUNITY"
        ]
        posts = [
            peer
            for peer in group
            if str(_metadata(peer).get("opportunity_scope") or "").upper() == "POST"
        ]

        audience_bonus = 0.0
        confidence_bonus = 0.0
        source_count = 0
        decay_weight = 0.0
        direction = "NONE"

        if scope == "COMMUNITY":
            strong_posts: list[tuple[Any, float]] = []
            seen_post_keys: set[str] = set()
            for post in posts:
                key = str(getattr(post, "canonical_key", "") or "").casefold()
                if not key or key in seen_post_keys:
                    continue
                post_scores = frozen_scores.get(id(post)) or {}
                post_weight = _post_recency_weight(_metadata(post), now)
                if (
                    _as_float(post_scores.get("audience_fit")) < 50.0
                    or _as_float(post_scores.get("confidence")) < 50.0
                    or post_weight <= 0.0
                ):
                    continue
                seen_post_keys.add(key)
                strong_posts.append((post, post_weight))

            if len(strong_posts) >= MIN_STRONG_POSTS_FOR_COMMUNITY_BOOST:
                direction = "POSTS_TO_COMMUNITY"
                source_count = len(strong_posts)
                decay_weight = round(
                    sum(weight for _, weight in strong_posts) / len(strong_posts), 3
                )
                for post, weight in strong_posts[:4]:
                    post_scores = frozen_scores.get(id(post)) or {}
                    audience_bonus += max(
                        0.0,
                        (_as_float(post_scores.get("audience_fit")) - 45.0) * 0.06 * weight,
                    )
                    confidence_bonus += max(
                        0.0,
                        (_as_float(post_scores.get("confidence")) - 45.0) * 0.08 * weight,
                    )
                audience_bonus = min(
                    MAX_POST_TO_COMMUNITY_AUDIENCE_BONUS,
                    audience_bonus,
                )
                confidence_bonus = min(
                    MAX_POST_TO_COMMUNITY_CONFIDENCE_BONUS,
                    confidence_bonus,
                )

        elif scope == "POST" and communities:
            strongest = max(
                communities,
                key=lambda peer: _as_float(
                    (frozen_scores.get(id(peer)) or {}).get("ranking_score")
                ),
            )
            community_scores = frozen_scores.get(id(strongest)) or {}
            community_weight = _community_recency_weight(_metadata(strongest), now)
            if (
                _as_float(community_scores.get("audience_fit")) >= 55.0
                and _as_float(community_scores.get("confidence")) >= 50.0
                and community_weight > 0.0
            ):
                direction = "COMMUNITY_TO_POST"
                source_count = 1
                decay_weight = round(community_weight, 3)
                audience_bonus = min(
                    MAX_COMMUNITY_TO_POST_AUDIENCE_BONUS,
                    max(
                        0.0,
                        (_as_float(community_scores.get("audience_fit")) - 55.0)
                        * 0.08
                        * community_weight,
                    ),
                )
                confidence_bonus = min(
                    MAX_COMMUNITY_TO_POST_CONFIDENCE_BONUS,
                    max(
                        0.0,
                        (_as_float(community_scores.get("confidence")) - 50.0)
                        * 0.08
                        * community_weight,
                    ),
                )

        if audience_bonus > 0.0 or confidence_bonus > 0.0:
            scores["audience_fit"] = _clamp(
                _as_float(scores.get("audience_fit")) + audience_bonus
            )
            scores["confidence"] = _clamp(
                _as_float(scores.get("confidence")) + confidence_bonus
            )
            scores["ranking_score"] = _ranking_score(scores)
            scores["exploration_candidate"] = _is_exploration_candidate(scores)
            metadata["intelligence_scores"] = scores

        metadata["graph_signal_propagation"] = {
            "scope": "SAME_TELEGRAM_PARENT_AND_ICP",
            "surface_parent_key": parent_key,
            "direction": direction,
            "source_surface_count": source_count,
            "decay_weight": decay_weight,
            "audience_fit_bonus": round(audience_bonus, 1),
            "confidence_bonus": round(confidence_bonus, 1),
            "context_fit_propagated": False,
            "actionability_propagated": False,
            "execution_permission_inferred": False,
        }
        result.append(_copy_with_metadata(item, metadata))

    return result


def _post_recency_weight(metadata: dict, now: datetime) -> float:
    target_url = str(metadata.get("action_target_url") or "").strip()
    if not target_url:
        return 0.0
    published_at = None
    for row in metadata.get("recent_context") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("url") or "").strip() != target_url:
            continue
        published_at = _parse_datetime(row.get("published_at"))
        if published_at is not None:
            break
    return _linear_recency_weight(published_at, now, POST_SIGNAL_MAX_AGE_DAYS)


def _community_recency_weight(metadata: dict, now: datetime) -> float:
    checked_at = _parse_datetime(metadata.get("source_checked_at"))
    return _linear_recency_weight(checked_at, now, COMMUNITY_SIGNAL_MAX_AGE_DAYS)


def _linear_recency_weight(
    observed_at: datetime | None,
    now: datetime,
    max_age_days: float,
) -> float:
    if observed_at is None:
        return 0.0
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    age_seconds = (now - observed_at.astimezone(UTC)).total_seconds()
    if age_seconds < 0:
        return 0.0
    age_days = age_seconds / 86400.0
    if age_days >= max_age_days:
        return 0.0
    return max(0.0, 1.0 - age_days / max_age_days)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


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
    platform = str(
        getattr(getattr(item, "platform", None), "value", getattr(item, "platform", ""))
    )
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
