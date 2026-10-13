from __future__ import annotations

import re
from collections import Counter
from typing import Any

MAX_ADJACENT_HINTS = 4
MIN_ADJACENT_AUDIENCE_FIT = 55.0
MIN_ADJACENT_CONFIDENCE = 55.0
MIN_CONTEXT_TERMS_PER_HINT = 2
MAX_CONTEXT_TERMS_PER_HINT = 4

_UTILITY_TERMS = {
    "about",
    "active",
    "advice",
    "around",
    "channel",
    "chat",
    "comments",
    "community",
    "discussion",
    "group",
    "help",
    "https",
    "http",
    "link",
    "links",
    "official",
    "online",
    "people",
    "public",
    "questions",
    "replies",
    "site",
    "telegram",
    "thread",
    "today",
    "with",
    "канал",
    "комментарии",
    "люди",
    "обсуждение",
    "официальный",
    "официальная",
    "ссылка",
    "сообщество",
    "советы",
    "чат",
}


def build_adjacent_discovery_hints(
    *,
    product: Any,
    icps: Any,
    opportunities: list[Any],
    max_hints: int = MAX_ADJACENT_HINTS,
) -> list[str]:
    """Build bounded adjacent-context hypotheses from observed public evidence.

    Search-query text is deliberately excluded: it records what Partizan asked for, not what
    a public source actually contained. Participant aggregates may strengthen confidence in a
    surface but never contribute identity or vocabulary to a hint.
    """

    limit = max(0, min(int(max_hints), MAX_ADJACENT_HINTS))
    if limit == 0:
        return []

    anchor_terms = _anchor_terms(product, icps)
    candidates: list[tuple[float, str, str]] = []
    context_frequency: Counter[str] = Counter()

    strong_surfaces: list[tuple[Any, float]] = []
    for opportunity in opportunities:
        if _platform(opportunity) != "TELEGRAM":
            continue
        metadata = _metadata(opportunity)
        scores = metadata.get("intelligence_scores")
        if not isinstance(scores, dict):
            continue
        audience_fit = _as_float(scores.get("audience_fit"))
        confidence = _as_float(scores.get("confidence"))
        if audience_fit < MIN_ADJACENT_AUDIENCE_FIT:
            continue
        if confidence < MIN_ADJACENT_CONFIDENCE:
            continue
        relevance_guard = metadata.get("relevance_guard")
        if isinstance(relevance_guard, dict) and str(
            relevance_guard.get("status") or ""
        ).upper() not in {"", "PASS"}:
            continue

        participant = metadata.get("participant_evidence")
        matched_participants = 0
        if isinstance(participant, dict) and participant.get("identity_persisted") is False:
            matched_participants = max(
                0,
                int(_as_float(participant.get("matched_participant_count"))),
            )
        strength = audience_fit * 0.55 + confidence * 0.35 + min(
            10.0,
            matched_participants * 2.0,
        )
        strong_surfaces.append((opportunity, strength))

    # Repeated adjacent vocabulary across independently strong surfaces is useful evidence.
    for opportunity, _ in strong_surfaces:
        context_frequency.update(
            set(_ordered_context_terms(opportunity, anchor_terms=anchor_terms))
        )

    for opportunity, strength in strong_surfaces:
        terms = _ordered_context_terms(opportunity, anchor_terms=anchor_terms)
        if not terms:
            continue
        ranked_terms = sorted(
            enumerate(terms),
            key=lambda row: (
                -context_frequency[row[1]],
                row[0],
                row[1],
            ),
        )
        selected = [
            term
            for _, term in ranked_terms[:MAX_CONTEXT_TERMS_PER_HINT]
        ]
        # Restore source order so the hint reads like an observed context phrase instead of
        # an arbitrary bag of keywords.
        selected_set = set(selected)
        phrase_terms = [term for term in terms if term in selected_set][
            :MAX_CONTEXT_TERMS_PER_HINT
        ]
        if len(phrase_terms) < MIN_CONTEXT_TERMS_PER_HINT:
            continue
        hint = " ".join(phrase_terms)
        repetition_bonus = sum(
            max(0, context_frequency[term] - 1) * 2.0 for term in phrase_terms
        )
        candidates.append(
            (
                strength + min(12.0, repetition_bonus),
                hint,
                str(getattr(opportunity, "canonical_key", "") or ""),
            )
        )

    candidates.sort(key=lambda row: (-row[0], row[1].casefold(), row[2]))
    result: list[str] = []
    seen: set[str] = set()
    for _, hint, _ in candidates:
        key = " ".join(hint.casefold().split())
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(hint)
        if len(result) >= limit:
            break
    return result


