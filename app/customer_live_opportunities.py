from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.runtime_store import RuntimeStateStore, get_runtime_store

CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE = "customer_live_opportunities"

LiveOpportunitySource = Literal[
    "OPERATIONAL_DISCOVERY",
    "RESEARCH",
    "TELEGRAM_PREVIEW",
]
LiveOpportunityStatus = Literal["ACTIVE", "PUBLISHED", "DISMISSED", "STALE"]
LiveOpportunityFreshness = Literal["NEW", "FRESH", "EXPIRING_SOON", "STALE"]
LiveOpportunityPublishability = Literal[
    "READY",
    "JOIN_REQUIRED",
    "NO_WRITE_ACCESS",
    "NO_DISCUSSION",
    "PRECHECK_FAILED",
    "JOIN_FAILED",
    "NEEDS_REVIEW",
    "MANUAL",
    "UNKNOWN",
]


class CustomerLiveOpportunityUpsert(BaseModel):
    opportunity_id: str | None = Field(default=None, max_length=180)
    source: LiveOpportunitySource = "OPERATIONAL_DISCOVERY"
    platform: str = Field(min_length=1, max_length=80)
    surface: str = Field(default="COMMUNITY", min_length=1, max_length=80)
    kind: str = Field(default="COMMENT", min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=500)
    url: HttpUrl
    rationale: str = Field(min_length=1, max_length=4000)
    recommended_action: str = Field(min_length=1, max_length=4000)
    suggested_content: str | None = Field(default=None, max_length=8000)
    relevance_score: float | None = Field(default=None, ge=0, le=1)
    publishability: LiveOpportunityPublishability = "UNKNOWN"
    publishability_detail: str | None = Field(default=None, max_length=1200)
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    last_checked_at: datetime | None = None
    status: LiveOpportunityStatus = "ACTIVE"
    metadata: dict = Field(default_factory=dict)

    @field_validator("platform", "surface", "kind")
    @classmethod
    def normalize_labels(cls, value: str) -> str:
        return "_".join(value.strip().upper().split())


class CustomerLiveOpportunityView(BaseModel):
    opportunity_id: str
    source: LiveOpportunitySource
    platform: str
    surface: str
    kind: str
    title: str
    url: HttpUrl
    rationale: str
    recommended_action: str
    suggested_content: str | None = None
    relevance_score: float | None = None
    publishability: LiveOpportunityPublishability
    publishability_detail: str | None = None
    discovered_at: datetime
    expires_at: datetime | None = None
    last_checked_at: datetime | None = None
    status: LiveOpportunityStatus
    freshness: LiveOpportunityFreshness


