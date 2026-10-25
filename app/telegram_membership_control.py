"""Customer-authorized Telegram membership control plane.

One workflow: preview an already researched public channel's linked group,
receive project-owner approval for exact native IDs, then operator-execute
one membership request. Never sends content, joins arbitrary groups, mutates
profiles, or interprets research fit as publishing permission.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from telethon.tl.functions.channels import JoinChannelRequest

from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.distribution_schemas import AudienceDistributionMapView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_publishing import CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE
from app.telegram_comment_access import preflight_comment_access
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_community_restrictions import telegram_community_restriction_memory
from app.telegram_opportunity_preflight import telegram_opportunity_preflight_service

INTENTS_NS = "telegram_linked_group_join_intents_v1"
ATTEMPTS_NS = "telegram_linked_group_join_attempts_v1"
TTL = timedelta(minutes=20)
MAX_DAILY_JOINS = 3
HANDLE_PATTERN = re.compile(r"^[a-zA-Z0-9_]{5,32}$")


class TelegramMembershipError(ValueError):
    pass


class TelegramMembershipPreviewRequest(BaseModel):
    handle: str = Field(min_length=5, max_length=32)


class TelegramMembershipApproveRequest(BaseModel):
    confirm_join: bool
    expected_channel_id: int = Field(gt=0)
    expected_linked_chat_id: int = Field(gt=0)


class TelegramMembershipService:
    def __init__(self, store: RuntimeStateStore | None = None, *, client_factory=None,
                 access_checker=None, joiner=None, restriction_checker=None):
        self.store = store or get_runtime_store()
        self.client_factory = client_factory or self._connected_client
        self.access_checker = access_checker or preflight_comment_access
        self.joiner = joiner or self._join_exact_entity
        self.restriction_checker = (
            restriction_checker or telegram_community_restriction_memory.get
        )

    def _validate_project(self, project_id: UUID) -> UUID:
        project = self.store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
        if not isinstance(project, dict) or project.get("deleted_at"):
            raise TelegramMembershipError("PROJECT_NOT_ACTIVE")
        if (project.get("channel_publisher_modes") or {}).get("TELEGRAM") != "CLIENT_OWNED":
            raise TelegramMembershipError("CLIENT_OWNED_TELEGRAM_REQUIRED")
        connection = self.store.get(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(project_id))
        if not isinstance(connection, dict) or connection.get("status") != "ACTIVE":
            raise TelegramMembershipError("TELEGRAM_CONNECTION_REQUIRED")
        raw_id = str(project.get("product_id") or "")
        try:
            return UUID(raw_id)
        except ValueError as exc:
            raise TelegramMembershipError("PRODUCT_NOT_CONFIGURED") from exc

    def _saved_channel(self, product_id: UUID, handle: str) -> int:
        raw = self.store.get("audience_distribution_map", str(product_id))
        if not isinstance(raw, dict):
            raise TelegramMembershipError("AUDIENCE_MAP_NOT_FOUND")
        mapping = AudienceDistributionMapView.model_validate(raw)
        candidates = [
            opp for opp in mapping.opportunities
            if opp.platform == DistributionPlatform.TELEGRAM
            and opp.kind == OpportunityKind.CHANNEL
            and str(opp.metadata.get("handle") or "").casefold().lstrip("@") == handle
        ]
        if len(candidates) != 1:
            raise TelegramMembershipError("SAVED_CHANNEL_NOT_UNIQUE")
        meta = candidates[0].metadata
        if (meta.get("research_audience_status") != "DIRECT_CONFIRMED"
                or meta.get("native_research_status") != "VERIFIED"):
            raise TelegramMembershipError("AUDIENCE_OR_NATIVE_ID_NOT_VERIFIED")
        entity_id = int(meta.get("telegram_entity_id") or 0)
        if entity_id <= 0:
            raise TelegramMembershipError("NATIVE_ENTITY_ID_MISSING")
        registry = TelegramCommunityRegistry(self.store)
        rows = [
            row for row in registry.list_for_product(product_id)
            if str(registry.hydrate_metadata(row).get("handle") or "").casefold() == handle
        ]
        if len(rows) != 1 or (rows[0].get("audience_fit") or {}).get("confirmed") is not True:
            raise TelegramMembershipError("REGISTRY_AUDIENCE_NOT_CONFIRMED")
        registry_id = int(registry.hydrate_metadata(rows[0]).get("telegram_entity_id") or 0)
        if registry_id != entity_id:
            raise TelegramMembershipError("SAVED_NATIVE_ID_MISMATCH")
        return entity_id

    def _connected_client(self, project_id: UUID):
        client, error = telegram_opportunity_preflight_service._client_for_project(
            project_id, "", None, "CHANNEL_COMMENT"
        )
        if error is not None or client is None:
            raise TelegramMembershipError("CUSTOMER_SESSION_UNAVAILABLE")
        return client

    @staticmethod
    async def _join_exact_entity(client, linked_entity):
        # The entity was checked against the owner's approved linked chat ID.
        return await client(JoinChannelRequest(linked_entity))

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def _get(self, project_id: UUID, intent_id: UUID) -> dict:
        record = self.store.get(INTENTS_NS, str(intent_id))
        if not isinstance(record, dict) or record.get("project_id") != str(project_id):
            raise TelegramMembershipError("JOIN_INTENT_NOT_FOUND")
        return record

    @staticmethod
    def _public(record: dict) -> dict:
        return {key: record.get(key) for key in (
            "id", "project_id", "channel_handle", "channel_id", "linked_chat_id",
            "status", "reason", "created_at", "expires_at", "approved_at",
            "executed_at", "joined", "write_access",
        ) if key in record}

    async def preview(self, project_id: UUID, handle: str) -> dict:
        handle = handle.strip().lstrip("@").casefold()
        if not HANDLE_PATTERN.fullmatch(handle):
            raise TelegramMembershipError("INVALID_PUBLIC_HANDLE")
        product_id = self._validate_project(project_id)
        channel_id = self._saved_channel(product_id, handle)
        if self.restriction_checker(project_id, handle) is not None:
            raise TelegramMembershipError("ACCOUNT_COMMUNITY_RESTRICTED")

        client = self.client_factory(project_id)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise TelegramMembershipError("SESSION_NOT_AUTHORIZED")
            channel = await client.get_entity("@" + handle)
            if int(getattr(channel, "id", 0) or 0) != channel_id:
                raise TelegramMembershipError("CHANNEL_ENTITY_CHANGED")
            access = await self.access_checker(client, channel)
        finally:
            await client.disconnect()
        if access.status not in {"JOIN_REQUIRED", "READY"}:
            raise TelegramMembershipError("LINKED_DISCUSSION_" + access.status)
        if not access.linked_chat_id or access.linked_chat_id <= 0:
            raise TelegramMembershipError("LINKED_DISCUSSION_UNAVAILABLE")

        now = self._now()
        intent_id = uuid4()
        record = {
            "id": str(intent_id), "project_id": str(project_id),
            "product_id": str(product_id), "channel_handle": handle,
            "channel_id": channel_id, "linked_chat_id": access.linked_chat_id,
            "status": "AWAITING_CUSTOMER_APPROVAL" if access.status == "JOIN_REQUIRED"
                      else "ALREADY_MEMBER",
            "created_at": now.isoformat(),
            "expires_at": (now + TTL).isoformat(),
            "joined": False, "write_access": (
                "READY" if access.status == "READY" else "JOIN_REQUIRED"
            ),
        }
        if not self.store.put_if_absent(INTENTS_NS, str(intent_id), record):
            raise TelegramMembershipError("JOIN_INTENT_COLLISION")
        return self._public(record)

    def approve(self, project_id: UUID, intent_id: UUID,
                payload: TelegramMembershipApproveRequest) -> dict:
        # Customer authentication and project ownership enforced at route boundary.
        self._validate_project(project_id)
        record = self._get(project_id, intent_id)
        if record["status"] != "AWAITING_CUSTOMER_APPROVAL":
            raise TelegramMembershipError("INTENT_NOT_APPROVABLE")
        if datetime.fromisoformat(record["expires_at"]) <= self._now():
            raise TelegramMembershipError("JOIN_INTENT_EXPIRED")
        if not payload.confirm_join:
            raise TelegramMembershipError("EXPLICIT_JOIN_CONFIRMATION_REQUIRED")
        if (record["channel_id"] != payload.expected_channel_id
                or record["linked_chat_id"] != payload.expected_linked_chat_id):
            raise TelegramMembershipError("EXPECTED_NATIVE_IDS_MISMATCH")
        record.update(status="CUSTOMER_APPROVED", approved_at=self._now().isoformat())
        self.store.put(INTENTS_NS, str(intent_id), record)
        return self._public(record)

    def get(self, project_id: UUID, intent_id: UUID) -> dict:
        self._validate_project(project_id)
        return self._public(self._get(project_id, intent_id))

    async def execute(self, project_id: UUID, intent_id: UUID) -> dict:
        product_id = self._validate_project(project_id)
        record = self._get(project_id, intent_id)
        if record["status"] != "CUSTOMER_APPROVED":
            raise TelegramMembershipError("EXPLICIT_CUSTOMER_APPROVAL_REQUIRED")
        if datetime.fromisoformat(record["expires_at"]) <= self._now():
            raise TelegramMembershipError("JOIN_INTENT_EXPIRED")
        handle = record["channel_handle"]
        if str(product_id) != record["product_id"]:
            raise TelegramMembershipError("PROJECT_PRODUCT_CHANGED")
        if self._saved_channel(product_id, handle) != record["channel_id"]:
            raise TelegramMembershipError("CHANNEL_ID_CHANGED")
        if self.restriction_checker(project_id, handle) is not None:
            raise TelegramMembershipError("ACCOUNT_COMMUNITY_RESTRICTED")
        day = self._now().date().isoformat()
        attempts_today = [
            row for row in self.store.list_namespace(ATTEMPTS_NS)
            if row.get("project_id") == str(project_id)
            and str(row.get("created_at") or "").startswith(day)
        ]
        if len(attempts_today) >= MAX_DAILY_JOINS:
            raise TelegramMembershipError("DAILY_JOIN_LIMIT_REACHED")

        # Durable atomic reservation BEFORE the first possible Telegram join.
        # An ambiguous remote result cannot be retried by replaying this action.
        if not self.store.put_if_absent(ATTEMPTS_NS, str(intent_id), {
            "project_id": str(project_id), "status": "IN_PROGRESS",
            "created_at": self._now().isoformat(),
        }):
            raise TelegramMembershipError("JOIN_ALREADY_ATTEMPTED_NO_AUTO_RETRY")

        status, joined, write_access, reason = "UNKNOWN", False, "UNKNOWN", None
        client = None
        try:
            client = self.client_factory(project_id)
            await client.connect()
            if not await client.is_user_authorized():
                raise TelegramMembershipError("SESSION_NOT_AUTHORIZED")
            channel = await client.get_entity("@" + handle)
            if int(getattr(channel, "id", 0) or 0) != record["channel_id"]:
                raise TelegramMembershipError("CHANNEL_ENTITY_CHANGED")
            access = await self.access_checker(client, channel)
            if access.linked_chat_id != record["linked_chat_id"]:
                raise TelegramMembershipError("LINKED_DISCUSSION_CHANGED")
            if access.status == "READY":
                status, write_access = "ALREADY_MEMBER", "READY"
            elif access.status != "JOIN_REQUIRED":
                status, reason = "BLOCKED", access.status
            else:
                linked = access._linked_entity
                if (linked is None or int(getattr(linked, "id", 0) or 0)
                        != record["linked_chat_id"]):
                    raise TelegramMembershipError("LINKED_ENTITY_ID_MISMATCH")
                # The only mutating Telegram call uses the EXACT previewed
                # entity. A second fresh preflight must not replace the target.
                try:
                    await self.joiner(client, linked)
                except Exception as exc:
                    checked = await self.access_checker(client, channel)
                    if (checked.linked_chat_id == record["linked_chat_id"]
                            and checked.status == "READY"):
                        joined, write_access, status = True, "READY", "JOINED_AND_READY"
                    else:
                        status, reason = "INDETERMINATE", type(exc).__name__
                else:
                    checked = await self.access_checker(client, channel)
                    if checked.linked_chat_id != record["linked_chat_id"]:
                        status, reason = "INDETERMINATE", "LINKED_DISCUSSION_CHANGED"
                    else:
                        joined, write_access = True, checked.status
                        status = ("JOINED_AND_READY" if checked.status == "READY"
                                  else "JOINED_BUT_RESTRICTED")
        except TelegramMembershipError as exc:
            status, reason = "BLOCKED", str(exc)
        except Exception as exc:
            status, reason = "INDETERMINATE", type(exc).__name__
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass
        record.update(status=status, joined=joined, write_access=write_access,
                      reason=reason, executed_at=self._now().isoformat())
        self.store.put(INTENTS_NS, str(intent_id), record)
        self.store.put(ATTEMPTS_NS, str(intent_id), {
            "project_id": str(project_id), "status": status,
            "created_at": day, "executed_at": record["executed_at"],
        })
        return self._public(record)


telegram_membership_service = TelegramMembershipService()
