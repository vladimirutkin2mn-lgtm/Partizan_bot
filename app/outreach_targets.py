from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, HttpUrl, computed_field, model_validator

from app.audience_intelligence_service import audience_intelligence_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

OUTREACH_TARGET_NAMESPACE = "outreach_target"
OUTREACH_TARGET_PRODUCT_NAMESPACE = "outreach_target_product"
OUTREACH_SUPPRESSION_NAMESPACE = "outreach_target_suppression"
OUTREACH_SUPPRESSION_CONTACT_NAMESPACE = "outreach_contact_suppression"

_EMAIL_LOCAL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
_DOMAIN_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class OutreachTargetType(StrEnum):
    CREATOR = "CREATOR"
    NEWSLETTER = "NEWSLETTER"
    COMPLEMENTARY_PRODUCT = "COMPLEMENTARY_PRODUCT"
    AFFILIATE = "AFFILIATE"
    PARTNER = "PARTNER"


class OutreachContactProvenanceType(StrEnum):
    PUBLIC_BUSINESS_SOURCE = "PUBLIC_BUSINESS_SOURCE"
    OPERATOR_SUPPLIED = "OPERATOR_SUPPLIED"


class OutreachSuppressionReason(StrEnum):
    OPERATOR_SUPPRESSED = "OPERATOR_SUPPRESSED"
    OPT_OUT = "OPT_OUT"
    HARD_BOUNCE = "HARD_BOUNCE"


class OutreachTargetStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPPRESSED = "SUPPRESSED"


class OutreachContactEvidence(BaseModel):
    provenance_type: OutreachContactProvenanceType
    source_url: HttpUrl | None = None
    source_label: str | None = Field(default=None, max_length=300)
    source_excerpt: str | None = Field(default=None, max_length=1000)
    observed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_provenance(self) -> "OutreachContactEvidence":
        if self.provenance_type == OutreachContactProvenanceType.PUBLIC_BUSINESS_SOURCE:
            if self.source_url is None:
                raise ValueError("Public business contact evidence requires source_url")
            if urlsplit(str(self.source_url)).scheme != "https":
                raise ValueError("Public business contact source_url must use HTTPS")
            if not self.source_excerpt or "@" not in self.source_excerpt:
                raise ValueError(
                    "Public business contact evidence requires an excerpt containing the observed email"
                )
        elif self.source_url is not None:
            raise ValueError(
                "Operator-supplied contact must not be represented as public-source evidence"
            )
        return self


class OutreachTargetCreateRequest(BaseModel):
    opportunity_id: UUID
    target_type: OutreachTargetType
    canonical_name: str = Field(min_length=1, max_length=300)
    target_url: HttpUrl
    business_email: str = Field(min_length=3, max_length=254)
    contact_evidence: OutreachContactEvidence
    relevance_rationale: str = Field(min_length=1, max_length=1500)
    icp_overlap_rationale: str = Field(min_length=1, max_length=1500)
    confidence: float = Field(ge=0, le=100)
    language: str | None = Field(default=None, max_length=50)
    jurisdiction: str | None = Field(default=None, max_length=120)


class OutreachSuppressionView(BaseModel):
    id: UUID
    outreach_target_id: UUID
    reason: OutreachSuppressionReason
    note: str | None = Field(default=None, max_length=1000)
    created_at: datetime


class OutreachSuppressRequest(BaseModel):
    reason: OutreachSuppressionReason
    note: str | None = Field(default=None, max_length=1000)


class OutreachTargetView(BaseModel):
    id: UUID
    product_id: UUID
    opportunity_id: UUID
    target_type: OutreachTargetType
    canonical_name: str
    target_url: HttpUrl
    business_email: str
    contact_key: str = Field(min_length=3, max_length=254)
    contact_evidence: OutreachContactEvidence
    relevance_rationale: str
    icp_overlap_rationale: str
    confidence: float
    language: str | None = None
    jurisdiction: str | None = None
    status: OutreachTargetStatus
    suppression: OutreachSuppressionView | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field(return_type=bool)
    @property
    def executable(self) -> bool:
        return self.status == OutreachTargetStatus.ACTIVE and self.suppression is None


class OutreachTargetListView(BaseModel):
    product_id: UUID
    targets: list[OutreachTargetView]


