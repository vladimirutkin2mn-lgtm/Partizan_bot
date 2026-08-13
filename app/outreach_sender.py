from __future__ import annotations

import asyncio
import hashlib
import re
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.config import get_settings
from app.distribution_execution_schemas import DistributionActionExecutionRequest
from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import DistributionActionStatus
from app.outreach_briefs import OutreachBriefStatus, outreach_brief_service
from app.outreach_targets import outreach_target_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

OUTREACH_SEND_AUTH_NAMESPACE = "outreach_send_authorization"
OUTREACH_SEND_AUTH_BRIEF_NAMESPACE = "outreach_send_authorization_brief"
OUTREACH_SEND_ATTEMPT_NAMESPACE = "outreach_send_attempt"
OUTREACH_SEND_AUTH_TTL_MINUTES = 30
OUTREACH_STARTED_STALE_SECONDS = 120

_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_URL_RE = re.compile(r"(?i)https?://[^\s<>]+")


class OutreachSendAuthorizationStatus(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    CONSUMED = "CONSUMED"
    REVOKED = "REVOKED"


class OutreachSendAttemptStatus(StrEnum):
    STARTED = "STARTED"
    SENT = "SENT"
    REJECTED = "REJECTED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class OutreachSenderReadinessView(BaseModel):
    provider: str
    ready: bool
    from_email: str | None = None
    from_name: str | None = None
    reply_to: str | None = None
    starttls: bool
    auth_configured: bool
    blockers: list[str] = Field(default_factory=list)


class OutreachSendAuthorizationCreateRequest(BaseModel):
    recipient_email: str = Field(min_length=3, max_length=254)
    confirm_one_initial_message: bool = False


class OutreachSendAuthorizationView(BaseModel):
    id: UUID
    brief_id: UUID
    action_id: UUID
    experiment_id: UUID
    outreach_target_id: UUID
    recipient_email: str
    sender_email: str
    sender_name: str
    reply_to: str
    message_fingerprint: str
    status: OutreachSendAuthorizationStatus
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None


class OutreachSendAttemptView(BaseModel):
    id: UUID
    authorization_id: UUID
    brief_id: UUID
    action_id: UUID
    experiment_id: UUID
    outreach_target_id: UUID
    recipient_email: str
    sender_email: str
    message_fingerprint: str
    provider: str
    status: OutreachSendAttemptStatus
    provider_reference: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class OutreachSMTPRejectedError(RuntimeError):
    pass


class OutreachSMTPAmbiguousError(RuntimeError):
    pass


class OutreachSMTPProvider:
    async def send(
        self,
        *,
        to_email: str,
        subject: str,
        body: str,
        fingerprint: str,
    ) -> str:
        settings = get_settings()
        readiness = build_sender_readiness()
        if not readiness.ready:
            raise OutreachSMTPRejectedError("SMTP sender is not ready")
        assert settings.smtp_host is not None
        assert readiness.from_email is not None
        assert readiness.from_name is not None
        assert readiness.reply_to is not None
        password = (
            settings.smtp_password.get_secret_value()
            if settings.smtp_password is not None
            else None
        )
        message = _build_message(
            from_email=readiness.from_email,
            from_name=readiness.from_name,
            reply_to=readiness.reply_to,
            to_email=to_email,
            subject=subject,
            body=body,
            fingerprint=fingerprint,
        )
        return await asyncio.to_thread(
            self._send_sync,
            message,
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_username,
            password,
            settings.smtp_starttls,
        )

    def _send_sync(
        self,
        message: EmailMessage,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        starttls: bool,
    ) -> str:
        client: smtplib.SMTP | None = None
        try:
            client = smtplib.SMTP(host, port, timeout=20)
            client.ehlo()
            if starttls:
                client.starttls()
                client.ehlo()
            if username:
                client.login(username, password or "")
        except Exception as exc:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            raise OutreachSMTPRejectedError("SMTP sender failed before message submission") from exc

        try:
            refused = client.send_message(message)
        except (
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused,
            smtplib.SMTPDataError,
        ) as exc:
            raise OutreachSMTPRejectedError("SMTP server definitively rejected the message") from exc
        except Exception as exc:
            raise OutreachSMTPAmbiguousError(
                "SMTP connection failed while message submission outcome was unknown"
            ) from exc
        finally:
            try:
                client.close()
            except Exception:
                pass

        if refused:
            raise OutreachSMTPRejectedError("SMTP server rejected the configured recipient")
        return str(message["Message-ID"])


def build_sender_readiness() -> OutreachSenderReadinessView:
    settings = get_settings()
    blockers: list[str] = []
    if settings.execution_provider.strip().lower() != "smtp":
        blockers.append("EXECUTION_PROVIDER must be smtp")
    if not settings.smtp_host:
        blockers.append("SMTP_HOST is required")
    if not settings.smtp_from_email:
        blockers.append("SMTP_FROM_EMAIL is required")
    elif not _valid_email(settings.smtp_from_email):
        blockers.append("SMTP_FROM_EMAIL is invalid")
    if not settings.smtp_from_name:
        blockers.append("SMTP_FROM_NAME is required")
    if not settings.smtp_reply_to:
        blockers.append("SMTP_REPLY_TO is required")
    elif not _valid_email(settings.smtp_reply_to):
        blockers.append("SMTP_REPLY_TO is invalid")
    if bool(settings.smtp_username) != bool(settings.smtp_password):
        blockers.append("SMTP_USERNAME and SMTP_PASSWORD must be configured together")
    return OutreachSenderReadinessView(
        provider="SMTP",
        ready=not blockers,
        from_email=settings.smtp_from_email,
        from_name=settings.smtp_from_name,
        reply_to=settings.smtp_reply_to,
        starttls=settings.smtp_starttls,
        auth_configured=bool(settings.smtp_username and settings.smtp_password),
        blockers=blockers,
    )


class OutreachSenderService:
    def __init__(
        self,
        store: RuntimeStateStore | None = None,
        provider: OutreachSMTPProvider | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._provider = provider

    def readiness(self) -> OutreachSenderReadinessView:
        return build_sender_readiness()

    def authorize(
        self,
        brief_id: UUID,
        payload: OutreachSendAuthorizationCreateRequest,
    ) -> OutreachSendAuthorizationView:
        if not payload.confirm_one_initial_message:
            raise ValueError("Explicit confirmation of one initial outreach message is required")
        readiness = self.readiness()
        if not readiness.ready:
            raise ValueError("; ".join(readiness.blockers))
        assert readiness.from_email is not None
        assert readiness.from_name is not None
        assert readiness.reply_to is not None

        brief = outreach_brief_service.get(brief_id)
        if brief.status != OutreachBriefStatus.DRAFT:
            raise ValueError("Only DRAFT OutreachBrief objects can be authorized")
        target = outreach_target_service.require_executable(brief.outreach_target_id)
        recipient = _normalize_email(payload.recipient_email)
        if recipient.casefold() != target.contact_key:
            raise ValueError("Authorization recipient must match the evidence-backed business contact")

        action = distribution_execution_service.get_action(brief.action_id)
        experiment = distribution_execution_service.get_experiment(brief.experiment_id)
        if action.status != DistributionActionStatus.PREPARED:
            raise ValueError("Outreach action must be PREPARED before send authorization")
        if experiment.status.value != "DRAFT":
            raise ValueError("Outreach experiment must be DRAFT before send authorization")
        subject, body = _exact_message(action.content_text, brief.tracking_url)
        fingerprint = _message_fingerprint(
            recipient=recipient,
            sender=readiness.from_email,
            reply_to=readiness.reply_to,
            subject=subject,
            body=body,
        )

        self._revoke_current_authorization(brief_id)
        now = datetime.now(UTC)
        authorization = OutreachSendAuthorizationView(
            id=uuid4(),
            brief_id=brief.id,
            action_id=brief.action_id,
            experiment_id=brief.experiment_id,
            outreach_target_id=brief.outreach_target_id,
            recipient_email=recipient,
            sender_email=readiness.from_email,
            sender_name=readiness.from_name,
            reply_to=readiness.reply_to,
            message_fingerprint=fingerprint,
            status=OutreachSendAuthorizationStatus.AUTHORIZED,
            created_at=now,
            expires_at=now + timedelta(minutes=OUTREACH_SEND_AUTH_TTL_MINUTES),
        )
        self._persist_authorization(authorization)
        self._store.put(
            OUTREACH_SEND_AUTH_BRIEF_NAMESPACE,
            str(brief.id),
            {"authorization_id": str(authorization.id)},
        )
        return authorization

    async def send(self, authorization_id: UUID) -> OutreachSendAttemptView:
        authorization = self.get_authorization(authorization_id)
        existing = self.get_attempt(authorization.brief_id)
        if existing is not None:
            if existing.status == OutreachSendAttemptStatus.SENT:
                self._reconcile_sent_action(existing)
            return existing
        if authorization.status != OutreachSendAuthorizationStatus.AUTHORIZED:
            raise ValueError("Outreach send authorization is no longer active")
        if authorization.expires_at <= datetime.now(UTC):
            self._revoke(authorization)
            raise ValueError("Outreach send authorization has expired")

        readiness = self.readiness()
        if not readiness.ready:
            raise ValueError("; ".join(readiness.blockers))
        if (
            readiness.from_email != authorization.sender_email
            or readiness.from_name != authorization.sender_name
            or readiness.reply_to != authorization.reply_to
        ):
            raise ValueError("SMTP sender identity changed; create a new send authorization")

        brief = outreach_brief_service.get(authorization.brief_id)
        target = outreach_target_service.require_executable(authorization.outreach_target_id)
        if target.contact_key != authorization.recipient_email.casefold():
            raise ValueError("Evidence-backed recipient changed after authorization")
        action = distribution_execution_service.get_action(authorization.action_id)
        subject, body = _exact_message(action.content_text, brief.tracking_url)
        current_fingerprint = _message_fingerprint(
            recipient=authorization.recipient_email,
            sender=authorization.sender_email,
            reply_to=authorization.reply_to,
            subject=subject,
            body=body,
        )
        if current_fingerprint != authorization.message_fingerprint:
            raise ValueError("Outreach message changed after authorization; authorize it again")

        self._ensure_action_approved(action.id)
        now = datetime.now(UTC)
        attempt = OutreachSendAttemptView(
            id=uuid4(),
            authorization_id=authorization.id,
            brief_id=authorization.brief_id,
            action_id=authorization.action_id,
            experiment_id=authorization.experiment_id,
            outreach_target_id=authorization.outreach_target_id,
            recipient_email=authorization.recipient_email,
            sender_email=authorization.sender_email,
            message_fingerprint=authorization.message_fingerprint,
            provider="SMTP",
            status=OutreachSendAttemptStatus.STARTED,
            started_at=now,
        )
        reserved = self._store.put_if_absent(
            OUTREACH_SEND_ATTEMPT_NAMESPACE,
            str(authorization.brief_id),
            attempt.model_dump(mode="json"),
        )
        if not reserved:
            existing = self.get_attempt(authorization.brief_id)
            if existing is None:
                raise RuntimeError("Outreach send reservation disappeared")
            return existing

        self._consume(authorization)
        provider = self._provider or OutreachSMTPProvider()
        try:
            provider_reference = await provider.send(
                to_email=authorization.recipient_email,
                subject=subject,
                body=body,
                fingerprint=authorization.message_fingerprint,
            )
        except OutreachSMTPRejectedError as exc:
            rejected = attempt.model_copy(
                update={
                    "status": OutreachSendAttemptStatus.REJECTED,
                    "error_code": "SMTP_REJECTED",
                    "error_detail": str(exc),
                    "finished_at": datetime.now(UTC),
                }
            )
            self._persist_attempt(rejected)
            return rejected
        except Exception:
            ambiguous = attempt.model_copy(
                update={
                    "status": OutreachSendAttemptStatus.RECONCILIATION_REQUIRED,
                    "error_code": "SMTP_OUTCOME_UNKNOWN",
                    "error_detail": (
                        "SMTP submission outcome is unknown; automatic retry is disabled"
                    ),
                    "finished_at": datetime.now(UTC),
                }
            )
            self._persist_attempt(ambiguous)
            return ambiguous

        sent = attempt.model_copy(
            update={
                "status": OutreachSendAttemptStatus.SENT,
                "provider_reference": provider_reference,
                "finished_at": datetime.now(UTC),
            }
        )
        self._persist_attempt(sent)
        self._reconcile_sent_action(sent)
        return sent

    def get_authorization(self, authorization_id: UUID) -> OutreachSendAuthorizationView:
        payload = self._store.get(OUTREACH_SEND_AUTH_NAMESPACE, str(authorization_id))
        if payload is None:
            raise KeyError(authorization_id)
        return OutreachSendAuthorizationView.model_validate(payload)

    def get_attempt(self, brief_id: UUID) -> OutreachSendAttemptView | None:
        payload = self._store.get(OUTREACH_SEND_ATTEMPT_NAMESPACE, str(brief_id))
        if payload is None:
            return None
        attempt = OutreachSendAttemptView.model_validate(payload)
        if (
            attempt.status == OutreachSendAttemptStatus.STARTED
            and datetime.now(UTC) - attempt.started_at
            >= timedelta(seconds=OUTREACH_STARTED_STALE_SECONDS)
        ):
            attempt = attempt.model_copy(
                update={
                    "status": OutreachSendAttemptStatus.RECONCILIATION_REQUIRED,
                    "error_code": "STALE_STARTED_ATTEMPT",
                    "error_detail": (
                        "A previous SMTP attempt did not reach a confirmed terminal state; "
                        "automatic retry is disabled"
                    ),
                    "finished_at": datetime.now(UTC),
                }
            )
            self._persist_attempt(attempt)
        return attempt

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(OUTREACH_SEND_AUTH_NAMESPACE)
            self._store.clear_namespace(OUTREACH_SEND_AUTH_BRIEF_NAMESPACE)
            self._store.clear_namespace(OUTREACH_SEND_ATTEMPT_NAMESPACE)

    def _ensure_action_approved(self, action_id: UUID) -> None:
        action = distribution_execution_service.get_action(action_id)
        if action.status == DistributionActionStatus.PREPARED:
            distribution_execution_service.approve_outreach(action_id)
            return
        if action.status != DistributionActionStatus.APPROVED:
            raise ValueError("Outreach action is no longer eligible for SMTP execution")

    def _reconcile_sent_action(self, attempt: OutreachSendAttemptView) -> None:
        action = distribution_execution_service.get_action(attempt.action_id)
        if action.status == DistributionActionStatus.EXECUTED:
            return
        if action.status == DistributionActionStatus.PREPARED:
            distribution_execution_service.approve_outreach(action.id)
        distribution_execution_service.mark_executed(
            action.id,
            DistributionActionExecutionRequest(
                external_reference=attempt.provider_reference,
                notes="Owned SMTP provider confirmed message acceptance",
            ),
        )

    def _revoke_current_authorization(self, brief_id: UUID) -> None:
        index = self._store.get(OUTREACH_SEND_AUTH_BRIEF_NAMESPACE, str(brief_id))
        if not index or not index.get("authorization_id"):
            return
        try:
            current = self.get_authorization(UUID(str(index["authorization_id"])))
        except KeyError:
            return
        if current.status == OutreachSendAuthorizationStatus.AUTHORIZED:
            self._revoke(current)

    def _revoke(self, authorization: OutreachSendAuthorizationView) -> None:
        revoked = authorization.model_copy(
            update={
                "status": OutreachSendAuthorizationStatus.REVOKED,
                "revoked_at": datetime.now(UTC),
            }
        )
        self._persist_authorization(revoked)

    def _consume(self, authorization: OutreachSendAuthorizationView) -> None:
        consumed = authorization.model_copy(
            update={
                "status": OutreachSendAuthorizationStatus.CONSUMED,
                "consumed_at": datetime.now(UTC),
            }
        )
        self._persist_authorization(consumed)

    def _persist_authorization(self, authorization: OutreachSendAuthorizationView) -> None:
        self._store.put(
            OUTREACH_SEND_AUTH_NAMESPACE,
            str(authorization.id),
            authorization.model_dump(mode="json"),
        )

    def _persist_attempt(self, attempt: OutreachSendAttemptView) -> None:
        self._store.put(
            OUTREACH_SEND_ATTEMPT_NAMESPACE,
            str(attempt.brief_id),
            attempt.model_dump(mode="json"),
        )


def _valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.fullmatch(value.strip())) and "\r" not in value and "\n" not in value


def _normalize_email(value: str) -> str:
    normalized = value.strip()
    if not _valid_email(normalized):
        raise ValueError("Invalid outreach email address")
    local, domain = normalized.rsplit("@", 1)
    return f"{local}@{domain.lower()}"


def _exact_message(content_text: str | None, tracking_url: str) -> tuple[str, str]:
    text = str(content_text or "")
    if not text.startswith("Subject: ") or "\n\n" not in text:
        raise ValueError("Outreach action must contain the exact Subject/body message format")
    subject_line, body = text.split("\n\n", 1)
    subject = subject_line.removeprefix("Subject: ").strip()
    if not subject or "\r" in subject or "\n" in subject:
        raise ValueError("Outreach subject is invalid")
    urls = [value.rstrip(".,);]") for value in _URL_RE.findall(body)]
    if urls != [tracking_url]:
        raise ValueError("Outreach body must contain exactly the current Partizan tracking URL")
    return subject, body


def _message_fingerprint(
    *,
    recipient: str,
    sender: str,
    reply_to: str,
    subject: str,
    body: str,
) -> str:
    value = "\n".join(
        [recipient.casefold(), sender.casefold(), reply_to.casefold(), subject, body]
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _build_message(
    *,
    from_email: str,
    from_name: str,
    reply_to: str,
    to_email: str,
    subject: str,
    body: str,
    fingerprint: str,
) -> EmailMessage:
    domain = from_email.rsplit("@", 1)[1].lower()
    message = EmailMessage()
    message["From"] = formataddr((from_name, from_email))
    message["Reply-To"] = reply_to
    message["To"] = to_email
    message["Subject"] = subject
    message["Message-ID"] = f"<partizan-{fingerprint[:32]}@{domain}>"
    message["X-Partizan-Message-Fingerprint"] = fingerprint
    message.set_content(body)
    return message


outreach_sender_service = OutreachSenderService()
