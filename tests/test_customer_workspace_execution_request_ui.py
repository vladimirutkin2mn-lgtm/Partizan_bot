from pathlib import Path

JS = Path("app/web/workspace.execution-request.v1.js").read_text(encoding="utf-8")


def test_execution_request_ui_waits_for_accepted_ready_handoff() -> None:
    assert "draft.review_status === 'ACCEPTED'" in JS
    assert "setup.state === 'READY_FOR_HANDOFF'" in JS
    assert "setup.platform === draft.platform" in JS
    assert "card.classList.add('hidden')" in JS


def test_execution_request_is_created_only_by_explicit_customer_click() -> None:
    assert "Request one prepared action →" in JS
    assert "execution-request-submit" in JS
    assert "$('execution-request-submit')?.addEventListener('click', async () =>" in JS
    assert "/starting-move/execution-request" in JS
    assert "{ method: 'POST', body: JSON.stringify({ confirm_request: true }) }" in JS
    assert JS.count("method: 'POST'") == 1
    assert "confirm_request: true" in JS


def test_execution_request_ui_keeps_final_execution_permissions_separate() -> None:
    assert "it does not approve execution or authorize publishing, account access or spend" in JS
    assert "Nothing was approved, published or funded by this request." in JS
    assert "Final customer publish confirmation remains required." in JS
    assert "/approve" not in JS
    assert "/publish" not in JS
    assert "/connection" not in JS
    assert "confirm_autonomous_spend" not in JS
    assert "method: 'PUT'" not in JS
    assert "method: 'PATCH'" not in JS
    assert "method: 'DELETE'" not in JS


def test_requested_state_is_read_only_and_visible_after_refresh() -> None:
    assert "executionRequest.status === 'REQUESTED'" in JS
    assert "One action is queued for preparation." in JS
    assert "Requested" in JS
    assert "partizan:workspace-ready" in JS
    assert "partizan:workspace-state-updated" in JS
