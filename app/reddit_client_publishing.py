from __future__ import annotations

from app import reddit_client_publishing_impl as _impl
from app.customer_execution_boundary import require_customer_bound_mutation_scope

for _name, _value in vars(_impl).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

_BaseCustomerRedditClientPublishService = _impl.CustomerRedditClientPublishService


class CustomerRedditClientPublishService(_BaseCustomerRedditClientPublishService):
    async def publish(
        self,
        project_id: UUID,
        customer_token: str,
        action_id: UUID,
        payload: RedditPublishRequest,
    ) -> RedditClientPublishReceipt:
        action = distribution_execution_service.get_action(action_id)
        try:
            require_customer_bound_mutation_scope(action, "Reddit client publish")
        except ValueError as exc:
            raise CustomerRedditClientPublishError(str(exc)) from exc
        return await super().publish(project_id, customer_token, action_id, payload)


customer_reddit_client_publish_service = CustomerRedditClientPublishService()
