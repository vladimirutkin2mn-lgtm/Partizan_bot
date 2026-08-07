import asyncio
import re
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.message import EmailMessage
from hashlib import sha1
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from pydantic import BaseModel, Field

from app.config import get_settings
from app.llm import LLMMessage, LLMProvider
from app.schemas import ChannelOpportunityView, GrowthPlayView, ProductProfileView

EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


@dataclass(frozen=True, slots=True)
class ContactTarget:
    method: str
    address: str | None
    name: str | None
    contact_url: str
    source: str


class OutreachDraft(BaseModel):
    subject: str = Field(min_length=3, max_length=160)
    body: str = Field(min_length=20, max_length=4000)


class ContactExtractor:
    def extract(
        self,
        channel: ChannelOpportunityView,
        override_email: str | None = None,
        override_name: str | None = None,
    ) -> ContactTarget:
        if override_email:
            self.validate_email(override_email)
            return ContactTarget(
                method="email",
                address=override_email,
                name=override_name,
                contact_url=channel.url,
                source="user_override",
            )

        for evidence in channel.evidence:
            for text in (evidence.snippet, evidence.title):
                match = EMAIL_PATTERN.search(text or "")
                if match:
                    email = match.group(0)
                    self.validate_email(email)
                    return ContactTarget(
                        method="email",
                        address=email,
                        name=override_name,
                        contact_url=channel.url,
                        source="public_evidence",
                    )
        return ContactTarget(
            method="platform",
            address=None,
            name=override_name,
            contact_url=channel.url,
            source="channel_url",
        )

    def validate_email(self, value: str) -> None:
        if "\n" in value or "\r" in value or not EMAIL_PATTERN.fullmatch(value.strip()):
            raise ValueError("Invalid contact email")


class TrackingLinkBuilder:
    def build(
        self,
        destination_url: str,
        product_id: UUID,
        play: GrowthPlayView,
    ) -> tuple[str, str]:
        parts = urlsplit(destination_url.strip())
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("A valid http(s) destination_url is required")
        referral_token = sha1(f"{product_id}:{play.id}".encode()).hexdigest()[:12]
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.update(
            {
                "utm_source": play.source_type,
                "utm_medium": "outreach",
                "utm_campaign": f"partizan-{str(product_id)[:8]}",
                "utm_content": str(play.id),
                "ref": f"partizan_{referral_token}",
            }
        )
        tracked = urlunsplit(
            (parts.scheme, parts.netloc, parts.path or "/", urlencode(query), "")
        )
        return tracked, referral_token


SYSTEM_PROMPT = """You are the Execution Assistant for Partizan Bot.
Draft one transparent, personalized business outreach message for an already approved Growth Play.

Rules:
1. Do not impersonate a customer, journalist, independent reviewer or other third party.
2. Do not make fabricated claims, fake urgency, fake social proof or guaranteed-result claims.
3. Keep the message specific to the recipient/channel and the supplied ICP hypothesis.
4. The CTA must use the supplied tracking URL where appropriate.
5. Keep it concise and useful; avoid manipulative or high-pressure language.
6. This is a one-to-one draft that still requires explicit user approval before sending.
7. If the recipient is a creator/publication/community owner, make the partnership value clear.
Return only the requested structured schema.
"""


class OutreachComposer:
    def __init__(self, provider: LLMProvider | None) -> None:
        self._provider = provider

    async def compose(
        self,
        product: ProductProfileView,
        play: GrowthPlayView,
        channel: ChannelOpportunityView,
        contact: ContactTarget,
        tracking_url: str,
    ) -> OutreachDraft:
        if self._provider is None:
            greeting = f"Hi {contact.name}," if contact.name else "Hi,"
            return OutreachDraft(
                subject=f"Partnership idea: {product.name}",
                body=(
                    f"{greeting}\n\n"
                    f"I’m reaching out about {product.name}. "
                    f"{product.value_proposition or product.description}\n\n"
                    f"I think it could be relevant to the audience around {channel.title}. "
                    f"The test idea is: {play.offer}.\n\n"
                    f"If useful, here is a tracked link for the test: {tracking_url}\n\n"
                    "Would you be open to discussing a small, measurable collaboration?"
                ),
            )
        return await self._provider.parse(
            messages=[
                LLMMessage(role="system", content=SYSTEM_PROMPT),
                LLMMessage(
                    role="user",
                    content=(
                        f"Product: {product.model_dump(mode='json')}\n"
                        f"Approved Growth Play: {play.model_dump(mode='json')}\n"
                        f"Channel: {channel.model_dump(mode='json')}\n"
                        f"Contact: {contact}\n"
                        f"Tracking URL: {tracking_url}"
                    ),
                ),
            ],
            response_model=OutreachDraft,
        )


class DeliveryProvider(ABC):
    @abstractmethod
    async def send_email(self, to_email: str, subject: str, body: str) -> str:
        raise NotImplementedError


class MockDeliveryProvider(DeliveryProvider):
    async def send_email(self, to_email: str, subject: str, body: str) -> str:
        digest = sha1(f"{to_email}:{subject}:{body}".encode()).hexdigest()[:12]
        return f"mock-{digest}"


class SMTPDeliveryProvider(DeliveryProvider):
    def __init__(
        self,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        from_email: str,
        starttls: bool,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_email = from_email
        self._starttls = starttls

    async def send_email(self, to_email: str, subject: str, body: str) -> str:
        if "\n" in subject or "\r" in subject:
            raise ValueError("Invalid subject")
        ContactExtractor().validate_email(to_email)
        message = EmailMessage()
        message["From"] = self._from_email
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)
        await asyncio.to_thread(self._send, message)
        digest = sha1(message.as_bytes()).hexdigest()[:12]
        return f"smtp-{digest}"

    def _send(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=20) as client:
            if self._starttls:
                client.starttls()
            if self._username:
                client.login(self._username, self._password or "")
            client.send_message(message)


def get_delivery_provider() -> DeliveryProvider:
    settings = get_settings()
    if settings.execution_provider == "mock":
        return MockDeliveryProvider()
    if settings.execution_provider == "smtp":
        if not settings.smtp_host or not settings.smtp_from_email:
            raise ValueError("SMTP_HOST and SMTP_FROM_EMAIL are required for SMTP execution")
        return SMTPDeliveryProvider(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            from_email=settings.smtp_from_email,
            starttls=settings.smtp_starttls,
        )
    raise ValueError(f"Unsupported execution provider: {settings.execution_provider}")
