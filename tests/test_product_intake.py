from typing import TypeVar

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.llm import LLMMessage, LLMProvider
from app.main import app
from app.product_agent import ProductIntakeAgent
from app.product_intake import product_intake_service

client = TestClient(app)
StructuredModelT = TypeVar("StructuredModelT", bound=BaseModel)


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()


class ScriptedProvider(LLMProvider):
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[list[LLMMessage]] = []

    async def parse(
        self,
        messages: list[LLMMessage],
        response_model: type[StructuredModelT],
    ) -> StructuredModelT:
        self.calls.append(messages)
        return response_model.model_validate(self.responses.pop(0))


@pytest.mark.asyncio
async def test_agent_extracts_structured_profile() -> None:
    provider = ScriptedProvider([{
        "name": "Pulse",
        "description": "Analytics assistant",
        "problem_or_desire": "Understand product metrics faster",
        "value_proposition": "Fast personalized analysis",
        "market": "US",
        "goal": "Acquire 100 paid users",
        "assumptions": ["Founder-led sales may be available"],
        "clarifications": [],
    }])
    analysis = await ProductIntakeAgent(provider).analyze(
        brief="Pulse is an analytics assistant for US startups.",
        reference_links=[],
    )
    assert analysis.name == "Pulse"
    assert analysis.market == "US"
    assert analysis.assumptions == ["Founder-led sales may be available"]
    assert "do not browse the web" in provider.calls[0][0].content.lower()


@pytest.mark.asyncio
async def test_agent_limits_questions_to_three() -> None:
    provider = ScriptedProvider([{
        "name": "Pulse",
        "description": "Analytics assistant",
        "clarifications": [{
            "field_name": "pricing_model",
            "question": "How is it monetized?",
            "rationale": "Changes economics.",
            "priority": 2,
        }],
    }])
    analysis = await ProductIntakeAgent(provider).analyze(
        brief="Pulse helps teams understand metrics.",
        reference_links=[],
    )
    assert len(analysis.clarifications) == 3
    assert {item.field_name for item in analysis.clarifications} == {
        "goal", "market", "problem_or_desire"
    }


@pytest.mark.asyncio
async def test_agent_surfaces_contradiction() -> None:
    provider = ScriptedProvider([{
        "name": "Pulse",
        "description": "Analytics assistant",
        "value_proposition": "Fast analysis",
        "market": "US",
        "goal": "Acquire users",
        "contradictions": ["The brief gives two different prices."],
        "clarifications": [],
    }])
    analysis = await ProductIntakeAgent(provider).analyze(
        brief="Pulse has inconsistent pricing details.",
        reference_links=[],
    )
    assert any(q.field_name.startswith("contradiction_") for q in analysis.clarifications)


def test_free_text_intake_requires_explicit_confirmation() -> None:
    response = client.post("/v1/products", json={"brief": (
        "Product: Pulse\n"
        "Description: Analytics assistant for startup teams.\n"
        "Value proposition: Faster understanding of product metrics.\n"
        "Market: US\n"
        "Goal: Acquire 100 paid users"
    )})
    body = response.json()
    assert body["product"]["status"] == "DRAFT"
    assert body["next_action"] == "confirm"
    product_id = body["product"]["id"]
    confirmed = client.post(f"/v1/products/{product_id}/confirm")
    assert confirmed.json()["product"]["status"] == "CONFIRMED"


def test_clarification_answers_reach_confirmable_draft() -> None:
    response = client.post("/v1/products", json={"brief": (
        "Product: Pulse\nDescription: Analytics assistant for startup teams."
    )})
    current = response.json()
    product_id = current["product"]["id"]
    answers = {
        "problem_or_desire": "Understand product metrics faster",
        "market": "US",
        "goal": "Acquire 100 paid users",
    }
    while current["clarifications"]:
        question = current["clarifications"][0]
        result = client.post(
            f"/v1/products/{product_id}/clarifications",
            json={"question_id": question["id"], "answer": answers[question["field_name"]]},
        )
        current = result.json()
    assert current["product"]["status"] == "DRAFT"
    assert current["next_action"] == "confirm"


def test_confirm_blocked_with_open_questions() -> None:
    response = client.post("/v1/products", json={"brief": (
        "Product: Pulse\nDescription: Analytics assistant for startup teams."
    )})
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 409
