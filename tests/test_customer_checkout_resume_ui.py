from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_paid_plan_checkout_resumes_directly_into_research() -> None:
    javascript = client.get("/start/assets/start.v2.js")

    assert javascript.status_code == 200
    source = javascript.text
    assert "const pollEntitlement = async () =>" in source
    assert "if (project.launch_unlocked)" in source
    assert "showUnlocked();\n        await startResearch(true);" in source
    assert "$('research-button').disabled = false;" in source
    already_unlocked_resume = (
        "if (data.already_unlocked) {\n"
        "        showUnlocked();\n"
        "        await startResearch(true);"
    )
    assert already_unlocked_resume in source
