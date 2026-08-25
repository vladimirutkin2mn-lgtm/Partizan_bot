from uuid import uuid4

import app.customer_account as customer_account_module
from app.customer_account import (
    CUSTOMER_ACCOUNT_EMAIL_NAMESPACE,
    CUSTOMER_ACCOUNT_NAMESPACE,
    CUSTOMER_ACCOUNT_PROJECT_ACCESS_NAMESPACE,
    CUSTOMER_ACCOUNT_PROJECT_CLAIM_NAMESPACE,
    CUSTOMER_ACCOUNT_SESSION_NAMESPACE,
    CustomerAccountService,
)
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, CustomerFunnelService
from app.customer_schemas import CustomerPreviewRequest
from app.runtime_store import DatabaseRuntimeStateStore


def test_database_customer_registration_and_login_round_trip(monkeypatch) -> None:
    store = DatabaseRuntimeStateStore()
    funnel = CustomerFunnelService(store)
    account_service = CustomerAccountService(store)
    monkeypatch.setattr(customer_account_module, "customer_funnel_service", funnel)

    email = f"founder-{uuid4()}@example.com"
    password = "correct-horse-42"
    preview = funnel.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers with a monthly subscription.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
        )
    )

    account = None
    first_session = None
    second_session = None
    try:
        account, first_session = account_service.register(
            email=email,
            password=password,
            project_id=preview.project_id,
            customer_token=preview.customer_token,
        )

        assert account.email == email
        assert account.projects[0].project_id == preview.project_id
        assert first_session

        logged_in, second_session = account_service.login(email=email, password=password)
        assert logged_in.account_id == account.account_id
        assert logged_in.projects[0].project_id == preview.project_id
        assert second_session
    finally:
        if first_session:
            account_service.logout(first_session)
        if second_session:
            account_service.logout(second_session)
        if account is not None:
            store.delete(CUSTOMER_ACCOUNT_NAMESPACE, str(account.account_id))
            store.delete(CUSTOMER_ACCOUNT_EMAIL_NAMESPACE, email)
            store.delete(
                CUSTOMER_ACCOUNT_PROJECT_ACCESS_NAMESPACE,
                account_service._project_access_key(account.account_id, preview.project_id),
            )
        store.delete(CUSTOMER_ACCOUNT_PROJECT_CLAIM_NAMESPACE, str(preview.project_id))
        store.delete(CUSTOMER_PROJECT_NAMESPACE, str(preview.project_id))
        store.clear_namespace(CUSTOMER_ACCOUNT_SESSION_NAMESPACE)
