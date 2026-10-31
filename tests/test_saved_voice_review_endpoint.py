"""Generated author voice must be visible to its owner and read-only."""
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.customer_account import customer_account_service
from app.customer_funnel import customer_funnel_service
from app.main import app
from app.runtime_store import get_runtime_store
from app.voice_profiles import (
    VOICE_ASSIGNMENT_NAMESPACE,
    VOICE_VERSION_NAMESPACE,
    VoiceAssignment,
    VoiceExample,
    VoiceOrigin,
    VoiceProfileDraft,
    VoiceProfileVersion,
    fingerprint,
    source_snapshot,
)


def _owner(email: str) -> TestClient:
    from app.customer_schemas import CustomerPreviewRequest

    client = TestClient(app)
    preview = customer_funnel_service.create_preview(CustomerPreviewRequest(
        brief="A valid application project to register account access.",
        market="US", goal="Grow subscribers", budget_usd=10,
    ))
    result = client.post("/customer/account/register", json={
        "email": email, "password": "voice-review-test-password-42",
        "project_id": str(preview.project_id), "customer_token": preview.customer_token,
    })
    assert result.status_code == 200, result.text
    return client


def _profile() -> VoiceProfileDraft:
    return VoiceProfileDraft(
        character_summary="Observant and quick-witted, focused on the detail in a conversation.",
        default_tone="Confident, warm and lightly playful.",
        speech_traits=["Short varied sentences", "Specific observations", "Natural rhythm"],
        adaptation_rules=["Adapt to the community language", "Avoid a canned joke"],
        style_instruction=(
            "Write short, direct messages with naturally varied rhythm. "
            "React to the most distinctive detail in the latest post, not a generic theme. "
            "Use an occasional surprising observation when the context invites it. "
            "Never pitch a product or invent personal experiences."
        ),
        derivation_summary="The adult conversational community values context-specific attention.",
        fact_refs=["description", "known_audience"],
        examples=[
            VoiceExample(
                surface="CHANNEL_COMMENT", context="Party dress code",
                message="И дресс-код внезапно стал главной интригой вечера.",
            ),
            VoiceExample(
                surface="STANDALONE_POST", context="General group exchange",
                message="Что-то подсказывает, сегодня будет интересная дискуссия.",
            ),
        ],
    )


def test_owner_sees_complete_saved_voice_and_preserves_it_after_rescan():
    customer_account_service.reset()
    customer_funnel_service.reset()
    client = _owner("saved-voice-review@example.com")
    created = client.post("/customer/account/projects", json={
        "name": "FemDom", "project_type": "TELEGRAM_COMMUNITY",
        "reference_url": "https://t.me/+testChannelReview",
        "brief": "Это телеграмм канал для тех, кому нравится femdom",
        "market": "Europe", "goal": "Grow subscribers", "budget_usd": 10,
    })
    assert created.status_code == 201, created.text
    project_id = created.json()["project_id"]
    url = f"/customer/account/projects/{project_id}/product/rescan"
    assert client.get(url).json()["voice"]["status"] == "NOT_GENERATED"

    draft = client.post(url).json()
    confirmed = client.post(url + "/confirm", json={
        "scan_id": draft["scan_id"],
        "name": "FemDom",
        "description": "Это телеграмм канал для тех, кому нравится femdom",
    })
    assert confirmed.status_code == 200, confirmed.text
    product_id = confirmed.json()["product_id"]
    store = get_runtime_store()
    product = store.get("product_intake", product_id)["product"]
    project = store.get("customer_acquisition_projects", project_id)
    project["voice_refresh_status"] = "CURRENT"
    store.put("customer_acquisition_projects", project_id, project)

    vid = uuid4()
    author = _profile()
    version = VoiceProfileVersion(
        id=vid, project_id=UUID(project_id), product_id=UUID(product_id),
        tenant_scope_id=project_id, version=1, origin=VoiceOrigin.INITIAL,
        generation_job_id="test-author-voice-1",
        source_fingerprint=fingerprint(source_snapshot(product)),
        source_snapshot=source_snapshot(product),
        generator_prompt_version="voice_generator_prompt_v2_explicit_schema",
        generation_config_id="test", content_hash=fingerprint(author.model_dump(mode="json")),
        created_at="2026-10-31T07:00:00+00:00", profile=author,
    )
    store.put(VOICE_VERSION_NAMESPACE, str(vid), version.model_dump(mode="json"))
    assignment = VoiceAssignment(
        project_id=UUID(project_id), product_id=UUID(product_id), tenant_scope_id=project_id,
        active_version_id=vid, assignment_revision=1, updated_at="2026-10-31T07:00:00+00:00",
        reason="test saved voice",
    )
    store.put(VOICE_ASSIGNMENT_NAMESPACE, f"{project_id}:{product_id}",
              assignment.model_dump(mode="json"))

    before = store.get("customer_acquisition_projects", project_id)
    result = client.get(url)
    assert result.status_code == 200, result.text
    view = result.json()["voice"]
    assert view["status"] == "SAVED"
    assert view["active_version_id"] == str(vid)
    assert len(view["versions"]) == 1
    assert view["versions"][0]["status"] == "ASSIGNED_VOICE"
    assert view["versions"][0]["character"] == author.model_dump(mode="json")
    assert store.get("customer_acquisition_projects", project_id) == before

    second = client.post(url).json()
    assert second["status"] == "PENDING"
    revised = client.post(url + "/confirm", json={
        "scan_id": second["scan_id"],
        "name": "FemDom",
        "description": "Это тематический телеграм-канал про FemDom, общение и публикации.",
    })
    assert revised.status_code == 200, revised.text
    after = client.get(url).json()["voice"]
    assert after["status"] == "REVIEW_REQUIRED"
    assert after["active_version_id"] == str(vid)
    assert after["versions"][0]["status"] == "PREVIOUS_VOICE_REVIEW_REQUIRED"
    assert after["versions"][0]["source_matches_current_product"] is False
    assert after["versions"][0]["character"] == author.model_dump(mode="json")

    other = _owner("unrelated-saved-voice-review@example.com")
    assert other.get(url).status_code == 403


def test_workspace_contains_character_history_without_automatic_generation():
    from pathlib import Path

    js = Path("app/web/workspace.projects.v1.js").read_text()
    css = Path("app/web/workspace.projects.v1.css").read_text()
    for token in (
        "Generated character &amp; tone",
        "product-voice-versions",
        "Previous, needs review",
        "Complete style instruction",
        "not a published message",
        "card.renderSavedVoices(result.voice)",
    ):
        assert token in js
    assert ".product-voice-version" in css
    assert "Rescan creates a product draft; it does not generate a new character." in js