class CustomerLiveOpportunityService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    @staticmethod
    def canonical_opportunity_id(platform: str, url: str) -> str:
        normalized_platform = "_".join(platform.strip().upper().split()) or "UNKNOWN"
        digest = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:24]
        return f"{normalized_platform.lower()}:{digest}"

    @staticmethod
    def _safe_id(value: str) -> str:
        normalized = value.strip()
        if not normalized or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,180}", normalized):
            raise ValueError("opportunity_id must contain only safe identifier characters")
        return normalized

    @staticmethod
    def _key(project_id: UUID, opportunity_id: str) -> str:
        return f"{project_id}:{opportunity_id}"

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def upsert(
        self,
        project_id: UUID,
        request: CustomerLiveOpportunityUpsert,
    ) -> CustomerLiveOpportunityView:
        url = str(request.url)
        opportunity_id = self._safe_id(
            request.opportunity_id
            or self.canonical_opportunity_id(request.platform, url)
        )
        key = self._key(project_id, opportunity_id)
        existing = self._store.get(CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE, key) or {}
        status: LiveOpportunityStatus = request.status
        if str(existing.get("status") or "") == "PUBLISHED" and status == "ACTIVE":
            status = "PUBLISHED"
        payload = {
            **request.model_dump(mode="json"),
            "project_id": str(project_id),
            "opportunity_id": opportunity_id,
            "status": status,
            "url": url,
            "created_at": existing.get("created_at") or datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self._store.put(CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE, key, payload)
        return self._view(payload)

    def list_for_project(
        self,
        project_id: UUID,
        *,
        now: datetime | None = None,
    ) -> list[CustomerLiveOpportunityView]:
        current = self._as_utc(now or datetime.now(UTC))
        rows = [
            row
            for row in self._store.list_namespace(CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE)
            if str(row.get("project_id") or "") == str(project_id)
        ]
        views = [self._view(row, now=current) for row in rows]
        freshness_rank = {"NEW": 0, "FRESH": 1, "EXPIRING_SOON": 2, "STALE": 3}
        status_rank = {"ACTIVE": 0, "PUBLISHED": 1, "DISMISSED": 2, "STALE": 3}
        views.sort(
            key=lambda item: (
                status_rank[item.status],
                freshness_rank[item.freshness],
                -(item.relevance_score or 0),
                -self._as_utc(item.discovered_at).timestamp(),
            )
        )
        return views

    def update_publishability_by_url(
        self,
        project_id: UUID,
        url: str,
        *,
        publishability: LiveOpportunityPublishability,
        detail: str | None = None,
        checked_at: datetime | None = None,
    ) -> int:
        normalized_url = url.strip().rstrip("/")
        updated = 0
        for row in self._project_rows(project_id):
            if str(row.get("url") or "").strip().rstrip("/") != normalized_url:
                continue
            row["publishability"] = publishability
            row["publishability_detail"] = detail
            row["last_checked_at"] = self._as_utc(checked_at or datetime.now(UTC)).isoformat()
            row["updated_at"] = datetime.now(UTC).isoformat()
            self._store.put(
                CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE,
                self._key(project_id, str(row["opportunity_id"])),
                row,
            )
            updated += 1
        return updated

    def mark_published_by_url(
        self,
        project_id: UUID,
        url: str,
        *,
        published_at: datetime | None = None,
    ) -> int:
        normalized_url = url.strip().rstrip("/")
        updated = 0
        for row in self._project_rows(project_id):
            if str(row.get("url") or "").strip().rstrip("/") != normalized_url:
                continue
            row["status"] = "PUBLISHED"
            row["publishability"] = "READY"
            row["published_at"] = self._as_utc(published_at or datetime.now(UTC)).isoformat()
            row["updated_at"] = datetime.now(UTC).isoformat()
            self._store.put(
                CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE,
                self._key(project_id, str(row["opportunity_id"])),
                row,
            )
            updated += 1
        return updated

    def _project_rows(self, project_id: UUID) -> list[dict]:
        return [
            row
            for row in self._store.list_namespace(CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE)
            if str(row.get("project_id") or "") == str(project_id)
        ]

    def _view(
        self,
        payload: dict,
        *,
        now: datetime | None = None,
    ) -> CustomerLiveOpportunityView:
        current = self._as_utc(now or datetime.now(UTC))
        discovered_at = self._as_utc(datetime.fromisoformat(str(payload["discovered_at"])))
        expires_at_raw = payload.get("expires_at")
        expires_at = (
            self._as_utc(datetime.fromisoformat(str(expires_at_raw)))
            if expires_at_raw
            else None
        )
        status = str(payload.get("status") or "ACTIVE")
        freshness: LiveOpportunityFreshness
        if status == "STALE" or (expires_at is not None and expires_at <= current):
            freshness = "STALE"
        elif expires_at is not None and expires_at - current <= timedelta(hours=48):
            freshness = "EXPIRING_SOON"
        elif current - discovered_at <= timedelta(hours=24):
            freshness = "NEW"
        else:
            freshness = "FRESH"
        return CustomerLiveOpportunityView(
            opportunity_id=str(payload["opportunity_id"]),
            source=str(payload.get("source") or "OPERATIONAL_DISCOVERY"),
            platform=str(payload.get("platform") or "UNKNOWN"),
            surface=str(payload.get("surface") or "COMMUNITY"),
            kind=str(payload.get("kind") or "COMMENT"),
            title=str(payload.get("title") or "Opportunity"),
            url=str(payload["url"]),
            rationale=str(payload.get("rationale") or "Relevant acquisition opportunity"),
            recommended_action=str(payload.get("recommended_action") or "Review this opportunity"),
            suggested_content=(
                str(payload.get("suggested_content"))
                if payload.get("suggested_content") is not None
                else None
            ),
            relevance_score=(
                float(payload["relevance_score"])
                if payload.get("relevance_score") is not None
                else None
            ),
            publishability=str(payload.get("publishability") or "UNKNOWN"),
            publishability_detail=(
                str(payload.get("publishability_detail"))
                if payload.get("publishability_detail") is not None
                else None
            ),
            discovered_at=discovered_at,
            expires_at=expires_at,
            last_checked_at=(
                self._as_utc(datetime.fromisoformat(str(payload["last_checked_at"])))
                if payload.get("last_checked_at")
                else None
            ),
            status=status,
            freshness=freshness,
        )

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE)


customer_live_opportunity_service = CustomerLiveOpportunityService()
