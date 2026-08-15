from __future__ import annotations

from typing import Any

from app import growth_run


class AuthenticatedApiClient(growth_run.ApiClient):
    """Attach the configured operator key to every internal Partizan API call.

    The generic runner already reads the key only from environment variables.
    Keeping the decision here means fresh-product POSTs and existing-product
    GETs behave consistently in production without putting a secret in CLI
    arguments, URLs or report output.
    """

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        operator: bool = False,
    ) -> Any:
        internal_api = path.startswith("/v1/")
        return super().request(
            method,
            path,
            body=body,
            query=query,
            operator=operator or (internal_api and bool(self.operator_key)),
        )


def main() -> None:
    growth_run.ApiClient = AuthenticatedApiClient
    growth_run.main()
