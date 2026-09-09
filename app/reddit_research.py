import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from app.distribution_schemas import CommunityPolicyView
from app.search import SearchHit

REDDIT_POLICY_MAX_AGE = timedelta(days=7)
REDDIT_ACTION_TARGET_MAX_AGE = timedelta(days=7)
REDDIT_MAX_ACTION_TARGETS = 12


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def policy_freshness_reason(
    policy: CommunityPolicyView,
    *,
    now: datetime | None = None,
) -> str | None:
    current = ensure_utc(now or utc_now())
    if policy.last_checked_at is None:
        return "Reddit CommunityPolicy has no freshness timestamp"
    checked = ensure_utc(policy.last_checked_at)
    if checked > current + timedelta(minutes=5):
        return "Reddit CommunityPolicy freshness timestamp is invalid"
    if current - checked > REDDIT_POLICY_MAX_AGE:
        return "Reddit CommunityPolicy is stale and must be refreshed"
    if policy.fresh_until is not None and current > ensure_utc(policy.fresh_until):
        return "Reddit CommunityPolicy is stale and must be refreshed"
    if policy.research_status in {"UNKNOWN", "STALE"}:
        return "Reddit CommunityPolicy research is not verified"
    return None


def reddit_thread_target_from_hit(
    hit: SearchHit,
    *,
    subreddit: str,
    checked_at: datetime,
) -> dict | None:
    if not _is_subreddit_thread(hit.url, subreddit):
        return None
    published_at, basis = _published_at(hit, checked_at)
    if published_at is None:
        freshness_status = "UNVERIFIED"
    else:
        age = ensure_utc(checked_at) - published_at
        freshness_status = (
            "FRESH" if timedelta(0) <= age <= REDDIT_ACTION_TARGET_MAX_AGE else "STALE"
        )
    return {
        "url": hit.url,
        "title": hit.title,
        "snippet": hit.snippet[:600],
        "source": "reddit_research",
        "checked_at": ensure_utc(checked_at).isoformat(),
        "published_at": published_at.isoformat() if published_at is not None else None,
        "freshness_basis": basis,
        "freshness_status": freshness_status,
    }


def action_target_is_fresh(target: dict, *, now: datetime | None = None) -> bool:
    if str(target.get("freshness_status", "")).upper() != "FRESH":
        return False
    raw = target.get("published_at")
    if not raw:
        return False
    try:
        published = ensure_utc(datetime.fromisoformat(str(raw)))
    except (TypeError, ValueError):
        return False
    current = ensure_utc(now or utc_now())
    age = current - published
    return timedelta(0) <= age <= REDDIT_ACTION_TARGET_MAX_AGE


def _is_subreddit_thread(url: str, subreddit: str) -> bool:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    host = parts.netloc.lower().removeprefix("www.")
    if host != "reddit.com" and not host.endswith(".reddit.com"):
        return False
    segments = [segment for segment in parts.path.split("/") if segment]
    lowered = [segment.lower() for segment in segments]
    if len(lowered) < 4 or lowered[0] != "r" or "comments" not in lowered:
        return False
    return lowered[1] == subreddit.lower().removeprefix("r/")


def _published_at(hit: SearchHit, checked_at: datetime) -> tuple[datetime | None, str]:
    for key in ("published_at", "created_at", "date"):
        raw = hit.metadata.get(key)
        if not raw:
            continue
        try:
            if isinstance(raw, datetime):
                return ensure_utc(raw), f"metadata.{key}"
            return ensure_utc(datetime.fromisoformat(str(raw).replace("Z", "+00:00"))), f"metadata.{key}"
        except (TypeError, ValueError):
            continue

    text = f"{hit.title} {hit.snippet}".lower()
    match = re.search(r"\b(\d{1,2})\s+(minute|hour|day|week)s?\s+ago\b", text)
    if match is None:
        return None, "none"
    amount = int(match.group(1))
    unit = match.group(2)
    delta = {
        "minute": timedelta(minutes=amount),
        "hour": timedelta(hours=amount),
        "day": timedelta(days=amount),
        "week": timedelta(weeks=amount),
    }[unit]
    return ensure_utc(checked_at) - delta, "snippet.relative_time"