class OutreachTargetService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def create(
        self,
        product_id: UUID,
        payload: OutreachTargetCreateRequest,
    ) -> OutreachTargetView:
        product_map = audience_intelligence_service.get(product_id)
        opportunity = next(
            (item for item in product_map.opportunities if item.id == payload.opportunity_id),
            None,
        )
        if opportunity is None:
            raise ValueError("OutreachTarget opportunity does not belong to this product")
        if not self._is_known_target_url(opportunity, str(payload.target_url)):
            raise ValueError(
                "OutreachTarget target_url must match the DistributionOpportunity or a persisted enrichment action target"
            )

        business_email, contact_key = self._normalize_email(payload.business_email)
        self._validate_contact_evidence(payload.contact_evidence, business_email)
        contact_suppression = self._contact_suppression(contact_key)
        if contact_suppression is not None:
            raise ValueError(
                "business_email is suppressed and cannot be reintroduced through another outreach target "
                f"({contact_suppression.reason.value})"
            )
        duplicate = self._find_duplicate(product_id, contact_key, payload.opportunity_id)
        if duplicate is not None:
            raise ValueError(
                "An OutreachTarget already exists for this product, opportunity and contact"
            )

        now = datetime.now(UTC)
        evidence = payload.contact_evidence.model_copy(
            update={"observed_at": payload.contact_evidence.observed_at or now}
        )
        target = OutreachTargetView(
            id=uuid4(),
            product_id=product_id,
            opportunity_id=payload.opportunity_id,
            target_type=payload.target_type,
            canonical_name=payload.canonical_name.strip(),
            target_url=payload.target_url,
            business_email=business_email,
            contact_key=contact_key,
            contact_evidence=evidence,
            relevance_rationale=payload.relevance_rationale.strip(),
            icp_overlap_rationale=payload.icp_overlap_rationale.strip(),
            confidence=payload.confidence,
            language=payload.language.strip() if payload.language else None,
            jurisdiction=payload.jurisdiction.strip() if payload.jurisdiction else None,
            status=OutreachTargetStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        self._persist(target)
        self._index_product(target)
        return target

    def get(self, target_id: UUID) -> OutreachTargetView:
        payload = self._store.get(OUTREACH_TARGET_NAMESPACE, str(target_id))
        if payload is None:
            raise KeyError(target_id)
        target = OutreachTargetView.model_validate(payload)
        contact_suppression = self._contact_suppression(target.contact_key)
        if contact_suppression is not None and target.suppression is None:
            return target.model_copy(
                update={
                    "status": OutreachTargetStatus.SUPPRESSED,
                    "suppression": contact_suppression,
                }
            )
        return target

    def list_product(self, product_id: UUID) -> OutreachTargetListView:
        index = self._store.get(OUTREACH_TARGET_PRODUCT_NAMESPACE, str(product_id)) or {}
        target_ids = [UUID(value) for value in index.get("target_ids", [])]
        targets = [self.get(target_id) for target_id in target_ids]
        targets.sort(key=lambda item: (-item.confidence, item.created_at, str(item.id)))
        return OutreachTargetListView(product_id=product_id, targets=targets)

    def suppress(
        self,
        target_id: UUID,
        payload: OutreachSuppressRequest,
    ) -> OutreachTargetView:
        target = self.get(target_id)
        if target.suppression is not None:
            return target
        now = datetime.now(UTC)
        suppression = OutreachSuppressionView(
            id=uuid4(),
            outreach_target_id=target_id,
            reason=payload.reason,
            note=payload.note.strip() if payload.note else None,
            created_at=now,
        )
        updated = target.model_copy(
            update={
                "status": OutreachTargetStatus.SUPPRESSED,
                "suppression": suppression,
                "updated_at": now,
            }
        )
        self._store.put(
            OUTREACH_SUPPRESSION_NAMESPACE,
            str(suppression.id),
            suppression.model_dump(mode="json"),
        )
        self._store.put(
            OUTREACH_SUPPRESSION_CONTACT_NAMESPACE,
            target.contact_key,
            {"suppression_id": str(suppression.id)},
        )
        self._persist(updated)
        return updated

    def require_executable(self, target_id: UUID) -> OutreachTargetView:
        target = self.get(target_id)
        if not target.executable:
            reason = (
                target.suppression.reason.value
                if target.suppression
                else target.status.value
            )
            raise ValueError(f"OutreachTarget is suppressed and cannot execute ({reason})")
        return target

    def _normalize_email(self, value: str) -> tuple[str, str]:
        email = value.strip()
        if not email or any(ord(char) < 32 or char.isspace() for char in email):
            raise ValueError("business_email contains whitespace or control characters")
        if email.count("@") != 1:
            raise ValueError("business_email must contain exactly one @")
        local, domain = email.rsplit("@", 1)
        if (
            not local
            or len(local) > 64
            or local.startswith(".")
            or local.endswith(".")
            or ".." in local
        ):
            raise ValueError("business_email local part is invalid")
        if not _EMAIL_LOCAL_RE.fullmatch(local):
            raise ValueError("business_email local part contains unsupported characters")
        domain = domain.rstrip(".").lower()
        if not domain or len(domain) > 253 or "." not in domain:
            raise ValueError("business_email domain is invalid")
        labels = domain.split(".")
        if any(not _DOMAIN_LABEL_RE.fullmatch(label) for label in labels):
            raise ValueError("business_email domain is invalid")
        normalized = f"{local}@{domain}"
        return normalized, normalized.casefold()

    def _validate_contact_evidence(
        self,
        evidence: OutreachContactEvidence,
        business_email: str,
    ) -> None:
        if evidence.provenance_type != OutreachContactProvenanceType.PUBLIC_BUSINESS_SOURCE:
            return
        assert evidence.source_excerpt is not None
        if business_email.casefold() not in evidence.source_excerpt.casefold():
            raise ValueError(
                "Public business contact evidence excerpt must contain the exact business_email"
            )

    def _is_known_target_url(self, opportunity, candidate_url: str) -> bool:
        known: set[str] = set()
        if opportunity.url is not None:
            known.add(self._url_key(str(opportunity.url)))
        enrichment = opportunity.metadata.get("enrichment", {})
        for item in enrichment.get("action_targets", []):
            if isinstance(item, dict) and item.get("url"):
                known.add(self._url_key(str(item["url"])))
        return self._url_key(candidate_url) in known

    def _url_key(self, value: str) -> str:
        parts = urlsplit(value.strip())
        return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"

    def _find_duplicate(
        self,
        product_id: UUID,
        contact_key: str,
        opportunity_id: UUID,
    ) -> OutreachTargetView | None:
        for target in self.list_product(product_id).targets:
            if target.contact_key == contact_key and target.opportunity_id == opportunity_id:
                return target
        return None

    def _contact_suppression(
        self,
        contact_key: str,
    ) -> OutreachSuppressionView | None:
        index = self._store.get(OUTREACH_SUPPRESSION_CONTACT_NAMESPACE, contact_key)
        if not index or not index.get("suppression_id"):
            return None
        payload = self._store.get(
            OUTREACH_SUPPRESSION_NAMESPACE,
            str(index["suppression_id"]),
        )
        if payload is None:
            return None
        return OutreachSuppressionView.model_validate(payload)

    def _index_product(self, target: OutreachTargetView) -> None:
        index = self._store.get(
            OUTREACH_TARGET_PRODUCT_NAMESPACE,
            str(target.product_id),
        ) or {}
        target_ids = list(index.get("target_ids", []))
        value = str(target.id)
        if value not in target_ids:
            target_ids.append(value)
        self._store.put(
            OUTREACH_TARGET_PRODUCT_NAMESPACE,
            str(target.product_id),
            {"target_ids": target_ids},
        )

    def _persist(self, target: OutreachTargetView) -> None:
        self._store.put(
            OUTREACH_TARGET_NAMESPACE,
            str(target.id),
            target.model_dump(mode="json"),
        )

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(OUTREACH_TARGET_NAMESPACE)
            self._store.clear_namespace(OUTREACH_TARGET_PRODUCT_NAMESPACE)
            self._store.clear_namespace(OUTREACH_SUPPRESSION_NAMESPACE)
            self._store.clear_namespace(OUTREACH_SUPPRESSION_CONTACT_NAMESPACE)


outreach_target_service = OutreachTargetService()
