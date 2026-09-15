from __future__ import annotations

from app import reddit_client_publishing_impl as _impl
from app.customer_execution_boundary import require_customer_bound_mutation_scope

for _name, _value in vars(_impl).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

_BaseCustomerRedditClientPublishService = _impl.CustomerRedditClientPublishService
_AMBIGUOUS_REDDIT_PUBLISH_ERRORS = frozenset(
    {
        "API_REQUEST_FAILED",
        "PROVIDER_RESPONSE_INVALID",
        "PUBLISH_RESULT_NOT_CONFIRMED",
        "PUBLISH_RESULT_URL_INVALID",
    }
)


class CustomerRedditClientPublishService(_BaseCustomerRedditClientPublishService):
    async def publish(
        self,
        project_id: _impl.UUID,
        customer_token: str,
        action_id: _impl.UUID,
        payload: _impl.RedditPublishRequest,
    ) -> _impl.RedditClientPublishReceipt:
        action = _impl.distribution_execution_service.get_action(action_id)
        try:
            require_customer_bound_mutation_scope(action, "Reddit client publish")
        except ValueError as exc:
            raise _impl.CustomerRedditClientPublishError(str(exc)) from exc
        if payload.retry:
            existing = self.get_receipt(action_id)
            if (
                existing is not None
                and existing.outcome == _impl.RedditClientPublishOutcome.FAILED
                and self._failed_publish_result_is_ambiguous(existing)
            ):
                raise _impl.CustomerRedditClientPublishError(
                    "The previous Reddit publish result is unknown; reconcile before retrying"
                )
        return await super().publish(project_id, customer_token, action_id, payload)

    def _failed_publish_result_is_ambiguous(
        self,
        receipt: _impl.RedditClientPublishReceipt,
    ) -> bool:
        metadata = receipt.metadata if isinstance(receipt.metadata, dict) else {}
        error_code = str(metadata.get("error_code") or "").strip()
        return bool(
            metadata.get("provider_error_type")
            or error_code in _AMBIGUOUS_REDDIT_PUBLISH_ERRORS
        )


customer_reddit_client_publish_service = CustomerRedditClientPublishService()
