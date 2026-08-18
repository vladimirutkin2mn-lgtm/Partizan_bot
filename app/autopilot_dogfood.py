from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.autonomous_growth_worker import AutonomousGrowthWorker
from app.config import Settings, get_settings
from app.customer_autopilot import customer_autopilot_service
from app.customer_funnel import customer_funnel_service
from app.product_intake import product_intake_service
from app.stripe_readiness import StripeReadinessError, verify_autopilot_price, verify_launch_price

DOGFOOD_PROJECT_ID_ENV = "PARTIZAN_DOGFOOD_PROJECT_ID"
DOGFOOD_CUSTOMER_TOKEN_ENV = "PARTIZAN_DOGFOOD_CUSTOMER_TOKEN"
LIVE_SPEND_CONFIRMATION = "RUN_ONE_LIVE_PAID_SWEEP"


class AutopilotDogfoodSnapshot(BaseModel):
    project_id: UUID
    product_id: UUID
    subscription_status: str
    autopilot_status: str
    meta_connected: bool
    growth_balance_available_usd: float = Field(ge=0)
    acquisition_spend_usd: float = Field(ge=0)
    remaining_acquisition_capacity_usd: float = Field(ge=0)
    management_fee_usd: float = Field(ge=0)
    settlement_ready: bool
    target_max_cac: float = Field(ge=0)
    paid_customers: int = Field(ge=0)
    revenue_usd: float = Field(ge=0)
    cac_usd: float | None = Field(default=None, ge=0)
    roas: float | None = Field(default=None, ge=0)
    running_experiments: int = Field(ge=0)
    waiting_experiments: int = Field(ge=0)
    readiness_blockers: list[str] = Field(default_factory=list)
    dogfood_complete: bool


