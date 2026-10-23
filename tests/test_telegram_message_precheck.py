"""Zero-token screening of obviously unsuitable latest Telegram messages."""

import pytest

from app.telegram_message_precheck import precheck_telegram_target


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("⬇️ЗАПОЛНИТЬ АНКЕТУ МОЖНО ТУТ⬇️", "BARE_CALL_TO_ACTION"),
        (
            "Тут ебут жестко, если смелый заходи\nt.me/example",
            "SHORT_INVITATION_WITHOUT_TOPIC",
        ),
        (
            "Правила 🌟\n\nВыход из чата означает удаление анкеты. "
            "Повторное вступление стоит 1000 рублей.",
            "ADMINISTRATIVE_ANNOUNCEMENT",
        ),
        (
            "Я расскажу о нашем предложении. Канал и личка в шапке профиля",
            "UNSOLICITED_PROMOTIONAL_MESSAGE",
        ),
        ("https://t.me/somewhere", "EMPTY_OR_LINK_ONLY"),
        ("Подписывайтесь на канал!", "NO_SUBSTANTIVE_CONVERSATION"),
    ],
)
def test_obvious_nonconversations_blocked_without_model(text, reason):
    assert precheck_telegram_target(text).eligible is False
    assert precheck_telegram_target(text).reason == reason


@pytest.mark.parametrize(
    "text",
    [
        "Caption: встречаемся в субботу, обсудим расписание.",
        "Встреча в субботу. Кто хотел бы выступить с вопросами?",
        "На мастер-классе всё для рисования выдадим на месте. С собой нужна удобная одежда.",
        "Как лучше начать разговор с новым участником?",
    ],
)
def test_meaningful_short_posts_are_not_blocked(text):
    assert precheck_telegram_target(text).eligible is True


def test_precheck_does_not_decide_relevance_from_community_topic():
    # This is nonempty but off-topic; semantic screening is a separate step.
    result = precheck_telegram_target(
        "Новая барахолка: книги, бумага, косметика, краски и материалы "
        "для творчества. Приглашаем продавцов в пятничную подборку."
    )
    assert result.eligible is True
    assert result.reason == "ELIGIBLE_FOR_CONTEXTUAL_REVIEW"
