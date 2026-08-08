import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from app.config import get_settings

StructuredModelT = TypeVar("StructuredModelT", bound=BaseModel)


@dataclass(slots=True)
class LLMMessage:
    role: str
    content: str


class LLMProvider(ABC):
    @abstractmethod
    async def parse(
        self,
        messages: list[LLMMessage],
        response_model: type[StructuredModelT],
    ) -> StructuredModelT:
        raise NotImplementedError


class OpenAIResponsesProvider(LLMProvider):
    def __init__(self, api_key: str, model: str) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self._model = model

    async def parse(
        self,
        messages: list[LLMMessage],
        response_model: type[StructuredModelT],
    ) -> StructuredModelT:
        response = await asyncio.to_thread(
            self._client.responses.parse,
            model=self._model,
            input=[
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            text_format=response_model,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("LLM returned no structured output")
        return parsed


def get_llm_provider() -> LLMProvider | None:
    settings = get_settings()
    if settings.llm_provider == "mock":
        return None
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        return OpenAIResponsesProvider(
            api_key=settings.openai_api_key,
            model=settings.llm_model,
        )
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")
