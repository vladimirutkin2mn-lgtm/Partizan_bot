from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from app.distribution_schemas import AudienceDistributionMapView
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_community_restrictions import TelegramCommunityRestrictionMemory

CUSTOMER_PROJECT_NAMESPACE = "customer_acquisition_projects"
AUDIENCE_MAP_NAMESPACE = "audience_distribution_map"

ResearchCommunityActionability = Literal["RESEARCH_ONLY"]
ResearchCommunityWriteAccess = Literal["UNKNOWN", "NO_WRITE_ACCESS"]

_TELEGRAM_HANDLE = re.compile(r"^[A-Za-z0-9_]{5,32}$")


class CustomerResearchCommunityView(BaseModel):
    opportunity_id: UUID
    platform: Literal["TELEGRAM"] = "TELEGRAM"
    title: str = Field(min_length=1, max_length=300)
    community_url: HttpUrl
    rationale: str | None = Field(default=None, max_length=4000)
    relevance_score: float | None = Field(default=None, ge=0, le=100)
    audience_fit: float | None = Field(default=None, ge=0, le=100)
    context_fit: float | None = Field(default=None, ge=0, le=100)
    actionability_score: float | None = Field(default=None, ge=0, le=100)
    confidence_score: float | None = Field(default=None, ge=0, le=100)
    ranking_score: float | None = Field(default=None, ge=0, le=100)
    exploration_candidate: bool = False
    observed_participant_count: int | None = Field(default=None, ge=0)
    matched_participant_count: int | None = Field(default=None, ge=0)
    repeat_participant_count: int | None = Field(default=None, ge=0)
    observed_message_count: int | None = Field(default=None, ge=0)
    matched_message_count: int | None = Field(default=None, ge=0)
    participant_match_ratio: float | None = Field(default=None, ge=0, le=1)
    participant_evidence_checked_at: datetime | None = None
    actionability: ResearchCommunityActionability = "RESEARCH_ONLY"
    write_access: ResearchCommunityWriteAccess = "UNKNOWN"
    write_access_detail: str | None = Field(default=None, max_length=1200)
    handle: str | None = Field(default=None, max_length=32)
    member_count: int | None = Field(default=None, ge=0)
    source_checked_at: datetime | None = None


class CustomerResearchCommunityService:
    """Expose audience research leads without turning them into publish targets."""

    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()
        self._restrictions = TelegramCommunityRestrictionMemory(self._store)

    def list_for_project(self, project_id: UUID) -> list[CustomerResearchCommunityView]:
        project = self._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
        if project is None or project.get("deleted_at"):
            return []
        product_id = str(project.get("product_id") or "").strip()
        if not product_id:
            return []
        payload = self._store.get(AUDIENCE_MAP_NAMESPACE, product_id)
        if payload is None:
            return []

        audience_map = AudienceDistributionMapView.model_validate(payload)
        result: list[CustomerResearchCommunityView] = []
        seen_urls: set[str] = set()
        for opportunity in audience_map.opportunities:
            if opportunity.platform.value != "TELEGRAM":
                continue
            metadata = opportunity.metadata if isinstance(opportunity.metadata, dict) else {}
            if str(metadata.get("opportunity_scope") or "").upper() != "COMMUNITY":
                continue
            if str(metadata.get("actionability") or "").upper() != "RESEARCH_ONLY":
                continue

            handle = self._community_handle(opportunity.url, metadata)
            if handle is None:
                continue
            community_url = f"https://t.me/{handle}"
            normalized_url = community_url.casefold()
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)

            restriction = self._restrictions.get(project_id, handle)
            write_access: ResearchCommunityWriteAccess = "UNKNOWN"
            write_access_detail = None
            if restriction is not None:
                write_access = "NO_WRITE_ACCESS"
                reason = str(restriction.get("reason") or "NO_WRITE_ACCESS")
                write_access_detail = (
                    f"Telegram previously confirmed a write restriction for this customer "
                    f"account in @{handle}: {reason}. The community remains useful audience evidence."
                )

            member_count = self._nonnegative_int(metadata.get("member_count"))
            scores = metadata.get("intelligence_scores")
            scores = scores if isinstance(scores, dict) else {}
            participant = self._participant_evidence(metadata)
            result.append(
                CustomerResearchCommunityView(
                    opportunity_id=opportunity.id,
                    title=opportunity.title,
                    community_url=community_url,
                    rationale=opportunity.rationale,
                    relevance_score=opportunity.relevance_score,
                    audience_fit=self._score(scores, "audience_fit"),
                    context_fit=self._score(scores, "context_fit"),
                    actionability_score=self._score(scores, "actionability"),
                    confidence_score=self._score(scores, "confidence"),
                    ranking_score=self._score(scores, "ranking_score"),
                    exploration_candidate=scores.get("exploration_candidate") is True,
                    observed_participant_count=self._nonnegative_int(
                        participant.get("distinct_participant_count")
                    ),
                    matched_participant_count=self._nonnegative_int(
                        participant.get("matched_participant_count")
                    ),
                    repeat_participant_count=self._nonnegative_int(
                        participant.get("repeat_participant_count")
                    ),
                    observed_message_count=self._nonnegative_int(
                        participant.get("observed_message_count")
                    ),
                    matched_message_count=self._nonnegative_int(
                        participant.get("matched_message_count")
                    ),
                    participant_match_ratio=self._ratio(
                        participant.get("matched_participant_ratio")
                    ),
                    participant_evidence_checked_at=self._datetime(
                        participant.get("source_checked_at")
                    ),
                    write_access=write_access,
                    write_access_detail=write_access_detail,
                    handle=handle,
                    member_count=member_count,
                    source_checked_at=self._source_checked_at(metadata),
                )
            )

        result.sort(
            key=lambda item: (
                -(item.ranking_score if item.ranking_score is not None else item.relevance_score or 0),
                item.title.casefold(),
                str(item.community_url),
            )
        )
        return result

    @staticmethod
    def _participant_evidence(metadata: dict) -> dict:
        value = metadata.get("participant_evidence")
        if not isinstance(value, dict):
            return {}
        # Fail closed if a future producer starts persisting participant identity.
        if value.get("identity_persisted") is not False:
            return {}
        if value.get("participant_ids_persisted") is not False:
            return {}
        if value.get("usernames_persisted") is not False:
            return {}
        return value

    @staticmethod
    def _community_handle(url: object, metadata: dict) -> str | None:
        metadata_handle = str(metadata.get("handle") or "").strip().lstrip("@")
        if _TELEGRAM_HANDLE.fullmatch(metadata_handle):
            return metadata_handle
        if url is None:
            return None
        parts = urlsplit(str(url))
        if parts.scheme != "https" or parts.netloc.casefold() not in {"t.me", "www.t.me"}:
            return None
        path = [part for part in parts.path.split("/") if part]
        if not path or path[0].startswith("+") or path[0].casefold() == "joinchat":
            return None
        return path[0] if _TELEGRAM_HANDLE.fullmatch(path[0]) else None

    @classmethod
    def _source_checked_at(cls, metadata: dict) -> datetime | None:
        return cls._datetime(metadata.get("source_checked_at"))

    @staticmethod
    def _datetime(value: object) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _nonnegative_int(value: object) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number >= 0 else None

    @staticmethod
    def _ratio(value: object) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return min(1.0, max(0.0, number))

    @staticmethod
    def _score(scores: dict, key: str) -> float | None:
        value = scores.get(key)
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return min(100.0, max(0.0, number))


customer_research_community_service = CustomerResearchCommunityService()
