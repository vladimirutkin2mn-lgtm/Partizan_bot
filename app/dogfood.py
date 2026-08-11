import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, HttpUrl

from app.channel_service import channel_service
from app.config import get_settings
from app.execution_service import execution_service
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.product_intake import product_intake_service
from app.schemas import ExecutionPrepareRequest, ProductCreateRequest


class DogfoodManifest(BaseModel):
    name: str
    brief: str = Field(min_length=20)
    reference_links: list[HttpUrl] = Field(default_factory=list)
    destination_url: HttpUrl | None = None
    contact_email: str | None = None
    contact_name: str | None = None
    selected_play_rank: int = Field(default=1, ge=1)


class DogfoodReport(BaseModel):
    manifest_name: str
    product_id: str
    product_status: str
    icp_count: int
    channel_count: int
    play_count: int
    selected_play_id: str | None = None
    selected_play_rank: int | None = None
    selected_play_source_type: str | None = None
    selected_play_priority: float | None = None
    execution_package_id: str | None = None
    experiment_id: str | None = None
    execution_status: str | None = None
    blockers: list[str] = Field(default_factory=list)
    provider_snapshot: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DogfoodOptions:
    prepare_execution: bool = False
    approve_execution: bool = False
    run_execution: bool = False
    require_live_search: bool = False
    require_live_delivery: bool = False


class DogfoodRunner:
    async def run(
        self,
        manifest: DogfoodManifest,
        options: DogfoodOptions | None = None,
    ) -> DogfoodReport:
        options = options or DogfoodOptions()
        self._validate_options(options)
        settings = get_settings()
        self._validate_providers(settings, options)

        intake = await product_intake_service.create_draft(
            ProductCreateRequest(
                brief=manifest.brief,
                reference_links=manifest.reference_links,
            )
        )
        if intake.clarifications:
            questions = "; ".join(item.question for item in intake.clarifications)
            raise ValueError(
                "Dogfood manifest is incomplete and needs founder clarification: "
                f"{questions}"
            )
        confirmed = product_intake_service.confirm(intake.product.id)
        product = confirmed.product

        icps = await icp_service.generate(product)
        channels = await channel_service.discover(product, icps)
        plays = await growth_play_service.generate(product, icps, channels)
        if manifest.selected_play_rank > len(plays.plays):
            raise ValueError("selected_play_rank is outside the generated play list")
        selected = plays.plays[manifest.selected_play_rank - 1]

        blockers = self._research_blockers(manifest, settings)
        report = DogfoodReport(
            manifest_name=manifest.name,
            product_id=str(product.id),
            product_status=product.status.value,
            icp_count=len(icps.icps),
            channel_count=len(channels.opportunities),
            play_count=len(plays.plays),
            selected_play_id=str(selected.id),
            selected_play_rank=selected.rank,
            selected_play_source_type=selected.source_type,
            selected_play_priority=selected.priority_score,
            blockers=blockers,
            provider_snapshot={
                "llm_provider": settings.llm_provider,
                "search_provider": settings.search_provider,
                "execution_provider": settings.execution_provider,
            },
        )

        if not options.prepare_execution:
            return report

        selected = growth_play_service.set_status(
            product.id,
            selected.id,
            "APPROVED",
        )
        destination = manifest.destination_url
        if destination is None and not product.reference_links:
            raise ValueError(
                "A destination_url or ProductProfile reference link is required "
                "before execution preparation"
            )
        package = await execution_service.prepare(
            product,
            selected,
            ExecutionPrepareRequest(
                destination_url=destination,
                contact_email=manifest.contact_email,
                contact_name=manifest.contact_name,
            ),
        )
        report = report.model_copy(
            update={
                "execution_package_id": str(package.id),
                "experiment_id": str(package.experiment_id),
                "execution_status": package.status,
                "blockers": self._execution_blockers(package, settings),
            }
        )
        if not options.approve_execution:
            return report

        approved = execution_service.approve(package.id)
        report = report.model_copy(update={"execution_status": approved.status})
        if not options.run_execution:
            return report

        launched = await execution_service.run(package.id)
        return report.model_copy(
            update={
                "execution_status": launched.package.status,
                "experiment_id": str(launched.experiment.id),
                "blockers": [],
            }
        )

    def _validate_options(self, options: DogfoodOptions) -> None:
        if options.run_execution and not options.approve_execution:
            raise ValueError("run_execution requires approve_execution")
        if options.approve_execution and not options.prepare_execution:
            raise ValueError("approve_execution requires prepare_execution")

    def _validate_providers(self, settings: Any, options: DogfoodOptions) -> None:
        if options.require_live_search and settings.search_provider == "mock":
            raise ValueError("Live dogfood requires SEARCH_PROVIDER=openai")
        if options.require_live_delivery and settings.execution_provider == "mock":
            raise ValueError("Live delivery requires a non-mock EXECUTION_PROVIDER")
        if options.run_execution and options.require_live_delivery:
            if settings.execution_provider != "smtp":
                raise ValueError("Milestone 8 live delivery currently requires SMTP")

    def _research_blockers(self, manifest: DogfoodManifest, settings: Any) -> list[str]:
        blockers: list[str] = []
        if settings.search_provider == "mock":
            blockers.append("SEARCH_PROVIDER is mock; channel opportunities are not live evidence")
        if manifest.destination_url is None and not manifest.reference_links:
            blockers.append("No live product destination URL/reference link is configured")
        return blockers

    def _execution_blockers(self, package: Any, settings: Any) -> list[str]:
        blockers: list[str] = []
        if package.contact.method != "email" or not package.contact.address:
            blockers.append("No executable email contact; platform outreach remains manual")
        if settings.execution_provider == "mock":
            blockers.append("EXECUTION_PROVIDER is mock; Run would not contact a real recipient")
        return blockers


def load_manifest(path: Path) -> DogfoodManifest:
    return DogfoodManifest.model_validate_json(path.read_text(encoding="utf-8"))


def save_report(path: Path, report: DogfoodReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a bounded Partizan Bot dogfood cycle")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--report", type=Path, default=Path("dogfood_report.json"))
    parser.add_argument("--prepare-execution", action="store_true")
    parser.add_argument("--approve-execution", action="store_true")
    parser.add_argument("--run-execution", action="store_true")
    parser.add_argument("--require-live-search", action="store_true")
    parser.add_argument("--require-live-delivery", action="store_true")
    return parser


async def _main() -> int:
    args = build_parser().parse_args()
    manifest = load_manifest(args.manifest)
    report = await DogfoodRunner().run(
        manifest,
        DogfoodOptions(
            prepare_execution=args.prepare_execution,
            approve_execution=args.approve_execution,
            run_execution=args.run_execution,
            require_live_search=args.require_live_search,
            require_live_delivery=args.require_live_delivery,
        ),
    )
    save_report(args.report, report)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
