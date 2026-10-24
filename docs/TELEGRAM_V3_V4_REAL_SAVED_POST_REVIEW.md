# Real saved Telegram post comparison: message prompt v3 vs v4

**Review date:** 2026-10-23. **Mode:** research-only, without Telegram API reads or publication.
**Source:** existing verified native snapshots saved in the Partizan Telegram community registry (not live current-post queries).
**Model:** `gpt-6-luna` via OpenAI Responses structured parse; **same product input, target text, output schema, common system wrapper, model and max-output cap** for each paired draft. Only the frozen v3/v4 guidance differs. This compares prompt instructions, **not** the full v3 production truncation against the full v4 drafting pipeline.

## Observed counts

| Measure | v3 | v4 |
| --- | ---: | ---: |
| Saved native posts paired | 6 | 6 |
| Channel posts | 5 | 5 |
| Group messages | 1 | 1 |
| Messages generated | 4 | 5 |
| Correctly abstained on clearly unrelated marketplace post | Yes | Yes |
| Incoming tokens | 5,846 | 5,984 |
| Outgoing tokens | 1,509 | 1,424 |
| Total tokens | 7,355 | 7,408 |

Total cost-sensitive token consumption increased by **0.72%** in v4; input tokens grew **2.36%** and output tokens decreased **5.63%**. These are usage counts, not a dollar price estimate. Three of the nine approved stored peers were excluded because their newest saved message had insufficient readable text.

**Publication actions: 0. Telegram API calls: 0.** A private, three-day GitHub Actions artifact holds the actual same-target draft pairs: workflow run [40007696878](https://github.com/vladimirutkin2mn-lgtm/Partizan_bot/actions/runs/40007696878), artifact `telegram-v3-v4-saved-post-review-PR545`. The artifact includes saved source text and should not be pasted into public issue comments.

## Assistant qualitative review — not independent human annotation

| Saved post | v3 observation | v4 observation | Preliminary judgement |
| --- | --- | --- | --- |
| `@a_sfera/2117` | Asks a fairly generic event safety question | References the event's *specific* Halloween dress-code guidance | v4 more specifically grounded, but neither has a clear acquisition next step |
| `@fem_tut/76` | Generic discussion of boundaries | Relates its response to the source's specific wording | Marginal specificity improvement; still not a compelling conversion mechanism |
| `@moscowellebdsm1/29` | Asks about a payment rule already answered in the source | Summarizes the re-entry rule and suggests saving the profile text | v4 marginally more helpful; both off the project's conversion path |
| `@poiskfanfikov/55717` | No response to unrelated resale announcement | No response to unrelated resale announcement | Both correctly abstain |
| `@znakomstvachannel/19710` | No response to bare 'fill in a form' CTA | Invents a generic tip about filling in a form | **v4 false positive**: the selected post contains insufficient substantive detail for a worthwhile reply |
| `@poiskrabovirabin/2750442` | Generic safety-oriented response | Positive-sounding reaction to an explicit self-promotion message | **Needs safety/tone review**; no credible conversion mechanism |

The content in the last case was an adult, aggressively worded group advertisement. Do not interpret a flattering reaction to it as an effective marketing message. The source is also a saved historical snapshot, not proof that the current group is available or that posting is permitted.

## Verdict

**Do not promote v4 to the live drafting champion on this evidence.** It improves specificity in some cases but over-generates when the post is only a CTA, and the group example has a tone/safety concern. No clicks, profile visits, signups or paid conversions were measured (nothing was published). Consequently **conversion uplift cannot be determined**.

The existing `CommunityMessagePromptAutoResearch` evaluator requires human-reviewed paired grades. This assessment is an **assistant qualitative review**, not an independent human scoring sample, so no champion update or fake `CANDIDATE_FOR_MANUAL_PROMOTION` evaluation is written.

Recommended next experimental challenger (v5, separate PR): verify a concrete target-specific conversational contribution is possible before calling the model, block bare link/CTA-only posts when there is no substantive discussion, improve brand/conversion mechanism without irrelevant product plugs, add a stricter moderation review for aggressive sexual advertising, then re-run the same six stored snapshots **only after changes are ready**. Continue to keep production one message/one call and no posting without explicit publishing approval.
