from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from uuid import UUID

from app.customer_funnel import (
    CUSTOMER_PROJECT_NAMESPACE,
    CustomerProjectAccessError,
    CustomerProjectNotFoundError,
)
from app.runtime_store import get_runtime_store


def recover_paid_customer_access(project_id: UUID, stripe_checkout_session_id: str) -> str:
    """Rotate the browser project token after Stripe has independently verified payment."""

    store = get_runtime_store()
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
    if project is None:
        raise CustomerProjectNotFoundError(project_id)
    if not project.get("launch_unlocked"):
        raise CustomerProjectAccessError(project_id)
    if project.get("stripe_checkout_session_id") != stripe_checkout_session_id:
        raise CustomerProjectAccessError(project_id)

    customer_token = secrets.token_urlsafe(32)
    project["customer_token_hash"] = hashlib.sha256(customer_token.encode()).hexdigest()
    project["access_recovered_at"] = datetime.now(UTC).isoformat()
    project["updated_at"] = datetime.now(UTC).isoformat()
    store.put(CUSTOMER_PROJECT_NAMESPACE, str(project_id), project)
    return customer_token
