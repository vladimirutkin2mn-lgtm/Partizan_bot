from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.operator_auth import OPERATOR_KEY_HEADER

WORKER_HEALTH_URL = "http://127.0.0.1:8000/v1/ops/workers/health"


def main() -> int:
    operator_key = os.environ.get("OPERATOR_API_KEY", "").strip()
    if not operator_key:
        print("worker health probe: OPERATOR_API_KEY is not configured", file=sys.stderr)
        return 2

    request = Request(
        WORKER_HEALTH_URL,
        headers={OPERATOR_KEY_HEADER: operator_key, "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"worker health probe: unavailable ({type(exc).__name__})", file=sys.stderr)
        return 2

    workers = list(payload.get("workers") or [])
    summary = ", ".join(
        f"{item.get('worker_name', 'unknown')}={item.get('state', 'UNKNOWN')}"
        for item in workers
    )
    if payload.get("healthy") is True:
        print(f"worker health probe: ok ({summary})")
        return 0
    print(f"worker health probe: not ready ({summary})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
