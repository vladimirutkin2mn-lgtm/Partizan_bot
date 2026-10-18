"""Offline replay of actual saved FemDom opportunities and safety gates."""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.saved_discovery_audit import audit_saved_opportunities

FEMDOM_SAMPLE = (
    Path(__file__).resolve().parents[1]
    / "ops"
    / "femdom"
    / "live_opportunities_2026-10-08.json"
)


def _rows():
    return json.loads(FEMDOM_SAMPLE.read_text(encoding="utf-8"))


def test_real_saved_femdom_october_posts_are_expired_and_not_publishable(monkeypatch):
    def forbid_network(*args, **kwargs):
        raise AssertionError("Saved discovery replay attempted network")

    monkeypatch.setattr(socket, "socket", forbid_network)
    result = audit_saved_opportunities(
        _rows(), as_of=datetime(2026, 10, 18, 12, tzinfo=UTC)
    )
    assert result["status"] == "OFFLINE_AUDIT_COMPLETE"
    assert result["read_only"] is True
    assert result["summary"]["saved_post_count"] == 3
    assert result["summary"]["distinct_public_post_count"] == 3
    assert result["summary"]["distinct_community_count"] == 2
    assert result["summary"]["expired_post_count"] == 3
    assert result["summary"]["publishable_without_live_checks"] == 0
    assert result["summary"]["reason_counts"] == {
        "EXPIRED": 3,
        "PUBLISHER_PREFLIGHT_NOT_READY": 3,
    }
    assert all(item["freshness"] == "STALE" for item in result["opportunities"])
    assert all(item["currently_publishable"] is False for item in result["opportunities"])
    assert result["effects"] == {
        "network_calls": 0,
        "telegram_calls": 0,
        "paid_calls": 0,
        "state_writes": 0,
        "publications": 0,
    }


def test_offline_saved_replay_is_deterministic_at_the_same_clock():
    at = datetime(2026, 10, 18, 12, tzinfo=UTC)
    assert audit_saved_opportunities(_rows(), as_of=at) == audit_saved_opportunities(
        _rows(), as_of=at
    )


def test_valid_but_recent_record_remains_unpublishable_without_live_revalidation():
    at = datetime(2026, 10, 18, 12, tzinfo=UTC)
    row = {
        "source": "OPERATIONAL_DISCOVERY",
        "platform": "TELEGRAM",
        "status": "ACTIVE",
        "url": "https://t.me/example_group/89",
        "title": "Current group post",
        "discovered_at": (at - timedelta(minutes=5)).isoformat(),
        "expires_at": (at + timedelta(days=2)).isoformat(),
        "publishability": "READY",
    }
    audit = audit_saved_opportunities([row], as_of=at)
    item = audit["opportunities"][0]
    assert item["freshness"] == "NEW"
    assert item["rejection_reasons"] == ["REQUIRES_LIVE_REVALIDATION"]
    assert item["currently_publishable"] is False
    assert audit["summary"]["expired_post_count"] == 0


def test_expiry_unverified_and_private_or_malformed_urls_are_blocked():
    at = datetime(2026, 10, 18, tzinfo=UTC)
    row = {
        "platform": "TELEGRAM",
        "url": "https://t.me/+PrivateInvite",
        "status": "ACTIVE",
        "publishability": "NEEDS_REVIEW",
        "discovered_at": at.isoformat(),
    }
    item = audit_saved_opportunities([row], as_of=at)["opportunities"][0]
    assert item["url"] is None
    assert "INVALID_OR_NONPUBLIC_TARGET" in item["rejection_reasons"]
    assert "EXPIRY_UNVERIFIED" in item["rejection_reasons"]
    assert item["currently_publishable"] is False

    with pytest.raises(ValueError, match="timezone"):
        audit_saved_opportunities([row], as_of=datetime(2026, 10, 18))
    with pytest.raises(ValueError, match="at most 200"):
        audit_saved_opportunities([row] * 201, as_of=at)


def test_exact_duplicate_posts_do_not_inflate_distinct_targets():
    at = datetime(2026, 10, 18, tzinfo=UTC)
    rows = _rows()
    duplicate = dict(rows[0], url="https://telegram.me/BIZARRE_AFISHA/794?ref=tracker")
    result = audit_saved_opportunities([rows[0], duplicate], as_of=at)
    assert result["summary"]["saved_post_count"] == 2
    assert result["summary"]["distinct_public_post_count"] == 1
    assert result["summary"]["distinct_community_count"] == 1
    assert "DUPLICATE_POST" in result["opportunities"][1]["rejection_reasons"]
