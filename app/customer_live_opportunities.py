from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.runtime_store import RuntimeStateStore, get_runtime_store

CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE = "customer_live_opportunities"
# Stable runtime namespace owned by the customer funnel. Repeated here deliberately so
# Audience Intelligence can publish customer-facing discoveries without importing the
# funnel module back into its own dependency graph.
CUSTOMER_PROJECT_NAMESPACE = "customer_acquisition_projects"
MAX_TELEGRAM_OPPORTUNITY_AGE = timedelta(days=7)
TELEGRAM_PUBLISHED_AT_FUTURE_TOLERANCE = timedelta(minutes=5)

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
    relevance_score: float | None = Field(default=None, ge=0, le=100)
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

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

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
        preserve_published = (
            str(existing.get("status") or "") == "PUBLISHED" and status == "ACTIVE"
        )
        if preserve_published:
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
        if preserve_published:
            payload["publishability"] = existing.get("publishability") or "READY"
            payload["published_at"] = existing.get("published_at")
        self._store.put(CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE, key, payload)
        return self._view(payload)

    def sync_distribution_opportunities(
        self,
        product_id: UUID,
        opportunities: list[object],
    ) -> int:
        """Persist concrete, truly recent Telegram targets discovered by Audience Intelligence.

        Search/crawl timestamps are not proof that a Telegram post itself is current. A target
        is promoted only when its matching native context contains a parseable ``published_at``
        no more than seven days old. Unknown, future-invalid or older posts stay out of the
        active customer feed. This method only records research; it never joins or publishes.
        """
        project_ids = self._project_ids_for_product(product_id)
        if not project_ids:
            return 0

        current = datetime.now(UTC)
        synced = 0
        for opportunity in opportunities:
            platform_value = getattr(getattr(opportunity, "platform", None), "value", None)
            if str(platform_value or "").upper() != "TELEGRAM":
                continue
            metadata = getattr(opportunity, "metadata", None)
            if not isinstance(metadata, dict):
                continue
            target_url = str(metadata.get("action_target_url") or "").strip()
            if not target_url or metadata.get("action_target_specific") is not True:
                continue

            checked_at = self._parse_datetime(metadata.get("source_checked_at")) or current
            published_at = self._telegram_target_published_at(metadata, target_url)
            if published_at is None:
                self._mark_target_stale_for_projects(
                    project_ids,
                    target_url,
                    detail="Telegram post published_at is unavailable; true freshness is unverified.",
                    checked_at=checked_at,
                )
                continue
            if published_at > current + TELEGRAM_PUBLISHED_AT_FUTURE_TOLERANCE:
                self._mark_target_stale_for_projects(
                    project_ids,
                    target_url,
                    detail="Telegram post published_at is in the future; freshness verification failed.",
                    checked_at=checked_at,
                )
                continue
            expires_at = published_at + MAX_TELEGRAM_OPPORTUNITY_AGE
            if expires_at <= current:
                self._mark_target_stale_for_projects(
                    project_ids,
                    target_url,
                    detail="Telegram post is older than the 7-day acquisition window.",
                    checked_at=checked_at,
                )
                continue

            kind_value = getattr(getattr(opportunity, "kind", None), "value", None) or "COMMENT"
            rationale = str(
                getattr(opportunity, "rationale", None)
                or "Partizan found fresh Telegram audience evidence for this target."
            )[:4000]
            title = str(getattr(opportunity, "title", None) or "Telegram opportunity")[:420]
            relevance = getattr(opportunity, "relevance_score", None)
            request = CustomerLiveOpportunityUpsert(
                source="RESEARCH",
                platform="TELEGRAM",
                surface="COMMUNITY",
                kind=str(kind_value),
                title=f"{title} — fresh Telegram thread",
                url=target_url,
                rationale=rationale,
                recommended_action=(
                    "Review this fresh thread and the participation angle. Before any publication, "
                    "Partizan must preflight linked-discussion membership and write access."
                ),
                relevance_score=float(relevance) if relevance is not None else None,
                publishability="NEEDS_REVIEW",
                publishability_detail=(
                    "Native Telegram research found a specific current target. Publisher membership "
                    "and write access have not been verified for this action yet."
                ),
                discovered_at=checked_at,
                expires_at=expires_at,
                last_checked_at=checked_at,
                status="ACTIVE",
                metadata={
                    "distribution_opportunity_id": str(getattr(opportunity, "id", "")),
                    "telegram_entity_id": metadata.get("telegram_entity_id"),
                    "handle": metadata.get("handle"),
                    "linked_discussion_id": metadata.get("linked_discussion_id"),
                    "source": "audience_intelligence_native_telegram",
                    "source_published_at": published_at.isoformat(),
                },
            )
            for project_id in project_ids:
                self.upsert(project_id, request)
                synced += 1
        return synced

    def _project_ids_for_product(self, product_id: UUID) -> list[UUID]:
        result: list[UUID] = []
        for project in self._store.list_namespace(CUSTOMER_PROJECT_NAMESPACE):
            if project.get("deleted_at"):
                continue
            if str(project.get("product_id") or "") != str(product_id):
                continue
            try:
                result.append(UUID(str(project["id"])))
            except (KeyError, ValueError, TypeError):
                continue
        return result

    def _telegram_target_published_at(self, metadata: dict, target_url: str) -> datetime | None:
        normalized_target = target_url.strip().rstrip("/")
        for item in metadata.get("recent_context") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("url") or "").strip().rstrip("/") != normalized_target:
                continue
            return self._parse_datetime(item.get("published_at"))
        return None

    def _telegram_target_expiry(self, metadata: dict, target_url: str) -> datetime | None:
        published_at = self._telegram_target_published_at(metadata, target_url)
        if published_at is None:
            return None
        return published_at + MAX_TELEGRAM_OPPORTUNITY_AGE

    def _mark_target_stale_for_projects(
        self,
        project_ids: list[UUID],
        target_url: str,
        *,
        detail: str,
        checked_at: datetime,
    ) -> None:
        normalized_target = target_url.strip().rstrip("/")
        now = datetime.now(UTC).isoformat()
        checked = self._as_utc(checked_at).isoformat()
        for project_id in project_ids:
            for row in self._project_rows(project_id):
                if str(row.get("url") or "").strip().rstrip("/") != normalized_target:
                    continue
                if str(row.get("status") or "") == "PUBLISHED":
                    continue
                row["status"] = "STALE"
                row["publishability"] = "NEEDS_REVIEW"
                row["publishability_detail"] = detail[:1200]
                row["last_checked_at"] = checked
                row["updated_at"] = now
                self._store.put(
                    CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE,
                    self._key(project_id, str(row["opportunity_id"])),
                    row,
                )

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
