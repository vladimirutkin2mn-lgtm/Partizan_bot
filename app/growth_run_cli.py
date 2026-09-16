from __future__ import annotations

from argparse import Namespace
from typing import Any

from app import growth_run

DEFAULT_TOP_ICP_COUNT = 3
MAX_TOP_ICP_COUNT = 3
_ORIGINAL_BUILD_PARSER = growth_run.build_parser
_ORIGINAL_RUN = growth_run.run


class AuthenticatedApiClient(growth_run.ApiClient):
    """Attach operator auth and bounded, resumable behavior to internal API calls.

    The generic runner already reads the key only from environment variables.
    Keeping production-only request shaping here means fresh-product POSTs and
    existing-product GETs behave consistently without putting a secret in CLI
    arguments, URLs or report output.
    """

    top_icp_count = DEFAULT_TOP_ICP_COUNT

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
        effective_operator = operator or (internal_api and bool(self.operator_key))

        if method == "POST" and path.endswith("/icps/generate"):
            existing_path = path.removesuffix("/generate")
            try:
                return super().request(
                    "GET",
                    existing_path,
                    operator=effective_operator,
                )
            except growth_run.GrowthRunHttpError as exc:
                if exc.status != 404:
                    raise

        if method == "POST" and path.endswith("/distribution/discover"):
            query = dict(query or {})
            query.setdefault("top_icp_count", self.top_icp_count)

        return super().request(
            method,
            path,
            body=body,
            query=query,
            operator=effective_operator,
        )


def _bounded_build_parser():
    parser = _ORIGINAL_BUILD_PARSER()
    parser.add_argument(
        "--top-icp-count",
        type=int,
        default=DEFAULT_TOP_ICP_COUNT,
        help="Limit distribution discovery to the top N ICPs (1-3).",
    )
    return parser


def _bounded_run(args: Namespace):
    if not 1 <= args.top_icp_count <= MAX_TOP_ICP_COUNT:
        raise ValueError(
            f"top-icp-count must be between 1 and {MAX_TOP_ICP_COUNT}"
        )
    AuthenticatedApiClient.top_icp_count = args.top_icp_count
    return _ORIGINAL_RUN(args)


def main() -> None:
    growth_run.ApiClient = AuthenticatedApiClient
    growth_run.build_parser = _bounded_build_parser
    growth_run.run = _bounded_run
    growth_run.main()
