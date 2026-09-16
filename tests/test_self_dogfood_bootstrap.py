from uuid import UUID

from app.config import Settings
from app.models import ProductProfileStatus
from app.product_intake import PRODUCT_INTAKE_NAMESPACE
from app.runtime_store import MemoryRuntimeStateStore
from app.schemas import ProductProfileView
from app.self_dogfood_bootstrap import (
    DEFAULT_SELF_DOGFOOD_PRODUCT_ID,
    ensure_self_dogfood_product,
)


def test_bootstrap_creates_confirmed_partizan_product_once() -> None:
    store = MemoryRuntimeStateStore()
    settings = Settings(partizan_public_base_url="https://partizanlabs.com")

    product_id, created = ensure_self_dogfood_product(store=store, settings=settings)
    repeated_id, repeated_created = ensure_self_dogfood_product(
        store=store,
        settings=settings,
    )

    assert product_id == DEFAULT_SELF_DOGFOOD_PRODUCT_ID
    assert repeated_id == product_id
    assert created is True
    assert repeated_created is False

    payload = store.get(PRODUCT_INTAKE_NAMESPACE, str(product_id))
    assert payload is not None
    product = ProductProfileView.model_validate(payload["product"])
    assert product.name == "Partizan"
    assert product.status == ProductProfileStatus.CONFIRMED
    assert product.reference_links == ["https://partizanlabs.com"]
    assert payload["questions"] == []


def test_explicit_environment_product_id_overrides_default_identity() -> None:
    store = MemoryRuntimeStateStore()
    explicit_id = UUID("11111111-2222-3333-4444-555555555555")
    settings = Settings(
        partizan_public_base_url="https://partizanlabs.com",
        partizan_self_dogfood_product_id=explicit_id,
    )

    product_id, created = ensure_self_dogfood_product(store=store, settings=settings)

    assert created is True
    assert product_id == explicit_id
    assert store.get(PRODUCT_INTAKE_NAMESPACE, str(explicit_id)) is not None
    assert store.get(PRODUCT_INTAKE_NAMESPACE, str(DEFAULT_SELF_DOGFOOD_PRODUCT_ID)) is None


def test_bootstrap_only_writes_product_intake_namespace() -> None:
    store = MemoryRuntimeStateStore()
    settings = Settings(partizan_public_base_url="https://partizanlabs.com")

    ensure_self_dogfood_product(store=store, settings=settings)

    assert len(store.list_namespace(PRODUCT_INTAKE_NAMESPACE)) == 1
    assert store.list_namespace("distribution_action") == []
    assert store.list_namespace("distribution_experiment") == []
    assert store.list_namespace("distribution_analytics_event") == []
