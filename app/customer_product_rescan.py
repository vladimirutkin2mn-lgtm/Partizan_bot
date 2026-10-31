"""Reviewable, zero-model-call rescans for existing account-owned products.

A source scan only creates a project-scoped proposal. The founder must confirm
specific product facts before durable product state changes. Old voice profiles,
research, distribution settings and campaign settings are never changed here.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from app.models import ProductProfileStatus
from app.product_intake import PRODUCT_INTAKE_NAMESPACE, product_intake_service
from app.product_source import ProductSourceReadError, read_public_product_source
from app.product_source_quality import is_campaign_asset_brief, is_distinct_product_value
from app.runtime_store import RuntimeStateStore
from app.schemas import ProductProfileView

PROJECT_NAMESPACE = "customer_acquisition_projects"
PROPOSAL_NAMESPACE = "customer_product_rescan_proposals"
HISTORY_NAMESPACE = "customer_product_revision_history"


def _digest(value: dict | None) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _trim(value: str | None, limit: int) -> str:
    return (value or "").strip()[:limit]


class ProductRescanService:
    def __init__(self, store: RuntimeStateStore):
        self.store = store

    def _project(self, project_id: UUID, account_id: str) -> dict:
        project = self.store.get(PROJECT_NAMESPACE, str(project_id))
        if project is None or project.get("deleted_at"):
            raise KeyError("PROJECT_NOT_FOUND")
        if str(project.get("customer_account_id") or "") != str(account_id):
            raise PermissionError("PROJECT_ACCOUNT_MISMATCH")
        return project

    def _source_product(self, project: dict) -> tuple[dict | None, dict | None]:
        product_id = project.get("product_id")
        if not product_id:
            return None, None
        row = self.store.get(PRODUCT_INTAKE_NAMESPACE, str(product_id))
        if row is None:
            raise RuntimeError("PROJECT_PRODUCT_RECORD_MISSING")
        return row, row.get("product") or None

    def current(self, project_id: UUID, account_id: str) -> dict:
        project = self._project(project_id, account_id)
        row, product = self._source_product(project)
        existing = self.store.get(PROPOSAL_NAMESPACE, str(project_id))
        return {
            "project_id": str(project_id),
            "product": product,
            "product_revision_id": project.get("product_revision_scan_id"),
            "pending": existing if existing and existing.get("status") == "PENDING" else None,
        }

    async def scan(self, project_id: UUID, account_id: str) -> dict:
        project = self._project(project_id, account_id)
        row, product = self._source_product(project)
        link = str(
            project.get("reference_url")
            or project.get("product_link")
            or project.get("website_url")
            or ""
        ).strip()
        source = None
        source_status = "NO_PUBLIC_LINK"
        if link:
            try:
                source = await read_public_product_source(link)
                source_status = (
                    "INSUFFICIENT_PUBLIC_EVIDENCE"
                    if source.needs_founder_context else "PUBLIC_SOURCE_READ"
                )
            except ProductSourceReadError:
                # Never replace the confirmed product with a guessed scraped page.
                source_status = "SOURCE_UNAVAILABLE"
        saved_description = str((product or {}).get("description") or "")
        project_brief = str(project.get("customer_brief") or "")
        description = (
            _trim(source.description, 2000)
            if source and not source.needs_founder_context
            and len((source.description or "").strip()) >= 40
            else _trim(
                project_brief if project_brief and not is_campaign_asset_brief(project_brief)
                else saved_description if not is_campaign_asset_brief(saved_description) else "",
                2000,
            )
        )
        previous_value = str((product or {}).get("value_proposition") or "")
        value = (
            _trim(previous_value, 1600)
            if is_distinct_product_value(saved_description, previous_value) else ""
        )
        audience = (product or {}).get("known_audience") or []
        proposal = {
            "status": "PENDING",
            "scan_id": str(uuid4()),
            "project_id": str(project_id),
            "account_id": str(account_id),
            "product_id": str(project.get("product_id") or ""),
            "source_url": link,
            "source_status": source_status,
            "source_excerpt": _trim(
                (source.description or source.text) if source else "", 1500
            ),
            "source_question": source.clarification_question if source else (
                "What do people get from this product or community?"
            ),
            "proposed": {
                "name": _trim(str((product or {}).get("name") or project.get("project_name") or ""), 120),
                "description": description,
                "value_proposition": value,
                "known_audience": _trim(str(audience[0]) if audience else "", 500),
            },
            "product_baseline_hash": _digest(row),
            "project_baseline_hash": _digest(project),
            "scanned_at": datetime.now(UTC).isoformat(),
        }
        # The proposal is independent from the confirmed product and voice.
        self.store.put(PROPOSAL_NAMESPACE, str(project_id), proposal)
        return proposal

    def confirm(
        self, project_id: UUID, account_id: str, *,
        scan_id: UUID, name: str, description: str,
        value_proposition: str, known_audience: str,
    ) -> dict:
        project = self._project(project_id, account_id)
        pending = self.store.get(PROPOSAL_NAMESPACE, str(project_id))
        if (
            pending is None or pending.get("status") != "PENDING"
            or pending.get("scan_id") != str(scan_id)
            or pending.get("account_id") != str(account_id)
        ):
            raise ValueError("RESCAN_PROPOSAL_NOT_CURRENT")
        if _digest(project) != pending["project_baseline_hash"]:
            raise ValueError("PROJECT_CHANGED_SINCE_RESCAN")
        old_row, old_product = self._source_product(project)
        if _digest(old_row) != pending["product_baseline_hash"]:
            raise ValueError("PRODUCT_CHANGED_SINCE_RESCAN")

        product_id = UUID(str(project.get("product_id"))) if project.get("product_id") else (
            uuid5(NAMESPACE_URL, f"partizan-rescan:{project_id}:{scan_id}")
        )
        name, description = name.strip(), description.strip()
        value_proposition, known_audience = value_proposition.strip(), known_audience.strip()
        if old_product:
            profile = ProductProfileView.model_validate(old_product).model_copy(update={
                "name": name,
                "description": description,
                "value_proposition": value_proposition,
                "known_audience": [known_audience],
                "status": ProductProfileStatus.CONFIRMED,
            })
        else:
            profile = ProductProfileView(
                id=product_id, input_brief=description,
                name=name, description=description, problem_or_desire=None,
                value_proposition=value_proposition, usp=None, use_cases=[],
                market=str(project.get("market") or "") or None, language=None,
                price=None, pricing_model=None, goal=project.get("goal"),
                budget=project.get("budget_usd"), max_cac=None,
                allowed_channels=[], constraints=[], known_audience=[known_audience],
                known_competitors=[],
                reference_links=[pending["source_url"]] if pending["source_url"] else [],
                assumptions=[], contradictions=[], status=ProductProfileStatus.CONFIRMED,
            )
        new_row = dict(old_row) if old_row else {
            "brief": description, "reference_links": profile.reference_links,
            "questions": [], "answers": [], "answered_fields": [],
        }
        new_row["product"] = profile.model_dump(mode="json")
        history_key = f"{project_id}:{scan_id}"
        history = {
            "project_id": str(project_id), "product_id": str(product_id),
            "scan_id": str(scan_id), "old_product": old_product,
            "new_product": new_row["product"],
            "source_status": pending["source_status"],
            "source_url": pending["source_url"],
            "confirmed_at": datetime.now(UTC).isoformat(),
        }
        if not self.store.put_if_absent(HISTORY_NAMESPACE, history_key, history):
            raise ValueError("RESCAN_ALREADY_CONFIRMED")
        if old_row:
            changed = self.store.compare_and_swap(
                PRODUCT_INTAKE_NAMESPACE, str(product_id), old_row, new_row
            )
        else:
            changed = self.store.put_if_absent(
                PRODUCT_INTAKE_NAMESPACE, str(product_id), new_row
            )
        if not changed:
            self.store.delete(HISTORY_NAMESPACE, history_key)
            raise ValueError("PRODUCT_CHANGED_SINCE_RESCAN")
        updated_project = dict(project)
        updated_project.update({
            "product_id": str(product_id),
            "understanding_confirmed": True,
            "customer_brief": description,
            "brief": description,
            "product_revision_scan_id": str(scan_id),
            "product_updated_at": datetime.now(UTC).isoformat(),
            "voice_refresh_status": "REVIEW_REQUIRED",
        })
        if not self.store.compare_and_swap(
            PROJECT_NAMESPACE, str(project_id), project, updated_project
        ):
            # Revert only if no other operation has modified the new product.
            if old_row:
                self.store.compare_and_swap(
                    PRODUCT_INTAKE_NAMESPACE, str(product_id), new_row, old_row
                )
            else:
                # Refuse deleting an unexpectedly modified new product.
                if self.store.get(PRODUCT_INTAKE_NAMESPACE, str(product_id)) == new_row:
                    self.store.delete(PRODUCT_INTAKE_NAMESPACE, str(product_id))
            self.store.delete(HISTORY_NAMESPACE, history_key)
            raise ValueError("PROJECT_CHANGED_SINCE_RESCAN")
        # ProductIntakeService caches state. Invalidate only the affected entry.
        product_intake_service._states.pop(product_id, None)
        self.store.put(PROPOSAL_NAMESPACE, str(project_id), {
            **pending, "status": "CONFIRMED", "confirmed_at": history["confirmed_at"],
        })
        return {
            "status": "CONFIRMED",
            "project_id": str(project_id), "product_id": str(product_id),
            "scan_id": str(scan_id), "voice_refresh_status": "REVIEW_REQUIRED",
        }