class AutopilotDogfoodRunner:
    """Production-only harness around customer Autopilot and the growth worker.

    A live sweep is fail-closed until the customer has a funded Growth Balance and the
    Partizan-funded provider payment rail is ready. The harness never falls back to a
    customer's provider billing method.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        stripe_verify: Callable[[Settings], None] | None = None,
        worker_factory: Callable[[], AutonomousGrowthWorker] = AutonomousGrowthWorker,
    ) -> None:
        self._settings = settings or get_settings()
        self._stripe_verify = stripe_verify or self._verify_stripe
        self._worker_factory = worker_factory

    def snapshot(self, project_id: UUID, customer_token: str) -> AutopilotDogfoodSnapshot:
        overview = customer_autopilot_service.overview(project_id, customer_token)
        if overview.product_id is None:
            raise ValueError("Autopilot dogfood requires completed internal acquisition research")
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        product = product_intake_service.get_product(overview.product_id)
        target_max_cac = max(0.0, float(project.get("autopilot_target_max_cac") or 0))
        blockers = [*overview.blockers, *self._runtime_blockers()]
        if not product.reference_links:
            blockers.append("Researched product has no website/reference link for real paid traffic")
        try:
            self._stripe_verify(self._settings)
        except (StripeReadinessError, RuntimeError, ValueError) as exc:
            blockers.append(str(exc))
        blockers = self._dedupe(blockers)
        balance = overview.growth_balance
        return AutopilotDogfoodSnapshot(
            project_id=project_id,
            product_id=overview.product_id,
            subscription_status=overview.subscription_status,
            autopilot_status=overview.autopilot_status,
            meta_connected=overview.meta.connected,
            growth_balance_available_usd=balance.available_usd,
            acquisition_spend_usd=balance.acquisition_spend_usd,
            remaining_acquisition_capacity_usd=balance.remaining_acquisition_capacity_usd,
            management_fee_usd=balance.management_fee_usd,
            settlement_ready=balance.settlement_ready,
            target_max_cac=target_max_cac,
            paid_customers=overview.paid_customers,
            revenue_usd=overview.revenue_usd,
            cac_usd=overview.cac_usd,
            roas=overview.roas,
            running_experiments=len(overview.running_experiments),
            waiting_experiments=len(overview.waiting_experiments),
            readiness_blockers=blockers,
            dogfood_complete=overview.paid_customers >= 1 and overview.cac_usd is not None,
        )

    def run_one_sweep(
        self,
        project_id: UUID,
        customer_token: str,
        *,
        confirmation: str,
    ) -> dict[str, Any]:
        before = self.snapshot(project_id, customer_token)
        self._assert_live_authorization(before, confirmation)
        emitted: list[str] = []
        exit_code = self._worker_factory().run(
            once=True,
            interval_seconds=300,
            product_id=before.product_id,
            emit=emitted.append,
        )
        after = self.snapshot(project_id, customer_token)
        return {
            "exit_code": exit_code,
            "worker_output": [self._safe_json(value) for value in emitted],
            "before": before.model_dump(mode="json"),
            "after": after.model_dump(mode="json"),
        }

    def _runtime_blockers(self) -> list[str]:
        blockers: list[str] = []
        if self._settings.app_env.strip().lower() not in {"production", "prod"}:
            blockers.append("Dogfood live sweep requires APP_ENV=production")
        if self._settings.runtime_storage.strip().lower() != "database":
            blockers.append("Dogfood live sweep requires RUNTIME_STORAGE=database")
        public_origin = self._settings.partizan_public_base_url or ""
        if not public_origin.startswith("https://"):
            blockers.append("Dogfood live sweep requires a public HTTPS Partizan origin")
        if self._settings.creative_provider != "openai" or not self._settings.openai_api_key:
            blockers.append("Dogfood Meta Autopilot requires CREATIVE_PROVIDER=openai and OPENAI_API_KEY")
        return blockers

    @staticmethod
    def _assert_live_authorization(
        snapshot: AutopilotDogfoodSnapshot,
        confirmation: str,
    ) -> None:
        if confirmation != LIVE_SPEND_CONFIRMATION:
            raise ValueError(
                "Live paid sweep was not authorized. Pass the exact confirmation phrase "
                f"{LIVE_SPEND_CONFIRMATION}."
            )
        if snapshot.readiness_blockers:
            raise ValueError("Live paid sweep is blocked: " + "; ".join(snapshot.readiness_blockers))
        if snapshot.subscription_status != "ACTIVE":
            raise ValueError("Autopilot subscription is not ACTIVE")
        if snapshot.autopilot_status != "ACTIVE":
            raise ValueError("Growth Mandate is not ACTIVE")
        if not snapshot.meta_connected:
            raise ValueError("Meta is not connected")
        if not snapshot.settlement_ready:
            raise ValueError("Partizan-funded provider payment rail is not ready")
        if snapshot.remaining_acquisition_capacity_usd <= 0:
            raise ValueError("Growth Balance has no acquisition capacity remaining")
        if snapshot.target_max_cac <= 0:
            raise ValueError("A positive target max CAC is required")

    @staticmethod
    def _verify_stripe(settings: Settings) -> None:
        verify_launch_price(settings)
        verify_autopilot_price(settings)

    @staticmethod
    def _safe_json(value: str) -> Any:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {"message": value[:2000]}

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            normalized = str(value).strip()
            if normalized and normalized not in result:
                result.append(normalized)
        return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect a real customer Autopilot dogfood project and optionally run exactly one "
            "existing bounded autonomous-growth sweep."
        )
    )
    parser.add_argument(
        "--run-one-sweep",
        action="store_true",
        help="Run one product-scoped growth sweep after all live gates pass.",
    )
    parser.add_argument(
        "--confirm-live-spend",
        default="",
        help=("Required only with --run-one-sweep. Exact value: " f"{LIVE_SPEND_CONFIRMATION}"),
    )
    parser.add_argument(
        "--require-paid-conversion",
        action="store_true",
        help="Exit non-zero until at least one PAID conversion and a calculable CAC exist.",
    )
    return parser


def _load_target() -> tuple[UUID, str]:
    project_raw = os.getenv(DOGFOOD_PROJECT_ID_ENV, "").strip()
    customer_token = os.getenv(DOGFOOD_CUSTOMER_TOKEN_ENV, "").strip()
    if not project_raw:
        raise ValueError(f"{DOGFOOD_PROJECT_ID_ENV} is required")
    if not customer_token:
        raise ValueError(f"{DOGFOOD_CUSTOMER_TOKEN_ENV} is required")
    try:
        project_id = UUID(project_raw)
    except ValueError as exc:
        raise ValueError(f"{DOGFOOD_PROJECT_ID_ENV} must be a UUID") from exc
    return project_id, customer_token


def main() -> int:
    args = build_parser().parse_args()
    try:
        project_id, customer_token = _load_target()
        runner = AutopilotDogfoodRunner()
        if args.run_one_sweep:
            result = runner.run_one_sweep(
                project_id,
                customer_token,
                confirmation=args.confirm_live_spend,
            )
            print(json.dumps(result, ensure_ascii=False, default=str))
            snapshot = AutopilotDogfoodSnapshot.model_validate(result["after"])
        else:
            snapshot = runner.snapshot(project_id, customer_token)
            print(snapshot.model_dump_json())
        if args.require_paid_conversion and not snapshot.dogfood_complete:
            return 3
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)[:2000]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
