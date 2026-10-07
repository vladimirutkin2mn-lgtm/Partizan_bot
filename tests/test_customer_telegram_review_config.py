import json

import pytest

from app.customer_telegram_preview import (
    PREVIEW_SCHEMA_VERSION,
    _validate_existing_preview,
)
from app.customer_telegram_review_bundle import (
    _load_review_config,
    _profile_about,
    _recommended_variant,
)


def test_review_config_controls_profile_story_and_recommended_variant(tmp_path) -> None:
    config_file = tmp_path / "review.json"
    config_file.write_text(
        json.dumps(
            {
                "proposal_id": "proposal-v2",
                "display_name": "Nika",
                "bio_cta_line": "Продолжение здесь ↓",
                "avatar": {
                    "change_recommended": True,
                    "visual_direction": "Dark premium editorial portrait.",
                },
                "story": {
                    "first_experiment": "include",
                    "copy": "Продолжение в bio ↓",
                    "visual_direction": "Matching editorial story visual.",
                },
                "recommended_comment": {
                    "conversion_mechanism": "profile_click",
                    "variant_name": "non_obvious_lens",
                },
            }
        ),
        encoding="utf-8",
    )

    config = _load_review_config(config_file)
    assert config is not None
    assert config.display_name == "Nika"
    assert config.story is not None
    assert config.story.first_experiment == "INCLUDE"
    assert config.recommended_comment is not None
    assert config.recommended_comment.conversion_mechanism == "PROFILE_CLICK"
    assert _profile_about(
        "FemDom",
        "https://t.me/+example",
        config.bio_cta_line,
    ) == "Продолжение здесь ↓\nhttps://t.me/+example"

    selected = _recommended_variant(
        [
            {
                "conversion_mechanism": "PROFILE_CLICK",
                "variant_name": "expertise_signal",
                "send_eligible": True,
            },
            {
                "conversion_mechanism": "PROFILE_CLICK",
                "variant_name": "non_obvious_lens",
                "send_eligible": True,
            },
        ],
        config.recommended_comment,
    )
    assert selected["variant_name"] == "non_obvious_lens"


def test_review_config_fails_closed_when_requested_variant_is_missing(tmp_path) -> None:
    config_file = tmp_path / "review.json"
    config_file.write_text(
        json.dumps(
            {
                "proposal_id": "proposal-v2",
                "recommended_comment": {
                    "conversion_mechanism": "PROFILE_CLICK",
                    "variant_name": "non_obvious_lens",
                },
            }
        ),
        encoding="utf-8",
    )
    config = _load_review_config(config_file)
    assert config is not None
    assert config.recommended_comment is not None

    with pytest.raises(ValueError, match="Configured recommended comment variant"):
        _recommended_variant(
            [
                {
                    "conversion_mechanism": "PROFILE_CLICK",
                    "variant_name": "expertise_signal",
                    "send_eligible": True,
                }
            ],
            config.recommended_comment,
        )


def test_preview_rejects_stale_marketing_memory_before_reusing_actions() -> None:
    record = {
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "marketing_memory_fingerprint": "old",
    }

    with pytest.raises(ValueError, match="stale project marketing memory"):
        _validate_existing_preview(
            record,
            expected_memory_fingerprint="new",
        )
