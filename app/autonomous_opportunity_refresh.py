from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.audience_intelligence_service import (
    InMemoryAudienceIntelligenceService,
    audience_intelligence_service,
)
from app.autonomy_schemas import GrowthMandateStatus, GrowthMandateView
from app.autonomy_service import GROWTH_MANDATE_NAMESPACE
from app.customer_live_opportunities import (
    CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE,
    CUSTOMER_PROJECT_NAMESPACE,
    customer_live_opportunity_service,
)
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_play_service import distribution_play_service
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.icp_service import icp_service
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_discovery_strategy import (
    MAX_ADAPTIVE_DISCOVERY_ROUNDS,
    TARGET_READY_TELEGRAM_OPPORTUNITIES,
)
from app.telegram_opportunity_preflight import (
    TelegramOpportunityPreflightResult,
    TelegramOpportunityPreflightService,
    telegram_opportunity_preflight_service,
)

AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE = "autonomous_opportunity_refresh"
DISTRIBUTION_LEARNING_NAMESPACE = "distribution_learning_entry"
DEFAULT_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS = 6 * 60 * 60
MIN_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS = 15 * 60


class AutonomousOpportunityRefreshService:
    """Periodically refresh execution opportunities for active autonomous products.

    Discovery is deliberately separate from execution. This service may read public
    evidence, persist fresh opportunities and rebuild Distribution Plays, but it never
    publishes, joins a community, changes a profile or mutates a provider account.

    The refresh is adaptive: after the first discovery pass it checks exact Telegram
    targets with the connected customer account. If fewer than the target number are
    READY, it creates new search hypotheses from ICP alternatives, product audience
    context, failed preflight outcomes and prior growth learning, then runs another
    bounded Telegram-only discovery round. The loop stops when the READY target is met,
    no genuinely new hypotheses remain, or the maximum round budget is exhausted.
    """

    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        audience_service: InMemoryAudienceIntelligenceService | None = None,
        telegram_preflight_service: TelegramOpportunityPreflightService | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._audience_service = audience_service or audience_intelligence_service
        self._telegram_preflight = (
            telegram_preflight_service or telegram_opportunity_preflight_service
        )

    async def run_once(
        self,
        *,
        product_id: UUID | None = None,
        interval_seconds: int = DEFAULT_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS,
    ) -> list[dict]:
        if interval_seconds < MIN_AUTONOMOUS_DISCOVERY_INTERVAL_SECONDS:
            raise ValueError(
                "autonomous opportunity refresh interval must be at least 900 seconds"
            )

        now = datetime.now(UTC)
        results: list[dict] = []
        for candidate in self._candidate_mandates(product_id):
            previous = self._store.get(
                AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE,
                str(candidate.product_id),
            )
            if not self._due(previous, now=now, interval_seconds=interval_seconds):
                results.append(
                    {
                        "product_id": str(candidate.product_id),
                        "status": "NOT_DUE",
                        "last_success_at": previous.get("last_success_at") if previous else None,
                    }
                )
                continue

            started_at = datetime.now(UTC)
            adaptive_rounds = 1
            adaptive_stop_reason = "NO_PUBLISHER_PROJECT"
            adaptive_hints_used: set[str] = set()
            try:
                product = product_intake_service.get_product(candidate.product_id)
                icps = icp_service.get(candidate.product_id)
                distribution = await self._audience_service.discover(product, icps)

                project_id = self._project_id_for_product(candidate.product_id)
                preflight_by_url: dict[str, TelegramOpportunityPreflightResult] = {}
                if project_id is not None:
                    preflight_by_url = await self._preflight_live_comment_targets(project_id)
                    distribution = await self._apply_comment_preflight_to_distribution(
                        project_id,
                        distribution,
                        preflight_by_url,
                    )
                    adaptive_stop_reason = "TARGET_READY_REACHED"

                    discover_with_hints = getattr(
                        self._audience_service,
                        "discover_with_hints",
                        None,
                    )
                    while (
                        self._ready_count(preflight_by_url)
                        < TARGET_READY_TELEGRAM_OPPORTUNITIES
                        and adaptive_rounds < MAX_ADAPTIVE_DISCOVERY_ROUNDS
                        and callable(discover_with_hints)
                    ):
                        next_round = adaptive_rounds + 1
                        hints = self._adaptive_discovery_hints(
                            product=product,
                            icps=icps,
                            product_id=candidate.product_id,
                            preflight_by_url=preflight_by_url,
                            round_number=next_round,
                        )
                        hints = [
                            hint
                            for hint in hints
                            if self._normalized_hint(hint) not in adaptive_hints_used
                        ]
                        if not hints:
                            adaptive_stop_reason = "NO_NEW_HYPOTHESES"
                            break

                        selected_hints = hints[:4]
                        adaptive_hints_used.update(
                            self._normalized_hint(hint) for hint in selected_hints
                        )
                        distribution = await discover_with_hints(
                            product,
                            icps,
                            telegram_hints=selected_hints,
                            merge_existing=True,
                        )
                        adaptive_rounds = next_round

                        round_preflight = await self._preflight_live_comment_targets(project_id)
                        preflight_by_url.update(round_preflight)
                        distribution = await self._apply_comment_preflight_to_distribution(
                            project_id,
                            distribution,
                            preflight_by_url,
                        )

                    if self._ready_count(preflight_by_url) >= TARGET_READY_TELEGRAM_OPPORTUNITIES:
                        adaptive_stop_reason = "TARGET_READY_REACHED"
                    elif adaptive_rounds >= MAX_ADAPTIVE_DISCOVERY_ROUNDS:
                        adaptive_stop_reason = "ROUND_BUDGET_EXHAUSTED"
                    elif not callable(discover_with_hints):
                        adaptive_stop_reason = "ADAPTIVE_DISCOVERY_UNAVAILABLE"

                plays = distribution_play_service.generate(
                    product,
                    distribution,
                    identities=distribution_control_plane_service.list_identities(),
                    community_policies=distribution_control_plane_service.list_policies(),
                    campaign_slots=distribution_control_plane_service.list_campaign_slots(),
                )
            except Exception as exc:
                # Discovery failures must not disable the existing portfolio. Persist a
                # safe diagnostic and allow the normal autonomous sweep to continue using
                # the last known-good plays.
                record = {
                    "product_id": str(candidate.product_id),
                    "status": "FAILED",
                    "error_type": type(exc).__name__,
                    "attempted_at": started_at.isoformat(),
                    "last_success_at": previous.get("last_success_at") if previous else None,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
                self._store.put(
                    AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE,
                    str(candidate.product_id),
                    record,
                )
                results.append(record)
                continue

            completed_at = datetime.now(UTC)
            statuses = [item.status for item in preflight_by_url.values()]
            record = {
                "product_id": str(candidate.product_id),
                "status": "REFRESHED",
                "opportunity_count": distribution.opportunity_count,
                "play_count": plays.play_count,
                "ready_play_count": plays.ready_count,
                "telegram_preflight_checked": len(statuses),
                "telegram_preflight_ready": statuses.count("READY"),
                "telegram_preflight_join_required": statuses.count("JOIN_REQUIRED"),
                "telegram_preflight_excluded": sum(
                    status not in {"READY", "JOIN_REQUIRED"} for status in statuses
                ),
                "adaptive_discovery_rounds": adaptive_rounds,
                "adaptive_ready_target": TARGET_READY_TELEGRAM_OPPORTUNITIES,
                "adaptive_stop_reason": adaptive_stop_reason,
                "adaptive_hypothesis_count": len(adaptive_hints_used),
                "attempted_at": started_at.isoformat(),
                "last_success_at": completed_at.isoformat(),
                "updated_at": completed_at.isoformat(),
            }
            self._store.put(
                AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE,
                str(candidate.product_id),
                record,
            )
            results.append(record)
        return results

    def _adaptive_discovery_hints(
        self,
        *,
        product,
        icps,
        product_id: UUID,
        preflight_by_url: dict[str, TelegramOpportunityPreflightResult],
        round_number: int,
    ) -> list[str]:
        hints: list[str] = []

        # First preference: themes that have already produced positive observed growth
        # signals. This closes the Learn -> Discovery part of the loop rather than only
        # using learning to re-rank already-found plays.
        hints.extend(self._learning_hints(product_id))

        top_icps = list(getattr(icps, "icps", []) or [])[:3]
        if round_number == 2:
            # Split alternatives into independent hypotheses. The first pass previously
            # bundled them, which could hide useful adjacent communities from search.
            for icp in top_icps:
                for alternative in list(getattr(icp, "alternatives", []) or [])[:3]:
                    hints.append(str(alternative))
                trigger = str(getattr(icp, "trigger", "") or "").strip()
                if trigger:
                    hints.append(trigger)
            hints.extend(str(item) for item in list(getattr(product, "known_audience", []) or [])[:3])
        else:
            # Final bounded round explores the underlying problem/outcome rather than the
            # category name. This is useful when direct category communities are closed,
            # stale or non-writable.
            for icp in top_icps:
                for value in (
                    getattr(icp, "pain", ""),
                    getattr(icp, "desired_outcome", ""),
                    getattr(icp, "description", ""),
                ):
                    value = str(value or "").strip()
                    if value:
                        hints.append(value)
            for value in (
                getattr(product, "problem_or_desire", ""),
                getattr(product, "value_proposition", ""),
            ):
                value = str(value or "").strip()
                if value:
                    hints.append(value)

        statuses = [item.status for item in preflight_by_url.values()]
        # Failure-aware exploration: if direct channel posts mostly fail because no
        # discussion surface exists, prefer audience/trigger hypotheses that the adaptive
        # adapter will search as public groups and discussion-heavy communities.
        if statuses.count("NO_DISCUSSION") >= max(1, statuses.count("READY")):
            for icp in top_icps:
                title = str(getattr(icp, "title", "") or "").strip()
                if title:
                    hints.append(title)
        if statuses.count("NO_WRITE_ACCESS") or statuses.count("JOIN_REQUIRED"):
            for icp in top_icps:
                trigger = str(getattr(icp, "trigger", "") or "").strip()
                if trigger:
                    hints.append(trigger)

        return self._dedupe_hints(hints)

    def _learning_hints(self, product_id: UUID) -> list[str]:
        positive_rows: list[dict] = []
        negative_opportunity_ids: set[str] = set()
        for row in self._store.list_namespace(DISTRIBUTION_LEARNING_NAMESPACE):
            if str(row.get("product_id") or "") != str(product_id):
                continue
            if str(row.get("platform") or "").upper() != "TELEGRAM":
                continue
            opportunity_id = str(row.get("opportunity_id") or "")
            action = str(row.get("action") or "").upper()
            paid_users = int(row.get("paid_users") or 0)
            replies = int(row.get("replies") or 0)
            removals = int(row.get("removals") or 0)
            if action == "STOP" or removals > 0:
                if opportunity_id:
                    negative_opportunity_ids.add(opportunity_id)
                continue
            if action in {"SCALE", "CONTINUE"} or paid_users > 0 or replies > 0:
                positive_rows.append(row)

        positive_rows.sort(
            key=lambda row: (
                int(row.get("paid_users") or 0),
                int(row.get("replies") or 0),
                str(row.get("created_at") or ""),
            ),
            reverse=True,
        )

        hints: list[str] = []
        for row in positive_rows[:8]:
            opportunity_id = str(row.get("opportunity_id") or "")
            if not opportunity_id or opportunity_id in negative_opportunity_ids:
                continue
            try:
                opportunity = self._audience_service.find_opportunity(opportunity_id)
            except (KeyError, TypeError, ValueError):
                continue
            metadata = opportunity.metadata if isinstance(opportunity.metadata, dict) else {}
            signals = metadata.get("research_signals")
            if isinstance(signals, dict):
                matched = [
                    str(item).strip()
                    for item in list(signals.get("matched_terms") or [])[:5]
                    if str(item).strip()
                ]
                if matched:
                    hints.append(" ".join(matched))
            recent_context = metadata.get("recent_context")
            if isinstance(recent_context, list):
                for context in recent_context[:3]:
                    if not isinstance(context, dict):
                        continue
                    matched = [
                        str(item).strip()
                        for item in list(context.get("matched_terms") or [])[:5]
                        if str(item).strip()
                    ]
                    if matched:
                        hints.append(" ".join(matched))
                        break
            title = str(getattr(opportunity, "title", "") or "").strip()
            if title:
                hints.append(title)
        return self._dedupe_hints(hints)[:6]

    @staticmethod
    def _dedupe_hints(values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = " ".join(str(raw or "").split()).strip()[:180]
            if len(value) < 3:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    @staticmethod
    def _normalized_hint(value: str) -> str:
        return " ".join(str(value or "").lower().split()).strip()

    @staticmethod
    def _ready_count(
        preflight_by_url: dict[str, TelegramOpportunityPreflightResult],
    ) -> int:
        return sum(item.status == "READY" for item in preflight_by_url.values())

    async def _preflight_live_comment_targets(
        self,
        project_id: UUID,
    ) -> dict[str, TelegramOpportunityPreflightResult]:
        results: dict[str, TelegramOpportunityPreflightResult] = {}
        for opportunity in customer_live_opportunity_service.list_for_project(project_id):
            if opportunity.status != "ACTIVE" or opportunity.freshness == "STALE":
                continue
            if opportunity.platform != "TELEGRAM":
                continue
            if opportunity.kind not in {"COMMENT", "CHANNEL"}:
                continue
            target_url = str(opportunity.url)
            normalized = self._normalized_url(target_url)
            if normalized in results:
                continue
            result = await self._telegram_preflight.check_comment_target(
                project_id,
                target_url,
            )
            results[normalized] = result
            self._persist_live_preflight(project_id, target_url, result)
        return results

    async def _apply_comment_preflight_to_distribution(
        self,
        project_id: UUID,
        distribution,
        preflight_by_url: dict[str, TelegramOpportunityPreflightResult],
    ):
        opportunities = list(getattr(distribution, "opportunities", []) or [])
        if not opportunities:
            return distribution

        updated_opportunities = []
        for opportunity in opportunities:
            if (
                getattr(opportunity, "platform", None) != DistributionPlatform.TELEGRAM
                or getattr(opportunity, "kind", None) != OpportunityKind.CHANNEL
            ):
                updated_opportunities.append(opportunity)
                continue

            metadata = (
                dict(opportunity.metadata)
                if isinstance(getattr(opportunity, "metadata", None), dict)
                else {}
            )
            target_url = str(metadata.get("action_target_url") or "").strip()
            if not target_url or metadata.get("action_target_specific") is not True:
                updated_opportunities.append(opportunity)
                continue

            capabilities = (
                dict(metadata.get("surface_capabilities"))
                if isinstance(metadata.get("surface_capabilities"), dict)
                else {}
            )
            native_comment_surface = str(capabilities.get("comment") or "").upper()
            metadata["native_comment_surface"] = native_comment_surface or "UNKNOWN"

            normalized = self._normalized_url(target_url)
            result = preflight_by_url.get(normalized)
            if result is None:
                if native_comment_surface != "AVAILABLE":
                    result = TelegramOpportunityPreflightResult(
                        status="NO_DISCUSSION",
                        handle=str(metadata.get("handle") or "") or None,
                        reason="NATIVE_COMMENT_SURFACE_UNAVAILABLE",
                    )
                else:
                    result = await self._telegram_preflight.check_comment_target(
                        project_id,
                        target_url,
                    )
                    preflight_by_url[normalized] = result
                    self._persist_live_preflight(project_id, target_url, result)

            metadata["publisher_comment_preflight"] = {
                **result.public_dict(),
                "checked_at": datetime.now(UTC).isoformat(),
            }
            # Existing DistributionPlayPlanner already blocks client-owned COMMENT
            # execution when comment capability is not AVAILABLE. Reuse that boundary:
            # only a preflight-confirmed READY target remains executable. JOIN_REQUIRED
            # stays visible to the customer but cannot be selected until membership is
            # handled through a separately authorized operation.
            capabilities["comment"] = "AVAILABLE" if result.execution_ready else "UNAVAILABLE"
            metadata["surface_capabilities"] = capabilities

            updated = opportunity.model_copy(update={"metadata": metadata})
            updated_opportunities.append(updated)
            update_opportunity = getattr(self._audience_service, "update_opportunity", None)
            if callable(update_opportunity):
                update_opportunity(updated)

        if hasattr(distribution, "model_copy"):
            return distribution.model_copy(update={"opportunities": updated_opportunities})
        return distribution

    def _persist_live_preflight(
        self,
        project_id: UUID,
        target_url: str,
        result: TelegramOpportunityPreflightResult,
    ) -> None:
        normalized = self._normalized_url(target_url)
        publishability = (
            result.status
            if result.status in {
                "READY",
                "JOIN_REQUIRED",
                "NO_WRITE_ACCESS",
                "NO_DISCUSSION",
                "PRECHECK_FAILED",
            }
            else "PRECHECK_FAILED"
        )
        status = "ACTIVE" if result.recoverable else "STALE"
        now = datetime.now(UTC).isoformat()
        detail = f"Telegram publisher preflight: {result.status}"
        if result.reason:
            detail = f"{detail} ({result.reason})"

        for row in self._store.list_namespace(CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE):
            if str(row.get("project_id") or "") != str(project_id):
                continue
            if self._normalized_url(str(row.get("url") or "")) != normalized:
                continue
            if str(row.get("status") or "") == "PUBLISHED":
                continue
            row["publishability"] = publishability
            row["publishability_detail"] = detail[:1200]
            row["last_checked_at"] = now
            row["status"] = status
            row["updated_at"] = now
            key = f"{project_id}:{row['opportunity_id']}"
            self._store.put(CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE, key, row)

    def _project_id_for_product(self, product_id: UUID) -> UUID | None:
        matches: list[UUID] = []
        for project in self._store.list_namespace(CUSTOMER_PROJECT_NAMESPACE):
            if project.get("deleted_at"):
                continue
            if str(project.get("product_id") or "") != str(product_id):
                continue
            try:
                matches.append(UUID(str(project["id"])))
            except (KeyError, TypeError, ValueError):
                continue
        return matches[0] if len(matches) == 1 else None

    def _candidate_mandates(self, product_id: UUID | None) -> list[GrowthMandateView]:
        if product_id is not None:
            payload = self._store.get(GROWTH_MANDATE_NAMESPACE, str(product_id))
            if payload is None:
                return []
            try:
                mandate = GrowthMandateView.model_validate(payload)
            except ValueError:
                return []
            return [mandate] if self._eligible(mandate) else []

        mandates: list[GrowthMandateView] = []
        for payload in self._store.list_namespace(GROWTH_MANDATE_NAMESPACE):
            try:
                mandate = GrowthMandateView.model_validate(payload)
            except ValueError:
                continue
            if self._eligible(mandate):
                mandates.append(mandate)
        mandates.sort(key=lambda item: str(item.product_id))
        return mandates

    @staticmethod
    def _eligible(mandate: GrowthMandateView) -> bool:
        return (
            mandate.status == GrowthMandateStatus.ACTIVE
            and DistributionPlatform.TELEGRAM in mandate.allowed_platforms
        )

    @staticmethod
    def _due(previous: dict | None, *, now: datetime, interval_seconds: int) -> bool:
        if not previous:
            return True
        raw = previous.get("last_success_at")
        if not raw:
            return True
        try:
            last_success = datetime.fromisoformat(str(raw))
        except ValueError:
            return True
        if last_success.tzinfo is None:
            last_success = last_success.replace(tzinfo=UTC)
        else:
            last_success = last_success.astimezone(UTC)
        return now >= last_success + timedelta(seconds=interval_seconds)

    @staticmethod
    def _normalized_url(value: str) -> str:
        return str(value or "").strip().rstrip("/")

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE)


autonomous_opportunity_refresh_service = AutonomousOpportunityRefreshService()
