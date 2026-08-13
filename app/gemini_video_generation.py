from __future__ import annotations

import base64
import binascii
import json
from typing import Protocol

import httpx

from app.config import get_settings
from app.creative_assets import CreativeBriefView, CreativeMediaType
from app.creative_blob_store import CreativeBlobStore, creative_blob_store
from app.creative_generation import (
    CreativeGenerationOutcome,
    CreativeGenerator,
    CreativeGeneratorResult,
    UnavailableCreativeGenerator,
    build_creative_generator,
)
from app.distribution_types import DistributionPlatform


class GeminiOmniVideoApiError(RuntimeError):
    pass


class GeminiOmniVideoClient(Protocol):
    def generate_mp4(
        self,
        *,
        model: str,
        prompt: str,
        aspect_ratio: str,
    ) -> bytes: ...


class HttpxGeminiOmniVideoClient:
    def __init__(self, *, api_key: str, timeout_seconds: float = 180.0) -> None:
        normalized = api_key.strip()
        if not normalized:
            raise ValueError("Gemini API key cannot be empty")
        self._api_key = normalized
        self._timeout_seconds = timeout_seconds

    def generate_mp4(
        self,
        *,
        model: str,
        prompt: str,
        aspect_ratio: str,
    ) -> bytes:
        url = "https://generativelanguage.googleapis.com/v1beta/interactions"
        payload = {
            "model": model,
            "input": prompt,
            "response_format": {
                "type": "video",
                "aspect_ratio": aspect_ratio,
            },
        }
        try:
            response = httpx.post(
                url,
                json=payload,
                headers={
                    "x-goog-api-key": self._api_key,
                    "Content-Type": "application/json",
                },
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise GeminiOmniVideoApiError("Gemini video generation request failed") from exc

        body = self._response_body(response)
        encoded, mime_type = self._find_inline_video(body)
        if mime_type != "video/mp4":
            raise GeminiOmniVideoApiError("Gemini video generation did not return video/mp4")
        try:
            video = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise GeminiOmniVideoApiError(
                "Gemini video generation returned invalid base64 video data"
            ) from exc
        if not video:
            raise GeminiOmniVideoApiError("Gemini video generation returned an empty video")
        return video

    def _response_body(self, response: httpx.Response) -> dict:
        try:
            body = response.json()
        except ValueError as exc:
            raise GeminiOmniVideoApiError("Gemini video generation returned invalid JSON") from exc
        if response.status_code >= 400:
            raise GeminiOmniVideoApiError(
                f"Gemini video generation HTTP {response.status_code}"
            )
        if not isinstance(body, dict):
            raise GeminiOmniVideoApiError("Gemini video generation returned an invalid response")
        return body

    def _find_inline_video(self, body: dict) -> tuple[str, str]:
        steps = body.get("steps")
        if not isinstance(steps, list):
            raise GeminiOmniVideoApiError("Gemini response contained no generation steps")
        for step in steps:
            if not isinstance(step, dict):
                continue
            content = step.get("content")
            items = content if isinstance(content, list) else [content]
            for item in items:
                if not isinstance(item, dict) or item.get("type") != "video":
                    continue
                encoded = item.get("data")
                mime_type = str(item.get("mime_type") or "")
                if isinstance(encoded, str) and encoded:
                    return encoded, mime_type
        raise GeminiOmniVideoApiError(
            "Gemini video response contained no inline video data within the Partizan size path"
        )


class GeminiOmniTikTokVideoGenerator:
    def __init__(
        self,
        *,
        client: GeminiOmniVideoClient,
        blob_store: CreativeBlobStore,
        public_base_url: str,
        model: str = "gemini-omni-flash-preview",
    ) -> None:
        self._client = client
        self._blob_store = blob_store
        self._public_base_url = public_base_url.rstrip("/")
        self._model = model.strip() or "gemini-omni-flash-preview"

    def generate(self, brief: CreativeBriefView) -> CreativeGeneratorResult:
        if (
            brief.platform != DistributionPlatform.TIKTOK
            or brief.media_type != CreativeMediaType.VIDEO
        ):
            return CreativeGeneratorResult(
                outcome=CreativeGenerationOutcome.UNAVAILABLE,
                message="Gemini Omni video generation currently applies only to TikTok VIDEO briefs.",
                provenance={"generator": "gemini_omni", "model": self._model},
            )
        try:
            video = self._client.generate_mp4(
                model=self._model,
                prompt=self._prompt(brief),
                aspect_ratio="9:16",
            )
            blob = self._blob_store.put(data=video, mime_type="video/mp4")
        except Exception:
            return CreativeGeneratorResult(
                outcome=CreativeGenerationOutcome.FAILED,
                message=(
                    "Gemini Omni video generation or bounded Partizan video persistence failed."
                ),
                provenance={
                    "generator": "gemini_omni",
                    "model": self._model,
                    "aspect_ratio": "9:16",
                },
            )
        return CreativeGeneratorResult(
            outcome=CreativeGenerationOutcome.READY,
            public_url=f"{self._public_base_url}/v1/public/creative-blobs/{blob.id}",
            mime_type="video/mp4",
            width=720,
            height=1280,
            provenance={
                "generator": "gemini_omni",
                "model": self._model,
                "aspect_ratio": "9:16",
                "blob_id": str(blob.id),
                "sha256": blob.sha256,
            },
            message=(
                "Gemini Omni generated a vertical MP4 source creative; TikTok provider upload "
                "is still required before paid staging."
            ),
        )

    def _prompt(self, brief: CreativeBriefView) -> str:
        content = json.dumps(
            brief.content,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        constraints = "\n".join(f"- {item}" for item in brief.constraints)
        return (
            "Create one short vertical 9:16 mobile advertising video for TikTok, approximately "
            "6 to 8 seconds long. Make the first visual immediately attention-grabbing and keep "
            "the pacing fast enough for a paid social test. Use the confirmed campaign brief only. "
            "Do not invent product facts, testimonials, endorsements, ratings, awards, scarcity, "
            "guarantees, or performance claims. Avoid text-heavy scenes because ad copy is supplied "
            "separately. Do not add spoken product claims unless the exact wording is present in the "
            "confirmed brief; prefer visual storytelling with ambient or instrumental audio.\n\n"
            f"Confirmed creative brief:\n{content}\n\nHard constraints:\n{constraints}"
        )[:12000]


class ConfiguredMultimediaCreativeGenerator:
    def __init__(
        self,
        *,
        image_generator: CreativeGenerator,
        video_generator: CreativeGenerator,
    ) -> None:
        self._image_generator = image_generator
        self._video_generator = video_generator

    def generate(self, brief: CreativeBriefView) -> CreativeGeneratorResult:
        if brief.media_type == CreativeMediaType.VIDEO:
            return self._video_generator.generate(brief)
        return self._image_generator.generate(brief)


def build_multimedia_creative_generator() -> CreativeGenerator:
    settings = get_settings()
    image_generator = build_creative_generator()
    if settings.creative_video_provider != "gemini_omni":
        video_generator: CreativeGenerator = UnavailableCreativeGenerator()
    elif not settings.gemini_api_key:
        video_generator = UnavailableCreativeGenerator()
    elif not settings.partizan_public_base_url:
        video_generator = UnavailableCreativeGenerator()
    else:
        video_generator = GeminiOmniTikTokVideoGenerator(
            client=HttpxGeminiOmniVideoClient(api_key=settings.gemini_api_key),
            blob_store=creative_blob_store,
            public_base_url=settings.partizan_public_base_url,
            model=settings.creative_video_model,
        )
    return ConfiguredMultimediaCreativeGenerator(
        image_generator=image_generator,
        video_generator=video_generator,
    )
