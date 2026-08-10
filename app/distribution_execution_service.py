from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_schemas import (
    DistributionActionEditRequest,
    DistributionActionExecutionRequest,
    DistributionExecutionPlanView,
    DistributionExecutionPrepareRequest,
    DistributionExperimentStatus,
    DistributionExperimentView,
)
from app.distribution_play_planner import TACTIC_CATALOG
from app.distribution_play_schemas import DistributionPlayStatus, DistributionPlayView
from app.distribution_policy import DistributionExecutionPolicy
from app.distribution_schemas import DistributionActionView
from app.distribution_types import (
    DistributionActionStatus,
    DistributionActionType,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.schemas import ProductProfileView

DISTRIBUTION_ACTION_NAMESPACE = "distribution_action"
DISTRIBUTION_EXPERIMENT_NAMESPACE = "distribution_experiment"


class DistributionTrackingLinkBuilder:
    def build(
        self,
        base_url: str,
        *,
        product_id: UUID,
        play_id: UUID,
        opportunity_id: UUID,
        action_id: UUID,
        experiment_id: UUID,
        medium: str,
    ) -> str:
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("Tracking destination must be an absolute http(s) URL")
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.update(
            {
                "utm_source": "partizan",
                "utm_medium": medium.lower(),
                "utm_campaign": str(product_id),
                "utm_content": str(play_id),
                "ptz_opportunity": str(opportunity_id),
                "ptz_action": str(action_id),
                "ptz_experiment": str(experiment_id),
            }
        )
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )


