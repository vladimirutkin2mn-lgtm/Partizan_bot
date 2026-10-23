# Conversation-first Telegram v5 review (research only)

**Date:** 2026-10-23. **PR:** #545. **Source:** six previously saved, native-verified latest posts from the FemDom research pool; no new Telegram messages were read.

## Changes

- `app/community_message_prompt.py` defines `community_message_v5_conversation_first`: concrete reasons to join a conversation instead of generic praise, unsolicited advice, moral lectures, paraphrases or compulsory questions. One message or null, in the target language. No deceptive first-person experience or hidden product affiliation.
- `app/telegram_message_precheck.py` adds conservative zero-model screening for bare CTA, tiny invitations, admin notices, and unsolicited self-promotional messages. This is **not** a universal semantic audience-fit model: relevant product-specific opportunities still require an audience-fit/target check.
- The filter runs **before a model call** in both the production-planning Telegram target path and the restricted FemDom research preview. Existing publishing permission and human approval checks remain separate and unchanged.
- `tools/review_saved_telegram_prompts_v5.py` is a research-only script. It uses a frozen previous unrelated verdict from the same exact product/post text to avoid paying again. The checked-out candidate prompt and precheck are injected into the previously deployed API container via GitHub Actions stdin/base64, without deploying branch code.
- No autonomous publication, no action scheduling, no account or Telegram state mutation.

## Observed offline test

[Private research run #40020446275](https://github.com/vladimirutkin2mn-lgtm/Partizan_bot/actions/runs/40020446275), artifact `telegram-v5-saved-post-review-PR545` (3-day retention).

| Source | Gate/outcome | LLM calls |
| --- | --- | ---: |
| `a_sfera/2117` | Model abstained: event announcement had no credible contribution/conversion angle | 1 |
| `fem_tut/76` | Short invitation, no real conversation | 0 |
| `moscowellebdsm1/29` | Admin rules, not an acquisition discussion | 0 |
| `poiskfanfikov/55717` | Exact target previously marked unrelated by v3 and v4; cached in research script | 0 |
| `znakomstvachannel/19710` | Bare questionnaire CTA | 0 |
| `poiskrabovirabin/2750442` | Unsolicited promotional message | 0 |

Six cases, **5 blocked without model**, **1 model call**. **1,419 input + 147 output = 1,566 total tokens**. Compared with the previous v3+v4 paired experiment's total of 14,763 tokens across 12 calls, this research run used 89.4% fewer tokens, but **that is not a clean same-workload per-version comparison** (one draft per remaining target versus two per target).

**No message was drafted; no publication.** This is evidence of cheaper screening/abstention, **not evidence that generated v5 messages sound human or attract users**. The v5 prompt is longer than v4; input tokens on a surviving eligible post may grow. Without a real target that warrants participation, naturalness cannot be meaningfully evaluated.

The previous v3/v4 artifact is the evidence behind the exact `poiskfanfikov/55717` cache key. This is a bounded benchmark optimization, not a production product-topic blacklist. Production still needs a product-aware, cached topicality gate for ambiguous off-topic long posts; blindly inventing keyword rules would suppress valid opportunities.

## Next experiment

Source more **already saved** audiences/posts from the existing product's database, but restrict to genuine conversation candidates and do not read hundreds of messages for known audience communities. Evaluate a handful of real, *on-topic and answer-worthy* selected targets, compare visible v3 vs v5 drafts with independent human rating and token counts. Include examples of AI-sounding replies and null responses; use the established `CommunityMessagePromptAutoResearch` review rubric. Avoid automated promotion to production until the generation quality is demonstrated and explicitly reviewed.

The one-shot workflow was reset to `workflow_dispatch` after the paid run: routine PR commits no longer repeat the experiment.
