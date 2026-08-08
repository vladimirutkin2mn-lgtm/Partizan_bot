from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.config import get_settings


@dataclass(slots=True)
class LLMMessage:
    role: str
    content: str


class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, messages: list[LLMMessage]) -> str:
        raise NotImplementedError


class MockLLMProvider(LLMProvider):
    async def generate(self, messages: list[LLMMessage]) -> str:
        last_message = messages[-1].content if messages else ""
        return f"mock-response:{last_message[:120]}"


def get_llm_provider() -> LLMProvider:
    provider = get_settings().llm_provider
    if provider == "mock":
        return MockLLMProvider()
    raise ValueError(f"Unsupported LLM provider: {provider}")
