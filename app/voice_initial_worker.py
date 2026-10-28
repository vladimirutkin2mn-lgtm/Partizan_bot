"""Explicit, bounded initial-voice worker; never registered in existing cron.

A confirmed product can queue a voice job without spending. A separately
enabled one-shot worker reserves a finite, operator-priced monthly envelope
before exactly one provider call. The envelope is an estimate, never a claim
about actual billed dollars. Unknown outcomes are never retried automatically.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any

from app.config import get_settings
from app.llm import LLMProvider, get_llm_provider
from app.product_intake import PRODUCT_INTAKE_NAMESPACE
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.voice_profiles import (
    VOICE_GENERATION_JOB_NAMESPACE,
    VoiceGenerationJob,
    VoiceProfileService,
    fingerprint,
    source_snapshot,
)

VOICE_BUDGET_SLOT_NAMESPACE = "voice_profile_monthly_budget_slots"


def _positive_decimal(raw: Any) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value.is_finite() and value > 0 else None


class VoiceInitialGenerationWorker:
    def __init__(
        self, *, store: RuntimeStateStore | None = None,
        provider: LLMProvider | None = None, settings: Any = None,
        now=None,
    ) -> None:
        self._store = store if store is not None else get_runtime_store()
        self._profiles = VoiceProfileService(self._store)
        self._provider = provider
        self._settings = settings if settings is not None else get_settings()
        self._now = now or (lambda: datetime.now(UTC))

    async def run_once(self) -> dict:
        settings = self._settings
        if not settings.voice_generation_worker_enabled:
            return {"status": "DISABLED", "provider_calls": 0}
        monthly = _positive_decimal(settings.voice_generation_monthly_budget_usd)
        per_call = _positive_decimal(settings.voice_generation_call_reservation_usd)
        max_tokens = settings.voice_generation_max_output_tokens
        if (
            monthly is None or per_call is None or per_call > monthly
            or not isinstance(max_tokens, int) or not 256 <= max_tokens <= 4000
        ):
            return {"status": "BUDGET_OR_OUTPUT_CAP_NOT_CONFIGURED", "provider_calls": 0}
        if self._provider is None and (
            settings.llm_provider != "openai" or not settings.openai_api_key
        ):
            return {"status": "PAID_PROVIDER_NOT_CONFIGURED", "provider_calls": 0}
        candidates = [
            VoiceGenerationJob.model_validate(raw)
            for raw in self._store.list_namespace(VOICE_GENERATION_JOB_NAMESPACE)
            if raw.get("status") == "RESERVED"
        ]
        for job in sorted(candidates, key=lambda j: (j.created_at, j.id)):
            try:
                self._profiles._verify_project(job.project_id, job.product_id)
            except PermissionError:
                continue
            project = self._store.get("customer_acquisition_projects", str(job.project_id))
            if not project or not project.get("understanding_confirmed"):
                continue
            product_record = self._store.get(PRODUCT_INTAKE_NAMESPACE, str(job.product_id))
            if not product_record or not isinstance(product_record.get("product"), dict):
                continue
            try:
                latest = fingerprint(source_snapshot(product_record["product"]))
            except ValueError:
                continue
            if latest != job.source_fingerprint:
                continue
            # Claim before spending. A competing worker sees the changed
            # status and cannot start the same charge, even across processes.
            claimed = job.model_copy(update={"status": "BUDGET_RESERVING"})
            if not self._store.compare_and_swap(
                VOICE_GENERATION_JOB_NAMESPACE, job.id,
                job.model_dump(mode="json"), claimed.model_dump(mode="json"),
            ):
                continue
            now = self._now()
            now = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)
            slots = min(
                100,
                int((monthly / per_call).to_integral_value(rounding=ROUND_FLOOR)),
            )
            slot_id = None
            prefix = f"{job.product_id}:{now.year:04d}-{now.month:02d}"
            for index in range(slots):
                key = f"{prefix}:{index:03d}"
                if self._store.put_if_absent(
                    VOICE_BUDGET_SLOT_NAMESPACE, key, {
                        "slot_id": key, "product_id": str(job.product_id),
                        "project_id": str(job.project_id), "job_id": job.id,
                        "estimated_reservation_usd": str(per_call),
                        "monthly_envelope_usd": str(monthly),
                        "claimed_at": now.isoformat(),
                        "actual_billed_usd": None,
                        "status": "RESERVED_ESTIMATE",
                    }
                ):
                    slot_id = key
                    break
            if slot_id is None:
                # No provider call has been made. Allow a later month to retry.
                self._store.compare_and_swap(
                    VOICE_GENERATION_JOB_NAMESPACE, job.id,
                    claimed.model_dump(mode="json"), job.model_dump(mode="json"),
                )
                return {"status": "MONTHLY_VOICE_BUDGET_EXHAUSTED", "provider_calls": 0}
            authorized = claimed.model_copy(update={
                "status": "AUTHORIZED", "budget_slot_id": slot_id,
            })
            if not self._store.compare_and_swap(
                VOICE_GENERATION_JOB_NAMESPACE, job.id,
                claimed.model_dump(mode="json"), authorized.model_dump(mode="json"),
            ):
                # Retain the slot conservatively; never silently release funds
                # when the job state is in doubt.
                return {"status": "VOICE_JOB_CLAIM_CONFLICT", "provider_calls": 0}
            try:
                provider = self._provider or get_llm_provider(max_output_tokens=max_tokens)
                if provider is None:
                    raise RuntimeError("VOICE_PROVIDER_NOT_AVAILABLE")
                usage: list[dict] = []
                version = await self._profiles.generate_reserved(
                    job.id, provider=provider, allow_llm_call=True,
                    activate_initial=True, usage_rows=usage,
                )
            except Exception as exc:
                # generate_reserved records PROVIDER_OUTCOME_UNKNOWN and never
                # retries. The budget envelope is kept even if usage absent.
                return {
                    "status": "VOICE_PROVIDER_OUTCOME_UNKNOWN",
                    "job_id": job.id, "provider_calls": 1,
                    "error_type": type(exc).__name__,
                }
            return {
                "status": "VOICE_INITIAL_CREATED",
                "job_id": job.id, "voice_profile_version_id": str(version.id),
                "provider_calls": 1,
                "estimated_reserved_usd": str(per_call),
                "actual_billed_usd": None,
                "usage": self._profiles.job(job.id).usage,
            }
        return {"status": "NO_ELIGIBLE_RESERVED_VOICE_JOBS", "provider_calls": 0}