class InMemoryDistributionExecutionService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()
        self._actions: dict[UUID, DistributionActionView] = {}
        self._experiments: dict[UUID, DistributionExperimentView] = {}
        self._tracking_builder = DistributionTrackingLinkBuilder()
        self._execution_policy = DistributionExecutionPolicy()

    def prepare(
        self,
        product: ProductProfileView,
        play: DistributionPlayView,
        payload: DistributionExecutionPrepareRequest,
    ) -> DistributionExecutionPlanView:
        if play.status != DistributionPlayStatus.READY:
            raise ValueError("Only READY DistributionPlay objects can be prepared")
        if play.product_id != product.id:
            raise ValueError("DistributionPlay does not belong to this product")

        opportunity = audience_intelligence_service.find_opportunity(play.opportunity_id)
        slot = self._require_active_slot(product.id, play)
        destination_url = self._resolve_destination(product, payload)
        target_url = self._resolve_target(play, opportunity.url, payload.target_url)

        action_id = uuid4()
        experiment_id = uuid4()
        referral_token = experiment_id.hex[:16]
        slot_route = slot.attribution_route if slot else None
        tracking_base = self._tracking_base(destination_url, play, slot_route)
        tracking_url = self._tracking_builder.build(
            tracking_base,
            product_id=product.id,
            play_id=play.id,
            opportunity_id=play.opportunity_id,
            action_id=action_id,
            experiment_id=experiment_id,
            medium=play.tactic_class.value,
        )

        action = DistributionActionView(
            id=action_id,
            platform=play.platform,
            opportunity_id=play.opportunity_id,
            distribution_identity_id=play.selected_identity_id,
            campaign_slot_id=slot.id if slot else None,
            experiment_id=experiment_id,
            action_type=play.action_type,
            status=DistributionActionStatus.PREPARED,
            automation_level=play.automation_level,
            attribution_level=play.attribution_level,
            target_url=target_url,
            content_text=payload.content_text,
            content_payload={
                "context_text": payload.context_text,
                "hypothesis": play.hypothesis,
                "execution_steps": play.execution_steps,
                "success_metric": play.success_metric,
            },
            tracking_url=tracking_url,
            operational_metadata={
                "distribution_play_id": str(play.id),
                "tactic_id": play.tactic_id,
                "tactic_class": play.tactic_class.value,
                "destination_url": destination_url,
                "referral_token": referral_token,
                "operator_confirmation_required": True,
            },
        )
        experiment = DistributionExperimentView(
            id=experiment_id,
            product_id=product.id,
            distribution_play_id=play.id,
            opportunity_id=play.opportunity_id,
            action_id=action.id,
            status=DistributionExperimentStatus.DRAFT,
            attribution_level=play.attribution_level,
            tracking_url=tracking_url,
            referral_token=referral_token,
        )
        self._actions[action.id] = action
        self._experiments[experiment.id] = experiment
        self._persist_action(action)
        self._persist_experiment(experiment)
        return DistributionExecutionPlanView(action=action, experiment=experiment)

    def edit(
        self,
        action_id: UUID,
        payload: DistributionActionEditRequest,
    ) -> DistributionExecutionPlanView:
        action = self.get_action(action_id)
        if action.status != DistributionActionStatus.PREPARED:
            raise ValueError("Only PREPARED DistributionAction objects can be edited")

        content_payload = dict(action.content_payload)
        if payload.context_text is not None:
            content_payload["context_text"] = payload.context_text
        updated = action.model_copy(
            update={
                "target_url": payload.target_url or action.target_url,
                "content_text": (
                    payload.content_text
                    if payload.content_text is not None
                    else action.content_text
                ),
                "content_payload": content_payload,
            }
        )
        self._actions[action_id] = updated
        self._persist_action(updated)
        experiment = self.get_experiment(updated.experiment_id)
        return DistributionExecutionPlanView(action=updated, experiment=experiment)

    def approve(self, action_id: UUID) -> DistributionExecutionPlanView:
        action = self.get_action(action_id)
        if action.status != DistributionActionStatus.PREPARED:
            raise ValueError("Only PREPARED DistributionAction objects can be approved")
        experiment = self.get_experiment(action.experiment_id)
        play_id = UUID(str(action.operational_metadata["distribution_play_id"]))
        play = self._find_play(experiment.product_id, play_id)
        opportunity = audience_intelligence_service.find_opportunity(action.opportunity_id)
        self._require_active_slot(experiment.product_id, play)
        self._validate_action_ready_for_approval(action)

        identity = None
        if action.distribution_identity_id is not None:
            identity = distribution_control_plane_service.get_identity(
                action.distribution_identity_id
            )
        policy = None
        if play.community_policy_required:
            try:
                policy = distribution_control_plane_service.get_policy(
                    action.opportunity_id
                )
            except KeyError:
                policy = None
        template = next(item for item in TACTIC_CATALOG if item.tactic_id == play.tactic_id)
        decision = self._execution_policy.evaluate(
            opportunity,
            play.action_type,
            identity=identity,
            community_policy=policy,
            has_direct_product_link=template.has_direct_product_link,
            has_product_mention=template.has_product_mention,
        )
        if not decision.allowed:
            raise ValueError("; ".join(decision.reasons))

        approved_action = action.model_copy(
            update={"status": DistributionActionStatus.APPROVED}
        )
        approved_experiment = experiment.model_copy(
            update={"status": DistributionExperimentStatus.APPROVED}
        )
        self._actions[action.id] = approved_action
        self._experiments[experiment.id] = approved_experiment
        self._persist_action(approved_action)
        self._persist_experiment(approved_experiment)
        return DistributionExecutionPlanView(
            action=approved_action,
            experiment=approved_experiment,
        )

    def skip(self, action_id: UUID) -> DistributionExecutionPlanView:
        action = self.get_action(action_id)
        if action.status not in {
            DistributionActionStatus.PREPARED,
            DistributionActionStatus.APPROVED,
        }:
            raise ValueError("Only PREPARED or APPROVED actions can be skipped")
        experiment = self.get_experiment(action.experiment_id)
        skipped_action = action.model_copy(update={"status": DistributionActionStatus.SKIPPED})
        cancelled_experiment = experiment.model_copy(
            update={"status": DistributionExperimentStatus.CANCELLED}
        )
        self._actions[action.id] = skipped_action
        self._experiments[experiment.id] = cancelled_experiment
        self._persist_action(skipped_action)
        self._persist_experiment(cancelled_experiment)
        return DistributionExecutionPlanView(
            action=skipped_action,
            experiment=cancelled_experiment,
        )

    def mark_executed(
        self,
        action_id: UUID,
        payload: DistributionActionExecutionRequest,
    ) -> DistributionExecutionPlanView:
        action = self.get_action(action_id)
        if action.status != DistributionActionStatus.APPROVED:
            raise ValueError("Action must be APPROVED before it can be marked executed")
        experiment = self.get_experiment(action.experiment_id)
        metadata = dict(action.operational_metadata)
        if payload.external_reference is not None:
            metadata["external_reference"] = payload.external_reference
        if payload.executed_url is not None:
            metadata["executed_url"] = str(payload.executed_url)
        if payload.notes is not None:
            metadata["execution_notes"] = payload.notes

        executed_action = action.model_copy(
            update={
                "status": DistributionActionStatus.EXECUTED,
                "executed_at": datetime.now(UTC),
                "operational_metadata": metadata,
            }
        )
        running_experiment = experiment.model_copy(
            update={"status": DistributionExperimentStatus.RUNNING}
        )
        self._actions[action.id] = executed_action
        self._experiments[experiment.id] = running_experiment
        self._persist_action(executed_action)
        self._persist_experiment(running_experiment)
        return DistributionExecutionPlanView(
            action=executed_action,
            experiment=running_experiment,
        )

    def finish_experiment(self, experiment_id: UUID) -> DistributionExperimentView:
        experiment = self.get_experiment(experiment_id)
        if experiment.status != DistributionExperimentStatus.RUNNING:
            raise ValueError("Only RUNNING DistributionExperiments can be finished")
        finished = experiment.model_copy(
            update={"status": DistributionExperimentStatus.FINISHED}
        )
        self._experiments[experiment_id] = finished
        self._persist_experiment(finished)
        return finished

    def get_action(self, action_id: UUID) -> DistributionActionView:
        cached = self._actions.get(action_id)
        if cached is not None:
            return cached
        payload = self._store.get(DISTRIBUTION_ACTION_NAMESPACE, str(action_id))
        if payload is None:
            raise KeyError(action_id)
        action = DistributionActionView.model_validate(payload)
        self._actions[action_id] = action
        return action

    def get_experiment(self, experiment_id: UUID) -> DistributionExperimentView:
        cached = self._experiments.get(experiment_id)
        if cached is not None:
            return cached
        payload = self._store.get(DISTRIBUTION_EXPERIMENT_NAMESPACE, str(experiment_id))
        if payload is None:
            raise KeyError(experiment_id)
        experiment = DistributionExperimentView.model_validate(payload)
        self._experiments[experiment_id] = experiment
        return experiment

    def list_experiments(
        self,
        product_id: UUID | None = None,
    ) -> list[DistributionExperimentView]:
        for payload in self._store.list_namespace(DISTRIBUTION_EXPERIMENT_NAMESPACE):
            experiment = DistributionExperimentView.model_validate(payload)
            self._experiments[experiment.id] = experiment
        experiments = list(self._experiments.values())
        if product_id is not None:
            experiments = [
                experiment
                for experiment in experiments
                if experiment.product_id == product_id
            ]
        return sorted(experiments, key=lambda experiment: str(experiment.id))

    def resolve_experiment(
        self,
        *,
        experiment_id: UUID | None = None,
        referral_token: str | None = None,
        action_id: UUID | None = None,
    ) -> tuple[DistributionExperimentView, str]:
        resolved: list[tuple[DistributionExperimentView, str]] = []
        if experiment_id is not None:
            resolved.append((self.get_experiment(experiment_id), "experiment_id"))
        if action_id is not None:
            action = self.get_action(action_id)
            if action.experiment_id is None:
                raise KeyError(action_id)
            resolved.append((self.get_experiment(action.experiment_id), "action_id"))
        if referral_token:
            matches = [
                experiment
                for experiment in self.list_experiments()
                if experiment.referral_token == referral_token
            ]
            if len(matches) != 1:
                raise KeyError(referral_token)
            resolved.append((matches[0], "referral_token"))
        if not resolved:
            raise ValueError("At least one DistributionExperiment attribution identifier is required")

        experiment_ids = {experiment.id for experiment, _ in resolved}
        if len(experiment_ids) != 1:
            raise ValueError("Attribution identifiers point to different DistributionExperiments")
        methods = "+".join(method for _, method in resolved)
        return resolved[0][0], methods

    def get_plan(self, action_id: UUID) -> DistributionExecutionPlanView:
        action = self.get_action(action_id)
        return DistributionExecutionPlanView(
            action=action,
            experiment=self.get_experiment(action.experiment_id),
        )

    def _find_play(self, product_id: UUID, play_id: UUID) -> DistributionPlayView:
        from app.distribution_play_service import distribution_play_service

        return distribution_play_service.find(product_id, play_id)

    def _require_active_slot(
        self,
        product_id: UUID,
        play: DistributionPlayView,
    ):
        if not play.identity_required:
            return None
        if play.selected_identity_id is None:
            raise ValueError("Identity-backed play has no selected Distribution Identity")
        try:
            return distribution_control_plane_service.find_active_slot(
                play.selected_identity_id,
                product_id,
            )
        except KeyError as exc:
            raise ValueError(
                "Activate a CampaignSlot for the selected Distribution Identity before execution"
            ) from exc

    def _resolve_destination(
        self,
        product: ProductProfileView,
        payload: DistributionExecutionPrepareRequest,
    ) -> str:
        if payload.destination_url is not None:
            return str(payload.destination_url)
        if product.reference_links:
            return str(product.reference_links[0])
        raise ValueError("A destination_url or product reference link is required")

    def _resolve_target(
        self,
        play: DistributionPlayView,
        opportunity_url,
        explicit_target,
    ):
        if explicit_target is not None:
            return explicit_target
        if play.action_type == DistributionActionType.STANDALONE_POST:
            return opportunity_url
        return None

    def _tracking_base(
        self,
        destination_url: str,
        play: DistributionPlayView,
        slot_route: str | None,
    ) -> str:
        if slot_route and play.attribution_level.value in {"PROFILE", "CAMPAIGN"}:
            return slot_route
        return destination_url

    def _validate_action_ready_for_approval(
        self,
        action: DistributionActionView,
    ) -> None:
        if action.action_type in {
            DistributionActionType.COMMENT,
            DistributionActionType.REPLY,
        }:
            if action.target_url is None:
                raise ValueError("Comment/reply action requires a concrete target URL")
            if not str(action.content_payload.get("context_text") or "").strip():
                raise ValueError("Comment/reply action requires local context before approval")
            if not str(action.content_text or "").strip():
                raise ValueError("Comment/reply action requires drafted content before approval")
            return

        if action.action_type == DistributionActionType.STANDALONE_POST:
            if action.target_url is None:
                raise ValueError("Standalone post requires a target community URL")
            if not str(action.content_text or "").strip():
                raise ValueError("Standalone post requires drafted content before approval")
            return

        if action.action_type == DistributionActionType.ORGANIC_VIDEO:
            if not str(action.content_text or "").strip():
                raise ValueError("Organic video requires a script/creative brief before approval")

    def _persist_action(self, action: DistributionActionView) -> None:
        self._store.put(
            DISTRIBUTION_ACTION_NAMESPACE,
            str(action.id),
            action.model_dump(mode="json"),
        )

    def _persist_experiment(self, experiment: DistributionExperimentView) -> None:
        self._store.put(
            DISTRIBUTION_EXPERIMENT_NAMESPACE,
            str(experiment.id),
            experiment.model_dump(mode="json"),
        )

    def reset(self) -> None:
        self._actions.clear()
        self._experiments.clear()
        if self._store.ephemeral:
            self._store.clear_namespace(DISTRIBUTION_ACTION_NAMESPACE)
            self._store.clear_namespace(DISTRIBUTION_EXPERIMENT_NAMESPACE)


distribution_execution_service = InMemoryDistributionExecutionService()
