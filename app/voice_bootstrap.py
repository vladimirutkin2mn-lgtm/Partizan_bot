"""Explicit bootstrap for a confirmed, existing product's initial LLM voice.

Default operation is a read-only preflight. A distinct reserve-only invocation
creates one durable job without provider spending. A third, explicitly approved
manual command may process ONLY the reviewed job ID within an estimated budget.
No Telegram I/O, background scheduling, profile edits or writer activation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from app.config import get_settings
from app.product_intake import PRODUCT_INTAKE_NAMESPACE
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.voice_initial_worker import VoiceInitialGenerationWorker, _positive_decimal
from app.voice_profiles import (
    VOICE_GENERATION_JOB_NAMESPACE,
    VOICE_GENERATOR_PROMPT_VERSION,
    VoiceGenerationJob,
    VoiceProfileService,
    fingerprint,
    source_snapshot,
)

APPROVAL = "GENERATE_ONE_PAID_INITIAL_VOICE_NO_TELEGRAM"
PROJECT_NAMESPACE = "customer_acquisition_projects"


class VoiceInitialBootstrap:
    def __init__(self, store: RuntimeStateStore) -> None:
        self.store = store
        self.profiles = VoiceProfileService(store)

    def _validated_source(self, project_id: UUID, product_id: UUID) -> tuple[dict, str]:
        self.profiles._verify_project(project_id, product_id)
        project = self.store.get(PROJECT_NAMESPACE, str(project_id))
        # As in CustomerFunnelService.continue_preview_research, older
        # confirmed profiles may predate the project-level confirmation flag.
        # An explicit False/None is a real veto, not a legacy omission.
        if (
            "understanding_confirmed" in project
            and project["understanding_confirmed"] is not True
        ):
            raise PermissionError("VOICE_PRODUCT_UNDERSTANDING_EXPLICITLY_UNCONFIRMED")
        record = self.store.get(PRODUCT_INTAKE_NAMESPACE, str(product_id))
        product = record.get("product") if isinstance(record, dict) else None
        if not isinstance(product, dict):
            raise RuntimeError("VOICE_PRODUCT_SOURCE_UNAVAILABLE")
        if str(product.get("id") or "") != str(product_id):
            raise PermissionError("VOICE_PRODUCT_ID_MISMATCH")
        if str(product.get("status") or "").upper() != "CONFIRMED":
            raise PermissionError("VOICE_PRODUCT_PROFILE_NOT_CONFIRMED")
        digest = fingerprint(source_snapshot(product))
        return product, digest

    def plan(self, *, project_id: UUID, product_id: UUID) -> dict:
        product, digest = self._validated_source(project_id, product_id)
        result = {
            "project_id": str(project_id), "product_id": str(product_id),
            "source_fingerprint": digest,
            "provider_calls": 0, "publication_actions": 0,
        }
        project = self.store.get(PROJECT_NAMESPACE, str(project_id))
        if "understanding_confirmed" not in project:
            # Read-only preflight reports the required free migration.
            # No automatic project writes or paid model calls.
            return {**result, "status": "LEGACY_CONFIRMATION_BACKFILL_REQUIRED"}
        assignment = self.store.get(
            "voice_profile_assignments", f"{project_id}:{product_id}"
        )
        if assignment is not None:
            current = self.profiles.active(
                project_id=project_id, product_id=product_id,
                product_data=product,
            )
            return {
                **result, "status": "ACTIVE",
                "voice_profile_version_id": str(current.id),
                "voice_profile_content_hash": current.content_hash,
            }
        matches = sorted(
            (
                VoiceGenerationJob.model_validate(row)
                for row in self.store.list_namespace(VOICE_GENERATION_JOB_NAMESPACE)
                if str(row.get("project_id")) == str(project_id)
                and str(row.get("product_id")) == str(product_id)
                and row.get("source_fingerprint") == digest
                and row.get("generator_prompt_version") == VOICE_GENERATOR_PROMPT_VERSION
            ),
            key=lambda job: job.created_at,
        )
        if len(matches) > 1:
            raise RuntimeError("VOICE_INITIAL_MULTIPLE_JOBS_NEED_RECONCILIATION")
        if not matches:
            return {**result, "status": "RESERVATION_NEEDED"}
        job = matches[0]
        return {
            **result, "status": job.status,
            "job_id": job.id, "version_id": str(job.version_id) if job.version_id else None,
            "error_code": job.error_code,
            "validation_diagnostics": job.validation_diagnostics,
        }

    def inspect(self, *, project_id: UUID, product_id: UUID) -> dict:
        """Read-only, scoped view of the current author's generated character."""
        product, _ = self._validated_source(project_id, product_id)
        version = self.profiles.active(
            project_id=project_id, product_id=product_id, product_data=product,
        )
        return {
            "status": "ACTIVE_VOICE_INSPECTED",
            "project_id": str(project_id), "product_id": str(product_id),
            "voice_profile_version_id": str(version.id),
            "voice_profile_content_hash": version.content_hash,
            "source_fingerprint": version.source_fingerprint,
            "generator_prompt_version": version.generator_prompt_version,
            "evidence_status": version.evidence_status,
            "character": version.profile.model_dump(mode="json"),
            "provider_calls": 0, "publication_actions": 0,
        }

    def reserve(self, *, project_id: UUID, product_id: UUID) -> dict:
        plan = self.plan(project_id=project_id, product_id=product_id)
        if plan["status"] == "LEGACY_CONFIRMATION_BACKFILL_REQUIRED":
            # Reserve-only is an explicit state-mutating operation. Reuse
            # the existing customer funnel's legacy-confirmed semantics:
            # missing flag + CONFIRMED ProductProfile. Never override a
            # project with an explicitly false/unknown confirmation flag.
            before = self.store.get(PROJECT_NAMESPACE, str(project_id))
            if "understanding_confirmed" in before:
                raise RuntimeError("VOICE_LEGACY_CONFIRMATION_CHANGED")
            updated = {
                **before, "understanding_confirmed": True,
                "understanding_confirmed_backfilled_at": datetime.now(UTC).isoformat(),
                "voice_legacy_confirmation_source": "CONFIRMED_PRODUCT_PROFILE",
            }
            if not self.store.compare_and_swap(
                PROJECT_NAMESPACE, str(project_id), before, updated
            ):
                raise RuntimeError("VOICE_LEGACY_CONFIRMATION_CAS_CONFLICT")
            plan = self.plan(project_id=project_id, product_id=product_id)
        if plan["status"] != "RESERVATION_NEEDED":
            return plan
        product, digest = self._validated_source(project_id, product_id)
        job = self.profiles.reserve_initial(
            project_id=project_id, product_id=product_id, product_data=product,
        )
        if job.source_fingerprint != digest or job.status != "RESERVED":
            raise RuntimeError("VOICE_INITIAL_RESERVATION_RECONCILE_REQUIRED")
        return self.plan(project_id=project_id, product_id=product_id)

    async def generate_one(
        self, *, project_id: UUID, product_id: UUID,
        expected_job_id: str, settings, provider=None,
    ) -> dict:
        current = self.plan(project_id=project_id, product_id=product_id)
        if current["status"] != "RESERVED" or current.get("job_id") != expected_job_id:
            raise RuntimeError("VOICE_INITIAL_EXPECTED_RESERVED_JOB_REQUIRED")
        # Worker atomically claims this exact job and checks fresh source again
        # before making a possibly paid call. Never silently take another job.
        return await VoiceInitialGenerationWorker(
            store=self.store, settings=settings, provider=provider,
        ).run_once(expected_job_id=expected_job_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Existing-product voice bootstrap: read-only by default")
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--product-id", type=UUID, required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--reserve-only", action="store_true")
    group.add_argument("--inspect-active", action="store_true")
    group.add_argument("--generate-paid", action="store_true")
    parser.add_argument("--expected-job-id", default="")
    parser.add_argument("--ack", default="")
    parser.add_argument("--monthly-budget-usd", default="")
    parser.add_argument("--reservation-usd", default="")
    parser.add_argument("--max-output-tokens", type=int)
    return parser


