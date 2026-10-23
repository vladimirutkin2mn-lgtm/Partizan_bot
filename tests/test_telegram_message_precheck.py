"""Only unreadable latest messages should be skipped before drafting."""

import pytest

from app.telegram_message_precheck import precheck_telegram_target


@pytest.mark.parametrize(
    "text",
    [
        "",
        "  ",
        "https://t.me/somewhere",
        "📣🔗",
        "@telegramchannel",
    ],
)
def test_no_readable_message_blocks_before_llm(text):
    result = precheck_telegram_target(text)
    assert result.eligible is False
    assert result.reason == "EMPTY_OR_LINK_ONLY"


@pytest.mark.parametrize(
    "text",
    [
        "⬇️ЗАПОЛНИТЬ АНКЕТУ МОЖНО ТУТ⬇️",
        "Подписывайтесь на канал!",
        "Тут новая встреча, заходи! t.me/example",
        "Правила 🌟\n\nВыход из чата означает удаление анкеты.",
        "Я расскажу о нашем предложении. Канал и личка в шапке профиля",
        "Caption: встречаемся в субботу, обсудим расписание.",
        "Встреча в субботу. Кто хотел бы выступить с вопросами?",
        "На мастер-классе всё для рисования выдадим на месте.",
        "Как лучше начать разговор с новым участником?",
    ],
)
def test_readable_messages_reach_generator_regardless_of_style(text):
    result = precheck_telegram_target(text)
    assert result.eligible is True
    assert result.reason == "READABLE_TARGET_FOR_REVIEW"


def test_relevance_requires_upstream_product_aware_evidence():
    # This message could be off-topic for the product; this cheap check is
    # not allowed to repeat semantic judgment from scratch.
    result = precheck_telegram_target(
        "Новая барахолка: книги, косметика, краски и материалы для творчества."
    )
    assert result.eligible is True
