from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.customer_account_schemas import (
    CustomerAccountProjectView,
    CustomerAccountView,
)
from app.customer_funnel import (
    CUSTOMER_PROJECT_NAMESPACE,
    CustomerProjectAccessError,
    CustomerProjectNotFoundError,
    customer_funnel_service,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store

CUSTOMER_ACCOUNT_NAMESPACE = "customer_accounts"
CUSTOMER_ACCOUNT_EMAIL_NAMESPACE = "customer_account_email_index"
CUSTOMER_ACCOUNT_SESSION_NAMESPACE = "customer_account_sessions"
CUSTOMER_ACCOUNT_PROJECT_ACCESS_NAMESPACE = "customer_account_project_access"
CUSTOMER_ACCOUNT_PROJECT_CLAIM_NAMESPACE = "customer_account_project_claims"
CUSTOMER_ACCOUNT_SESSION_COOKIE = "partizan_customer_session"
CUSTOMER_ACCOUNT_SESSION_DAYS = 30


class CustomerAccountAuthenticationError(PermissionError):
    pass


class CustomerAccountConflictError(ValueError):
    pass


class CustomerAccountService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def register(
        self,
        *,
        email: str,
        password: str,
        project_id: UUID,
        customer_token: str,
    ) -> tuple[CustomerAccountView, str]:
        normalized_email = self._normalize_email(email)
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        owner = project.get("customer_account_id")
        if owner:
            raise CustomerAccountConflictError("This project already belongs to a Partizan account")

        account_id = uuid4()
        salt = secrets.token_bytes(16)
        password_hash = self._password_hash(password, salt)
        now = datetime.now(UTC)
        account = {
            "id": str(account_id),
            "email": normalized_email,
            "password_salt": base64.b64encode(salt).decode("ascii"),
            "password_hash": base64.b64encode(password_hash).decode("ascii"),
            "project_ids": [],
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        self._store.put(CUSTOMER_ACCOUNT_NAMESPACE, str(account_id), account)
        email_reserved = self._store.put_if_absent(
            CUSTOMER_ACCOUNT_EMAIL_NAMESPACE,
            normalized_email,
            {"account_id": str(account_id)},
        )
        if not email_reserved:
            self._store.delete(CUSTOMER_ACCOUNT_NAMESPACE, str(account_id))
            raise CustomerAccountConflictError(
                "An account with this email already exists. Sign in instead."
            )
        try:
            self._claim_project(account_id, project_id, customer_token)
        except Exception:
            self._store.delete(CUSTOMER_ACCOUNT_NAMESPACE, str(account_id))
            email_index = self._store.get(CUSTOMER_ACCOUNT_EMAIL_NAMESPACE, normalized_email)
            if email_index and str(email_index.get("account_id")) == str(account_id):
                self._store.delete(CUSTOMER_ACCOUNT_EMAIL_NAMESPACE, normalized_email)
            raise
        session_token = self._create_session(account_id)
        return self.view(account_id), session_token

    def login(self, *, email: str, password: str) -> tuple[CustomerAccountView, str]:
        account = self._account_by_email(email)
        if account is None or not self._password_matches(account, password):
            raise CustomerAccountAuthenticationError("Email or password is incorrect")
        account_id = UUID(str(account["id"]))
        session_token = self._create_session(account_id)
        return self.view(account_id), session_token

    def logout(self, session_token: str | None) -> None:
        if not session_token:
            return
        self._store.delete(CUSTOMER_ACCOUNT_SESSION_NAMESPACE, self._session_key(session_token))

    def account_for_session(self, session_token: str | None) -> dict:
        if not session_token:
            raise CustomerAccountAuthenticationError("Sign in to Partizan")
        record = self._store.get(CUSTOMER_ACCOUNT_SESSION_NAMESPACE, self._session_key(session_token))
        if record is None:
            raise CustomerAccountAuthenticationError("Partizan session is invalid")
        expires_at = self._as_utc(datetime.fromisoformat(str(record["expires_at"])))
        if expires_at <= datetime.now(UTC):
            self._store.delete(CUSTOMER_ACCOUNT_SESSION_NAMESPACE, self._session_key(session_token))
            raise CustomerAccountAuthenticationError("Partizan session has expired")
        account = self._store.get(CUSTOMER_ACCOUNT_NAMESPACE, str(record["account_id"]))
        if account is None:
            raise CustomerAccountAuthenticationError("Partizan account no longer exists")
        return account

    def view_for_session(self, session_token: str | None) -> CustomerAccountView:
        account = self.account_for_session(session_token)
        return self.view(UUID(str(account["id"])))

    def claim_project(
        self,
        *,
        session_token: str | None,
        project_id: UUID,
        customer_token: str,
    ) -> CustomerAccountView:
        account = self.account_for_session(session_token)
        account_id = UUID(str(account["id"]))
        self._claim_project(account_id, project_id, customer_token)
        return self.view(account_id)

    def project_access(
        self,
        *,
        session_token: str | None,
        project_id: UUID,
    ) -> tuple[CustomerAccountView, str]:
        account = self.account_for_session(session_token)
        account_id = UUID(str(account["id"]))
        project = self._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
        if project is None:
            raise CustomerProjectNotFoundError(project_id)
        if str(project.get("customer_account_id") or "") != str(account_id):
            raise CustomerProjectAccessError(project_id)
        access = self._store.get(
            CUSTOMER_ACCOUNT_PROJECT_ACCESS_NAMESPACE,
            self._project_access_key(account_id, project_id),
        )
        if access is None or not access.get("customer_token"):
            raise CustomerProjectAccessError(project_id)
        return self.view(account_id), str(access["customer_token"])

    def view(self, account_id: UUID) -> CustomerAccountView:
        account = self._store.get(CUSTOMER_ACCOUNT_NAMESPACE, str(account_id))
        if account is None:
            raise CustomerAccountAuthenticationError("Partizan account no longer exists")
        projects: list[CustomerAccountProjectView] = []
        for project_id_raw in account.get("project_ids", []):
            project = self._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id_raw))
            if project is None:
                continue
            created_at = self._as_utc(datetime.fromisoformat(str(project["created_at"])))
            projects.append(
                CustomerAccountProjectView(
                    project_id=UUID(str(project["id"])),
                    brief=str(project.get("brief") or ""),
                    market=str(project.get("market") or ""),
                    goal=str(project.get("goal") or ""),
                    research_state=str(project.get("research_state") or "NOT_STARTED"),
                    launch_unlocked=bool(project.get("launch_unlocked")),
                    created_at=created_at,
                )
            )
        projects.sort(key=lambda item: item.created_at, reverse=True)
        return CustomerAccountView(
            account_id=account_id,
            email=str(account["email"]),
            projects=projects,
        )

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(CUSTOMER_ACCOUNT_NAMESPACE)
            self._store.clear_namespace(CUSTOMER_ACCOUNT_EMAIL_NAMESPACE)
            self._store.clear_namespace(CUSTOMER_ACCOUNT_SESSION_NAMESPACE)
            self._store.clear_namespace(CUSTOMER_ACCOUNT_PROJECT_ACCESS_NAMESPACE)
            self._store.clear_namespace(CUSTOMER_ACCOUNT_PROJECT_CLAIM_NAMESPACE)

    def _claim_project(self, account_id: UUID, project_id: UUID, customer_token: str) -> None:
        project = self._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
        if project is None:
            raise CustomerProjectNotFoundError(project_id)
        existing_owner = str(project.get("customer_account_id") or "")
        if existing_owner:
            self._require_existing_project_access(account_id, project_id, existing_owner)
            return

        customer_funnel_service.get_project_payload(project_id, customer_token)
        claim_key = str(project_id)
        now = datetime.now(UTC)
        claimed = self._store.put_if_absent(
            CUSTOMER_ACCOUNT_PROJECT_CLAIM_NAMESPACE,
            claim_key,
            {
                "account_id": str(account_id),
                "project_id": str(project_id),
                "created_at": now.isoformat(),
            },
        )
        if not claimed:
            current = self._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
            current_owner = str((current or {}).get("customer_account_id") or "")
            if current_owner:
                self._require_existing_project_access(account_id, project_id, current_owner)
                return
            raise CustomerAccountConflictError("This project is already being claimed")

        try:
            project = self._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
            if project is None:
                raise CustomerProjectNotFoundError(project_id)
            existing_owner = str(project.get("customer_account_id") or "")
            if existing_owner:
                self._require_existing_project_access(account_id, project_id, existing_owner)
                return

            service_token = secrets.token_urlsafe(32)
            project["customer_token_hash"] = hashlib.sha256(
                service_token.encode("utf-8")
            ).hexdigest()
            project["customer_account_id"] = str(account_id)
            project["customer_account_claimed_at"] = datetime.now(UTC).isoformat()
            project["updated_at"] = datetime.now(UTC).isoformat()
            self._store.put(CUSTOMER_PROJECT_NAMESPACE, str(project_id), project)
            self._store.put(
                CUSTOMER_ACCOUNT_PROJECT_ACCESS_NAMESPACE,
                self._project_access_key(account_id, project_id),
                {
                    "account_id": str(account_id),
                    "project_id": str(project_id),
                    "customer_token": service_token,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
            self._attach_project_to_account(account_id, project_id)
        except Exception:
            current = self._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
            current_owner = str((current or {}).get("customer_account_id") or "")
            if current_owner != str(account_id):
                reservation = self._store.get(CUSTOMER_ACCOUNT_PROJECT_CLAIM_NAMESPACE, claim_key)
                if reservation and str(reservation.get("account_id")) == str(account_id):
                    self._store.delete(CUSTOMER_ACCOUNT_PROJECT_CLAIM_NAMESPACE, claim_key)
            raise

    def _require_existing_project_access(
        self,
        account_id: UUID,
        project_id: UUID,
        existing_owner: str,
    ) -> None:
        if existing_owner != str(account_id):
            raise CustomerAccountConflictError("This project already belongs to another account")
        access = self._store.get(
            CUSTOMER_ACCOUNT_PROJECT_ACCESS_NAMESPACE,
            self._project_access_key(account_id, project_id),
        )
        if access is None:
            raise CustomerProjectAccessError(project_id)
        self._attach_project_to_account(account_id, project_id)

    def _attach_project_to_account(self, account_id: UUID, project_id: UUID) -> None:
        account = self._store.get(CUSTOMER_ACCOUNT_NAMESPACE, str(account_id))
        if account is None:
            raise CustomerAccountAuthenticationError("Partizan account no longer exists")
        project_ids = [str(item) for item in account.get("project_ids", [])]
        if str(project_id) not in project_ids:
            project_ids.append(str(project_id))
            account["project_ids"] = project_ids
            account["updated_at"] = datetime.now(UTC).isoformat()
            self._store.put(CUSTOMER_ACCOUNT_NAMESPACE, str(account_id), account)

    def _create_session(self, account_id: UUID) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        self._store.put(
            CUSTOMER_ACCOUNT_SESSION_NAMESPACE,
            self._session_key(token),
            {
                "account_id": str(account_id),
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=CUSTOMER_ACCOUNT_SESSION_DAYS)).isoformat(),
            },
        )
        return token

    def _account_by_email(self, email: str) -> dict | None:
        normalized = self._normalize_email(email)
        index = self._store.get(CUSTOMER_ACCOUNT_EMAIL_NAMESPACE, normalized)
        if index is None:
            return None
        return self._store.get(CUSTOMER_ACCOUNT_NAMESPACE, str(index.get("account_id") or ""))

    def _password_matches(self, account: dict, password: str) -> bool:
        try:
            salt = base64.b64decode(str(account["password_salt"]), validate=True)
            expected = base64.b64decode(str(account["password_hash"]), validate=True)
        except (KeyError, ValueError):
            return False
        actual = self._password_hash(password, salt)
        return hmac.compare_digest(expected, actual)

    @staticmethod
    def _password_hash(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().casefold()

    @staticmethod
    def _session_key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _project_access_key(account_id: UUID, project_id: UUID) -> str:
        return f"{account_id}:{project_id}"

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


customer_account_service = CustomerAccountService()
