from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_customer_workspace_login_is_compact_and_focused() -> None:
    response = client.get("/workspace")

    assert response.status_code == 200
    html = response.text
    assert 'class="login-gate rd-auth hidden"' in html
    assert 'class="auth-card rd-auth-form"' in html
    assert 'id="workspace-login-form"' in html
    assert 'id="workspace-login-email"' in html
    assert 'id="workspace-login-password"' in html
    assert "Sign in to Partizan." in html
    assert "Continue to your customer acquisition workspace." in html
    assert "Analyze your product →" in html
    assert "login-pills" not in html
    assert "Performance</span><span>Channels" not in html


def test_customer_workspace_login_has_full_width_auth_controls() -> None:
    html = client.get("/workspace").text

    css = client.get("/workspace/assets/design.v1.css").text
    assert "rd-auth-story" in html
    assert "auth-submit rd-btn rd-full" in html
    assert ".rd-auth{max-width:1110px" in css
    assert ".rd-auth-story{display:none}" in css
    assert 'autocomplete="current-password"' in html
    assert "Explore demo workspace" not in html
