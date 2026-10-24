"""The latest Telegram post is the only source; compaction costs no LLM call."""

import pytest

from app.telegram_target_excerpt import (
    MAX_TARGET_CHARS,
    compact_telegram_target,
)


def test_short_message_is_passed_through_without_rewriting():
    source = (
        "Дресс-код черно-оранжевый, программа с шибари, "
        "безалкогольный вечер и шашлыки."
    )
    result = compact_telegram_target(source)
    assert result.text == source
    assert not result.compacted
    assert result.strategy == "FULL_SAVED_TEXT"
    assert result.source_chars == len(source)
    assert result.included_chars == len(source)


def test_long_post_keeps_beginning_fee_from_middle_and_ending():
    beginning = "Хэллоуинская вечеринка и маскарад! " + "Общий анонс. " * 40
    filler = "Вводные подробности мероприятия для гостей. " * 40
    price = "Стоимость билета: взнос 4311 ₽ с каждого участника."
    tail = "Последний день записи в пятницу, количество мест ограничено."
    text = beginning + "\n" + filler + "\n" + price + "\n" + (
        "Немного обычных описаний. " * 35
    ) + "\n" + tail
    result = compact_telegram_target(text)
    assert result.compacted
    assert result.source_chars == len(text)
    assert result.included_chars <= MAX_TARGET_CHARS
    assert len(result.text) <= MAX_TARGET_CHARS
    assert "Хэллоуинская вечеринка" in result.text
    assert "4311" in result.text
    assert "Последний день записи" in result.text
    assert "parts of this SAME post omitted" in result.text
    assert "отсутств" not in result.text


def test_no_extra_model_call_is_needed_and_output_is_deterministic():
    text = (
        "Старт программы. " + "Расписание встреч и подробности " * 130
        + "Финальные условия и контакты."
    )
    first = compact_telegram_target(text)
    second = compact_telegram_target(text)
    assert first == second
    assert first.compacted
    assert first.included_chars <= MAX_TARGET_CHARS


@pytest.mark.parametrize("n", [0, 12, 1200, 1400])
def test_messages_at_or_under_hard_budget_are_untouched(n):
    result = compact_telegram_target("р" * n)
    assert result.text == "р" * n
    assert result.source_chars == n
    assert not result.compacted


@pytest.mark.parametrize("limit", [0, 100, 599])
def test_tiny_budget_is_rejected(limit):
    with pytest.raises(ValueError, match="at least 600"):
        compact_telegram_target("Данные", max_chars=limit)


def test_does_not_guess_anything_from_missing_upstream_saved_text():
    # The historic a_sfera snapshot was clipped at an upstream 1200 chars.
    # This must not pretend to know the missing 4311 price or other details.
    saved = "Анонс мероприятия " * 65
    result = compact_telegram_target(saved)
    assert result.text == saved.strip()
    assert "4311" not in result.text
    assert result.source_chars == len(saved.strip())
