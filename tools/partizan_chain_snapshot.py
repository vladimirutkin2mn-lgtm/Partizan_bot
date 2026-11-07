"""One selected project: stored facts and wiring only; no models, Telegram or writes."""
import hashlib
import importlib.util
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.config import get_settings
from app.openai_usage import openai_task_options, openai_usage_context
from app.project_marketing_memory import ProjectMarketingMemoryService
from app.runtime_store import get_runtime_store
from app.telegram_community_registry import TelegramCommunityRegistry
from app.voice_profiles import fingerprint, source_snapshot, voice_source_readiness_issues

PRODUCT = UUID("52d7e59e-5ac5-4363-b829-c93e33e48272")
PROJECT = UUID("124cdd70-49b0-48b3-aee0-a037dda4bcd0")
URL = re.compile(r"https?://[^\s<>\]\)\"']+")
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
counts = {"get": 0, "list": 0, "write_attempts": 0}


def clean(value):
    if isinstance(value, str):
        return EMAIL.sub("[EMAIL_REDACTED]", URL.sub("[URL_REDACTED]", value))
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(x) for x in value]
    return value


def subset(row, keys):
    return {k: row[k] for k in keys if k in row}


def deny(*args, **kwargs):
    counts["write_attempts"] += 1
    raise RuntimeError("READ_ONLY_AUDIT_WRITE_BLOCKED")


class ReadOnly:
    ephemeral = False

    def __init__(self, backend):
        self.backend = backend
        for name in ("put", "put_if_absent", "compare_and_swap", "delete", "clear_namespace"):
            setattr(backend, name, deny)

    def get(self, namespace, key):
        counts["get"] += 1
        return self.backend.get(namespace, key)

    def list_namespace(self, namespace):
        counts["list"] += 1
        return self.backend.list_namespace(namespace)

    put = put_if_absent = compare_and_swap = delete = clear_namespace = deny


def scoped(row):
    return str(row.get("project_id") or "") == str(PROJECT) or str(row.get("product_id") or "") == str(PRODUCT)


def record(row, depth=0):
    keys = ("id", "project_id", "product_id", "status", "state", "created_at", "updated_at",
            "applied_at", "verified_at", "published_at", "expires_at", "date", "display_name",
            "about", "bio", "profile_bio", "cta_value", "cta_label", "headline", "caption",
            "avatar_sha256", "avatar_filename", "avatar_mime_type", "profile_strategy_key",
            "source_fingerprint", "evidence_status", "snapshot_type", "changed_fields",
            "publication_id", "snapshot_id", "after_snapshot_id", "error_code", "story_id",
            "period_seconds", "profile_verified", "has_avatar", "avatar_present", "is_pinned")
    result = subset(row, keys)
    if depth < 2:
        for k in ("profile", "after_profile", "verified_profile", "content", "proposed", "plan", "story", "avatar"):
            if isinstance(row.get(k), dict):
                result[k] = record(row[k], depth + 1)
    return clean(result)


