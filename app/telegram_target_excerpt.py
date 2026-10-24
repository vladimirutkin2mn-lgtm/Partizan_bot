"""Zero-LLM, bounded selection of details from ONE Telegram target message.

Keep all of a normal short post. Long posts get a deterministic mix of
opening, concrete details from the middle, and ending; never look at older
messages and never silently present fragments as the complete source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_TARGET_CHARS = 1400
_MARK = "\n[...some parts of this SAME post omitted...]\n"
_DETAIL = re.compile(
    r"(?iu)(?:\b\d+[\d\s]*[₽$€%]?\b|"
    r"\b(?:цена|стоимост|взнос|оплат|скидк|билет|мест[ао]?|"
    r"дресс.?код|программ|услови|запис|вход|регистрац|"
    r"время|начал|окончани|возраст|срок|участ|адрес|"
    r"приз|аукцион|мастер|практик|экшен|shibari|"
    r"price|fee|ticket|limited|spots|schedule|"
    r"dress.?code|registration|auction|workshop)\w*)",
    re.UNICODE,
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZА-ЯЁ0-9])")


@dataclass(frozen=True, slots=True)
class TelegramTargetExcerpt:
    text: str
    source_chars: int
    included_chars: int
    compacted: bool
    strategy: str


def _units(text: str) -> list[str]:
    """Break lines and long continuous paragraphs into usable detail units."""
    units: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for sentence in _SENTENCE_END.split(line):
            sentence = sentence.strip()
            if not sentence:
                continue
            # A single very long line must not use the whole budget.
            if len(sentence) > 320:
                units.extend(
                    sentence[i : i + 240].strip()
                    for i in range(0, len(sentence), 240)
                )
            else:
                units.append(sentence)
    return units


def compact_telegram_target(
    text: str,
    *,
    max_chars: int = MAX_TARGET_CHARS,
) -> TelegramTargetExcerpt:
    """Deterministic selection, without summarizing or inventing details.

    Source metadata describes how much text is available to this function.
    If an upstream ingestion step already cut the post, this function cannot
    reconstruct it.
    """
    text = str(text or "").strip()
    if max_chars < 600:
        raise ValueError("Telegram target budget must be at least 600 chars")
    source_chars = len(text)
    if source_chars <= max_chars:
        return TelegramTargetExcerpt(
            text, len(text), len(text), False, "FULL_SAVED_TEXT"
        )

    # Limit scanning costs and prevent pathological text payloads.
    text = text[:12000]
    budget = max_chars - 3 * len(_MARK)
    lead_n = min(500, int(budget * 0.42))
    tail_n = min(300, int(budget * 0.23))
    middle_budget = budget - lead_n - tail_n

    prefix = text[:lead_n].rstrip()
    suffix = text[-tail_n:].lstrip()
    middle = text[lead_n:-tail_n]
    ranked = []
    for idx, unit in enumerate(_units(middle)):
        occurrences = _DETAIL.findall(unit)
        score = min(len(occurrences), 5) * 3 + bool(re.search(r"\d", unit))
        if re.search(r"(?iu)(?:взнос|стоимост|цен[аы]|оплат|fee|price|\d+\s*[₽$€])", unit):
            score += 8
        if score:
            ranked.append((-score, idx, unit))
    ranked.sort()
    selected = []
    used = 0
    for _, idx, unit in ranked:
        if used + len(unit) + 2 > middle_budget:
            continue
        selected.append((idx, unit))
        used += len(unit) + 2
        if used >= middle_budget - 100:
            break
    selected.sort()
    extra = " ".join(unit for _, unit in selected)

    parts = [prefix]
    if extra:
        parts.append(extra)
    parts.append(suffix)
    result = _MARK.join(parts)
    # The actual output always fits. Never pass an incomplete JSON fragment.
    result = result[:max_chars]
    return TelegramTargetExcerpt(
        result, source_chars, len(result), True, "OPENING_SALIENT_DETAILS_END"
    )
