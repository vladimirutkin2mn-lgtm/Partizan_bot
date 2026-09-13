from pathlib import Path

SERVICE = Path("app/customer_execution_requests.py").read_text(encoding="utf-8")
ROUTES = Path("app/customer_execution_request_routes.py").read_text(encoding="utf-8")


def test_execution_request_layer_does_not_create_or_approve_execution_state() -> None:
    source = f"{SERVICE}\n{ROUTES}"

    assert "distribution_execution_service" not in source
    assert "DistributionAction" not in source
    assert "DistributionExperiment" not in source
    assert "/approve" not in source
    assert "/publish" not in source
    assert "confirm_autonomous_spend" not in source
    assert "execution_allowed=False" in SERVICE
    assert "customer_publish_confirmation_required=True" in SERVICE


def test_customer_request_is_explicit_and_operator_queue_is_separate() -> None:
    assert "CustomerExecutionRequestCreate" in ROUTES
    assert "confirm_request: Literal[True]" in Path(
        "app/customer_execution_request_schemas.py"
    ).read_text(encoding="utf-8")
    assert "/starting-move/execution-request" in ROUTES
    assert 'prefix="/v1"' in ROUTES
    assert "dependencies=[Depends(require_operator)]" in ROUTES
