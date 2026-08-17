from __future__ import annotations

import os
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings, get_settings
from app.runtime_store import RuntimeStateStore, get_runtime_store

PROVIDER_SECRET_NAMESPACE = "provider_secret"
PROVIDER_SECRET_PREFIX = "CUSTOMER_META_ACCESS_TOKEN_"


class ProviderSecretConfigurationError(RuntimeError):
    pass


class ProviderSecretStore:
    """Persist provider credentials encrypted at rest in the runtime store."""

    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._settings = settings or get_settings()

    def create_reference(self) -> str:
        return f"{PROVIDER_SECRET_PREFIX}{uuid4().hex.upper()}"

    def put(self, reference: str, plaintext: str) -> None:
        if not reference.startswith(PROVIDER_SECRET_PREFIX):
            raise ValueError("Customer provider secret reference is invalid")
        if not plaintext:
            raise ValueError("Provider secret cannot be empty")
        encrypted = self._fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
        self._store.put(
            PROVIDER_SECRET_NAMESPACE,
            reference,
            {"reference": reference, "ciphertext": encrypted, "version": 1},
        )

    def get(self, reference: str) -> str | None:
        payload = self._store.get(PROVIDER_SECRET_NAMESPACE, reference)
        if payload is None:
            return None
        ciphertext = str(payload.get("ciphertext") or "")
        if not ciphertext:
            raise ProviderSecretConfigurationError("Stored provider secret is malformed")
        try:
            return self._fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise ProviderSecretConfigurationError(
                "Stored provider secret cannot be decrypted with the configured key"
            ) from exc

    def delete(self, reference: str) -> None:
        self._store.delete(PROVIDER_SECRET_NAMESPACE, reference)

    def _fernet(self) -> Fernet:
        configured = self._settings.provider_secret_encryption_key
        if configured is None:
            raise ProviderSecretConfigurationError(
                "PROVIDER_SECRET_ENCRYPTION_KEY is required for customer provider connections"
            )
        try:
            return Fernet(configured.get_secret_value().encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise ProviderSecretConfigurationError(
                "PROVIDER_SECRET_ENCRYPTION_KEY must be a valid Fernet key"
            ) from exc


class ProviderSecretResolver:
    """Resolve customer-encrypted credentials first, then legacy environment refs."""

    def __init__(self, secret_store: ProviderSecretStore | None = None) -> None:
        self._secret_store = secret_store or ProviderSecretStore()

    def resolve(self, name: str) -> str | None:
        encrypted = self._secret_store.get(name)
        if encrypted is not None:
            return encrypted
        value = os.getenv(name)
        return value if value and value.strip() else None


provider_secret_store = ProviderSecretStore()
