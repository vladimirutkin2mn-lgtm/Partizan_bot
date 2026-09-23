from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.conversion_scenarios import ConversionMechanism
from app.distribution_schemas import CampaignSlotView, DistributionActionView, DistributionIdentityView


class ConversionPathStatus(StrEnum):
    READY = "READY"
    SETUP_REQUIRED = "SETUP_REQUIRED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class ConversionPathAssessment:
    mechanism: ConversionMechanism
    status: ConversionPathStatus
    steps: tuple[str, ...]
    measurement: str
    blockers: tuple[str, ...] = ()
    required_profile_url: str | None = None

    @property
    def send_eligible(self) -> bool:
        return self.status == ConversionPathStatus.READY

    def as_dict(self) -> dict:
        return {
            "mechanism": self.mechanism.value,
            "status": self.status.value,
            "send_eligible": self.send_eligible,
            "steps": list(self.steps),
            "measurement": self.measurement,
            "blockers": list(self.blockers),
            "required_profile_url": self.required_profile_url,
        }


class ConversionPathValidator:
    """Fail closed unless the selected copy has a complete measurable handoff."""

    def assess(
        self,
        *,
        mechanism: ConversionMechanism,
        action: DistributionActionView,
        identity: DistributionIdentityView | None,
        slot: CampaignSlotView | None,
        profile_route_url: str | None = None,
    ) -> ConversionPathAssessment:
        tracking_url = str(action.tracking_url or "").strip()

        if mechanism == ConversionMechanism.DIRECT_LINK:
            blockers: list[str] = []
            if not tracking_url:
                blockers.append("Prepared action has no attributable tracking URL")
            if tracking_url and tracking_url not in str(action.content_text or ""):
                blockers.append("Draft does not contain the attributable tracking URL")
            return ConversionPathAssessment(
                mechanism=mechanism,
                status=(
                    ConversionPathStatus.READY
                    if not blockers
                    else ConversionPathStatus.SETUP_REQUIRED
                ),
                steps=(
                    "Community contribution",
                    "Partizan tracked link",
                    "Product entry",
                    "SIGNUP / ACTIVATED / PAID",
                ),
                measurement="VISIT via Partizan redirect, then server-side conversion events",
                blockers=tuple(blockers),
            )

        if mechanism in {
            ConversionMechanism.PROFILE_CLICK,
            ConversionMechanism.REPLY_ENGAGEMENT,
        }:
            blockers = []
            if identity is None:
                blockers.append("No Distribution Identity is attached to this action")
            if slot is None:
                blockers.append("No ACTIVE CampaignSlot is attached to this action")
            if not tracking_url:
                blockers.append("Prepared action has no attributable tracking URL")
            if not profile_route_url:
                blockers.append("No stable Partizan profile conversion route is configured")

            profile_config = (
                identity.profile_config
                if identity is not None and isinstance(identity.profile_config, dict)
                else {}
            )
            verified = profile_config.get("conversion_profile_verified") is True
            configured_url = str(profile_config.get("conversion_profile_url") or "").strip()
            if not verified:
                blockers.append("Telegram profile conversion CTA has not been verified")
            if profile_route_url and configured_url != profile_route_url:
                blockers.append(
                    "Telegram profile conversion CTA does not point to the stable Partizan profile route"
                )

            prefix = (
                ("Community contribution", "Author profile")
                if mechanism == ConversionMechanism.PROFILE_CLICK
                else ("Community contribution", "Public reply engagement", "Author profile")
            )
            return ConversionPathAssessment(
                mechanism=mechanism,
                status=(
                    ConversionPathStatus.READY
                    if not blockers
                    else ConversionPathStatus.SETUP_REQUIRED
                ),
                steps=(
                    *prefix,
                    "Stable Partizan profile route",
                    "Selected experiment tracking link",
                    "Product entry",
                    "SIGNUP / ACTIVATED / PAID",
                ),
                measurement=(
                    "Profile itself is not observable by Partizan; the stable profile route forwards "
                    "to the selected experiment, where VISIT and downstream conversions are attributed"
                ),
                blockers=tuple(blockers),
                required_profile_url=profile_route_url or None,
            )

        if mechanism == ConversionMechanism.BRAND_SEARCH:
            profile_config = (
                identity.profile_config
                if identity is not None and isinstance(identity.profile_config, dict)
                else {}
            )
            if profile_config.get("brand_search_attribution_verified") is True:
                return ConversionPathAssessment(
                    mechanism=mechanism,
                    status=ConversionPathStatus.READY,
                    steps=(
                        "Community contribution",
                        "Brand search",
                        "Attributed product entry",
                        "SIGNUP / ACTIVATED / PAID",
                    ),
                    measurement="Verified downstream brand-search attribution",
                )
            return ConversionPathAssessment(
                mechanism=mechanism,
                status=ConversionPathStatus.BLOCKED,
                steps=(
                    "Community contribution",
                    "Brand search",
                    "Product entry",
                ),
                measurement="No deterministic attribution is configured",
                blockers=(
                    "Brand-search attribution is not verified; Partizan cannot optimize this path",
                ),
            )

        return ConversionPathAssessment(
            mechanism=mechanism,
            status=ConversionPathStatus.BLOCKED,
            steps=("Community contribution",),
            measurement="Unavailable",
            blockers=("Unsupported conversion mechanism",),
        )


conversion_path_validator = ConversionPathValidator()
