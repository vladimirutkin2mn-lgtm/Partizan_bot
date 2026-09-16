from __future__ import annotations

import json
from uuid import UUID

from app.config import Settings, get_settings
from app.models import ProductProfileStatus
from app.product_intake import PRODUCT_INTAKE_NAMESPACE
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.schemas import ProductProfileView

DEFAULT_SELF_DOGFOOD_PRODUCT_ID = UUID("eb8f160f-1b85-5f1b-a4f0-18038f63ac34")

_PARTIZAN_BRIEF = (
    "Product: Partizan\n"
    "Partizan researches acquisition opportunities, recommends the next measurable "
    "customer-acquisition move, coordinates permissioned experiments, and tracks spend, "
    "conversions, CAC, and learning. It is for founders and small teams that already have "
    "a product but need a disciplined way to find and test customer acquisition channels."
)


def _profile(product_id: UUID, settings: Settings) -> ProductProfileView:
    reference_links = [settings.partizan_public_base_url] if settings.partizan_public_base_url else []
    return ProductProfileView(
        id=product_id,
        input_brief=_PARTIZAN_BRIEF,
        name="Partizan",
        description=(
            "AI customer-acquisition system that researches opportunities, recommends the next "
            "measurable move, coordinates permissioned experiments, and tracks acquisition economics."
        ),
        problem_or_desire=(
            "Founders have a product but do not know which acquisition channel or small experiment "
            "to run next."
        ),
        value_proposition=(
            "Turn product context into evidence-backed acquisition actions and measurable learning "
            "without granting blanket execution permission."
        ),
        usp=(
            "Research-first acquisition planning tied to bounded execution, first-party measurement, "
            "and explicit safety gates."
        ),
        use_cases=[
            "Find the first evidence-backed customer acquisition opportunity",
            "Choose a small measurable acquisition test",
            "Track acquisition spend, conversions, CAC, and learning",
        ],
        market="Global English-speaking founders and small teams",
        language="English",
        price=49.0,
        pricing_model="Acquisition Plan plus managed-spend fee",
        goal="Acquire paying customers for Partizan using Partizan itself",
        budget=100.0,
        max_cac=49.0,
        allowed_channels=["INSTAGRAM", "REDDIT", "TELEGRAM"],
        constraints=[
            "No external publish or paid spend from bootstrap",
            "Execution requires the normal channel permissions and safety gates",
        ],
        known_audience=[
            "Founders and small teams with a launched product who need customer acquisition",
        ],
        known_competitors=[],
        reference_links=reference_links,
        assumptions=[],
        contradictions=[],
        product_type="AI customer-acquisition software/service",
        business_model="Paid acquisition software/service with managed-spend fee",
        customer_hypotheses=[
            "Early-stage founders who need a disciplined first acquisition loop",
            "Small product teams that need measurable channel tests without a growth team",
        ],
        confidence=1.0,
        missing_information=[],
        status=ProductProfileStatus.CONFIRMED,
    )


def ensure_self_dogfood_product(
    *,
    store: RuntimeStateStore | None = None,
    settings: Settings | None = None,
) -> tuple[UUID, bool]:
    """Create the internal Partizan Product snapshot once; never starts an experiment."""

    store = store or get_runtime_store()
    settings = settings or get_settings()
    product_id = settings.partizan_self_dogfood_product_id or DEFAULT_SELF_DOGFOOD_PRODUCT_ID
    existing = store.get(PRODUCT_INTAKE_NAMESPACE, str(product_id))
    if existing is not None:
        ProductProfileView.model_validate(existing["product"])
        return product_id, False

    profile = _profile(product_id, settings)
    payload = {
        "product": profile.model_dump(mode="json"),
        "brief": _PARTIZAN_BRIEF,
        "reference_links": list(profile.reference_links),
        "questions": [],
        "answers": [],
        "answered_fields": [
            "business_model",
            "known_audience",
            "market",
            "name",
            "problem_or_desire",
        ],
    }
    created = store.put_if_absent(
        PRODUCT_INTAKE_NAMESPACE,
        str(product_id),
        payload,
    )
    if not created:
        persisted = store.get(PRODUCT_INTAKE_NAMESPACE, str(product_id))
        if persisted is None:
            raise RuntimeError("Self-dogfood Product changed concurrently")
        ProductProfileView.model_validate(persisted["product"])
    return product_id, created


def main() -> int:
    try:
        product_id, created = ensure_self_dogfood_product()
        print(json.dumps({"configured_product_id": str(product_id), "created": created}))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)[:2000]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
