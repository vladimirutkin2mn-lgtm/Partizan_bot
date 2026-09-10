from __future__ import annotations

import argparse
import json
from uuid import UUID

from app.community_distribution_acceptance import (
    CommunityDistributionAcceptanceError,
    community_distribution_acceptance_service,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report read-only Community Distribution production acceptance evidence."
    )
    parser.add_argument(
        "--project-id",
        type=UUID,
        default=None,
        help="Optional customer project UUID used to scope all evidence.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON for operator review.",
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    try:
        report = community_distribution_acceptance_service.report(args.project_id)
    except CommunityDistributionAcceptanceError as exc:
        print(
            json.dumps(
                {"status": "INVALID_SCOPE", "detail": str(exc)},
                sort_keys=True,
            )
        )
        return 2

    print(
        json.dumps(
            report.model_dump(mode="json"),
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
