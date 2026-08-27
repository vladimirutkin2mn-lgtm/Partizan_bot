from __future__ import annotations

import argparse
import json

from app.self_research_benchmark_schemas import SelfResearchSplit
from app.self_research_loop import self_research_loop_service
from app.self_research_loop_schemas import SelfResearchRunRequest, SelfResearchTrialStatus


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run bounded offline Partizan self-research iterations."
    )
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument(
        "--split",
        choices=[SelfResearchSplit.TRAIN.value, SelfResearchSplit.DEV.value],
        default=SelfResearchSplit.DEV.value,
        help="Research split. TEST is intentionally unavailable to autonomous tuning.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Maximum bounded iterations to run (1-100).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.iterations < 1 or args.iterations > 100:
        print(json.dumps({"error": "--iterations must be between 1 and 100"}))
        return 2
    try:
        request = SelfResearchRunRequest(
            dataset_version=args.dataset_version,
            split=SelfResearchSplit(args.split),
        )
        for _ in range(args.iterations):
            trial = self_research_loop_service.run_once(request)
            print(trial.model_dump_json())
            if trial.status in {
                SelfResearchTrialStatus.BLOCKED,
                SelfResearchTrialStatus.EXHAUSTED,
            }:
                break
    except (KeyError, RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
