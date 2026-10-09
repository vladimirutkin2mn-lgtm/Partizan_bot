from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.runtime_store import RuntimeStateStore, get_runtime_store

DISCOVERY_NEGATIVE_LEARNING_NAMESPACE = "discovery_negative_learning"
NEGATIVE_LEARNING_TTL = timedelta(days=30)
MAX_BLOCKED_CANONICAL_KEYS = 200
MAX_NEGATIVE_TERMS = 40
MIN_NEGATIVE_THEME_OBSERVATIONS = 2

_GENERIC_TERMS = {
    "about",
    "advice",
    "channel",
    "chat",
    "community",
    "discussion",
    "group",
    "help",
    "official",
    "people",
    "questions",
    "telegram",
    "thread",
    "today",
    "with",
    "канал",
    "комментарии",
    "люди",
    "обсуждение",
    "официальный",
    "официальная",
    "сообщество",
    "советы",
    "чат",
}


class DiscoveryRelevanceLearningService:
    """Filter weak acquisition candidates and learn reusable negative discovery signals.

    Learning is scoped to a product. An exact community rejected for weak semantic fit is
    suppressed on later rounds for 30 days. Theme terms are activated only after they are
    independently observed in at least two rejected candidates and only when they are not
    already part of the product/ICP vocabulary.
    """

    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def filter_and_learn(
        self,
        *,
        product: Any,
        icps: list[Any],
        opportunities: list[Any],
    ) -> tuple[list[Any], list[dict]]:
        product_id = self._product_id(product)
        snapshot = self.snapshot(product_id) if product_id is not None else self._empty_snapshot()
        accepted: list[Any] = []
        rejected: list[dict] = []

        for opportunity in opportunities:
            decision = self._decision(opportunity, snapshot)
            if decision["accepted"]:
                metadata = (
                    dict(getattr(opportunity, "metadata", {}) or {})
                    if isinstance(getattr(opportunity, "metadata", {}), dict)
                    else {}
                )
                metadata["relevance_guard"] = {
                    "status": "PASS",
                    "reason": decision["reason"],
                    "signal_families": decision["signal_families"],
                }
                if hasattr(opportunity, "model_copy"):
                    opportunity = opportunity.model_copy(update={"metadata": metadata})
                accepted.append(opportunity)
                continue

            rejected.append(
                {
                    "canonical_key": str(getattr(opportunity, "canonical_key", "") or ""),
                    "platform": self._platform_value(getattr(opportunity, "platform", None)),
                    "title": str(getattr(opportunity, "title", "") or ""),
                    "reason": decision["reason"],
                    "signal_families": decision["signal_families"],
                }
            )

        if product_id is not None and rejected:
            self.observe_rejections(
                product_id=product_id,
                product=product,
                icps=icps,
                rejections=rejected,
            )
        return accepted, rejected

    def filter_hints(
        self,
        product_id: UUID | str | None,
        hints: list[str] | tuple[str, ...],
    ) -> list[str]:
        """Remove hypotheses that repeat product-scoped negative discovery themes.

        This is intentionally applied before adaptive search requests are built. The
        relevance gate still validates returned evidence afterwards, but later rounds no
        longer spend query budget on themes already learned to be irrelevant.
        """
        normalized_product_id = self._uuid(product_id)
        if normalized_product_id is None:
            return self._dedupe_hints(hints)
        negative_terms = self.snapshot(normalized_product_id)["negative_terms"]
        filtered: list[str] = []
        for hint in self._dedupe_hints(hints):
            if self._tokens(hint) & negative_terms:
                continue
            filtered.append(hint)
        return filtered

    def snapshot(self, product_id: UUID) -> dict[str, set[str]]:
        payload = self._store.get(
            DISCOVERY_NEGATIVE_LEARNING_NAMESPACE,
            str(product_id),
        ) or {}
        now = datetime.now(UTC)
        blocked = {
            str(key).casefold()
            for key, entry in (payload.get("blocked_keys") or {}).items()
            if self._entry_active(entry, now)
        }
        negative_terms = {
            str(term).casefold()
            for term, entry in (payload.get("negative_terms") or {}).items()
            if self._entry_active(entry, now)
            and int((entry or {}).get("count") or 0) >= MIN_NEGATIVE_THEME_OBSERVATIONS
        }
        return {
            "blocked_keys": blocked,
            "negative_terms": negative_terms,
        }

    def observe_rejections(
        self,
        *,
        product_id: UUID,
        product: Any,
        icps: list[Any],
        rejections: list[dict],
    ) -> None:
        now = datetime.now(UTC)
        payload = self._store.get(
            DISCOVERY_NEGATIVE_LEARNING_NAMESPACE,
            str(product_id),
        ) or {
            "product_id": str(product_id),
            "blocked_keys": {},
            "negative_terms": {},
        }
        blocked = dict(payload.get("blocked_keys") or {})
        term_rows = dict(payload.get("negative_terms") or {})

        for row in rejections:
            canonical_key = str(row.get("canonical_key") or "").strip()
            if not canonical_key:
                continue
            current = dict(blocked.get(canonical_key.casefold()) or {})
            current["count"] = int(current.get("count") or 0) + 1
            current["last_seen_at"] = now.isoformat()
            current["reason"] = str(row.get("reason") or "IRRELEVANT")
            current["platform"] = str(row.get("platform") or "")
            blocked[canonical_key.casefold()] = current

        anchor_terms = self._anchor_terms(product, icps)
        batch_term_counts: Counter[str] = Counter()
        for row in rejections:
            if str(row.get("reason") or "") != "INSUFFICIENT_SEMANTIC_EVIDENCE":
                continue
            title_terms = {
                term
                for term in self._tokens(str(row.get("title") or ""))
                if term not in anchor_terms and term not in _GENERIC_TERMS
            }
            batch_term_counts.update(title_terms)

        for term, count in batch_term_counts.items():
            current = dict(term_rows.get(term) or {})
            current["count"] = int(current.get("count") or 0) + int(count)
            current["last_seen_at"] = now.isoformat()
            term_rows[term] = current

        blocked = self._trim_entries(blocked, MAX_BLOCKED_CANONICAL_KEYS)
        term_rows = self._trim_entries(term_rows, MAX_NEGATIVE_TERMS)
        self._store.put(
            DISCOVERY_NEGATIVE_LEARNING_NAMESPACE,
            str(product_id),
            {
                "product_id": str(product_id),
                "blocked_keys": blocked,
                "negative_terms": term_rows,
                "updated_at": now.isoformat(),
            },
        )

    def _decision(self, opportunity: Any, snapshot: dict[str, set[str]]) -> dict:
        canonical_key = str(getattr(opportunity, "canonical_key", "") or "").casefold()
        if canonical_key and canonical_key in snapshot["blocked_keys"]:
            return self._rejected("NEGATIVE_COMMUNITY_MEMORY")

        title_terms = self._tokens(str(getattr(opportunity, "title", "") or ""))
        negative_theme_overlap = title_terms & snapshot["negative_terms"]
        if len(negative_theme_overlap) >= 2:
            return self._rejected("NEGATIVE_THEME_MEMORY")

        metadata = getattr(opportunity, "metadata", {})
        signals = metadata.get("research_signals") if isinstance(metadata, dict) else None
        if not isinstance(signals, dict):
            return self._rejected("INSUFFICIENT_SEMANTIC_EVIDENCE")

        ratios = {
            "fit": self._as_float(signals.get("fit_ratio")),
            "pain": self._as_float(signals.get("pain_ratio")),
            "trigger": self._as_float(signals.get("trigger_ratio")),
            "alternative": self._as_float(signals.get("alternative_ratio")),
        }
        signal_families = [name for name, value in ratios.items() if value >= 0.15]
        matched_terms = {
            str(term).casefold()
            for term in list(signals.get("matched_terms") or [])
            if len(str(term).strip()) >= 3
        }
        demand_intent = int(signals.get("demand_intent_hits") or 0)
        commercial_intent = int(signals.get("commercial_intent_hits") or 0)
        intent_hits = demand_intent + commercial_intent
        independent_evidence = int(signals.get("independent_evidence_count") or 0)
        confidence = str(signals.get("confidence") or "LOW").upper()

        diversified_match = len(signal_families) >= 2 and len(matched_terms) >= 2
        direct_problem_match = (
            max(ratios["pain"], ratios["trigger"]) >= 0.25
            and intent_hits >= 1
            and len(matched_terms) >= 2
        )
        corroborated_fit = (
            ratios["fit"] >= 0.35
            and independent_evidence >= 2
            and (intent_hits >= 1 or ratios["alternative"] >= 0.15)
            and len(matched_terms) >= 2
        )
        confidence_supported = (
            confidence in {"MEDIUM", "HIGH"}
            and len(signal_families) >= 2
            and len(matched_terms) >= 2
        )

        if diversified_match or direct_problem_match or corroborated_fit or confidence_supported:
            return {
                "accepted": True,
                "reason": "MULTI_SIGNAL_RELEVANCE",
                "signal_families": signal_families,
            }
        return self._rejected(
            "INSUFFICIENT_SEMANTIC_EVIDENCE",
            signal_families=signal_families,
        )

    @staticmethod
    def _rejected(reason: str, *, signal_families: list[str] | None = None) -> dict:
        return {
            "accepted": False,
            "reason": reason,
            "signal_families": list(signal_families or []),
        }

    def _anchor_terms(self, product: Any, icps: list[Any]) -> set[str]:
        values = [
            getattr(product, "name", ""),
            getattr(product, "problem_or_desire", ""),
            getattr(product, "value_proposition", ""),
            " ".join(getattr(product, "known_audience", []) or []),
        ]
        for icp in icps:
            values.extend(
                [
                    getattr(icp, "title", ""),
                    getattr(icp, "description", ""),
                    getattr(icp, "pain", ""),
                    getattr(icp, "trigger", ""),
                    getattr(icp, "desired_outcome", ""),
                    " ".join(getattr(icp, "alternatives", []) or []),
                ]
            )
        result: set[str] = set()
        for value in values:
            result.update(self._tokens(str(value or "")))
        return result

    @staticmethod
    def _tokens(value: str) -> set[str]:
        words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", value.casefold())
        return {
            word
            for word in words
            if len(word) > 3 and not word.isdigit() and word not in _GENERIC_TERMS
        }

    @staticmethod
    def _dedupe_hints(values: list[str] | tuple[str, ...]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = " ".join(str(raw or "").split()).strip()[:180]
            if len(value) < 3:
                continue
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    @staticmethod
    def _platform_value(platform: Any) -> str:
        return str(getattr(platform, "value", platform) or "")

    @staticmethod
    def _as_float(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _product_id(cls, product: Any) -> UUID | None:
        return cls._uuid(getattr(product, "id", None))

    @staticmethod
    def _uuid(value: Any) -> UUID | None:
        if value is None:
            return None
        try:
            return value if isinstance(value, UUID) else UUID(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _entry_active(self, entry: Any, now: datetime) -> bool:
        if not isinstance(entry, dict):
            return False
        last_seen = self._parse_datetime(entry.get("last_seen_at"))
        return last_seen is not None and last_seen >= now - NEGATIVE_LEARNING_TTL

    def _trim_entries(self, entries: dict[str, dict], limit: int) -> dict[str, dict]:
        ranked = sorted(
            entries.items(),
            key=lambda item: (
                int((item[1] or {}).get("count") or 0),
                str((item[1] or {}).get("last_seen_at") or ""),
            ),
            reverse=True,
        )
        return dict(ranked[:limit])

    @staticmethod
    def _empty_snapshot() -> dict[str, set[str]]:
        return {"blocked_keys": set(), "negative_terms": set()}


# Production singleton. Per-product state in RuntimeStateStore keeps learning isolated
# between customers while allowing the same generic algorithm to be reused everywhere.
discovery_relevance_learning_service = DiscoveryRelevanceLearningService()
