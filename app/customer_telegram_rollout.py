from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import UTC, datetime
from uuid import UUID

from app.action_drafting import distribution_action_drafting_service
from app.audience_intelligence_service import audience_intelligence_service
from app.autonomous_controlled_growth import AUTONOMOUS_GROWTH_ADVISORY_LOCK_KEY
from app.autonomy_schemas import AutonomyDecision, AutonomyEvaluationRequest, GrowthMandateStatus
from app.autonomy_service import growth_mandate_service
from app.customer_autopilot import customer_autopilot_service
from app.customer_channels import customer_channel_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.database_advisory_lock import postgres_session_advisory_lock
from app.distribution_control_plane_schemas import (
    CampaignSlotCreateRequest,
    DistributionIdentityCreateRequest,
)
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_schemas import DistributionPlayStatus
from app.distribution_play_service import distribution_play_service
from app.distribution_types import (
    CampaignSlotStatus,
    DistributionActionStatus,
    DistributionActionType,
    DistributionIdentityStatus,
    DistributionPlatform,
    OpportunityKind,
)
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store
from app.telegram_autonomous_execution import customer_telegram_autonomous_execution_service
from app.telegram_client_governance import (
    TelegramAutomationAuthorizationRequest,
    customer_telegram_governance_service,
)
from app.telegram_client_publishing import (
    CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
    TelegramClientPublishOutcome,
)

ROLLOUT_NAMESPACE = "customer_telegram_first_auto_rollout"
CONFIRMATION = "RUN_ONE_CLIENT_OWNED_TELEGRAM_COMMENT"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one exact, bounded client-owned Telegram AUTO rollout."
    )
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--expected-product-id", type=UUID, required=True)
    parser.add_argument("--expected-product-name", required=True)
    parser.add_argument("--expected-telegram-entity-id", type=int, required=True)
    parser.add_argument("--expected-handle", required=True)
    parser.add_argument("--confirm", required=True)
    return parser


def _marker_key(project_id: UUID, entity_id: int) -> str:
    return f"{project_id}:{entity_id}"


def _load_exact_target(args: argparse.Namespace):
    store = get_runtime_store()
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(args.project_id))
    if project is None:
        raise ValueError("Customer project not found")
    if str(project.get("product_id") or "") != str(args.expected_product_id):
        raise ValueError("Customer project product does not match the expected product")
    if project.get("research_state") != "READY":
        raise ValueError("Customer project research must be READY")

    product = product_intake_service.get_product(args.expected_product_id)
    expected_name = args.expected_product_name.strip().casefold()
    if expected_name not in str(product.name or "").casefold():
        raise ValueError("Product name does not match the expected rollout product")

    distribution = audience_intelligence_service.get(product.id)
    handle = args.expected_handle.strip().lstrip("@").casefold()
    matches = [
        item
        for item in distribution.opportunities
        if item.platform == DistributionPlatform.TELEGRAM
        and int(item.metadata.get("telegram_entity_id") or 0) == args.expected_telegram_entity_id
        and str(item.metadata.get("handle") or "").casefold() == handle
    ]
    if len(matches) != 1:
        raise ValueError("Expected Telegram opportunity is not uniquely persisted")
    opportunity = matches[0]
    metadata = opportunity.metadata if isinstance(opportunity.metadata, dict) else {}
    capabilities = metadata.get("surface_capabilities")
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    target_url = str(metadata.get("action_target_url") or "").strip()
    if str(metadata.get("native_research_status") or "").upper() != "VERIFIED":
        raise ValueError("Expected Telegram opportunity is not native VERIFIED")
    if opportunity.kind != OpportunityKind.CHANNEL:
        raise ValueError("First AUTO rollout only allows a verified Telegram CHANNEL comment")
    if str(capabilities.get("comment") or "").upper() != "AVAILABLE":
        raise ValueError("Expected Telegram opportunity does not have AVAILABLE comments")
    if not metadata.get("action_target_specific") or not target_url:
        raise ValueError("Expected Telegram opportunity has no specific native message target")
    if f"t.me/{handle}/" not in target_url.casefold():
        raise ValueError("Native action target does not belong to the expected Telegram handle")
    return store, project, product, distribution, opportunity, target_url


