"""Autonomous Telegram membership recovery during an *authorized* publish.

A preflight JOIN_REQUIRED is actionable only with a separately granted
community-joins scope, fresh native evidence and a verified community policy.
Discovery never joins; publishing never silently joins without these gates.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from telethon.tl.functions.channels import JoinChannelRequest

from app.distribution_schemas import AudienceDistributionMapView, DistributionActionView
from app.distribution_types import (
    DistributionActionStatus,
    DistributionActionType,
    DistributionPlatform,
    OpportunityKind,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_comment_access import preflight_comment_access
from app.telegram_community_restrictions import telegram_community_restriction_memory
from app.telegram_opportunity_preflight import (
    TelegramOpportunityPreflightService,
    telegram_opportunity_preflight_service,
)

AUTONOMOUS_TELEGRAM_JOIN_ATTEMPTS_NS = "telegram_autonomous_join_attempts_v1"
_AUTONOMOUS_MAX_JOINS_PER_DAY = 3
_MAX_NATIVE_EVIDENCE_AGE = timedelta(hours=24)


class TelegramAutonomousJoinBlocked(ValueError):
    """Expected no-join result. Does not grant permission to retry."""


class TelegramAutonomousMembershipRecovery:
    def __init__(
        self, store: RuntimeStateStore | None = None, *,
        preflight: TelegramOpportunityPreflightService | None = None,
        restriction_checker=None,
        joiner=None,
    ) -> None:
        self.store = store or get_runtime_store()
        self.preflight = preflight or telegram_opportunity_preflight_service
        self.restriction_checker = (
            restriction_checker or telegram_community_restriction_memory.get
        )
        self.joiner = joiner or self._join_once

    @staticmethod
    async def _join_once(client, entity):
        await client(JoinChannelRequest(entity))

    @staticmethod
    def _mode(action_type: DistributionActionType) -> str:
        return {
            DistributionActionType.COMMENT: "CHANNEL_COMMENT",
            DistributionActionType.REPLY: "GROUP_REPLY",
            DistributionActionType.STANDALONE_POST: "STANDALONE_POST",
        }.get(action_type, "")

    def _verified_opportunity(self, product_id: UUID, action: DistributionActionView,
                              mode: str) -> tuple[str, int]:
        raw = self.store.get("audience_distribution_map", str(product_id))
        if not isinstance(raw, dict):
            raise TelegramAutonomousJoinBlocked("AUDIENCE_MAP_MISSING")
        mapping = AudienceDistributionMapView.model_validate(raw)
        selected = [opp for opp in mapping.opportunities if opp.id == action.opportunity_id]
        if len(selected) != 1 or selected[0].platform != DistributionPlatform.TELEGRAM:
            raise TelegramAutonomousJoinBlocked("OPPORTUNITY_NOT_VERIFIED")
        opp = selected[0]
        if (mode == "CHANNEL_COMMENT") != (opp.kind == OpportunityKind.CHANNEL):
            raise TelegramAutonomousJoinBlocked("OPPORTUNITY_KIND_MISMATCH")
        if mode != "CHANNEL_COMMENT" and opp.kind != OpportunityKind.GROUP:
            raise TelegramAutonomousJoinBlocked("OPPORTUNITY_KIND_MISMATCH")
        meta = opp.metadata
        expected_url = str(action.target_url or "").rstrip("/")
        if (
            meta.get("native_research_status") != "VERIFIED"
            or str(meta.get("action_target_mode") or "") != mode
            or str(meta.get("action_target_url") or "").rstrip("/") != expected_url
            or not meta.get("community_fit_pass")
            or str(meta.get("research_audience_status") or "") != "DIRECT_CONFIRMED"
        ):
            raise TelegramAutonomousJoinBlocked("NATIVE_TARGET_OR_AUDIENCE_NOT_VERIFIED")
        # Membership is a material provider write. Don't infer the rules from
        # the mere presence of comments, or from an LLM audience label.
        rules = meta.get("community_commercial_rules")
        if not (
            isinstance(rules, dict)
            and rules.get("status") == "VERIFIED_ALLOWED"
            and rules.get("allows_participation") is True
            and rules.get("allows_promotion") is True
            and str(rules.get("source_url") or "").startswith("https://")
        ):
            raise TelegramAutonomousJoinBlocked("COMMERCIAL_COMMUNITY_RULES_UNVERIFIED")

        checked = meta.get("research_last_native_checked_at") or meta.get("source_checked_at")
        try:
            date = datetime.fromisoformat(str(checked))
            date = date if date.tzinfo else date.replace(tzinfo=UTC)
            if not timedelta(0) <= datetime.now(UTC) - date <= _MAX_NATIVE_EVIDENCE_AGE:
                raise ValueError("Out of window")
        except (TypeError, ValueError) as exc:
            raise TelegramAutonomousJoinBlocked("LATEST_NATIVE_EVIDENCE_STALE") from exc

        handle = str(meta.get("handle") or "").lstrip("@").lower()
        entity_id = int(meta.get("telegram_entity_id") or 0)
        if entity_id <= 0:
            raise TelegramAutonomousJoinBlocked("NATIVE_ENTITY_ID_MISSING")
        parts = urlsplit(expected_url)
        segments = [p for p in parts.path.split("/") if p]
        if (
            parts.scheme != "https" or parts.netloc not in {"t.me", "telegram.me"}
            or not segments or segments[0].lower() != handle
            or (mode == "STANDALONE_POST" and len(segments) != 1)
            or (mode != "STANDALONE_POST" and (
                len(segments) != 2 or not segments[1].isdigit()
            ))
        ):
            raise TelegramAutonomousJoinBlocked("TARGET_HANDLE_OR_URL_CHANGED")
        return handle, entity_id

    async def ensure_ready(
        self, *, project_id: UUID, product_id: UUID,
        action: DistributionActionView, allow_community_joins: bool,
    ) -> str:
        mode = self._mode(action.action_type)
        if (not mode or action.status != DistributionActionStatus.APPROVED
                or action.platform != DistributionPlatform.TELEGRAM):
            raise TelegramAutonomousJoinBlocked("ACTION_NOT_APPROVED_FOR_TELEGRAM")
        target = str(action.target_url or "")
        first = await self.preflight.check_target(project_id, target, mode)
        if first.status == "READY":
            return "READY"
        if first.status != "JOIN_REQUIRED":
            raise TelegramAutonomousJoinBlocked(f"WRITE_PREFLIGHT_{first.status}")
        if not allow_community_joins:
            raise TelegramAutonomousJoinBlocked("AUTONOMOUS_JOIN_NOT_AUTHORIZED")

        handle, channel_id = self._verified_opportunity(product_id, action, mode)
        if self.restriction_checker(project_id, handle) is not None:
            raise TelegramAutonomousJoinBlocked("KNOWN_ACCOUNT_COMMUNITY_RESTRICTION")
        day = datetime.now(UTC).date().isoformat()
        attempts = [
            row for row in self.store.list_namespace(AUTONOMOUS_TELEGRAM_JOIN_ATTEMPTS_NS)
            if row.get("project_id") == str(project_id)
            and str(row.get("at") or "").startswith(day)
        ]
        if len(attempts) >= _AUTONOMOUS_MAX_JOINS_PER_DAY:
            raise TelegramAutonomousJoinBlocked("AUTONOMOUS_DAILY_JOIN_LIMIT")
        if mode == "CHANNEL_COMMENT":
            if first.linked_chat_id is None or first.linked_chat_id <= 0:
                raise TelegramAutonomousJoinBlocked("LINKED_DISCUSSION_UNRESOLVED")
            destination_id = first.linked_chat_id
        else:
            destination_id = channel_id

        # One irreversible attempt per group/connected project, independent
        # of how many actions reference it. Reserve before any Telegram writes.
        attempt_key = f"{project_id}:{destination_id}"
        if not self.store.put_if_absent(
            AUTONOMOUS_TELEGRAM_JOIN_ATTEMPTS_NS, attempt_key,
            {"project_id": str(project_id), "target_id": destination_id,
             "at": datetime.now(UTC).isoformat(), "status": "IN_PROGRESS"},
        ):
            raise TelegramAutonomousJoinBlocked("PREVIOUS_JOIN_ATTEMPT_REQUIRES_REVIEW")

        client, error = self.preflight._client_for_project(project_id, handle, None, mode)
        status = "INDETERMINATE"
        try:
            if error is not None or client is None:
                raise TelegramAutonomousJoinBlocked("ACCOUNT_SESSION_UNAVAILABLE")
            await client.connect()
            if not await client.is_user_authorized():
                raise TelegramAutonomousJoinBlocked("ACCOUNT_SESSION_UNAUTHORIZED")
            entity = await client.get_entity("@" + handle)
            if int(getattr(entity, "id", 0) or 0) != channel_id:
                raise TelegramAutonomousJoinBlocked("NATIVE_ENTITY_ID_CHANGED")
            if mode == "CHANNEL_COMMENT":
                current = await preflight_comment_access(client, entity)
                if current.linked_chat_id != destination_id:
                    raise TelegramAutonomousJoinBlocked("LINKED_DISCUSSION_CHANGED")
                if current.status != "JOIN_REQUIRED":
                    raise TelegramAutonomousJoinBlocked("JOIN_NOT_CURRENTLY_REQUIRED")
                join_entity = current._linked_entity
                if join_entity is None or int(join_entity.id) != destination_id:
                    raise TelegramAutonomousJoinBlocked("LINKED_ENTITY_ID_CHANGED")
            else:
                if not bool(getattr(entity, "megagroup", False)):
                    raise TelegramAutonomousJoinBlocked("TARGET_IS_NOT_A_GROUP")
                group_status, _reason = await self.preflight._group_write_status(
                    client, entity
                )
                if group_status != "JOIN_REQUIRED":
                    raise TelegramAutonomousJoinBlocked("JOIN_NOT_CURRENTLY_REQUIRED")
                join_entity = entity

            await self.joiner(client, join_entity)
            status = "JOIN_ATTEMPTED"
        except Exception as exc:
            status = f"BLOCKED_{type(exc).__name__}"
            raise TelegramAutonomousJoinBlocked(
                str(exc) if isinstance(exc, TelegramAutonomousJoinBlocked)
                else "AUTONOMOUS_JOIN_REMOTE_RESULT_UNCERTAIN"
            ) from exc
        finally:
            if client is not None:
                await self.preflight._disconnect_safely(client)
            self.store.put(AUTONOMOUS_TELEGRAM_JOIN_ATTEMPTS_NS, attempt_key, {
                "project_id": str(project_id), "target_id": destination_id,
                "at": datetime.now(UTC).isoformat(), "status": status,
            })

        # New read-only session and target-specific preflight; joining is never
        # enough to automatically conclude that content can be published.
        second = await self.preflight.check_target(project_id, target, mode)
        if second.status != "READY":
            raise TelegramAutonomousJoinBlocked(
                f"POST_JOIN_WRITE_PREFLIGHT_{second.status}"
            )
        return "JOINED_AND_READY"


telegram_autonomous_membership_recovery = TelegramAutonomousMembershipRecovery()
