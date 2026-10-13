from __future__ import annotations

from typing import Any

MAX_PARTICIPANT_CONFIDENCE_BONUS = 8.0
MIN_MATCHED_PARTICIPANTS = 2


def apply_participant_confidence(opportunities: list[Any]) -> list[Any]:
    """Use participant diversity as confidence evidence, never as execution permission."""

    result: list[Any] = []
    for item in opportunities:
        metadata = _metadata(item)
        evidence = metadata.get("participant_evidence")
        scores = dict(metadata.get("intelligence_scores") or {})
        if not isinstance(evidence, dict) or not scores:
            result.append(item)
            continue

        matched_participants = max(0, int(_as_float(evidence.get("matched_participant_count"))))
        matched_messages = max(0, int(_as_float(evidence.get("matched_message_count"))))
        repeat_participants = max(0, int(_as_float(evidence.get("repeat_participant_count"))))
        identity_persisted = bool(evidence.get("identity_persisted"))

        bonus = 0.0
        reason = "INSUFFICIENT_INDEPENDENT_PARTICIPANTS"
        if (
            not identity_persisted
            and matched_participants >= MIN_MATCHED_PARTICIPANTS
            and matched_messages >= MIN_MATCHED_PARTICIPANTS
        ):
            bonus = min(
                MAX_PARTICIPANT_CONFIDENCE_BONUS,
                matched_participants * 2.0 + min(2.0, repeat_participants * 0.5),
            )
            reason = "MULTI_PARTICIPANT_CORROBORATION"

        if bonus > 0.0:
            scores["confidence"] = round(
                min(100.0, _as_float(scores.get("confidence")) + bonus),
                1,
            )
            scores["ranking_score"] = _ranking_score(scores)
            metadata["intelligence_scores"] = scores

        metadata["participant_signal"] = {
            "reason": reason,
            "matched_participant_count": matched_participants,
            "matched_message_count": matched_messages,
            "repeat_participant_count": repeat_participants,
            "confidence_bonus": round(bonus, 1),
            "audience_fit_propagated": False,
            "context_fit_propagated": False,
            "actionability_propagated": False,
            "identity_persisted": False,
            "execution_permission_inferred": False,
        }
        result.append(_copy_with_metadata(item, metadata))
    return result


def _ranking_score(scores: dict) -> float:
    return round(
        min(
            100.0,
            max(
                0.0,
                _as_float(scores.get("audience_fit")) * 0.50
                + _as_float(scores.get("context_fit")) * 0.10
                + _as_float(scores.get("actionability")) * 0.20
                + _as_float(scores.get("confidence")) * 0.20,
            ),
        ),
        1,
    )


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
