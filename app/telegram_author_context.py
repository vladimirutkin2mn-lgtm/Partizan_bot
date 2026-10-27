"""Read public-facing identity of an already connected Telegram publisher.

No login, profile mutation, joining or posting. A product may belong to more
than one project; never guess which customer's Telegram identity to use.
The live profile reader is injected for tests and invoked only for the unique
active, matching customer-owned account.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_publishing import (
    CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
    customer_telegram_client_publish_service,
)

_PROFILE_READ_TIMEOUT_SECONDS = 8


class ConnectedTelegramAuthorContext:
    def __init__(self, *, store: RuntimeStateStore | None = None, reader=None):
        self._store = store if store is not None else get_runtime_store()
        self._reader = (
            reader if reader is not None
            else customer_telegram_client_publish_service.profile_internal
        )

    def _unique_project_connection(self, product_id: UUID):
        matching: list[tuple[UUID, dict]] = []
        for connection in self._store.list_namespace(
            CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE
        ):
            if not isinstance(connection, dict) or connection.get("status") != "ACTIVE":
                continue
            try:
                project_id = UUID(str(connection["project_id"]))
            except (KeyError, ValueError, TypeError):
                continue
            project = self._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
            if not isinstance(project, dict):
                continue
            if (
                project.get("deleted_at")
                or str(project.get("product_id") or "") != str(product_id)
            ):
                continue
            modes = project.get("channel_publisher_modes")
            if isinstance(modes, dict):
                selected = str(modes.get("TELEGRAM") or "")
                if selected and selected != "CLIENT_OWNED":
                    continue
            matching.append((project_id, connection))
            if len(matching) > 1:
                # No cross-project mixing of identity, bio or persona.
                return None
        return matching[0] if len(matching) == 1 else None

    async def for_product(self, product_id: UUID) -> dict | None:
        match = self._unique_project_connection(product_id)
        if match is None:
            return None
        project_id, connection = match
        # Public identity fields were saved at successful Telegram sign-in.
        # Never pass session secrets, project identifiers or phone numbers.
        username = str(connection.get("username") or "").strip()[:32]
        display_name = str(connection.get("display_name") or "").strip()[:120]
        bio = ""
        status = "connected_identity_only"
        try:
            profile = await asyncio.wait_for(
                self._reader(project_id), timeout=_PROFILE_READ_TIMEOUT_SECONDS
            )
            current_username = str(getattr(profile, "username", "") or "").strip()[:32]
            current_name = str(getattr(profile, "display_name", "") or "").strip()[:120]
            # The customer can legitimately rename an account after login.
            # The session remains scoped to the uniquely selected project.
            username = current_username or username
            display_name = current_name or display_name
            bio = str(getattr(profile, "about", "") or "").strip()[:512]
            status = "live_profile_read"
        except Exception:
            # An expired/read-failing Telegram session should not change the
            # selected author into another identity or break drafting.
            # Only the saved identity is supplied, without guessed bio.
            pass
        if not (username or display_name or bio):
            return None
        return {
            "source": "connected_customer_telegram_account",
            "profile_read_status": status,
            "username": username,
            "display_name": display_name,
            "profile_bio": bio,
        }


connected_telegram_author_context = ConnectedTelegramAuthorContext()
