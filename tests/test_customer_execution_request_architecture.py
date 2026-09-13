from pathlib import Path

SERVICE = Path("app/customer_execution_requests.py").read_text(encoding="utf-8")
ROUTES = Path("app/customer_execution_request_routes.py").read_text(encoding="utf-8")
SCHEMAS = Path("app/customer_execution_request_schemas.py").read_text(encoding="utf-8")
EXECUTION = Path("app/distribution_execution_service.py").read_text(encoding="utf-8")
APPROVAL = Path("app/customer_operator_approval.py").read_text(encoding="utf-8")


def test_customer_prepare_boundary_creates_only_locked_prepared_state() -> None:
    prepare_start = ROUTES.index("def prepare_customer_execution_action(")
    approval_start = ROUTES.index("def approve_customer_execution_action(")
    prepare_block = ROUTES[prepare_start:approval_start]

    assert "/customer-execution-requests/{request_id}/prepare-action" in ROUTES
    assert "distribution_execution_service.prepare" in prepare_block
    assert "customer_execution_request_id=request.id" in prepare_block
    assert '"status": "ACTION_PREPARED"' in SERVICE
    assert '"distribution_action_id": plan.action.id' in SERVICE
    assert '"experiment_id": plan.experiment.id' in SERVICE

    assert "customer_operator_approval_service.approve" not in prepare_block
    assert "/mark-executed" not in ROUTES
    assert "/publish" not in ROUTES
    assert "paid_campaign_spec_service" not in ROUTES
    assert "confirm_autonomous_spend" not in ROUTES
    assert "execution_allowed=False" in SERVICE
    assert "customer_publish_confirmation_required=True" in SERVICE


def test_customer_prepared_action_is_content_locked_and_approval_gated() -> None:
    assert '"customer_exact_content_locked": True' in EXECUTION
    assert '"customer_publish_confirmation_required": True' in EXECUTION
    assert 'action.operational_metadata.get("customer_exact_content_locked") is True' in EXECUTION
    assert "Customer-requested action content is locked" in EXECUTION
    assert "_require_customer_publish_confirmation(action)" in EXECUTION
    assert "customer_publish_confirmed_at" in EXECUTION


def test_customer_request_and_prepare_are_explicit_operator_separated_actions() -> None:
    assert "CustomerExecutionRequestCreate" in ROUTES
    assert "confirm_request: Literal[True]" in SCHEMAS
    assert "confirm_prepare: Literal[True]" in SCHEMAS
    assert "/starting-move/execution-request" in ROUTES
    assert 'prefix="/v1"' in ROUTES
    assert "dependencies=[Depends(require_operator)]" in ROUTES


def test_operator_approval_is_a_separate_confirmed_non_execution_boundary() -> None:
    approval_start = ROUTES.index("def approve_customer_execution_action(")
    approval_block = ROUTES[approval_start:]

    assert "/customer-execution-requests/{request_id}/approve-action" in ROUTES
    assert "confirm_approval: Literal[True]" in SCHEMAS
    assert "customer_operator_approval_service.approve(request_id)" in approval_block
    assert "PUBLISH_CONFIRMED" in APPROVAL
    assert "customer_publish_confirmation_fingerprint" in APPROVAL
    assert "customer_execution_request_id" in APPROVAL
    assert "self._execution_service.approve(plan.action.id)" in APPROVAL
    assert "execute(" not in APPROVAL
    assert "publish(" not in APPROVAL
    assert "mark_executed" not in APPROVAL
    assert '"status": "OPERATOR_APPROVED"' in SERVICE
    assert "execution_allowed=False" in SERVICE
