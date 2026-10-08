from io import BytesIO

from PIL import Image

from app.customer_telegram_story_replace_reviewed import (
    EXPECTED_SIZE,
    _asset_bytes,
    _telegram_photo_bytes,
)


def test_approved_story_asset_is_complete_and_expected_size() -> None:
    content = _asset_bytes()
    assert content.startswith(b"RIFF")
    assert content[8:12] == b"WEBP"
    with Image.open(BytesIO(content)) as image:
        image.load()
        assert image.size == EXPECTED_SIZE


def test_approved_story_is_encoded_as_telegram_photo_without_resizing() -> None:
    source = _asset_bytes()
    content = _telegram_photo_bytes(source)

    assert content.startswith(b"\xff\xd8\xff")
    with Image.open(BytesIO(content)) as image:
        image.load()
        assert image.format == "JPEG"
        assert image.size == EXPECTED_SIZE
