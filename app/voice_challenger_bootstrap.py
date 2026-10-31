"""Review an existing confirmed product and reserve an UNPAID voice challenger.

No provider calls, Telegram connections, automatic promotions or scheduling.
A future paid run must use a distinct operator-reviewed, budgeted pathway.
"""
from __future__ import annotations

import argparse
import json
from uuid import UUID

from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.voice_bootstrap import VoiceInitialBootstrap
from app.voice_profiles import (
    VOICE_CHALLENGER_PROMPT_VERSION,
    VOICE_GENERATION_JOB_NAMESPACE,
    VoiceGenerationJob,
    VoiceOrigin,
    VoiceProfileService,
    voice_source_readiness_issues,
)


class VoiceChallengerBootstrap:
    def __init__(self, store: RuntimeStateStore):
        self.store = store
        self.initial = VoiceInitialBootstrap(store)
        self.profiles = VoiceProfileService(store)

    def plan(self, *, project_id: UUID, product_id: UUID) -> dict:
        product, source_fingerprint = self.initial._validated_source(
            project_id, product_id
        )
        # A founder-approved rescan may invalidate the old voice for writing,
        # but it must not prevent researching a new, inactive voice candidate.
        active = self.profiles.challenger_parent(
            project_id=project_id, product_id=product_id, product_data=product,
        )
        issues = voice_source_readiness_issues(product)
        result = {
            "project_id": str(project_id), "product_id": str(product_id),
            "status": "CHALLENGER_RESERVATION_NEEDED",
            "parent_voice_version_id": str(active.id),
            "source_fingerprint": source_fingerprint,
            "challenger_prompt_version": VOICE_CHALLENGER_PROMPT_VERSION,
            "provider_calls": 0, "publication_actions": 0,
        }
        if issues:
            return {
                **result, "status": "PRODUCT_UNDERSTANDING_INSUFFICIENT",
                "missing_dimensions": issues,
            }
        jobs = [
            VoiceGenerationJob.model_validate(row)
            for row in self.store.list_namespace(VOICE_GENERATION_JOB_NAMESPACE)
            if str(row.get("project_id")) == str(project_id)
            and str(row.get("product_id")) == str(product_id)
            and row.get("source_fingerprint") == source_fingerprint
            and row.get("generator_prompt_version")
            == VOICE_CHALLENGER_PROMPT_VERSION
            and row.get("parent_version_id") == str(active.id)
            and row.get("origin") == VoiceOrigin.AUTORESEARCH.value
        ]
        if len(jobs) > 1:
            raise RuntimeError("VOICE_CHALLENGER_MULTIPLE_JOBS_RECONCILE")
        if jobs:
            job = jobs[0]
            result.update({
                "status": job.status,
                "job_id": job.id,
                "generated_version_id": (
                    str(job.version_id) if job.version_id is not None else None
                ),
            })
        return result

    def reserve(self, *, project_id: UUID, product_id: UUID) -> dict:
        if self.store.ephemeral:
            raise RuntimeError("VOICE_CHALLENGER_DURABLE_DATABASE_REQUIRED")
        current = self.plan(project_id=project_id, product_id=product_id)
        if current["status"] != "CHALLENGER_RESERVATION_NEEDED":
            return current
        product, _ = self.initial._validated_source(project_id, product_id)
        self.profiles.reserve_challenger(
            project_id=project_id, product_id=product_id, product_data=product
        )
        return self.plan(project_id=project_id, product_id=product_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Review voice candidate, no model calls")
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--product-id", type=UUID, required=True)
    parser.add_argument("--reserve-only", action="store_true")
    args = parser.parse_args()
    try:
        service = VoiceChallengerBootstrap(get_runtime_store())
        result = (
            service.reserve(project_id=args.project_id, product_id=args.product_id)
            if args.reserve_only
            else service.plan(project_id=args.project_id, product_id=args.product_id)
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "FAILED", "error_type": type(exc).__name__,
            "error_code": str(exc)[:180], "provider_calls": 0,
            "publication_actions": 0,
        }, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
