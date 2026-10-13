from __future__ import annotations

from app import telegram_client_publishing_impl as _impl
from app.customer_execution_boundary import require_customer_bound_mutation_scope
from app.telegram_community_restrictions import TelegramCommunityRestrictionMemory

for _name, _value in vars(_impl).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

_BaseCustomerTelegramClientPublishService = _impl.CustomerTelegramClientPublishService
_AMBIGUOUS_TELEGRAM_PUBLISH_ERRORS = frozenset({"PUBLISH_FAILED"})


class CustomerTelegramClientPublishService(_BaseCustomerTelegramClientPublishService):
    def __init__(
        self,
        *args,
        community_restrictions: TelegramCommunityRestrictionMemory | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._community_restrictions = (
            community_restrictions or TelegramCommunityRestrictionMemory(self._store)
        )

    async def publish(
        self,
        project_id: _impl.UUID,
        customer_token: str,
        action_id: _impl.UUID,
        payload: _impl.TelegramPublishRequest,
    ) -> _impl.TelegramClientPublishReceipt:
        action = _impl.distribution_execution_service.get_action(action_id)
        try:
            require_customer_bound_mutation_scope(action, "Telegram client publish")
        except ValueError as exc:
            raise _impl.CustomerTelegramClientPublishError(str(exc)) from exc
        self._require_retry_safe(action_id, payload)
        project = _impl.customer_funnel_service.get_project_payload(project_id, customer_token)
        self._require_known_community_write_allowed(
            project_id,
            project,
            action,
            payload,
        )
        receipt = await super().publish(project_id, customer_token, action_id, payload)
        self._community_restrictions.observe_publish_receipt(project_id, receipt)
        return receipt

    async def publish_internal(
        self,
        project_id: _impl.UUID,
        project: dict,
        action_id: _impl.UUID,
        payload: _impl.TelegramPublishRequest,
    ) -> _impl.TelegramClientPublishReceipt:
        action = _impl.distribution_execution_service.get_action(action_id)
        try:
            require_customer_bound_mutation_scope(action, "Telegram autonomous client publish")
        except ValueError as exc:
            raise _impl.CustomerTelegramClientPublishError(str(exc)) from exc
        self._require_retry_safe(action_id, payload)
        self._require_known_community_write_allowed(
            project_id,
            project,
            action,
            payload,
        )
        receipt = await super().publish_internal(project_id, project, action_id, payload)
        self._community_restrictions.observe_publish_receipt(project_id, receipt)
        return receipt

    def _require_known_community_write_allowed(
        self,
        project_id: _impl.UUID,
        project: dict,
        action,
        payload: _impl.TelegramPublishRequest,
    ) -> None:
        """Fail closed before Telegram I/O when a hard write restriction is already known.

        Restriction memory is deliberately execution-only: discovery/research candidates remain
        available as audience evidence even when this particular customer account cannot write to
        the community.
        """
        existing = self.get_receipt(action.id)
        if existing is not None:
            if not payload.retry:
                return
            if existing.outcome in {
                _impl.TelegramClientPublishOutcome.EXECUTED,
                _impl.TelegramClientPublishOutcome.IN_PROGRESS,
            }:
                return

        if action.platform != _impl.DistributionPlatform.TELEGRAM:
            return
        if action.experiment_id is None:
            return
        experiment = _impl.distribution_execution_service.get_experiment(action.experiment_id)
        if str(project.get("product_id") or "") != str(experiment.product_id):
            return

        try:
            target = self._parse_target(action.action_type, str(action.target_url or ""))
        except _impl.CustomerTelegramClientPublishError:
            # Preserve the canonical target-validation error from the base service.
            return

        known = self._community_restrictions.get(project_id, target.username)
        if known is None:
            return
        reason = str(known.get("reason") or "NO_WRITE_ACCESS")
        raise _impl.CustomerTelegramClientPublishError(
            "Telegram writes to this community are blocked by a known account/community "
            f"restriction ({reason})"
        )

    def _require_retry_safe(
        self,
        action_id: _impl.UUID,
        payload: _impl.TelegramPublishRequest,
    ) -> None:
        if not payload.retry:
            return
        existing = self.get_receipt(action_id)
        if (
            existing is not None
            and existing.outcome == _impl.TelegramClientPublishOutcome.FAILED
            and self._failed_publish_result_is_ambiguous(existing)
        ):
            raise _impl.CustomerTelegramClientPublishError(
                "The previous Telegram publish result is unknown; reconcile before retrying"
            )

    def _failed_publish_result_is_ambiguous(
        self,
        receipt: _impl.TelegramClientPublishReceipt,
    ) -> bool:
        metadata = receipt.metadata if isinstance(receipt.metadata, dict) else {}
        error_code = str(metadata.get("error_code") or "").strip()
        return bool(
            metadata.get("provider_error_type")
            or error_code in _AMBIGUOUS_TELEGRAM_PUBLISH_ERRORS
        )


customer_telegram_client_publish_service = CustomerTelegramClientPublishService()