def interleave_adjacent_hints(
    base_hints: list[str],
    adjacent_hints: list[str],
) -> list[str]:
    """Reserve roughly one of four adaptive hypotheses for adjacent exploration.

    The caller still applies its existing dedupe, already-used-hypothesis tracking, global
    four-hint round cap and negative-learning filter in the Telegram adapter.
    """

    base = _dedupe(base_hints)
    adjacent = [
        hint
        for hint in _dedupe(adjacent_hints)
        if _normalized(hint) not in {_normalized(item) for item in base}
    ]
    if not adjacent:
        return base
    if not base:
        return adjacent

    result: list[str] = [base[0], adjacent[0]]
    base_index = 1
    adjacent_index = 1
    while base_index < len(base) or adjacent_index < len(adjacent):
        # Keep the first 4 positions at one adjacent hypothesis when possible. Additional
        # adjacent hints sit deeper in the list and can surface in a later round after the
        # caller removes already-used hypotheses.
        if base_index < len(base):
            result.append(base[base_index])
            base_index += 1
        if base_index < len(base):
            result.append(base[base_index])
            base_index += 1
        if adjacent_index < len(adjacent):
            result.append(adjacent[adjacent_index])
            adjacent_index += 1
    return _dedupe(result)


def _ordered_context_terms(opportunity: Any, *, anchor_terms: set[str]) -> list[str]:
    metadata = _metadata(opportunity)
    observed_parts = [
        str(getattr(opportunity, "title", "") or ""),
        str(metadata.get("about") or ""),
    ]
    for evidence in list(getattr(opportunity, "evidence", []) or [])[:6]:
        if not isinstance(evidence, dict):
            continue
        # Do not use evidence["query"] here. Search terms are provenance, not observation.
        observed_parts.extend(
            [
                str(evidence.get("title") or ""),
                str(evidence.get("snippet") or ""),
            ]
        )

    result: list[str] = []
    seen: set[str] = set()
    for part in observed_parts:
        for term in _ordered_tokens(part):
            if term in anchor_terms or term in seen:
                continue
            seen.add(term)
            result.append(term)
    return result


def _anchor_terms(product: Any, icps: Any) -> set[str]:
    values = [
        getattr(product, "name", ""),
        getattr(product, "problem_or_desire", ""),
        getattr(product, "value_proposition", ""),
        getattr(product, "description", ""),
        " ".join(getattr(product, "known_audience", []) or []),
    ]
    for icp in list(getattr(icps, "icps", icps if isinstance(icps, list) else []) or [])[:3]:
        values.extend(
            [
                getattr(icp, "title", ""),
                getattr(icp, "description", ""),
                getattr(icp, "pain", ""),
                getattr(icp, "trigger", ""),
                getattr(icp, "desired_outcome", ""),
                " ".join(getattr(icp, "alternatives", []) or []),
            ]
        )
    terms: set[str] = set()
    for value in values:
        terms.update(_ordered_tokens(str(value or "")))
    return terms


def _ordered_tokens(value: str) -> list[str]:
    result: list[str] = []
    for token in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", str(value or "").casefold()):
        if len(token) <= 3 or token.isdigit() or token in _UTILITY_TERMS:
            continue
        if token not in result:
            result.append(token)
    return result


def _platform(opportunity: Any) -> str:
    platform = getattr(opportunity, "platform", None)
    return str(getattr(platform, "value", platform) or "").upper()


def _metadata(opportunity: Any) -> dict:
    value = getattr(opportunity, "metadata", {})
    return dict(value) if isinstance(value, dict) else {}


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = " ".join(str(raw or "").split()).strip()[:180]
        if len(value) < 3:
            continue
        key = _normalized(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _normalized(value: str) -> str:
    return " ".join(str(value or "").casefold().split()).strip()


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