def _ensure_customer_identity(project_id: UUID, product, connection: dict):
    candidates = []
    for identity in distribution_control_plane_service.list_identities(
        DistributionPlatform.TELEGRAM
    ):
        config = identity.profile_config if isinstance(identity.profile_config, dict) else {}
        if (
            identity.status == DistributionIdentityStatus.ACTIVE
            and str(config.get("customer_project_id") or "") == str(project_id)
            and config.get("publisher_mode") == "CLIENT_OWNED"
        ):
            candidates.append(identity)
    if candidates:
        identity = candidates[0]
    else:
        username = str(connection.get("username") or "").strip().lstrip("@")
        identity = distribution_control_plane_service.create_identity(
            DistributionIdentityCreateRequest(
                platform=DistributionPlatform.TELEGRAM,
                theme=(f"Customer-owned Telegram identity for {product.name}")[:160],
                language=product.language,
                public_positioning=(
                    "Customer-owned Telegram account used for transparent, non-explicit, "
                    "value-first community participation."
                ),
                profile_url=(f"https://t.me/{username}" if username else None),
                profile_config={
                    "customer_project_id": str(project_id),
                    "publisher_mode": "CLIENT_OWNED",
                    "telegram_user_id": connection.get("telegram_user_id"),
                    "rollout_scope": "CHANNEL_COMMENT_ONLY",
                },
                allowed_opportunity_kinds=[OpportunityKind.CHANNEL],
                allowed_actions=[DistributionActionType.COMMENT],
            )
        )

    try:
        distribution_control_plane_service.find_active_slot(identity.id, product.id)
    except KeyError:
        distribution_control_plane_service.create_campaign_slot(
            product.id,
            CampaignSlotCreateRequest(
                distribution_identity_id=identity.id,
                status=CampaignSlotStatus.ACTIVE,
                metadata={
                    "source": "customer_telegram_first_auto_rollout",
                    "customer_project_id": str(project_id),
                },
            ),
        )
    return identity


def _generate_exact_play(product, distribution, identity, opportunity):
    identities = [
        item
        for item in distribution_control_plane_service.list_identities()
        if item.platform != DistributionPlatform.TELEGRAM
    ]
    identities.append(identity)
    generation = distribution_play_service.generate(
        product,
        distribution,
        identities=identities,
        community_policies=distribution_control_plane_service.list_policies(),
        campaign_slots=distribution_control_plane_service.list_campaign_slots(),
    )
    matches = [
        play
        for play in generation.plays
        if play.opportunity_id == opportunity.id
        and play.platform == DistributionPlatform.TELEGRAM
        and play.action_type == DistributionActionType.COMMENT
        and play.tactic_id == "telegram_channel_comment"
        and play.status == DistributionPlayStatus.READY
        and play.selected_identity_id == identity.id
    ]
    if len(matches) != 1:
        blockers = [
            {
                "tactic_id": play.tactic_id,
                "status": play.status.value,
                "blockers": play.blockers,
            }
            for play in generation.plays
            if play.opportunity_id == opportunity.id
            and play.platform == DistributionPlatform.TELEGRAM
        ]
        raise ValueError(f"Exact Telegram COMMENT play is not READY: {blockers}")
    return matches[0]


