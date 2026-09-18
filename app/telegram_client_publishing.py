from __future__ import annotations

from app import telegram_client_publishing_impl as _impl
from app.customer_execution_boundary import require_customer_bound_mutation_scope

for _name, _value in vars(_impl).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

_BaseCustomerTelegramClientPublishService = _impl.CustomerTelegramClientPublishService
_AMBIGUOUS_TELEGRAM_PUBLISH_ERRORS = frozenset({"PUBLISH_FAILED"})


class CustomerTelegramClientPublishService(_BaseCustomerTelegramClientPublishService):
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
        return await super().publish(project_id, customer_token, action_id, payload)

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
        return await super().publish_internal(project_id, project, action_id, payload)

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
