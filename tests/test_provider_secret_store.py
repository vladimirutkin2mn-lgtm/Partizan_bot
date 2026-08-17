from cryptography.fernet import Fernet

from app.config import Settings
from app.provider_secret_store import PROVIDER_SECRET_NAMESPACE, ProviderSecretStore
from app.runtime_store import MemoryRuntimeStateStore


def test_customer_provider_token_is_encrypted_at_rest_and_round_trips() -> None:
    store = MemoryRuntimeStateStore()
    settings = Settings(
        _env_file=None,
        provider_secret_encryption_key=Fernet.generate_key().decode("ascii"),
    )
    secrets = ProviderSecretStore(store=store, settings=settings)
    reference = secrets.create_reference()
    plaintext = "EAAB-not-a-real-meta-token"

    secrets.put(reference, plaintext)

    persisted = store.get(PROVIDER_SECRET_NAMESPACE, reference)
    assert persisted is not None
    assert plaintext not in str(persisted)
    assert persisted["ciphertext"] != plaintext
    assert secrets.get(reference) == plaintext


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