def _validate_draft(product, plan, expected_target_url: str) -> None:
    action = plan.action
    if action.status != DistributionActionStatus.PREPARED:
        raise ValueError("First rollout action was not PREPARED")
    if str(action.target_url or "") != expected_target_url:
        raise ValueError("Prepared action target changed from the exact verified native target")
    text = str(action.content_text or "").strip()
    if len(text) < 10 or len(text) > 700:
        raise ValueError("First rollout Telegram comment must be between 10 and 700 characters")
    lowered = text.casefold()
    if re.search(r"https?://|\bt\.me/", lowered):
        raise ValueError("First rollout comment must not contain a URL")
    if str(product.name or "").strip().casefold() in lowered:
        raise ValueError("First rollout comment must not directly name the promoted asset")
    for link in product.reference_links:
        if str(link).strip().casefold() in lowered:
            raise ValueError("First rollout comment must not contain the product destination")
    promotional_markers = (
        "subscribe",
        "join our",
        "join my",
        "our channel",
        "my channel",
        "подписывай",
        "подпишись",
        "наш канал",
        "мой канал",
    )
    if any(marker in lowered for marker in promotional_markers):
        raise ValueError("First rollout comment must be value-first, not direct promotion")


def _evaluate_or_raise(product_id: UUID, *, requires_prepare: bool, requires_approval: bool):
    result = growth_mandate_service.evaluate(
        product_id,
        AutonomyEvaluationRequest(
            platform=DistributionPlatform.TELEGRAM,
            action_type=DistributionActionType.COMMENT,
            proposed_budget=0,
            requires_prepare=requires_prepare,
            requires_approval=requires_approval,
            requests_paid_activation=False,
        ),
    )
    if result.decision != AutonomyDecision.ALLOW:
        raise ValueError("Growth Mandate blocked the rollout: " + "; ".join(result.reasons))
    return result


