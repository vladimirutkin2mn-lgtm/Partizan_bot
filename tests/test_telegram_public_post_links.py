"""Latest Telegram post can link to a different community with discussions."""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.platform_discovery import TelegramDiscoveryAdapter
from app.telegram_public_post_links import referenced_public_posts
from app.telegram_research import (
    SurfaceAvailability,
    TelegramCommunitySnapshot,
    TelegramRecentContext,
    TelegramSurfaceKind,
    TelethonTelegramResearchTransport,
)


def test_linked_bizarre_channel_from_message_body_and_url_button():
    message = SimpleNamespace(
        message="Подробности в https://t.me/bizarreclubmsk/7552 и у нас",
        entities=[SimpleNamespace(url="https://t.me/bizarreclubmsk/7552")],
        buttons=[[SimpleNamespace(url="https://t.me/a_sfera/2117")]],
    )
    assert referenced_public_posts(
        message, own_handle="Bizarre_afisha",
    ) == (
        "https://t.me/bizarreclubmsk/7552",
        "https://t.me/a_sfera/2117",
    )


def test_external_links_private_invites_and_own_post_never_become_leads():
    obj = SimpleNamespace(
        message=(
            "https://t.me/+private123 "
            "https://t.me/current_channel/15 "
            "http://t.me/offtopic/17 "
            "https://t.me/other_public/21"
        ),
        entities=[
            SimpleNamespace(url="https://example.org/this/is/not/telegram"),
            SimpleNamespace(url="https://t.me/new_public/40"),
        ],
        buttons=None,
    )
    assert referenced_public_posts(obj, own_handle="current_channel") == (
        "https://t.me/other_public/21",
        "https://t.me/new_public/40",
    )


def test_native_snapshot_retains_referenced_posts_as_unverified_leads():
    transport = TelethonTelegramResearchTransport(
        api_id=1, api_hash="mock-hash", session_string="mock-session",
    )
    entity = SimpleNamespace(
        id=77, username="Bizarre_afisha",
        megagroup=False, broadcast=True, title="Bizarre",
    )
    full = SimpleNamespace(
        full_chat=SimpleNamespace(
            linked_chat_id=None, about="Event channel", participants_count=250,
        ),
    )
    message = SimpleNamespace(
        id=910,
        message="Другой канал: https://t.me/bizarreclubmsk/7552",
        date=datetime(2026, 10, 24, tzinfo=UTC),
        entities=[],
    )
    snap = transport._snapshot(entity, full, [message], query="")
    assert snap.recent_context[0].linked_post_urls == (
        "https://t.me/bizarreclubmsk/7552",
    )
    md = TelegramDiscoveryAdapter._native_metadata(
        None, SimpleNamespace(discovery_query=SimpleNamespace(query="")), snap,
    )
    assert md["linked_public_post_leads"] == [
        "https://t.me/bizarreclubmsk/7552"
    ]
    assert md["recent_context"][0]["linked_post_urls"] == [
        "https://t.me/bizarreclubmsk/7552"
    ]
    assert md["surface_capabilities"]["comment"] == (
        SurfaceAvailability.UNAVAILABLE.value
    )
    assert md["surface_capabilities"]["publisher_permission_verified"] is False
