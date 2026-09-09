from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, HttpUrl

from app.audience_intelligence_service import audience_intelligence_service
from app.channel_execution import PublisherMode
from app.config import Settings, get_settings
from app.customer_funnel import customer_funnel_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_schemas import DistributionActionExecutionRequest
from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import (
    DistributionActionStatus,
    DistributionActionType,
    DistributionPlatform,
)
from app.product_intake import product_intake_service
from app.provider_secret_store import (
    REDDIT_OAUTH_SECRET_PREFIX,
    ProviderSecretConfigurationError,
    ProviderSecretStore,
    provider_secret_store,
)
from app.reddit_research import action_target_is_fresh, policy_freshness_reason
from app.runtime_store import RuntimeStateStore, get_runtime_store

CUSTOMER_REDDIT_OAUTH_STATE_NAMESPACE = "customer_reddit_oauth_state"
CUSTOMER_REDDIT_CONNECTION_NAMESPACE = "customer_reddit_connection"
CUSTOMER_REDDIT_PUBLISH_RECEIPT_NAMESPACE = "customer_reddit_publish_receipt"
CUSTOMER_REDDIT_PUBLISH_GUARD_NAMESPACE = "customer_reddit_publish_guard"
CUSTOMER_REDDIT_OBSERVATION_NAMESPACE = "customer_reddit_publish_observation"

_REQUIRED_SCOPES = frozenset({"identity", "read", "submit"})
_STATE_TTL = timedelta(minutes=15)
_DUPLICATE_WINDOW = timedelta(hours=24)
_DAILY_WINDOW = timedelta(hours=24)
_MIN_PUBLISH_INTERVAL = timedelta(minutes=2)
_MAX_PUBLISHES_PER_DAY = 5
_MAX_CONTENT_LENGTH = 10_000
_MAX_TITLE_LENGTH = 300
_MAX_OBSERVATION_HISTORY = 50
_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)


class CustomerRedditClientPublishError(RuntimeError):
    pass


class RedditClientPublishTransportError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        restriction_signal: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.restriction_signal = restriction_signal
        self.retry_after_seconds = retry_after_seconds


