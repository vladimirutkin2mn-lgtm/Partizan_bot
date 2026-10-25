"""Membership endpoints require project ownership or operator auth, never both bypasses."""
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app import telegram_membership_routes as routes
from app.customer_account import CUSTOMER_ACCOUNT_SESSION_COOKIE, CustomerAccountAuthenticationError
from app.operator_auth import require_operator

PROJECT = uuid4()
INTENT = uuid4()


class StubMembership:
    async def preview(self, project_id, handle):
        assert project_id == PROJECT
        assert handle == "bizarreclubmsk"
        return {"status": "AWAITING_CUSTOMER_APPROVAL", "linked_chat_id": 54}

    def approve(self, project_id, intent_id, payload):
        assert project_id == PROJECT
        assert intent_id == INTENT
        assert payload.confirm_join is True
        return {"status": "CUSTOMER_APPROVED"}

    def get(self, project_id, intent_id):
        assert project_id == PROJECT and intent_id == INTENT
        return {"status": "CUSTOMER_APPROVED"}

    async def execute(self, project_id, intent_id):
        assert project_id == PROJECT and intent_id == INTENT
        return {"status": "JOINED_AND_READY"}


@pytest.mark.asyncio
async def test_customer_join_preview_requires_authenticated_project_owner(monkeypatch):
    app = FastAPI()
    app.include_router(routes.customer_router)
    monkeypatch.setattr(routes, "telegram_membership_service", StubMembership())
    def check_owner(*, session_token, project_id):
        if session_token != "owner":
            raise CustomerAccountAuthenticationError("No session")
        assert project_id == PROJECT
        return ({}, "opaque-customer-token")
    monkeypatch.setattr(routes.customer_account_service, "project_access", check_owner)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        path = f"/customer/workspace/{PROJECT}/telegram/linked-group-joins/preview"
        unauthorized = await client.post(path, json={"handle": "bizarreclubmsk"})
        assert unauthorized.status_code == 401
        verified = await client.post(
            path, json={"handle": "bizarreclubmsk"},
            cookies={CUSTOMER_ACCOUNT_SESSION_COOKIE: "owner"},
        )
        assert verified.status_code == 200
        assert verified.json()["linked_chat_id"] == 54


@pytest.mark.asyncio
async def test_operator_only_execution_remains_separate(monkeypatch):
    app = FastAPI()
    app.include_router(routes.operator_router)
    monkeypatch.setattr(routes, "telegram_membership_service", StubMembership())
    path = f"/v1/telegram/membership/{PROJECT}/linked-group-joins/{INTENT}/execute"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # In production, require_operator enforces the environment's operator key.
        # This verifies the route inherits the dependency rather than bypassing it.
        assert any(
            dependency.call is require_operator
            for route in app.routes
            if getattr(route, "path", "") ==
            "/v1/telegram/membership/{project_id}/linked-group-joins/{intent_id}/execute"
            for dependency in getattr(route, "dependant", None).dependencies
        )
        app.dependency_overrides[require_operator] = lambda: None
        result = await client.post(path)
        assert result.status_code == 200
        assert result.json()["status"] == "JOINED_AND_READY"
