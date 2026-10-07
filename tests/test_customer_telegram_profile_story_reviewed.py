from io import BytesIO

from PIL import Image

from app.customer_telegram_profile_story_reviewed import (
    AVATAR_SIZE,
    STORY_SIZE,
    prepare_avatar_for_telegram,
    prepare_story_media,
)


def _source_jpeg() -> bytes:
    image = Image.new("RGB", (512, 512), (24, 24, 24))
    output = BytesIO()
    image.save(output, format="JPEG", quality=90)
    return output.getvalue()


def _size(content: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(content)) as image:
        return image.size


def test_prepare_avatar_uses_higher_resolution() -> None:
    prepared = prepare_avatar_for_telegram(_source_jpeg())
    assert _size(prepared) == AVATAR_SIZE
    assert prepared.startswith(b"\xff\xd8")
    assert prepared.endswith(b"\xff\xd9")


def test_prepare_story_uses_native_portrait_canvas() -> None:
    prepared = prepare_story_media(_source_jpeg())
    assert _size(prepared) == STORY_SIZE
    assert prepared.startswith(b"\xff\xd8")
    assert prepared.endswith(b"\xff\xd9")
