import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from app.config import get_settings
from app.openai_usage import log_openai_usage, openai_task_options

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
    def __init__(
        self, api_key: str, model: str, *,
        max_output_tokens: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        from openai import OpenAI

        options = {"max_retries": max_retries} if max_retries is not None else {}
        self._client = OpenAI(api_key=api_key, **options)
        self._model = model
        self._max_output_tokens = max_output_tokens

    async def parse(
        self,
        messages: list[LLMMessage],
        response_model: type[StructuredModelT],
    ) -> StructuredModelT:
        options = openai_task_options(self._model)
        if self._max_output_tokens is not None:
            options["max_output_tokens"] = self._max_output_tokens
        started = time.perf_counter()
        response = await asyncio.to_thread(
            self._client.responses.parse,
            **options,
            input=[
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            text_format=response_model,
        )
        log_openai_usage(
            response,
            options=options,
            duration_seconds=time.perf_counter() - started,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("LLM returned no structured output")
        return parsed


def get_llm_provider(
    *, max_output_tokens: int | None = None,
    max_retries: int | None = None,
) -> LLMProvider | None:
    settings = get_settings()
    if settings.llm_provider == "mock":
        return None
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        return OpenAIResponsesProvider(
            api_key=settings.openai_api_key,
            model=settings.llm_model,
            max_output_tokens=max_output_tokens,
            max_retries=max_retries,
        )
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")
