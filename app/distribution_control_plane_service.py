from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_control_plane_schemas import (
    CampaignSlotCreateRequest,
    CommunityPolicyUpsertRequest,
    DistributionIdentityCreateRequest,
)
from app.distribution_schemas import (
    CampaignSlotView,
    CommunityPolicyView,
    DistributionIdentityView,
)
from app.distribution_types import (
    CampaignSlotStatus,
    DistributionIdentityStatus,
    DistributionPlatform,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store

DISTRIBUTION_IDENTITY_NAMESPACE = "distribution_identity"
COMMUNITY_POLICY_NAMESPACE = "community_policy"
CAMPAIGN_SLOT_NAMESPACE = "campaign_slot"


class InMemoryDistributionControlPlaneService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()
        self._identities: dict[UUID, DistributionIdentityView] = {}
        self._policies: dict[UUID, CommunityPolicyView] = {}
        self._slots: dict[UUID, CampaignSlotView] = {}

    def create_identity(
        self,
        payload: DistributionIdentityCreateRequest,
    ) -> DistributionIdentityView:
        identity = DistributionIdentityView(
            id=uuid4(),
            platform=payload.platform,
            theme=payload.theme,
            language=payload.language,
            geography_hints=payload.geography_hints,
            public_positioning=payload.public_positioning,
            profile_url=payload.profile_url,
            profile_config=payload.profile_config,
            eligibility={
                "allowed_opportunity_kinds": [
                    kind.value for kind in payload.allowed_opportunity_kinds
                ],
                "allowed_actions": [action.value for action in payload.allowed_actions],
            },
            reputation_metadata={},
            attribution_route=payload.attribution_route,
            status=DistributionIdentityStatus.ACTIVE,
        )
        self._identities[identity.id] = identity
        self._persist_identity(identity)
        return identity

    def get_identity(self, identity_id: UUID) -> DistributionIdentityView:
        cached = self._identities.get(identity_id)
        if cached is not None:
            return cached
        payload = self._store.get(DISTRIBUTION_IDENTITY_NAMESPACE, str(identity_id))
        if payload is None:
            raise KeyError(identity_id)
        identity = DistributionIdentityView.model_validate(payload)
        self._identities[identity_id] = identity
        return identity

    def list_identities(
        self,
        platform: DistributionPlatform | None = None,
    ) -> list[DistributionIdentityView]:
        self._hydrate_identities()
        identities = list(self._identities.values())
        if platform is not None:
            identities = [identity for identity in identities if identity.platform == platform]
        return sorted(
            identities,
            key=lambda identity: (
                identity.platform.value,
                identity.theme,
                str(identity.id),
            ),
        )

    def set_identity_status(
        self,
        identity_id: UUID,
        status: DistributionIdentityStatus,
    ) -> DistributionIdentityView:
        identity = self.get_identity(identity_id)
        updated = identity.model_copy(update={"status": status})
        self._identities[identity_id] = updated
        self._persist_identity(updated)
        return updated

    def upsert_policy(
        self,
        opportunity_id: UUID,
        payload: CommunityPolicyUpsertRequest,
    ) -> CommunityPolicyView:
        opportunity = audience_intelligence_service.find_opportunity(opportunity_id)
        if opportunity.platform != DistributionPlatform.REDDIT:
            raise ValueError("CommunityPolicy control plane is required only for Reddit in MVP")

        try:
            existing = self.get_policy(opportunity_id)
        except KeyError:
            existing = None
        policy = CommunityPolicyView(
            id=existing.id if existing is not None else uuid4(),
            opportunity_id=opportunity_id,
            commercial_participation_allowed=payload.commercial_participation_allowed,
            self_promotion_allowed=payload.self_promotion_allowed,
            links_allowed=payload.links_allowed,
            product_mentions_allowed=payload.product_mentions_allowed,
            standalone_posts_allowed=payload.standalone_posts_allowed,
            comments_allowed=payload.comments_allowed,
            disclosure_required=payload.disclosure_required,
            special_promotion_windows=payload.special_promotion_windows,
            ai_content_constraints=payload.ai_content_constraints,
            evidence=payload.evidence,
            last_checked_at=payload.last_checked_at or datetime.now(UTC),
            confidence=payload.confidence,
        )
        self._policies[opportunity_id] = policy
        self._store.put(
            COMMUNITY_POLICY_NAMESPACE,
            str(opportunity_id),
            policy.model_dump(mode="json"),
        )
        return policy

    def get_policy(self, opportunity_id: UUID) -> CommunityPolicyView:
        cached = self._policies.get(opportunity_id)
        if cached is not None:
            return cached
        payload = self._store.get(COMMUNITY_POLICY_NAMESPACE, str(opportunity_id))
        if payload is None:
            raise KeyError(opportunity_id)
        policy = CommunityPolicyView.model_validate(payload)
        self._policies[opportunity_id] = policy
        return policy

    def list_policies(self) -> list[CommunityPolicyView]:
        for payload in self._store.list_namespace(COMMUNITY_POLICY_NAMESPACE):
            policy = CommunityPolicyView.model_validate(payload)
            self._policies[policy.opportunity_id] = policy
        return sorted(self._policies.values(), key=lambda policy: str(policy.opportunity_id))

    def create_campaign_slot(
        self,
        product_id: UUID,
        payload: CampaignSlotCreateRequest,
    ) -> CampaignSlotView:
        identity = self.get_identity(payload.distribution_identity_id)
        if payload.status == CampaignSlotStatus.ACTIVE:
            self._assert_identity_has_no_active_slot(identity.id)

        slot = CampaignSlotView(
            id=uuid4(),
            product_id=product_id,
            distribution_identity_id=identity.id,
            platform=identity.platform,
            status=payload.status,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            attribution_route=payload.attribution_route,
            metadata=payload.metadata,
        )
        self._slots[slot.id] = slot
        self._persist_slot(slot)
        return slot

    def set_campaign_slot_status(
        self,
        slot_id: UUID,
        status: CampaignSlotStatus,
    ) -> CampaignSlotView:
        slot = self._get_slot(slot_id)
        if status == CampaignSlotStatus.ACTIVE and slot.status != CampaignSlotStatus.ACTIVE:
            self._assert_identity_has_no_active_slot(
                slot.distribution_identity_id,
                excluding=slot.id,
            )
        updated = slot.model_copy(update={"status": status})
        self._slots[slot_id] = updated
        self._persist_slot(updated)
        return updated

    def list_campaign_slots(
        self,
        product_id: UUID | None = None,
    ) -> list[CampaignSlotView]:
        self._hydrate_slots()
        slots = list(self._slots.values())
        if product_id is not None:
            slots = [slot for slot in slots if slot.product_id == product_id]
        return sorted(slots, key=lambda slot: str(slot.id))

    def find_active_slot(
        self,
        identity_id: UUID,
        product_id: UUID,
    ) -> CampaignSlotView:
        self._hydrate_slots()
        for slot in self._slots.values():
            if (
                slot.distribution_identity_id == identity_id
                and slot.product_id == product_id
                and slot.status == CampaignSlotStatus.ACTIVE
            ):
                return slot
        raise KeyError((identity_id, product_id))

    def _get_slot(self, slot_id: UUID) -> CampaignSlotView:
        cached = self._slots.get(slot_id)
        if cached is not None:
            return cached
        payload = self._store.get(CAMPAIGN_SLOT_NAMESPACE, str(slot_id))
        if payload is None:
            raise KeyError(slot_id)
        slot = CampaignSlotView.model_validate(payload)
        self._slots[slot_id] = slot
        return slot

    def _hydrate_identities(self) -> None:
        for payload in self._store.list_namespace(DISTRIBUTION_IDENTITY_NAMESPACE):
            identity = DistributionIdentityView.model_validate(payload)
            self._identities[identity.id] = identity

    def _hydrate_slots(self) -> None:
        for payload in self._store.list_namespace(CAMPAIGN_SLOT_NAMESPACE):
            slot = CampaignSlotView.model_validate(payload)
            self._slots[slot.id] = slot

    def _persist_identity(self, identity: DistributionIdentityView) -> None:
        self._store.put(
            DISTRIBUTION_IDENTITY_NAMESPACE,
            str(identity.id),
            identity.model_dump(mode="json"),
        )

    def _persist_slot(self, slot: CampaignSlotView) -> None:
        self._store.put(
            CAMPAIGN_SLOT_NAMESPACE,
            str(slot.id),
            slot.model_dump(mode="json"),
        )

    def _assert_identity_has_no_active_slot(
        self,
        identity_id: UUID,
        *,
        excluding: UUID | None = None,
    ) -> None:
        self._hydrate_slots()
        for slot in self._slots.values():
            if excluding is not None and slot.id == excluding:
                continue
            if (
                slot.distribution_identity_id == identity_id
                and slot.status == CampaignSlotStatus.ACTIVE
            ):
                raise ValueError("Distribution Identity already has an ACTIVE campaign slot")

    def reset(self) -> None:
        self._identities.clear()
        self._policies.clear()
        self._slots.clear()
        if self._store.ephemeral:
            self._store.clear_namespace(DISTRIBUTION_IDENTITY_NAMESPACE)
            self._store.clear_namespace(COMMUNITY_POLICY_NAMESPACE)
            self._store.clear_namespace(CAMPAIGN_SLOT_NAMESPACE)


distribution_control_plane_service = InMemoryDistributionControlPlaneService()
