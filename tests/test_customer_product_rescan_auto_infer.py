"""Short, founder-supplied Telegram context should not need marketing expertise."""
from fastapi.testclient import TestClient

from app.customer_account import customer_account_service
from app.customer_funnel import customer_funnel_service
from app.main import app
from app.product_understanding_autofill import infer_telegram_channel
from app.runtime_store import get_runtime_store


def test_femdom_telegram_topic_autofill_is_minimal_and_no_invented_product_features():
    derived = infer_telegram_channel(
        project_name="FemDom", saved_name="Promoted asset: FemDom",
        description="Это телеграмм канал для тех, кому нравится femdom",
        project_type="TELEGRAM_COMMUNITY",
        source_url="https://t.me/+private-invite",
    )
    assert derived is not None
    assert derived.name == "FemDom"
    assert derived.product_type == "Telegram channel"
    assert derived.description == "Это телеграмм канал для тех, кому нравится femdom"
    assert "FemDom" in derived.known_audience
    assert "FemDom" in derived.value_proposition
    for unsupported in ("AI", "paid", "chat", "bot", "subscription fee"):
        assert unsupported not in derived.value_proposition


def test_no_fabricated_audience_from_generic_telegram_group_or_campaign():
    assert infer_telegram_channel(
        project_name="Existing community", saved_name="Promoted asset",
        description="Test budget: $20. Get followers from an unknown asset.",
        project_type="TELEGRAM_COMMUNITY",
        source_url="https://t.me/+hidden",
    ) is None


def test_femdom_one_sentence_confirmation_without_value_or_audience():
    customer_account_service.reset()
    customer_funnel_service.reset()
    client = TestClient(app)
    from app.customer_schemas import CustomerPreviewRequest

    preview = customer_funnel_service.create_preview(CustomerPreviewRequest(
        brief="Test signup for a separate existing customer account.",
        market="US", goal="Grow audience", budget_usd=10,
    ))
    account = client.post("/customer/account/register", json={
        "email": "rescan-auto-infer@example.com", "password": "test-password-autofill-42",
        "project_id": str(preview.project_id), "customer_token": preview.customer_token,
    })
    assert account.status_code == 200, account.text
    project = client.post("/customer/account/projects", json={
        "name": "FemDom", "project_type": "TELEGRAM_COMMUNITY",
        "reference_url": "https://t.me/+inviteForTesting",
        "brief": "Это телеграмм канал для тех, кому нравится femdom",
        "market": "Europe", "goal": "Grow subscribers", "budget_usd": 10,
    })
    assert project.status_code == 201, project.text
    project_id = project.json()["project_id"]
    store = get_runtime_store()
    assert store.get("customer_acquisition_projects", project_id)["product_id"] is None
    rescan_url = f"/customer/account/projects/{project_id}/product/rescan"
    preview_rescan = client.post(rescan_url)
    assert preview_rescan.status_code == 200, preview_rescan.text
    draft = preview_rescan.json()
    assert draft["source_status"] == "INSUFFICIENT_PUBLIC_EVIDENCE"
    assert draft["inference_basis"] == "PROJECT_NAME_AND_DESCRIPTION"
    assert draft["can_confirm"] is True
    assert draft["proposed"]["name"] == "FemDom"
    assert "FemDom" in draft["proposed"]["value_proposition"]
    assert "FemDom" in draft["proposed"]["known_audience"]
    assert store.get("customer_acquisition_projects", project_id)["product_id"] is None

    confirmed = client.post(rescan_url + "/confirm", json={
        "scan_id": draft["scan_id"],
        "name": "FemDom",
        "description": "Это телеграмм канал для тех, кому нравится femdom",
    })
    assert confirmed.status_code == 200, confirmed.text
    product_id = confirmed.json()["product_id"]
    product = store.get("product_intake", product_id)["product"]
    assert product["product_type"] == "Telegram channel"
    assert product["value_proposition"] == draft["proposed"]["value_proposition"]
    assert product["known_audience"] == [draft["proposed"]["known_audience"]]
    assert product["use_cases"] == []
    assert product["status"] == "CONFIRMED"
    assert store.get("customer_acquisition_projects", project_id)["voice_refresh_status"] == "REVIEW_REQUIRED"
    assert store.get("voice_profile_assignments", f"{project_id}:{product_id}") is None


def test_short_brief_edit_recomputes_topic_value_without_trusting_stale_draft():
    from app.product_understanding_autofill import infer_telegram_channel

    current = infer_telegram_channel(
        project_name="FemDom", saved_name="FemDom",
        description="Это телеграмм канал для тех, кому нравится femdom",
        project_type="TELEGRAM_COMMUNITY", source_url="https://t.me/+private-invite",
    )
    changed = infer_telegram_channel(
        project_name="FemDom", saved_name="FemDom",
        description="Канал о femdom: тематический контент и авторские публикации",
        project_type="TELEGRAM_COMMUNITY", source_url="https://t.me/+private-invite",
    )
    assert current and changed
    assert changed.description != current.description
    assert changed.value_proposition == current.value_proposition