async def run(args: argparse.Namespace) -> dict:
    if args.confirm != CONFIRMATION:
        raise ValueError(f"Exact confirmation is required: {CONFIRMATION}")

    store, project, product, distribution, opportunity, target_url = _load_exact_target(args)
    marker_key = _marker_key(args.project_id, args.expected_telegram_entity_id)
    existing_marker = store.get(ROLLOUT_NAMESPACE, marker_key)
    if existing_marker is not None:
        status = str(existing_marker.get("status") or "")
        if status == "EXECUTED":
            return {
                "status": "already_executed",
                "project_id": str(args.project_id),
                "product_id": str(product.id),
                "opportunity_id": str(opportunity.id),
                "executed_url": existing_marker.get("executed_url"),
            }
        raise ValueError(
            f"First rollout already has a non-terminal-safe marker ({status}); "
            "automatic retry is refused"
        )

    connection = store.get(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(args.project_id))
    if connection is None or str(connection.get("status") or "").upper() != "ACTIVE":
        raise ValueError("An active customer-owned Telegram connection is required")

    identity = _ensure_customer_identity(args.project_id, product, connection)
    play = _generate_exact_play(product, distribution, identity, opportunity)

    with postgres_session_advisory_lock(AUTONOMOUS_GROWTH_ADVISORY_LOCK_KEY) as acquired:
        if not acquired:
            raise ValueError("Autonomous growth is already running; refusing concurrent rollout")

        customer_telegram_governance_service.authorize_automation_internal(
            args.project_id,
            TelegramAutomationAuthorizationRequest(
                confirm_client_owned_execution=True,
                max_publishes_per_day=1,
            ),
        )
        auto_enabled = False
        plan = None
        try:
            customer_channel_service.enable_telegram_auto_internal(args.project_id)
            auto_enabled = True
            mandate = customer_autopilot_service.refresh_channel_policy_internal(args.project_id)
            if mandate is None or mandate.status != GrowthMandateStatus.ACTIVE:
                raise ValueError("Telegram Growth Mandate is not ACTIVE after channel refresh")
            if mandate.allowed_platforms != [DistributionPlatform.TELEGRAM]:
                raise ValueError("First rollout requires a Telegram-only Growth Mandate")

            _evaluate_or_raise(product.id, requires_prepare=True, requires_approval=False)
            plan = await distribution_action_drafting_service.auto_prepare(
                product=product,
                play=play,
            )
            _validate_draft(product, plan, target_url)

            store.put(
                ROLLOUT_NAMESPACE,
                marker_key,
                {
                    "status": "ATTEMPTED",
                    "project_id": str(args.project_id),
                    "product_id": str(product.id),
                    "opportunity_id": str(opportunity.id),
                    "play_id": str(play.id),
                    "action_id": str(plan.action.id),
                    "target_url": target_url,
                    "attempted_at": datetime.now(UTC).isoformat(),
                },
            )

            _evaluate_or_raise(product.id, requires_prepare=False, requires_approval=True)
            approved = distribution_execution_service.approve(plan.action.id)
            _evaluate_or_raise(product.id, requires_prepare=False, requires_approval=False)
            execution = await customer_telegram_autonomous_execution_service.execute(
                product_id=product.id,
                action_id=approved.action.id,
                retry=False,
            )
            if execution.receipt.outcome != TelegramClientPublishOutcome.EXECUTED:
                raise ValueError(
                    "Telegram did not confirm execution: "
                    f"{execution.receipt.outcome.value}"
                )

            observation_state = None
            try:
                observation = customer_telegram_governance_service.get_observation_internal(
                    args.project_id,
                    approved.action.id,
                )
                observation_state = observation.latest.state.value
            except (KeyError, RuntimeError, ValueError):
                observation_state = None

            final_action = distribution_execution_service.get_action(approved.action.id)
            store.put(
                ROLLOUT_NAMESPACE,
                marker_key,
                {
                    "status": "EXECUTED",
                    "project_id": str(args.project_id),
                    "product_id": str(product.id),
                    "opportunity_id": str(opportunity.id),
                    "play_id": str(play.id),
                    "action_id": str(approved.action.id),
                    "experiment_id": str(approved.experiment.id),
                    "target_url": target_url,
                    "executed_url": (
                        str(execution.receipt.executed_url)
                        if execution.receipt.executed_url is not None
                        else None
                    ),
                    "observation_state": observation_state,
                    "published_at": (
                        execution.receipt.published_at.isoformat()
                        if execution.receipt.published_at is not None
                        else None
                    ),
                    "content_text": str(final_action.content_text or ""),
                    "completed_at": datetime.now(UTC).isoformat(),
                },
            )
            return {
                "status": "executed",
                "project_id": str(args.project_id),
                "product_id": str(product.id),
                "opportunity_id": str(opportunity.id),
                "opportunity_url": str(opportunity.url or ""),
                "play_id": str(play.id),
                "action_id": str(approved.action.id),
                "experiment_id": str(approved.experiment.id),
                "target_url": target_url,
                "executed_url": (
                    str(execution.receipt.executed_url)
                    if execution.receipt.executed_url is not None
                    else None
                ),
                "observation_state": observation_state,
                "automation_max_publishes_per_day": 1,
                "content_text": str(final_action.content_text or ""),
            }
        except Exception as exc:
            if plan is not None:
                try:
                    action = distribution_execution_service.get_action(plan.action.id)
                    if action.status in {
                        DistributionActionStatus.PREPARED,
                        DistributionActionStatus.APPROVED,
                    }:
                        distribution_execution_service.skip(action.id)
                except (KeyError, RuntimeError, ValueError):
                    pass
            if auto_enabled:
                try:
                    customer_telegram_governance_service.pause_automation_internal(args.project_id)
                except (KeyError, RuntimeError, ValueError):
                    pass
                try:
                    customer_channel_service.disable_telegram_auto_internal(args.project_id)
                except (KeyError, RuntimeError, ValueError):
                    pass
            marker = store.get(ROLLOUT_NAMESPACE, marker_key) or {}
            marker.update(
                {
                    "status": "FAILED",
                    "project_id": str(args.project_id),
                    "product_id": str(product.id),
                    "opportunity_id": str(opportunity.id),
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1000],
                    "failed_at": datetime.now(UTC).isoformat(),
                }
            )
            store.put(ROLLOUT_NAMESPACE, marker_key, marker)
            raise


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"error_type": type(exc).__name__, "error": str(exc)[:2000]},
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
