import pytest

from app.customer_funnel import CustomerFunnelService
from app.customer_schemas import CustomerPreviewRequest
from app.runtime_store import MemoryRuntimeStateStore


class CapturedProductRequest(RuntimeError):
    pass


@pytest.mark.asyncio
async def test_customer_website_is_persisted_and_forwarded_to_product_intake(monkeypatch) -> None:
    store = MemoryRuntimeStateStore()
    service = CustomerFunnelService(store=store)
    website = "https://example.com/landing"
    preview = service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for freelancers with a monthly subscription.",
            website_url=website,
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
        )
    )
    project_id = preview.project_id
    token = preview.customer_token
    session_id = "cs_test_customer_destination"
    service.mark_checkout_pending(project_id, token, session_id)
    assert service.unlock_launch(
        project_id,
        stripe_checkout_session_id=session_id,
        stripe_customer_id=None,
    )

    saved = service.get_project(project_id, token)
    assert str(saved.website_url).rstrip("/") == website

    seen = []

    async def capture(request):
        seen.append(request)
        raise CapturedProductRequest

    monkeypatch.setattr(
        "app.customer_funnel.product_intake_service.create_draft",
        capture,
    )

    with pytest.raises(CapturedProductRequest):
        await service.start_deep_research(project_id, token)

    assert len(seen) == 1
    assert [str(item).rstrip("/") for item in seen[0].reference_links] == [website]
    assert f"Website: {website}" in seen[0].brief


def test_customer_preview_accepts_no_website_for_backwards_compatible_plan_only_flow() -> None:
    payload = CustomerPreviewRequest(
        brief="A valid product description that is long enough for the customer preview.",
        market="United States",
        goal="Validate demand",
        budget_usd=500,
    )

    assert payload.website_url is None
