from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_customer_workspace_login_is_compact_and_focused() -> None:
    response = client.get("/workspace")

    assert response.status_code == 200
    html = response.text
    assert 'class="login-gate auth-login hidden"' in html
    assert 'class="auth-card"' in html
    assert 'id="workspace-login-form"' in html
    assert 'id="workspace-login-email"' in html
    assert 'id="workspace-login-password"' in html
    assert "Sign in to Partizan." in html
    assert "Continue to your customer acquisition workspace." in html
    assert "Run the free scan →" in html
    assert "login-pills" not in html
    assert "Performance</span><span>Channels" not in html


def test_customer_workspace_login_has_full_width_auth_controls() -> None:
    html = client.get("/workspace").text

    assert ".auth-card{width:min(100%,470px)" in html
    assert ".auth-field input{width:100%;height:48px" in html
    assert ".auth-submit{width:100%;height:48px" in html
