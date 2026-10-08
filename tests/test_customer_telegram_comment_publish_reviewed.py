from pathlib import Path

from app.customer_telegram_comment_publish_reviewed import (
    CONTENT_SHA256,
    ENTITY_ID,
    HANDLE,
    POST_ID,
    TARGET_URL,
    _load_config,
)


CONFIG = Path("ops/femdom/telegram_comment_asfera_2100_approved_v1.json")


def test_reviewed_comment_contract_is_exact_and_comment_only() -> None:
    config = _load_config(CONFIG)

    assert config["expected_handle"] == HANDLE
    assert config["expected_telegram_entity_id"] == ENTITY_ID
    assert config["target_post_id"] == POST_ID
    assert config["target_url"] == TARGET_URL
    assert config["content_sha256"] == CONTENT_SHA256
    assert config["authorization_phrase_received"] == "публикуй"
    assert config["authorization_scope"] == "COMMENT_ONLY"
    assert config["community_publish_authorized"] is True
    assert config["profile_mutation_authorized"] is False
    assert config["story_mutation_authorized"] is False
    assert config["reply_publish_authorized"] is False
    assert config["message_publish_authorized"] is False
