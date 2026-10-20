# Historical Telegram audience recovery under RESEARCH_ONLY

Problem observed in production FemDom (20 October 2026): 10 old opportunity
rows (9 public communities) in `audience_distribution_map`, none in
`telegram_community_registry`. PR #520 introduced staging but calls it only
inside Discovery; paused mandates can block that entire engine.

`python -m app.telegram_historical_recovery --product-id UUID --project-id UUID`
is a **bounded one-off research migration** that requires the project Telegram
channel to remain `RESEARCH_ONLY` and does not use the growth/discovery engine.

1. Migrate public handles to canonical registry as unverified RESEARCH_ONLY,
   preserve old opportunity IDs and product scope, do not inherit historical
   READY/VERIFIED status or targets.
2. Read already known public peers through `refresh_registered` in
   conservative batches of 3, max 12 handles, with shared FloodWait discipline.
   Unknown audiences retain initial context sample; confirmed audiences fetch
   **only the newest post**. No new Telegram group search, paid web or LLM.
3. Use the existing native audience-fit gate. When a specific newest post is
   verified, has readable text, is <14 days old and exact message ID/URL match,
   copy its fresh native metadata to the **existing persisted opportunity**,
   keeping its ID. It is still `NEEDS_PREFLIGHT`, not publishing permission.
   If missing, unrelated or unverifiable, leave `RESEARCH_ONLY` and no target.
4. Do not turn on channel automation, send Telegram messages, join communities,
   approve posts, prepare ad copy or change user profile/mandate. FemDom send
   requires operator's explicit `публикуй`.

Manual UI screenshot for @a_sfera proves there is a visible comment surface
for the pictured post, **not** that the connected Partizan identity can write.
A real account-specific publishing preflight remains a separate requirement.

Run produces JSON report (private short-lived workflow artifact) listing each
inspected community, fitness decision, target URL or explicit rejection reason.
