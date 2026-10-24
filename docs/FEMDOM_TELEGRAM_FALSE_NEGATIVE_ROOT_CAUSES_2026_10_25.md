# FemDom Telegram: why hand-verified audiences were rejected (2026-10-25)

## Evidence sources and scope
- Cached-fit integrity analysis: `docs/TELEGRAM_SEMANTIC_CACHE_REGISTRY_RECONCILIATION.md`, original runs 40099190034 and 40099340243.
- Read-only nine-peer native acceptance: `docs/FEMDOM_TELEGRAM_NINE_SAVED_PEERS_ACCEPTANCE_2026_10_24.md`, run 40098710720.
- Human observations: `ops/femdom/telegram_operator_review_2026-10-25.json`, applied in run 40137103321.
- Previously failing automatic review: run 40137459026, with `Expected Telegram opportunity has no specific native message target`.
- This is an evidence-backed root-cause analysis, **not** a claim that fresh write access has been confirmed.

## Different false-negative causes

| Handle | Earlier stored classification | User feedback | Root cause |
| --- | --- | --- | --- |
| `findom_gospoja0` | cache DIRECT, registry not confirmed | DIRECT; group-root writing after membership | cached semantic result not reconciled with registry; surface/membership was a separate gate |
| `moscowellebdsm1` | cache DIRECT, registry not confirmed | DIRECT; no writing surface | same cache/registry mismatch; audience fit does not grant comments |
| `poiskrabovirabin` | cache DIRECT, registry not confirmed | DIRECT; standalone group post after membership | same cache/registry mismatch; group-root was incorrectly subordinated to message reply |
| `a_sfera` | cache ADJACENT (0.95), registry not confirmed | DIRECT; visible writing surface | semantic rubric treated broad BDSM as mere adjacency; input could omit post-specific audience evidence; no valid saved exact action target later blocked old preview |
| `play_hard_community` | cache ADJACENT | DIRECT; visible writing surface | same overly narrow audience rubric; independent native posting preflight is still needed |
| `bizarreclubmsk` | not among original nine cache-only sample | DIRECT; visible comments | referral from `bizarre_afisha` led to an additional known public peer; absence in nine-peer classification is not a negative model verdict |

Two other manual DIRECT confirmations (`fem_tut`, `bizarre_afisha`) were already in the registry, but neither had usable comments at the examined newest post; `bizarre_afisha` referred to another channel. `znakomstvachannel` and `poiskfanfikov` were UNRELATED in the cached sample. Do **not** silently convert unspecified list indices to named labels.

## Fixes and expected behaviour

1. Cached DIRECT verdicts are reconciled before rotating native inspection; user-confirmed fit is stored in the registry independently of the model. The earlier run applied eight human labels and produced 8 confirmed audiences from 5 without granting sender rights.
2. New semantic evaluation is about the **participants and actual audience**, not whether every participant or newest message mentions the product's narrow keyword. Broad communities remain ADJACENT unless there is concrete evidence. Short evidence now includes the **tail of long posts**, where attendee requirements may appear. Two recent native examples at most; one model evaluation per uncached community. Existing cached evaluations are reused without new charges.
3. `STANDALONE_POST` requires a fresh verified native group-root surface (`https://t.me/<handle>`), no fabricated Telegram message ID, and remains human-review-only.
4. The legacy `customer_telegram_preview` first-publish rollout only accepts CHANNEL + native specific comment target. It should **not** be automatically run as a generic FemDom researcher after each deploy. Its failure was expected on a saved `action_target_specific=False` research-only opportunity. The bounded FemDom research-only preview instead refreshes two saved peers (`a_sfera`, `play_hard_community`), checks newest native message availability and builds at most one unsendable draft each. If the channel lacks current comments or an eligible post, it reports `RESEARCH_PREVIEW_BLOCKED` with a reason rather than pretending it can post.
5. New bounded reconciliation reports original fit reason, cache verdict and whether a user/model disagreement persists, preserving manual reasoning and avoiding paying to rejudge confirmed communities.

## Not yet proven
- Current Telegram-native comment or standalone-post access for all eight communities; account-specific membership and commercial rules.
- Current eligible newest post and generated draft from **production** after this patch: CI and post-merge one-off run must establish it.
- Audience fit at scale: these are two audited policy false negatives, not a measured false-negative rate.
- Actual customer conversion after any posting: no publication is authorized.

## Non-negotiable boundaries

No global Telegram discovery, no automatic joining, no sending, no profile changes, no channel toggle, no granting marketing permissions and no merging research fit with publisher permissions. The experiment still requires the user's explicit «публикуй» before every send.