class RedditConnectionStatus(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    ACTIVE = "ACTIVE"


class RedditClientPublishOutcome(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


class RedditRemoteState(StrEnum):
    PRESENT = "PRESENT"
    REMOVED = "REMOVED"
    INACCESSIBLE = "INACCESSIBLE"
    UNKNOWN = "UNKNOWN"


class RedditOAuthStartView(BaseModel):
    authorization_url: HttpUrl
    expires_at: datetime


class RedditConnectionView(BaseModel):
    status: RedditConnectionStatus
    username: str | None = None
    scopes: list[str] = Field(default_factory=list)
    connected_at: datetime | None = None
    last_verified_at: datetime | None = None


class RedditPublishRequest(BaseModel):
    retry: bool = False


class RedditClientPublishReceipt(BaseModel):
    action_id: UUID
    outcome: RedditClientPublishOutcome
    message: str
    external_reference: str | None = Field(default=None, max_length=500)
    executed_url: HttpUrl | None = None
    published_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime


class RedditObservationEvent(BaseModel):
    state: RedditRemoteState
    score: int | None = None
    reply_count: int | None = None
    restriction_signal: str | None = None
    checked_at: datetime


class RedditPublishObservationView(BaseModel):
    action_id: UUID
    state: RedditRemoteState | None = None
    score: int | None = None
    reply_count: int | None = None
    restriction_signal: str | None = None
    checked_at: datetime | None = None
    executed_url: HttpUrl | None = None
    history: list[RedditObservationEvent] = Field(default_factory=list)


class RedditTokenBundle(BaseModel):
    access_token: str
    refresh_token: str
    scopes: list[str]
    expires_at: datetime


class RedditIdentity(BaseModel):
    username: str


class RedditPublishTarget(BaseModel):
    subreddit: str
    parent_fullname: str | None = None
    thread_post_id: str | None = None
    title: str | None = None


class RedditPublishResult(BaseModel):
    fullname: str
    url: HttpUrl
    published_at: datetime


class RedditObservationResult(BaseModel):
    state: RedditRemoteState
    score: int | None = None
    reply_count: int | None = None
    restriction_signal: str | None = None


class RedditClientPublishTransport(Protocol):
    async def exchange_code(self, *, code: str, redirect_uri: str) -> RedditTokenBundle: ...

    async def refresh(self, refresh_token: str) -> RedditTokenBundle: ...

    async def identity(self, access_token: str) -> RedditIdentity: ...

    async def publish(
        self,
        *,
        access_token: str,
        target: RedditPublishTarget,
        action_type: DistributionActionType,
        text: str,
    ) -> RedditPublishResult: ...

    async def observe(self, *, access_token: str, fullname: str) -> RedditObservationResult: ...


class HttpxRedditClientPublishTransport:
    """Minimal OAuth transport: identity, submit/comment, and exact-item read only."""

    def __init__(self, settings: Settings | None = None, timeout_seconds: float = 20.0) -> None:
        self._settings = settings or get_settings()
        self._timeout = timeout_seconds

    async def exchange_code(self, *, code: str, redirect_uri: str) -> RedditTokenBundle:
        payload = await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            }
        )
        return self._token_bundle(payload, require_refresh=True)

    async def refresh(self, refresh_token: str) -> RedditTokenBundle:
        payload = await self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )
        bundle = self._token_bundle(payload, require_refresh=False)
        if not bundle.refresh_token:
            bundle = bundle.model_copy(update={"refresh_token": refresh_token})
        return bundle

    async def identity(self, access_token: str) -> RedditIdentity:
        payload = await self._api_request("GET", "/api/v1/me", access_token=access_token)
        username = str(payload.get("name") or "").strip()
        if not username:
            raise RedditClientPublishTransportError("IDENTITY_NOT_AVAILABLE")
        return RedditIdentity(username=username)

    async def publish(
        self,
        *,
        access_token: str,
        target: RedditPublishTarget,
        action_type: DistributionActionType,
        text: str,
    ) -> RedditPublishResult:
        if action_type == DistributionActionType.STANDALONE_POST:
            if not target.title:
                raise RedditClientPublishTransportError("POST_TITLE_REQUIRED")
            payload = await self._api_request(
                "POST",
                "/api/submit",
                access_token=access_token,
                data={
                    "api_type": "json",
                    "kind": "self",
                    "sr": target.subreddit,
                    "title": target.title,
                    "text": text,
                    "resubmit": "true",
                    "sendreplies": "true",
                },
            )
        elif action_type in {DistributionActionType.COMMENT, DistributionActionType.REPLY}:
            if not target.parent_fullname:
                raise RedditClientPublishTransportError("PARENT_REQUIRED")
            payload = await self._api_request(
                "POST",
                "/api/comment",
                access_token=access_token,
                data={
                    "api_type": "json",
                    "thing_id": target.parent_fullname,
                    "text": text,
                },
            )
        else:
            raise RedditClientPublishTransportError("ACTION_NOT_SUPPORTED")

        data = self._json_data(payload)
        things = data.get("things") if isinstance(data, dict) else None
        thing = things[0] if isinstance(things, list) and things else None
        thing_data = thing.get("data") if isinstance(thing, dict) else None
        fullname = str((thing_data or {}).get("name") or "").strip()
        url = str((thing_data or {}).get("url") or "").strip()
        if not fullname or not url:
            raise RedditClientPublishTransportError("PUBLISH_RESULT_NOT_CONFIRMED")
        return RedditPublishResult(
            fullname=fullname,
            url=url,
            published_at=datetime.now(UTC),
        )

    async def observe(self, *, access_token: str, fullname: str) -> RedditObservationResult:
        try:
            payload = await self._api_request(
                "GET",
                "/api/info",
                access_token=access_token,
                params={"id": fullname},
            )
        except RedditClientPublishTransportError as exc:
            if exc.code == "TOKEN_REJECTED":
                return RedditObservationResult(
                    state=RedditRemoteState.INACCESSIBLE,
                    restriction_signal="ACCOUNT_AUTHORIZATION_REVOKED",
                )
            if exc.code == "WRITE_FORBIDDEN":
                return RedditObservationResult(
                    state=RedditRemoteState.INACCESSIBLE,
                    restriction_signal="COMMUNITY_RESTRICTED",
                )
            if exc.code == "RATE_LIMITED":
                return RedditObservationResult(
                    state=RedditRemoteState.UNKNOWN,
                    restriction_signal="RATE_LIMITED",
                )
            return RedditObservationResult(state=RedditRemoteState.UNKNOWN)

        data = payload.get("data") if isinstance(payload, dict) else None
        children = data.get("children") if isinstance(data, dict) else None
        if not isinstance(children, list) or not children:
            return RedditObservationResult(
                state=RedditRemoteState.REMOVED,
                restriction_signal="THING_NOT_RETURNED",
            )
        first = children[0]
        row = first.get("data") if isinstance(first, dict) else None
        if not isinstance(row, dict):
            return RedditObservationResult(state=RedditRemoteState.UNKNOWN)
        removed_by_category = row.get("removed_by_category")
        body = str(row.get("body") or row.get("selftext") or "")
        if removed_by_category or body in {"[removed]", "[deleted]"}:
            return RedditObservationResult(
                state=RedditRemoteState.REMOVED,
                score=self._optional_int(row.get("score")),
                reply_count=self._optional_int(row.get("num_comments")),
                restriction_signal="THING_REMOVED_OR_UNAVAILABLE",
            )
        return RedditObservationResult(
            state=RedditRemoteState.PRESENT,
            score=self._optional_int(row.get("score")),
            reply_count=self._optional_int(row.get("num_comments")),
        )

    async def _token_request(self, data: dict[str, str]) -> dict:
        client_id = self._settings.reddit_client_publish_client_id
        secret = self._settings.reddit_client_publish_client_secret
        if not client_id or secret is None:
            raise RedditClientPublishTransportError("OAUTH_APP_NOT_CONFIGURED")
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    "https://www.reddit.com/api/v1/access_token",
                    data=data,
                    auth=(client_id, secret.get_secret_value()),
                    headers={"User-Agent": self._user_agent()},
                )
        except httpx.HTTPError:
            raise RedditClientPublishTransportError("OAUTH_REQUEST_FAILED") from None
        return self._response_payload(response, oauth=True)

    async def _api_request(
        self,
        method: str,
        path: str,
        *,
        access_token: str,
        data: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method,
                    f"https://oauth.reddit.com/{path.lstrip('/')}",
                    data=data,
                    params=params,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "User-Agent": self._user_agent(),
                    },
                )
        except httpx.HTTPError:
            raise RedditClientPublishTransportError("API_REQUEST_FAILED") from None
        return self._response_payload(response, oauth=False)

    def _response_payload(self, response: httpx.Response, *, oauth: bool) -> dict:
        if response.status_code == 429:
            retry_after = self._optional_int(response.headers.get("Retry-After"))
            raise RedditClientPublishTransportError(
                "RATE_LIMITED",
                retry_after_seconds=retry_after,
            )
        if response.status_code == 401:
            raise RedditClientPublishTransportError("TOKEN_REJECTED")
        if response.status_code == 403:
            raise RedditClientPublishTransportError(
                "WRITE_FORBIDDEN",
                restriction_signal="COMMUNITY_OR_ACCOUNT_RESTRICTED",
            )
        if response.status_code >= 400:
            raise RedditClientPublishTransportError(
                "OAUTH_REJECTED" if oauth else "API_REJECTED"
            )
        try:
            payload = response.json()
        except ValueError:
            raise RedditClientPublishTransportError("PROVIDER_RESPONSE_INVALID") from None
        if not isinstance(payload, dict):
            raise RedditClientPublishTransportError("PROVIDER_RESPONSE_INVALID")
        errors = payload.get("json", {}).get("errors") if isinstance(payload.get("json"), dict) else None
        if errors:
            raise RedditClientPublishTransportError("API_REJECTED")
        if payload.get("error"):
            raise RedditClientPublishTransportError(
                "OAUTH_REJECTED" if oauth else "API_REJECTED"
            )
        return payload

    def _token_bundle(self, payload: dict, *, require_refresh: bool) -> RedditTokenBundle:
        access_token = str(payload.get("access_token") or "").strip()
        refresh_token = str(payload.get("refresh_token") or "").strip()
        if not access_token or (require_refresh and not refresh_token):
            raise RedditClientPublishTransportError("OAUTH_TOKEN_NOT_CONFIRMED")
        raw_scope = str(payload.get("scope") or "")
        scopes = sorted({part for part in re.split(r"[\s,]+", raw_scope) if part})
        if not _REQUIRED_SCOPES.issubset(scopes):
            raise RedditClientPublishTransportError("REQUIRED_SCOPES_NOT_CONFIRMED")
        expires_in = self._optional_int(payload.get("expires_in")) or 3600
        return RedditTokenBundle(
            access_token=access_token,
            refresh_token=refresh_token,
            scopes=scopes,
            expires_at=datetime.now(UTC) + timedelta(seconds=max(60, expires_in)),
        )

    def _user_agent(self) -> str:
        value = self._settings.reddit_client_publish_user_agent
        if not value:
            raise RedditClientPublishTransportError("USER_AGENT_NOT_CONFIGURED")
        return value

    @staticmethod
    def _json_data(payload: dict) -> dict:
        nested = payload.get("json")
        if not isinstance(nested, dict):
            return {}
        data = nested.get("data")
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _optional_int(value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None


class CustomerRedditClientPublishService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        settings: Settings | None = None,
        secret_store: ProviderSecretStore | None = None,
        transport: RedditClientPublishTransport | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._settings = settings or get_settings()
        self._secret_store = secret_store or provider_secret_store
        self._transport = transport or HttpxRedditClientPublishTransport(self._settings)
        self._publish_lock = asyncio.Lock()

    def readiness_blocker(self) -> str | None:
        if self._settings.reddit_client_publish_provider != "oauth":
            return "Reddit client-owned publishing provider is unavailable"
        if not self._settings.reddit_client_publish_public_ready:
            return "Reddit client-owned publishing is not enabled for customers yet"
        if not self._settings.reddit_commercial_access_verified:
            return "Reddit commercial/API access has not been explicitly verified"
        if (
            not self._settings.reddit_client_publish_client_id
            or self._settings.reddit_client_publish_client_secret is None
        ):
            return "Reddit OAuth application credentials are not configured"
        if not self._settings.reddit_client_publish_user_agent:
            return "Reddit API user agent is not configured"
        if not self._settings.partizan_public_base_url:
            return "PARTIZAN_PUBLIC_BASE_URL is required for Reddit OAuth"
        if self._settings.provider_secret_encryption_key is None:
            return "Encrypted provider secret storage is not configured"
        return None

    def begin_connection(
        self,
        project_id: UUID,
        customer_token: str,
    ) -> RedditOAuthStartView:
        customer_funnel_service.get_project_payload(project_id, customer_token)
        self._require_ready()
        state = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        expires_at = now + _STATE_TTL
        self._store.put(
            CUSTOMER_REDDIT_OAUTH_STATE_NAMESPACE,
            self._state_key(state),
            {
                "project_id": str(project_id),
                "created_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
                "used": False,
            },
        )
        query = urlencode(
            {
                "client_id": self._settings.reddit_client_publish_client_id,
                "response_type": "code",
                "state": state,
                "redirect_uri": self._redirect_uri(),
                "duration": "permanent",
                "scope": " ".join(sorted(_REQUIRED_SCOPES)),
            }
        )
        return RedditOAuthStartView(
            authorization_url=f"https://www.reddit.com/api/v1/authorize?{query}",
            expires_at=expires_at,
        )

    def pending_project(self, state: str) -> UUID | None:
        record = self._store.get(CUSTOMER_REDDIT_OAUTH_STATE_NAMESPACE, self._state_key(state))
        if record is None:
            return None
        try:
            return UUID(str(record["project_id"]))
        except (KeyError, ValueError):
            return None

    async def complete_connection(self, *, state: str, code: str) -> UUID:
        self._require_ready()
        state_key = self._state_key(state)
        record = self._store.get(CUSTOMER_REDDIT_OAUTH_STATE_NAMESPACE, state_key)
        if record is None or record.get("used"):
            raise CustomerRedditClientPublishError("Reddit OAuth state is invalid or already used")
        try:
            expires_at = self._as_utc(datetime.fromisoformat(str(record["expires_at"])))
            project_id = UUID(str(record["project_id"]))
        except (KeyError, ValueError) as exc:
            raise CustomerRedditClientPublishError("Reddit OAuth state is malformed") from exc
        if expires_at <= datetime.now(UTC):
            raise CustomerRedditClientPublishError("Reddit OAuth state has expired")
        record["used"] = True
        self._store.put(CUSTOMER_REDDIT_OAUTH_STATE_NAMESPACE, state_key, record)

        try:
            bundle = await self._transport.exchange_code(
                code=code,
                redirect_uri=self._redirect_uri(),
            )
            self._require_scopes(bundle.scopes)
            identity = await self._transport.identity(bundle.access_token)
        except RedditClientPublishTransportError as exc:
            raise CustomerRedditClientPublishError(
                f"Reddit OAuth connection failed safely ({exc.code})"
            ) from None

        secret_reference = self._secret_store.create_reference(prefix=REDDIT_OAUTH_SECRET_PREFIX)
        self._secret_store.put(secret_reference, bundle.model_dump_json())
        previous = self._store.get(CUSTOMER_REDDIT_CONNECTION_NAMESPACE, str(project_id))
        if previous is not None:
            old_reference = str(previous.get("secret_reference") or "")
            if old_reference:
                self._safe_delete_secret(old_reference)
        now = datetime.now(UTC)
        self._store.put(
            CUSTOMER_REDDIT_CONNECTION_NAMESPACE,
            str(project_id),
            {
                "project_id": str(project_id),
                "status": RedditConnectionStatus.ACTIVE.value,
                "secret_reference": secret_reference,
                "username": identity.username,
                "scopes": sorted(bundle.scopes),
                "connected_at": now.isoformat(),
                "last_verified_at": now.isoformat(),
            },
        )
        self._store.delete(CUSTOMER_REDDIT_OAUTH_STATE_NAMESPACE, state_key)
        return project_id

    def connection(self, project_id: UUID, customer_token: str) -> RedditConnectionView:
        customer_funnel_service.get_project_payload(project_id, customer_token)
        record = self._store.get(CUSTOMER_REDDIT_CONNECTION_NAMESPACE, str(project_id))
        if record is None:
            return RedditConnectionView(status=RedditConnectionStatus.DISCONNECTED)
        return self._connection_view(record)

    def is_connected(self, project_id: UUID) -> bool:
        record = self._store.get(CUSTOMER_REDDIT_CONNECTION_NAMESPACE, str(project_id))
        return bool(record and record.get("status") == RedditConnectionStatus.ACTIVE.value)

    def disconnect(self, project_id: UUID, customer_token: str) -> RedditConnectionView:
        customer_funnel_service.get_project_payload(project_id, customer_token)
        record = self._store.get(CUSTOMER_REDDIT_CONNECTION_NAMESPACE, str(project_id))
        if record is not None:
            reference = str(record.get("secret_reference") or "")
            if reference:
                self._safe_delete_secret(reference)
            self._store.delete(CUSTOMER_REDDIT_CONNECTION_NAMESPACE, str(project_id))
        return RedditConnectionView(status=RedditConnectionStatus.DISCONNECTED)

    async def publish(
        self,
        project_id: UUID,
        customer_token: str,
        action_id: UUID,
        payload: RedditPublishRequest,
    ) -> RedditClientPublishReceipt:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        self._require_ready()
        action = distribution_execution_service.get_action(action_id)
        if action.platform != DistributionPlatform.REDDIT:
            raise CustomerRedditClientPublishError("Action is not a Reddit action")
        if action.action_type not in {
            DistributionActionType.COMMENT,
            DistributionActionType.REPLY,
            DistributionActionType.STANDALONE_POST,
        }:
            raise CustomerRedditClientPublishError(
                "Reddit client publishing does not support this action"
            )
        if action.experiment_id is None:
            raise CustomerRedditClientPublishError("Reddit action has no DistributionExperiment")
        experiment = distribution_execution_service.get_experiment(action.experiment_id)
        if str(project.get("product_id") or "") != str(experiment.product_id):
            raise CustomerRedditClientPublishError(
                "Reddit action does not belong to this customer project"
            )

        existing = self.get_receipt(action_id)
        if existing is not None and not payload.retry:
            return existing
        if existing is not None and existing.outcome == RedditClientPublishOutcome.EXECUTED:
            return existing
        if existing is not None and existing.outcome == RedditClientPublishOutcome.IN_PROGRESS:
            raise CustomerRedditClientPublishError(
                "The previous Reddit publish outcome is unknown; reconcile before retrying"
            )
        if action.status != DistributionActionStatus.APPROVED:
            raise CustomerRedditClientPublishError(
                "Reddit action must be explicitly APPROVED before publishing"
            )
        self._require_client_owned_mode(project)

        connection = self._connection_record(project_id)
        access_token = await self._access_token(connection)
        opportunity = audience_intelligence_service.find_opportunity(action.opportunity_id)
        if opportunity.platform != DistributionPlatform.REDDIT:
            raise CustomerRedditClientPublishError("Reddit action opportunity is not Reddit")
        policy = self._current_policy(action.opportunity_id)
        text = str(action.content_text or "").strip()
        if not text:
            raise CustomerRedditClientPublishError("Reddit action has no approved content")
        if len(text) > _MAX_CONTENT_LENGTH:
            raise CustomerRedditClientPublishError(
                f"Reddit content exceeds the {_MAX_CONTENT_LENGTH}-character safety limit"
            )
        self._enforce_current_policy(
            policy=policy,
            action_type=action.action_type,
            text=text,
            product_id=experiment.product_id,
            content_payload=action.content_payload,
        )
        target = self._parse_target(action, opportunity)
        fingerprint = self._fingerprint(target, text)

        async with self._publish_lock:
            self._enforce_publish_guard(project_id, fingerprint)
            in_progress = RedditClientPublishReceipt(
                action_id=action.id,
                outcome=RedditClientPublishOutcome.IN_PROGRESS,
                message="Reddit client-owned publish attempt started.",
                metadata={
                    "action_type": action.action_type.value,
                    "subreddit": target.subreddit,
                    "parent_fullname": target.parent_fullname,
                },
                created_at=datetime.now(UTC),
            )
            self._persist_receipt(in_progress)
            try:
                result = await self._transport.publish(
                    access_token=access_token,
                    target=target,
                    action_type=action.action_type,
                    text=text,
                )
            except RedditClientPublishTransportError as exc:
                failed = RedditClientPublishReceipt(
                    action_id=action.id,
                    outcome=RedditClientPublishOutcome.FAILED,
                    message="Reddit rejected or failed the client-owned publish attempt.",
                    metadata={
                        "action_type": action.action_type.value,
                        "subreddit": target.subreddit,
                        "error_code": exc.code,
                        "restriction_signal": exc.restriction_signal,
                        "retry_after_seconds": exc.retry_after_seconds,
                    },
                    created_at=datetime.now(UTC),
                )
                self._persist_receipt(failed)
                return failed
            except Exception as exc:
                failed = RedditClientPublishReceipt(
                    action_id=action.id,
                    outcome=RedditClientPublishOutcome.FAILED,
                    message="Reddit client-owned publish failed without a confirmed remote result.",
                    metadata={
                        "action_type": action.action_type.value,
                        "subreddit": target.subreddit,
                        "provider_error_type": type(exc).__name__,
                    },
                    created_at=datetime.now(UTC),
                )
                self._persist_receipt(failed)
                return failed

            tracking_url = str(action.tracking_url or "")
            receipt = RedditClientPublishReceipt(
                action_id=action.id,
                outcome=RedditClientPublishOutcome.EXECUTED,
                message="Reddit confirmed the client-owned publish.",
                external_reference=f"reddit-client:{result.fullname}",
                executed_url=result.url,
                published_at=result.published_at,
                metadata={
                    "action_type": action.action_type.value,
                    "subreddit": target.subreddit,
                    "remote_fullname": result.fullname,
                    "parent_fullname": target.parent_fullname,
                    "reddit_username": connection.get("username"),
                    "tracking_included": bool(tracking_url and tracking_url in text),
                    "restriction_signal": None,
                },
                created_at=datetime.now(UTC),
            )
            self._persist_receipt(receipt)
            self._record_publish(project_id, fingerprint)
            distribution_execution_service.mark_executed(
                action.id,
                DistributionActionExecutionRequest(
                    external_reference=receipt.external_reference,
                    executed_url=receipt.executed_url,
                    notes="Confirmed by Reddit client-owned publishing transport.",
                ),
            )
            return receipt

    async def observe_publish(
        self,
        project_id: UUID,
        customer_token: str,
        action_id: UUID,
    ) -> RedditPublishObservationView:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        self._require_ready()
        action = distribution_execution_service.get_action(action_id)
        if action.platform != DistributionPlatform.REDDIT or action.experiment_id is None:
            raise CustomerRedditClientPublishError("Action is not an observable Reddit action")
        experiment = distribution_execution_service.get_experiment(action.experiment_id)
        if str(project.get("product_id") or "") != str(experiment.product_id):
            raise CustomerRedditClientPublishError(
                "Reddit action does not belong to this customer project"
            )
        receipt = self.get_receipt(action_id)
        if receipt is None or receipt.outcome != RedditClientPublishOutcome.EXECUTED:
            raise CustomerRedditClientPublishError(
                "A confirmed Reddit client-owned publish receipt is required before observation"
            )
        fullname = str(receipt.metadata.get("remote_fullname") or "")
        if not fullname:
            raise CustomerRedditClientPublishError("Reddit publish receipt has no remote identifier")
        connection = self._connection_record(project_id)
        access_token = await self._access_token(connection)
        result = await self._transport.observe(access_token=access_token, fullname=fullname)
        event = RedditObservationEvent(
            state=result.state,
            score=result.score,
            reply_count=result.reply_count,
            restriction_signal=result.restriction_signal,
            checked_at=datetime.now(UTC),
        )
        history = self._observation_history(action_id)
        history.append(event)
        history = history[-_MAX_OBSERVATION_HISTORY:]
        self._store.put(
            CUSTOMER_REDDIT_OBSERVATION_NAMESPACE,
            str(action_id),
            {
                "action_id": str(action_id),
                "executed_url": str(receipt.executed_url) if receipt.executed_url else None,
                "history": [item.model_dump(mode="json") for item in history],
            },
        )
        return self.get_observation(project_id, customer_token, action_id)

    def get_observation(
        self,
        project_id: UUID,
        customer_token: str,
        action_id: UUID,
    ) -> RedditPublishObservationView:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        action = distribution_execution_service.get_action(action_id)
        if action.platform != DistributionPlatform.REDDIT or action.experiment_id is None:
            raise CustomerRedditClientPublishError("Action is not an observable Reddit action")
        experiment = distribution_execution_service.get_experiment(action.experiment_id)
        if str(project.get("product_id") or "") != str(experiment.product_id):
            raise CustomerRedditClientPublishError(
                "Reddit action does not belong to this customer project"
            )
        payload = self._store.get(CUSTOMER_REDDIT_OBSERVATION_NAMESPACE, str(action_id))
        if payload is None:
            return RedditPublishObservationView(action_id=action_id)
        history = [
            RedditObservationEvent.model_validate(item)
            for item in payload.get("history", [])
            if isinstance(item, dict)
        ]
        latest = history[-1] if history else None
        return RedditPublishObservationView(
            action_id=action_id,
            state=latest.state if latest else None,
            score=latest.score if latest else None,
            reply_count=latest.reply_count if latest else None,
            restriction_signal=latest.restriction_signal if latest else None,
            checked_at=latest.checked_at if latest else None,
            executed_url=payload.get("executed_url"),
            history=history,
        )

    def get_receipt(self, action_id: UUID) -> RedditClientPublishReceipt | None:
        payload = self._store.get(CUSTOMER_REDDIT_PUBLISH_RECEIPT_NAMESPACE, str(action_id))
        if payload is None:
            return None
        return RedditClientPublishReceipt.model_validate(payload)

    def reset(self) -> None:
        for record in self._store.list_namespace(CUSTOMER_REDDIT_CONNECTION_NAMESPACE):
            reference = str(record.get("secret_reference") or "")
            if reference:
                self._safe_delete_secret(reference)
        for namespace in (
            CUSTOMER_REDDIT_OAUTH_STATE_NAMESPACE,
            CUSTOMER_REDDIT_CONNECTION_NAMESPACE,
            CUSTOMER_REDDIT_PUBLISH_RECEIPT_NAMESPACE,
            CUSTOMER_REDDIT_PUBLISH_GUARD_NAMESPACE,
            CUSTOMER_REDDIT_OBSERVATION_NAMESPACE,
        ):
            self._store.clear_namespace(namespace)

    def _require_ready(self) -> None:
        blocker = self.readiness_blocker()
        if blocker is not None:
            raise CustomerRedditClientPublishError(blocker)

    def _require_client_owned_mode(self, project: dict) -> None:
        raw_modes = project.get("channel_publisher_modes")
        selected = (
            str(raw_modes.get(DistributionPlatform.REDDIT.value) or PublisherMode.MANUAL.value)
            if isinstance(raw_modes, dict)
            else PublisherMode.MANUAL.value
        )
        if selected != PublisherMode.CLIENT_OWNED.value:
            raise CustomerRedditClientPublishError(
                "Select CLIENT_OWNED as the Reddit publisher mode before publishing"
            )

    def _connection_record(self, project_id: UUID) -> dict:
        record = self._store.get(CUSTOMER_REDDIT_CONNECTION_NAMESPACE, str(project_id))
        if record is None or record.get("status") != RedditConnectionStatus.ACTIVE.value:
            raise CustomerRedditClientPublishError("Connect an authorised Reddit account first")
        return record

    async def _access_token(self, connection: dict) -> str:
        reference = str(connection.get("secret_reference") or "")
        try:
            raw = self._secret_store.get(reference) if reference else None
        except ProviderSecretConfigurationError as exc:
            raise CustomerRedditClientPublishError(
                "The authorised Reddit token cannot be decrypted"
            ) from exc
        if raw is None:
            raise CustomerRedditClientPublishError(
                "The authorised Reddit token is no longer available"
            )
        try:
            bundle = RedditTokenBundle.model_validate_json(raw)
        except ValueError as exc:
            raise CustomerRedditClientPublishError("Stored Reddit OAuth token is malformed") from exc
        self._require_scopes(bundle.scopes)
        if self._as_utc(bundle.expires_at) <= datetime.now(UTC) + timedelta(minutes=2):
            try:
                refreshed = await self._transport.refresh(bundle.refresh_token)
            except RedditClientPublishTransportError as exc:
                raise CustomerRedditClientPublishError(
                    f"Reddit OAuth refresh failed safely ({exc.code})"
                ) from None
            if not refreshed.refresh_token:
                refreshed = refreshed.model_copy(update={"refresh_token": bundle.refresh_token})
            self._require_scopes(refreshed.scopes)
            self._secret_store.put(reference, refreshed.model_dump_json())
            bundle = refreshed
        return bundle.access_token

    def _current_policy(self, opportunity_id: UUID):
        try:
            return distribution_control_plane_service.get_policy(opportunity_id)
        except KeyError as exc:
            raise CustomerRedditClientPublishError(
                "Reddit CommunityPolicy is required before publishing"
            ) from exc

    def _enforce_current_policy(
        self,
        *,
        policy,
        action_type: DistributionActionType,
        text: str,
        product_id: UUID,
        content_payload: dict,
    ) -> None:
        freshness = policy_freshness_reason(policy)
        if freshness is not None:
            raise CustomerRedditClientPublishError(freshness)
        if not policy.commercial_participation_allowed:
            raise CustomerRedditClientPublishError(
                "Community policy does not allow commercial participation"
            )
        if action_type in {DistributionActionType.COMMENT, DistributionActionType.REPLY}:
            if not policy.comments_allowed:
                raise CustomerRedditClientPublishError(
                    "Community policy does not allow comments/replies"
                )
        elif action_type == DistributionActionType.STANDALONE_POST:
            if not policy.standalone_posts_allowed:
                raise CustomerRedditClientPublishError(
                    "Community policy does not allow standalone posts"
                )
        if _URL_PATTERN.search(text) and not policy.links_allowed:
            raise CustomerRedditClientPublishError("Community policy does not allow direct links")
        try:
            product_name = product_intake_service.get_product(product_id).name.strip()
        except KeyError:
            product_name = ""
        if (
            product_name
            and product_name.casefold() in text.casefold()
            and not policy.product_mentions_allowed
        ):
            raise CustomerRedditClientPublishError(
                "Community policy does not allow product mentions"
            )
        if policy.disclosure_required and content_payload.get("disclosure_included") is not True:
            raise CustomerRedditClientPublishError(
                "Community policy requires explicit disclosure in the approved content"
            )
        constraints = {str(item).upper() for item in policy.ai_content_constraints}
        if "AI_CONTENT_PROHIBITED" in constraints:
            raise CustomerRedditClientPublishError("Community policy prohibits AI-generated content")
        if (
            "AI_CONTENT_DISCLOSURE_REQUIRED" in constraints
            and content_payload.get("ai_disclosure_included") is not True
        ):
            raise CustomerRedditClientPublishError(
                "Community policy requires AI-content disclosure"
            )
        if (
            policy.special_promotion_windows
            and content_payload.get("community_policy_constraints_confirmed") is not True
        ):
            raise CustomerRedditClientPublishError(
                "Community-specific promotion constraints require explicit confirmation"
            )

    def _parse_target(self, action, opportunity) -> RedditPublishTarget:
        subreddit = self._subreddit(opportunity)
        if action.action_type == DistributionActionType.STANDALONE_POST:
            title = str(action.content_payload.get("title") or "").strip()
            if not title:
                raise CustomerRedditClientPublishError(
                    "Reddit standalone post requires an approved title"
                )
            if len(title) > _MAX_TITLE_LENGTH:
                raise CustomerRedditClientPublishError(
                    f"Reddit post title exceeds {_MAX_TITLE_LENGTH} characters"
                )
            return RedditPublishTarget(subreddit=subreddit, title=title)

        target_url = str(action.target_url or "")
        parsed = self._thread_parts(target_url)
        if parsed is None:
            raise CustomerRedditClientPublishError(
                "Reddit comments/replies require a concrete public Reddit thread target"
            )
        target_subreddit, post_id, comment_id = parsed
        if target_subreddit.casefold() != subreddit.casefold():
            raise CustomerRedditClientPublishError(
                "Reddit action target does not belong to the selected subreddit"
            )
        if not self._thread_is_fresh(opportunity.metadata, post_id):
            raise CustomerRedditClientPublishError(
                "Reddit comment/reply target is stale or freshness is unverified"
            )
        if action.action_type == DistributionActionType.COMMENT:
            return RedditPublishTarget(
                subreddit=subreddit,
                parent_fullname=f"t3_{post_id}",
                thread_post_id=post_id,
            )
        if not comment_id:
            raise CustomerRedditClientPublishError(
                "Reddit reply requires a concrete comment target"
            )
        return RedditPublishTarget(
            subreddit=subreddit,
            parent_fullname=f"t1_{comment_id}",
            thread_post_id=post_id,
        )

    def _thread_is_fresh(self, metadata: dict, post_id: str) -> bool:
        enrichment = metadata.get("enrichment") if isinstance(metadata, dict) else None
        targets = enrichment.get("action_targets") if isinstance(enrichment, dict) else None
        if not isinstance(targets, list):
            return False
        for target in targets:
            if not isinstance(target, dict) or not action_target_is_fresh(target):
                continue
            parts = self._thread_parts(str(target.get("url") or ""))
            if parts is not None and parts[1].casefold() == post_id.casefold():
                return True
        return False

    @staticmethod
    def _thread_parts(url: str) -> tuple[str, str, str | None] | None:
        try:
            parts = urlsplit(url.strip())
        except ValueError:
            return None
        host = parts.netloc.lower().removeprefix("www.")
        if host != "reddit.com" and not host.endswith(".reddit.com"):
            return None
        segments = [segment for segment in parts.path.split("/") if segment]
        lowered = [segment.lower() for segment in segments]
        if len(segments) < 4 or lowered[0] != "r" or lowered[2] != "comments":
            return None
        subreddit = segments[1]
        post_id = segments[3]
        comment_id = segments[5] if len(segments) >= 6 else None
        return subreddit, post_id, comment_id

    @staticmethod
    def _subreddit(opportunity) -> str:
        canonical = str(opportunity.canonical_key or "")
        if canonical.lower().startswith("subreddit:"):
            value = canonical.split(":", 1)[1].strip()
            if value:
                return value
        url = str(opportunity.url or "")
        try:
            segments = [segment for segment in urlsplit(url).path.split("/") if segment]
        except ValueError:
            segments = []
        if len(segments) >= 2 and segments[0].lower() == "r":
            return segments[1]
        raise CustomerRedditClientPublishError("Reddit opportunity has no subreddit identity")

    def _enforce_publish_guard(self, project_id: UUID, fingerprint: str) -> None:
        now = datetime.now(UTC)
        events = self._guard_events(project_id)
        recent = [item for item in events if now - item["published_at"] <= _DAILY_WINDOW]
        if any(
            item["fingerprint"] == fingerprint
            and now - item["published_at"] <= _DUPLICATE_WINDOW
            for item in recent
        ):
            raise CustomerRedditClientPublishError(
                "Duplicate Reddit content/target is blocked for 24 hours"
            )
        if recent:
            last = max(item["published_at"] for item in recent)
            if now - last < _MIN_PUBLISH_INTERVAL:
                raise CustomerRedditClientPublishError(
                    "Wait at least 2 minutes between confirmed Reddit publishes"
                )
        if len(recent) >= _MAX_PUBLISHES_PER_DAY:
            raise CustomerRedditClientPublishError(
                f"Reddit client-owned publishing is capped at {_MAX_PUBLISHES_PER_DAY} per 24 hours"
            )

    def _record_publish(self, project_id: UUID, fingerprint: str) -> None:
        events = self._guard_events(project_id)
        events.append({"fingerprint": fingerprint, "published_at": datetime.now(UTC)})
        cutoff = datetime.now(UTC) - _DAILY_WINDOW
        serializable = [
            {"fingerprint": item["fingerprint"], "published_at": item["published_at"].isoformat()}
            for item in events
            if item["published_at"] >= cutoff
        ]
        self._store.put(
            CUSTOMER_REDDIT_PUBLISH_GUARD_NAMESPACE,
            str(project_id),
            {"project_id": str(project_id), "events": serializable},
        )

    def _guard_events(self, project_id: UUID) -> list[dict]:
        payload = self._store.get(CUSTOMER_REDDIT_PUBLISH_GUARD_NAMESPACE, str(project_id))
        rows = payload.get("events", []) if payload else []
        events: list[dict] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            try:
                events.append(
                    {
                        "fingerprint": str(item["fingerprint"]),
                        "published_at": self._as_utc(
                            datetime.fromisoformat(str(item["published_at"]))
                        ),
                    }
                )
            except (KeyError, ValueError):
                continue
        return events

    @staticmethod
    def _fingerprint(target: RedditPublishTarget, text: str) -> str:
        normalized = " ".join(text.casefold().split())
        basis = "|".join(
            [
                target.subreddit.casefold(),
                str(target.parent_fullname or "").casefold(),
                str(target.title or "").casefold(),
                normalized,
            ]
        )
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    def _persist_receipt(self, receipt: RedditClientPublishReceipt) -> None:
        self._store.put(
            CUSTOMER_REDDIT_PUBLISH_RECEIPT_NAMESPACE,
            str(receipt.action_id),
            receipt.model_dump(mode="json"),
        )

    def _observation_history(self, action_id: UUID) -> list[RedditObservationEvent]:
        payload = self._store.get(CUSTOMER_REDDIT_OBSERVATION_NAMESPACE, str(action_id))
        if payload is None:
            return []
        return [
            RedditObservationEvent.model_validate(item)
            for item in payload.get("history", [])
            if isinstance(item, dict)
        ]

    def _connection_view(self, record: dict) -> RedditConnectionView:
        return RedditConnectionView(
            status=RedditConnectionStatus(str(record.get("status") or "DISCONNECTED")),
            username=str(record.get("username") or "") or None,
            scopes=[str(item) for item in record.get("scopes", [])],
            connected_at=self._optional_datetime(record.get("connected_at")),
            last_verified_at=self._optional_datetime(record.get("last_verified_at")),
        )

    def _require_scopes(self, scopes: list[str]) -> None:
        normalized = {str(item).strip() for item in scopes if str(item).strip()}
        missing = sorted(_REQUIRED_SCOPES - normalized)
        if missing:
            raise CustomerRedditClientPublishError(
                f"Reddit OAuth connection is missing required scopes: {', '.join(missing)}"
            )

    def _redirect_uri(self) -> str:
        base = self._settings.partizan_public_base_url
        if not base:
            raise CustomerRedditClientPublishError(
                "PARTIZAN_PUBLIC_BASE_URL is required for Reddit OAuth"
            )
        return f"{base}/customer/reddit/oauth/callback"

    @staticmethod
    def _state_key(state: str) -> str:
        return hashlib.sha256(state.encode("utf-8")).hexdigest()

    def _safe_delete_secret(self, reference: str) -> None:
        try:
            self._secret_store.delete(reference)
        except Exception:
            pass

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _optional_datetime(self, value: object) -> datetime | None:
        if not value:
            return None
        try:
            return self._as_utc(datetime.fromisoformat(str(value)))
        except ValueError:
            return None


customer_reddit_client_publish_service = CustomerRedditClientPublishService()
