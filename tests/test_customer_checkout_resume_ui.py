from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_paid_plan_checkout_resumes_directly_into_research() -> None:
    javascript = client.get("/start/assets/start.v2.js")

    assert javascript.status_code == 200
    source = javascript.text
    assert "const pollEntitlement = async () =>" in source
    assert "if (project.launch_unlocked)" in source
    assert "showUnlocked();\n        await startResearch();" in source
    assert "$('research-button').disabled = false;" in source
    already_unlocked_resume = (
        "if (data.already_unlocked) {\n"
        "        showUnlocked();\n"
        "        await startResearch();"
    )
    assert already_unlocked_resume in source


def test_autonomous_callbacks_are_handed_back_to_workspace() -> None:
    javascript = client.get("/start/assets/start.v2.js")

    assert javascript.status_code == 200
    source = javascript.text
    assert "if (projectId && (growthBalanceState || metaState))" in source
    assert "await accountOwnsProject(projectId)" in source
    assert "window.location.replace(`/workspace?${query.toString()}`)" in source