def main():
    store = ReadOnly(get_runtime_store())
    project = store.get("customer_acquisition_projects", str(PROJECT))
    assert project and str(project.get("product_id")) == str(PRODUCT)
    intake = store.get("product_intake", str(PRODUCT))
    assert intake and str(intake["product"]["id"]) == str(PRODUCT)
    raw = intake["product"]
    product = subset(raw, ("id", "name", "description", "problem_or_desire", "value_proposition", "usp",
                           "use_cases", "known_audience", "customer_hypotheses", "product_type", "business_model",
                           "language", "market", "price", "pricing_model", "assumptions", "contradictions",
                           "missing_information", "confidence", "status", "goal", "budget", "max_cac",
                           "allowed_channels", "constraints", "known_competitors"))
    current_hash = fingerprint(source_snapshot(raw))
    settings = get_settings()
    flags = subset(settings.model_dump(), ("llm_provider", "llm_model", "runtime_storage",
                    "persisted_voice_writer_enabled", "telegram_writer_v18_enabled",
                    "voice_initial_reservation_enabled", "voice_generation_worker_enabled",
                    "telegram_research_provider", "telegram_audience_semantic_enabled",
                    "telegram_paid_discovery_enabled", "telegram_client_publish_provider"))
    versions = []
    for row in store.list_namespace("voice_profile_versions"):
        if not scoped(row):
            continue
        v = subset(row, ("id", "version", "origin", "created_at", "source_fingerprint", "content_hash",
                         "generator_prompt_version", "evidence_status", "profile"))
        v["source_matches_current_product"] = row.get("source_fingerprint") == current_hash
        versions.append(clean(v))
    jobs = [subset(row, ("id", "status", "created_at", "updated_at", "generator_prompt_version",
                        "source_fingerprint", "error_code"))
            for row in store.list_namespace("voice_profile_generation_jobs") if scoped(row)]
    assignment = store.get("voice_profile_assignments", f"{PROJECT}:{PRODUCT}")
    icp = store.get("icp_generation", str(PRODUCT)) or {}
    icps = [clean(subset(r, ("id", "rank", "title", "description", "pain", "desired_outcome", "trigger",
                             "message_hook", "willingness_to_pay", "score"))) for r in icp.get("icps", [])[:15]]
    registry = TelegramCommunityRegistry(store)
    communities = []
    for row in registry.list_for_product(PRODUCT):
        meta = registry.hydrate_metadata(row)
        communities.append(clean({
            "title": row.get("title"), "kind": meta.get("community_type") or meta.get("kind"),
            "audience_fit": row.get("audience_fit"), "verified_at": row.get("verified_at"),
            "last_native_read_at": (row.get("native_snapshot") or {}).get("observed_at"),
            "recent_context_count": len(meta.get("recent_context") or []),
            "target_mode": meta.get("action_target_mode"), "native_status": meta.get("native_research_status"),
            "community_fit_pass": meta.get("community_fit_pass"),
        }))
    memory = ProjectMarketingMemoryService(store)
    contexts = {kind: clean(memory.prompt_context_for_product(
        PRODUCT, platform="TELEGRAM", action_type=kind).rendered or "")[:700]
        for kind in ("COMMENT", "STANDALONE_POST")}
    namespaces = ("customer_telegram_profile_snapshot", "customer_telegram_profile_mutation",
                  "customer_telegram_profile_conversion_setup", "reviewed_customer_telegram_profile_hq",
                  "reviewed_customer_telegram_profile_apply", "reviewed_customer_telegram_story_publish",
                  "reviewed_customer_telegram_story_replacement", "telegram_story_publication",
                  "telegram_profile_conversion_pack", "managed_telegram_profile_strategy")
    profile_records = {ns: [record(r) for r in store.list_namespace(ns) if scoped(r)] for ns in namespaces}
    learning = {}
    for ns in ("growth_autoresearch_execution", "growth_autoresearch_execution_trial", "community_message_autoresearch"):
        rows = [r for r in store.list_namespace(ns) if scoped(r)]
        learning[ns] = {"count": len(rows), "records": [record(r) for r in rows[:12]]}
    revisions = [r for r in store.list_namespace("customer_product_revision_history") if scoped(r)]
    revision_summaries = [{"scan_id": r.get("scan_id"), "confirmed_at": r.get("confirmed_at"),
                          "new_product": clean(subset(r.get("new_product") or {},
                                                ("name", "description", "value_proposition", "product_type", "known_audience")))}
                         for r in revisions]
    options = {}
    for task in ("product_intake", "icp_generation", "voice_profile_generate", "action_auto_prepare", "action_variant_draft"):
        with openai_usage_context(task, PRODUCT):
            options[task] = openai_task_options(settings.llm_model)
    source_hashes = {}
    for module in ("customer_funnel", "customer_product_rescan", "product_understanding_autofill", "action_drafting",
                   "voice_profiles", "community_voice", "telegram_writer_v18"):
        spec = importlib.util.find_spec("app." + module)
        source_hashes[module] = hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest() if spec and spec.origin else None
    result = {
        "status": "READ_ONLY_CHAIN_SNAPSHOT_COMPLETE", "observed_at": datetime.now(UTC).isoformat(),
        "release_sha": os.getenv("PARTIZAN_RELEASE_SHA"), "source_sha256": source_hashes,
        "project_id": str(PROJECT), "product_id": str(PRODUCT),
        "project": clean(subset(project, ("status", "project_name", "project_type", "customer_brief", "brief",
                           "understanding_confirmed", "research_state", "product_revision_scan_id", "product_updated_at",
                           "voice_refresh_status", "voice_initial_status", "voice_initial_job_id",
                           "channel_preferences", "channel_publisher_modes", "selected_acquisition_channel"))),
        "product": clean(product), "product_source_fingerprint": current_hash,
        "redacted_source_matches_original": fingerprint(source_snapshot(clean(raw))) == current_hash,
        "product_readiness_issues": voice_source_readiness_issues(raw),
        "intake_question_count": len(intake.get("questions") or []), "revision_history": revision_summaries,
        "runtime_flags": flags, "task_options": options,
        "voice_assignment": clean(assignment), "voice_versions": versions, "voice_jobs": clean(jobs),
        "icp": {"count": len(icp.get("icps", [])), "top": icps,
                "has_product_revision": any(k in icp for k in ("product_revision_id", "source_fingerprint", "product_fingerprint"))},
        "registry": {"count": len(communities), "confirmed": sum(r["community_fit_pass"] is True for r in communities),
                     "rows": communities}, "marketing_memory": contexts,
        "profile_records": profile_records, "autoresearch": learning,
        "read_counts": counts, "paid_llm_calls": 0, "telegram_api_calls": 0, "publication_actions": 0,
    }
    assert counts["write_attempts"] == 0
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "SNAPSHOT_FAILED", "error_type": type(exc).__name__, "read_counts": counts}))
        raise SystemExit(1) from None
