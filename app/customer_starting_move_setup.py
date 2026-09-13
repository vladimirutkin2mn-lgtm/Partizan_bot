from __future__ import annotations

from uuid import UUID

from app.channel_execution import ChannelCapability, PublisherMode
from app.customer_channel_schemas import (
    CustomerChannelView,
    CustomerStartingMoveDraftView,
    CustomerStartingMoveSetupStepView,
    CustomerStartingMoveSetupView,
)


class CustomerStartingMoveSetupService:
    def view(
        self,
        *,
        project_id: UUID,
        draft: CustomerStartingMoveDraftView | None,
        channel: CustomerChannelView | None,
    ) -> CustomerStartingMoveSetupView | None:
        if draft is None or draft.review_status != "ACCEPTED":
            return None
        if channel is None or not channel.selected or channel.platform != draft.platform:
            return None

        publish = next(
            (item for item in channel.capabilities if item.capability == ChannelCapability.PUBLISH),
            None,
        )
        measure = next(
            (item for item in channel.capabilities if item.capability == ChannelCapability.MEASURE),
            None,
        )
        steps = [
            CustomerStartingMoveSetupStepView(
                key="REVIEW",
                state="READY",
                title="Review decision",
                detail=(
                    "The first-test draft is accepted for setup only. Publishing, account access "
                    "and acquisition spend remain separately controlled."
                ),
            ),
            self._publisher_step(channel),
            self._connection_step(channel),
            self._publish_step(channel, publish),
            self._measure_step(measure),
        ]

        if channel.publisher_mode == PublisherMode.MANUAL:
            state = "READY_FOR_HANDOFF"
            next_step = (
                "Manual handoff is ready: use the accepted draft yourself, or open Channels to "
                "choose a supported publisher mode. Partizan still has no execution permission."
            )
        elif publish is not None and publish.ready:
            state = "READY_FOR_HANDOFF"
            next_step = (
                "The publishing path is configured. Any Partizan execution still requires a "
                "separate permitted action; this setup plan does not create one."
            )
        elif channel.connected is False:
            state = "NEEDS_SETUP"
            next_step = (
                f"Open Channels and complete the {channel.label} account connection before any "
                "separate publishing permission can be considered."
            )
        else:
            state = "UNAVAILABLE"
            blocker = publish.blocker if publish is not None else None
            next_step = blocker or (
                "Automatic publishing is not available for this channel. Keep the accepted draft "
                "as a manual handoff."
            )

        return CustomerStartingMoveSetupView(
            project_id=project_id,
            platform=channel.platform,
            channel_label=channel.label,
            state=state,
            channel_mode=channel.mode,
            publisher_mode=channel.publisher_mode,
            connected=channel.connected,
            steps=steps,
            execution_allowed=False,
            next_step=next_step,
        )

    @staticmethod
    def _publisher_step(channel: CustomerChannelView) -> CustomerStartingMoveSetupStepView:
        if channel.publisher_mode == PublisherMode.MANUAL:
            detail = (
                "Manual handoff is selected. Partizan will not publish this draft or connect an "
                "account on your behalf."
            )
        elif channel.publisher_mode == PublisherMode.CLIENT_OWNED:
            detail = (
                "Client-owned publishing is selected. A supported authorised customer account is "
                "required before Partizan can publish."
            )
        else:
            detail = (
                "Partizan-managed publishing is selected, but execution permission remains "
                "separate from this review decision."
            )
        return CustomerStartingMoveSetupStepView(
            key="PUBLISHER",
            state="READY",
            title="Publisher mode",
            detail=detail,
        )

    @staticmethod
    def _connection_step(channel: CustomerChannelView) -> CustomerStartingMoveSetupStepView:
        if channel.publisher_mode == PublisherMode.MANUAL:
            return CustomerStartingMoveSetupStepView(
                key="CONNECTION",
                state="READY",
                title="Account connection",
                detail="No account connection is required while publishing remains manual.",
            )
        if channel.connected is True:
            return CustomerStartingMoveSetupStepView(
                key="CONNECTION",
                state="READY",
                title="Account connection",
                detail="The selected channel has an authorised customer connection.",
            )
        if channel.connected is False:
            return CustomerStartingMoveSetupStepView(
                key="CONNECTION",
                state="NEEDS_ACTION",
                title="Account connection",
                detail=f"Connect an authorised {channel.label} account in Channels first.",
            )
        return CustomerStartingMoveSetupStepView(
            key="CONNECTION",
            state="UNAVAILABLE",
            title="Account connection",
            detail="A customer-owned connection workflow is not available for this channel.",
        )

    @staticmethod
    def _publish_step(
        channel: CustomerChannelView,
        publish: object | None,
    ) -> CustomerStartingMoveSetupStepView:
        if channel.publisher_mode == PublisherMode.MANUAL:
            return CustomerStartingMoveSetupStepView(
                key="PUBLISH",
                state="READY",
                title="Publishing handoff",
                detail=(
                    "Manual publishing is the current path. The accepted draft can be used by the "
                    "customer without granting Partizan publish permission."
                ),
            )
        if publish is not None and getattr(publish, "ready", False):
            return CustomerStartingMoveSetupStepView(
                key="PUBLISH",
                state="READY",
                title="Publishing path",
                detail=(
                    "The selected publisher path is technically ready. This does not create, "
                    "approve or execute a distribution action."
                ),
            )
        blocker = getattr(publish, "blocker", None)
        state = "NEEDS_ACTION" if channel.connected is False else "UNAVAILABLE"
        return CustomerStartingMoveSetupStepView(
            key="PUBLISH",
            state=state,
            title="Publishing path",
            detail=blocker or "Partizan publishing is not available for this channel yet.",
        )

    @staticmethod
    def _measure_step(measure: object | None) -> CustomerStartingMoveSetupStepView:
        if measure is not None and getattr(measure, "ready", False):
            return CustomerStartingMoveSetupStepView(
                key="MEASURE",
                state="READY",
                title="Outcome measurement",
                detail="The channel has a supported outcome-measurement path.",
            )
        blocker = getattr(measure, "blocker", None)
        return CustomerStartingMoveSetupStepView(
            key="MEASURE",
            state="UNAVAILABLE",
            title="Outcome measurement",
            detail=blocker or "Automatic outcome measurement is not available yet.",
        )


customer_starting_move_setup_service = CustomerStartingMoveSetupService()
