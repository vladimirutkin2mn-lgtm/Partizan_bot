import asyncio

from fastapi.testclient import TestClient

from app.jobs import InlineJobQueue, mock_growth_job
from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_product_intake_asks_for_missing_high_value_fields() -> None:
    response = client.post(
        "/v1/products",
        json={
            "name": "Oracle",
            "description": (
                "AI entertainment product that gives personalized relationship readings."
            ),
            "price": 9.99,
            "pricing_model": "subscription",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["product"]["status"] == "NEEDS_CLARIFICATION"
    assert [item["field_name"] for item in body["clarifications"]] == [
        "value_proposition",
        "market",
        "goal",
    ]


def test_product_intake_confirms_complete_profile() -> None:
    response = client.post(
        "/v1/products",
        json={
            "name": "Oracle",
            "description": (
                "AI entertainment product that gives personalized relationship readings."
            ),
            "value_proposition": "Instant personalized readings available at any time.",
            "market": "US",
            "goal": "Acquire 100 paid users",
            "budget": 500,
            "max_cac": 5,
        },
    )
    assert response.status_code == 201
    assert response.json()["product"]["status"] == "CONFIRMED"
    assert response.json()["clarifications"] == []


def test_clarification_flow_reaches_confirmed() -> None:
    response = client.post(
        "/v1/products",
        json={
            "name": "Oracle",
            "description": (
                "AI entertainment product that gives personalized relationship readings."
            ),
            "market": "US",
            "goal": "Acquire 100 paid users",
        },
    )
    body = response.json()
    product_id = body["product"]["id"]
    question = body["clarifications"][0]

    answer = client.post(
        f"/v1/products/{product_id}/clarifications",
        json={
            "question_id": question["id"],
            "answer": "Personalized readings that remember the user's story.",
        },
    )
    assert answer.status_code == 200
    assert answer.json()["product"]["status"] == "CONFIRMED"


def test_mock_workflow_exposes_growth_loop() -> None:
    response = client.post(
        "/v1/products",
        json={
            "name": "Oracle",
            "description": (
                "AI entertainment product that gives personalized relationship readings."
            ),
            "value_proposition": "Instant personalized readings available at any time.",
            "market": "US",
            "goal": "Acquire 100 paid users",
        },
    )
    product_id = response.json()["product"]["id"]
    workflow = client.post(f"/v1/products/{product_id}/mock-workflow")

    assert workflow.status_code == 200
    assert [stage["name"] for stage in workflow.json()["stages"]] == [
        "product_profile",
        "icp_generation",
        "channel_discovery",
        "growth_play_generation",
        "experiment",
        "measurement",
        "decision",
    ]


def test_inline_queue_executes_job() -> None:
    async def run() -> tuple[str, dict[str, str]]:
        queue = InlineJobQueue()
        job_id = await queue.enqueue(mock_growth_job, "product-1")
        return job_id, queue.completed_jobs[job_id]

    job_id, result = asyncio.run(run())
    assert job_id == "inline-1"
    assert result["next_stage"] == "icp_generation"
