import asyncio

from fastapi.testclient import TestClient

from app.jobs import InlineJobQueue, mock_growth_job
from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_mock_workflow_exposes_growth_loop_after_confirmation() -> None:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Value proposition: Personalized readings that remember the user's story.\n"
                "Market: US\n"
                "Goal: Acquire 100 paid users"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    confirmed = client.post(f"/v1/products/{product_id}/confirm")
    assert confirmed.status_code == 200

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
