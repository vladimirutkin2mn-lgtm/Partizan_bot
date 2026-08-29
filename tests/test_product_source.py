from __future__ import annotations

import pytest

from app.product_source import (
    ProductSourceType,
    description_source_context,
    detect_product_source_type,
    normalize_product_link,
    read_public_product_source,
)
from app.website_intake import WebsiteSnapshot


@pytest.mark.parametrize(
    ("raw", "expected_type"),
    [
        ("https://example.com", ProductSourceType.WEBSITE),
        ("t.me/example_bot", ProductSourceType.TELEGRAM),
        ("https://apps.apple.com/us/app/example/id123", ProductSourceType.IOS_APP),
        ("https://play.google.com/store/apps/details?id=com.example", ProductSourceType.ANDROID_APP),
        ("https://github.com/acme/example", ProductSourceType.GITHUB),
        ("https://chromewebstore.google.com/detail/example/abc", ProductSourceType.CHROME_EXTENSION),
        ("https://www.producthunt.com/products/example", ProductSourceType.PRODUCT_PAGE),
    ],
)
def test_product_link_normalization_and_source_detection(raw: str, expected_type: ProductSourceType) -> None:
    normalized = normalize_product_link(raw)
    assert normalized.startswith(("http://", "https://"))
    assert detect_product_source_type(normalized) == expected_type


def test_text_description_uses_the_same_product_source_contract() -> None:
    context = description_source_context(
        "An AI Telegram bot that gives personalized astrology readings and daily horoscopes."
    )

    assert context.source_type == ProductSourceType.DESCRIPTION
    assert context.link is None
    assert context.needs_founder_context is False
    assert context.render_untrusted_brief().startswith("An AI Telegram bot")


@pytest.mark.asyncio
async def test_sparse_telegram_metadata_requests_one_targeted_clarification(monkeypatch) -> None:
    async def fake_reader(url: str, *, minimum_text_chars: int = 80) -> WebsiteSnapshot:
        assert url == "https://t.me/example_bot"
        assert minimum_text_chars == 0
        return WebsiteSnapshot(
            url=url,
            title="Example Bot",
            description="Contact @example_bot on Telegram",
            text="Telegram: Contact @example_bot",
        )

    monkeypatch.setattr("app.product_source.read_public_website", fake_reader)
    context = await read_public_product_source("t.me/example_bot")

    assert context.source_type == ProductSourceType.TELEGRAM
    assert context.needs_founder_context is True
    assert context.clarification_question == "What does this bot help users do?"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "expected_type", "title"),
    [
        (
            "https://apps.apple.com/us/app/focus/id123",
            ProductSourceType.IOS_APP,
            "Focus — App Store",
        ),
        (
            "https://play.google.com/store/apps/details?id=com.focus",
            ProductSourceType.ANDROID_APP,
            "Focus - Apps on Google Play",
        ),
        (
            "https://github.com/acme/focus",
            ProductSourceType.GITHUB,
            "acme/focus: Focus tasks from the terminal",
        ),
    ],
)
async def test_rich_public_metadata_can_build_understanding_without_source_question(
    monkeypatch,
    url: str,
    expected_type: ProductSourceType,
    title: str,
) -> None:
    async def fake_reader(value: str, *, minimum_text_chars: int = 80) -> WebsiteSnapshot:
        assert value == url
        assert minimum_text_chars == 0
        return WebsiteSnapshot(
            url=value,
            title=title,
            description="A focused productivity tool that helps people organize tasks and finish deep work.",
            text=(
                "Plan tasks, organize projects, track progress and reduce distractions. "
                "Built for independent professionals and small teams that need a simple workflow."
            ),
        )

    monkeypatch.setattr("app.product_source.read_public_website", fake_reader)
    context = await read_public_product_source(url)

    assert context.source_type == expected_type
    assert context.needs_founder_context is False
    brief = context.render_untrusted_brief()
    assert "PRODUCT_SOURCE_CONTENT (UNTRUSTED)" in brief
    assert f"SOURCE_TYPE: {expected_type.value}" in brief
    assert title in brief
