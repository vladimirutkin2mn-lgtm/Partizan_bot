from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_execution_service import distribution_execution_service
from app.telegram_native_attribution import (
    TelegramNativeAttributionError,
    telegram_native_attribution_service,
)
from app.telegram_profile_conversion_pack import (
    TelegramProfilePackStatus,
    telegram_profile_conversion_pack_service,
)


class TelegramProfileLearningSignal(StrEnum):
    NONE = "NONE"
    REPLY = "REPLY"
    VISIT = "VISIT"
    JOIN = "JOIN"
    BOT_START = "BOT_START"
    SIGNUP = "SIGNUP"
    ACTIVATED = "ACTIVATED"
    PAID = "PAID"


class TelegramProfileLearningRow(BaseModel):
    pack_id: UUID
    action_id: UUID
    experiment_id: UUID
    pack_status: TelegramProfilePackStatus
    profile_treatment_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    display_name: str | None = None
    avatar_sha256: str | None = None
    cta_type: str
    native_attribution_kind: str | None = None
    message_strategy: str | None = None
    conversion_mechanism: str | None = None
    visits: int = Field(default=0, ge=0)
    joins: int = Field(default=0, ge=0)
    bot_starts: int = Field(default=0, ge=0)
    story_views: int = Field(default=0, ge=0)
    signups: int = Field(default=0, ge=0)
    activated_users: int = Field(default=0, ge=0)
    paid_users: int = Field(default=0, ge=0)
    replies: int = Field(default=0, ge=0)
    removals: int = Field(default=0, ge=0)
    revenue_usd: float = Field(default=0, ge=0)
    deepest_signal: TelegramProfileLearningSignal
    deepest_signal_count: int = Field(default=0, ge=0)
    analytics_available: bool = True


class TelegramProfileLearningView(BaseModel):
    project_id: UUID
    rows: list[TelegramProfileLearningRow]
    experiment_count: int = Field(ge=0)
    measured_experiment_count: int = Field(ge=0)
    generated_at: datetime


class TelegramProfileLearningService:
    def __init__(
        self,
        *,
        pack_service=None,
        execution_service=None,
        analytics_service=None,
        native_attribution_service=None,
    ) -> None:
        self._packs = pack_service or telegram_profile_conversion_pack_service
        self._execution = execution_service or distribution_execution_service
        self._analytics = analytics_service or distribution_analytics_service
        self._native = native_attribution_service or telegram_native_attribution_service

    def overview(
        self,
        project_id: UUID,
        customer_token: str,
    ) -> TelegramProfileLearningView:
        rows: list[TelegramProfileLearningRow] = []
        for pack in self._packs.list(project_id, customer_token):
            if pack.status == TelegramProfilePackStatus.ARCHIVED:
                continue
            try:
                action = self._execution.get_action(pack.action_id)
            except KeyError:
                continue

            native_kind = None
            try:
                native = self._native.get(project_id, customer_token, pack.id)
            except TelegramNativeAttributionError:
                native = None
            if native is not None:
                native_kind = native.kind.value

            message_strategy = self._text_or_none(
                action.content_payload.get("draft_variant")
            )
            conversion_mechanism = self._text_or_none(
                action.content_payload.get("conversion_mechanism")
            )

            analytics_available = True
            try:
                analytics = self._analytics.experiment_analytics(pack.experiment_id)
            except (KeyError, ValueError):
                analytics_available = False
                metrics = None
                replies = 0
                removals = 0
            else:
                metrics = analytics.metrics
                replies = int(analytics.replies)
                removals = int(analytics.removals)

            visits = int(metrics.visits) if metrics is not None else 0
            joins = int(metrics.joins) if metrics is not None else 0
            bot_starts = int(metrics.bot_starts) if metrics is not None else 0
            story_views = int(metrics.story_views) if metrics is not None else 0
            signups = int(metrics.signups) if metrics is not None else 0
            activated = int(metrics.activated_users) if metrics is not None else 0
            paid = int(metrics.paid_users) if metrics is not None else 0
            revenue = float(metrics.revenue) if metrics is not None else 0.0
            deepest_signal, deepest_count = self._deepest_signal(
                replies=replies,
                visits=visits,
                joins=joins,
                bot_starts=bot_starts,
                signups=signups,
                activated=activated,
                paid=paid,
            )

            avatar_sha = pack.avatar.sha256 if pack.avatar is not None else None
            rows.append(
                TelegramProfileLearningRow(
                    pack_id=pack.id,
                    action_id=pack.action_id,
                    experiment_id=pack.experiment_id,
                    pack_status=pack.status,
                    profile_treatment_key=self._profile_treatment_key(pack),
                    display_name=pack.display_name,
                    avatar_sha256=avatar_sha,
                    cta_type=pack.cta_type.value,
                    native_attribution_kind=native_kind,
                    message_strategy=message_strategy,
                    conversion_mechanism=conversion_mechanism,
                    visits=visits,
                    joins=joins,
                    bot_starts=bot_starts,
                    story_views=story_views,
                    signups=signups,
                    activated_users=activated,
                    paid_users=paid,
                    replies=replies,
                    removals=removals,
                    revenue_usd=round(revenue, 2),
                    deepest_signal=deepest_signal,
                    deepest_signal_count=deepest_count,
                    analytics_available=analytics_available,
                )
            )

        rows.sort(key=lambda item: (str(item.experiment_id), str(item.pack_id)))
        return TelegramProfileLearningView(
            project_id=project_id,
            rows=rows,
            experiment_count=len(rows),
            measured_experiment_count=sum(item.analytics_available for item in rows),
            generated_at=datetime.now(UTC),
        )

    @staticmethod
    def _profile_treatment_key(pack) -> str:
        avatar_sha = pack.avatar.sha256 if pack.avatar is not None else None
        payload = {
            "display_name": pack.display_name,
            "bio": pack.bio,
            "cta_type": pack.cta_type.value,
            "cta_value": pack.cta_value,
            "avatar_sha256": avatar_sha,
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _deepest_signal(
        *,
        replies: int,
        visits: int,
        joins: int,
        bot_starts: int,
        signups: int,
        activated: int,
        paid: int,
    ) -> tuple[TelegramProfileLearningSignal, int]:
        for signal, count in (
            (TelegramProfileLearningSignal.PAID, paid),
            (TelegramProfileLearningSignal.ACTIVATED, activated),
            (TelegramProfileLearningSignal.SIGNUP, signups),
            (TelegramProfileLearningSignal.BOT_START, bot_starts),
            (TelegramProfileLearningSignal.JOIN, joins),
            (TelegramProfileLearningSignal.VISIT, visits),
            (TelegramProfileLearningSignal.REPLY, replies),
        ):
            if count > 0:
                return signal, count
        return TelegramProfileLearningSignal.NONE, 0

    @staticmethod
    def _text_or_none(value) -> str | None:
        text = str(value or "").strip()
        return text or None


telegram_profile_learning_service = TelegramProfileLearningService()
