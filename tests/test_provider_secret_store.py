import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.provider_secret_store import (
    PROVIDER_SECRET_NAMESPACE,
    PROVIDER_SECRET_PREFIX,
    TELEGRAM_LOGIN_SECRET_PREFIX,
    TELEGRAM_SESSION_SECRET_PREFIX,
    ProviderSecretStore,
)
from app.runtime_store import MemoryRuntimeStateStore


def _secret_store() -> tuple[ProviderSecretStore, MemoryRuntimeStateStore]:
    store = MemoryRuntimeStateStore()
    settings = Settings(
        _env_file=None,
        provider_secret_encryption_key=Fernet.generate_key().decode("ascii"),
    )
    return ProviderSecretStore(store=store, settings=settings), store


def test_customer_provider_token_is_encrypted_at_rest_and_round_trips() -> None:
    secrets, store = _secret_store()
    reference = secrets.create_reference()
    plaintext = "EAAB-not-a-real-meta-token"

    secrets.put(reference, plaintext)

    persisted = store.get(PROVIDER_SECRET_NAMESPACE, reference)
    assert persisted is not None
    assert plaintext not in str(persisted)
    assert persisted["ciphertext"] != plaintext
    assert secrets.get(reference) == plaintext
    assert reference.startswith(PROVIDER_SECRET_PREFIX)


@pytest.mark.parametrize(
    "prefix",
    [TELEGRAM_SESSION_SECRET_PREFIX, TELEGRAM_LOGIN_SECRET_PREFIX],
)
def test_telegram_provider_secrets_use_explicit_encrypted_reference_prefixes(prefix: str) -> None:
    secrets, store = _secret_store()
    reference = secrets.create_reference(prefix=prefix)
    plaintext = "not-a-real-telegram-session"

    secrets.put(reference, plaintext)

    persisted = store.get(PROVIDER_SECRET_NAMESPACE, reference)
    assert persisted is not None
    assert plaintext not in str(persisted)
    assert secrets.get(reference) == plaintext
    assert reference.startswith(prefix)


def test_arbitrary_provider_secret_prefix_is_rejected() -> None:
    secrets, _ = _secret_store()

    with pytest.raises(ValueError, match="prefix"):
        secrets.create_reference(prefix="CUSTOMER_ARBITRARY_SECRET_")


def test_customer_provider_token_fails_closed_without_encryption_key() -> None:
    store = MemoryRuntimeStateStore()
    secrets = ProviderSecretStore(store=store, settings=Settings(_env_file=None))
    reference = secrets.create_reference()

    try:
        secrets.put(reference, "secret")
    except RuntimeError as exc:
        assert "PROVIDER_SECRET_ENCRYPTION_KEY" in str(exc)
    else:
        raise AssertionError("unencrypted provider secret write unexpectedly succeeded")