async def run(args: argparse.Namespace, *, store: RuntimeStateStore | None = None,
              settings=None, provider=None) -> dict:
    store = store if store is not None else get_runtime_store()
    settings = settings if settings is not None else get_settings()
    service = VoiceInitialBootstrap(store)
    if getattr(args, "inspect_active", False):
        return service.inspect(project_id=args.project_id, product_id=args.product_id)
    if not args.reserve_only and not args.generate_paid:
        return service.plan(project_id=args.project_id, product_id=args.product_id)
    if store.ephemeral:
        raise RuntimeError("VOICE_BOOTSTRAP_REQUIRES_DURABLE_DATABASE")
    if args.reserve_only:
        return service.reserve(project_id=args.project_id, product_id=args.product_id)
    if args.ack != APPROVAL or not args.expected_job_id:
        raise PermissionError("VOICE_BOOTSTRAP_EXPLICIT_PAID_APPROVAL_AND_JOB_ID_REQUIRED")
    monthly = _positive_decimal(args.monthly_budget_usd)
    reservation = _positive_decimal(args.reservation_usd)
    if monthly is None or reservation is None or reservation > monthly:
        raise ValueError("VOICE_BOOTSTRAP_FINITE_BUDGET_REQUIRED")
    if args.max_output_tokens is None or not 256 <= args.max_output_tokens <= 4000:
        raise ValueError("VOICE_BOOTSTRAP_OUTPUT_CAP_REQUIRED")
    if provider is None and (settings.llm_provider != "openai" or not settings.openai_api_key):
        raise RuntimeError("VOICE_BOOTSTRAP_PAID_PROVIDER_REQUIRED")
    safe_settings = settings.model_copy(update={
        "voice_generation_worker_enabled": True,
        "voice_generation_monthly_budget_usd": monthly,
        "voice_generation_call_reservation_usd": reservation,
        "voice_generation_max_output_tokens": args.max_output_tokens,
    })
    return await service.generate_one(
        project_id=args.project_id, product_id=args.product_id,
        expected_job_id=args.expected_job_id, settings=safe_settings,
        provider=provider,
    )


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("status") in {
            "ACTIVE", "ACTIVE_VOICE_INSPECTED", "RESERVATION_NEEDED",
            "LEGACY_CONFIRMATION_BACKFILL_REQUIRED", "RESERVED",
            "VOICE_INITIAL_CREATED",
        } else 2
    except Exception as exc:
        print(json.dumps({
            "status": "FAILED", "error_type": type(exc).__name__,
            "error_code": str(exc)[:180], "provider_calls": 0,
        }, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
