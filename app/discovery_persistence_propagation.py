from __future__ import annotations

from typing import Any

from app.discovery_intelligence import portfolio_score, propagate_surface_corroboration


def recompute_persisted_discovery_signals(opportunities: list[Any]) -> list[Any]:
    """Recompute propagation over a merged discovery map without compounding bonuses.

    Persisted opportunities may already contain exact-surface and graph propagation bonuses
    from a prior discovery round. Before recomputing the merged map, subtract those recorded
    bonuses from audience/confidence, then run propagation once over the whole accumulated
    set. Context fit and actionability are never changed by this reset. The merged map is
    finally re-ranked by intelligence score so accumulated evidence changes prioritisation.
    """

    reset = [_reset_propagated_scores(item) for item in opportunities]
    propagated = propagate_surface_corroboration(reset)
    return sorted(
        propagated,
        key=lambda item: (-portfolio_score(item), str(getattr(item, "canonical_key", ""))),
    )


def _reset_propagated_scores(item: Any) -> Any:
    metadata = _metadata(item)
    scores = dict(metadata.get("intelligence_scores") or {})
    if not scores:
        return item

    audience_bonus = 0.0
    confidence_bonus = 0.0
    exact = metadata.get("signal_propagation")
    if isinstance(exact, dict):
        audience_bonus += _as_float(exact.get("audience_fit_bonus"))
        confidence_bonus += _as_float(exact.get("confidence_bonus"))
    graph = metadata.get("graph_signal_propagation")
    if isinstance(graph, dict):
        audience_bonus += _as_float(graph.get("audience_fit_bonus"))
        confidence_bonus += _as_float(graph.get("confidence_bonus"))

    scores["audience_fit"] = round(
        max(0.0, _as_float(scores.get("audience_fit")) - audience_bonus),
        1,
    )
    scores["confidence"] = round(
        max(0.0, _as_float(scores.get("confidence")) - confidence_bonus),
        1,
    )
    metadata["intelligence_scores"] = scores
    metadata.pop("signal_propagation", None)
    metadata.pop("graph_signal_propagation", None)
    return _copy_with_metadata(item, metadata)


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


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
