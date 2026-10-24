# Telegram message tone: from neutral to audience-specific playful voice

**Date:** 2026-10-24. **PR:** #545, open and not deployed.
**Research:** Three bounded, read-only one-call experiments on the same saved post:
[v8 run 40082613111](https://github.com/vladimirutkin2mn-lgtm/Partizan_bot/actions/runs/40082613111),
[v9 run 40082959653](https://github.com/vladimirutkin2mn-lgtm/Partizan_bot/actions/runs/40082959653),
[v10 run 40083165571](https://github.com/vladimirutkin2mn-lgtm/Partizan_bot/actions/runs/40083165571).
Each run makes zero Telegram API calls, sends zero messages and uses one `gpt-6-luna` model call.

## What the human reviewer clarified

For an adult D/s Telegram community, generic event praise and polite logistical questions are not a natural contribution. The desired tone is **lightly flirtatious, witty and aware of the community's own language** (e.g., "нижние", "экшены", "мастера"), with grounded references to event features such as auction, role dynamics, dress code, limited capacity and refreshments. The operator supplied seven *illustrative* examples for `https://t.me/a_sfera/2117`; these are **style preferences, not claims that the model wrote them**. The generator must learn the *mechanism* of teasing rather than copy an example or make up attendance/identity/participation rules.

v8 encouraged surprising contrasts; v9 allowed plainly hypothetical role teasing; v10 prefers in-group anticipation and power-dynamic jokes rather than defaulting to a meal-related punchline. All remain constrained to **one draft in one LLM call**, the target's language, factual source details, and separate publishing approval/commercial honesty.

## Actual single-draft comparisons, same archived Telegram post

| Version | Actual generated draft | Input | Output | Total |
| --- | --- | ---: | ---: | ---: |
| v7 (previous) | Чёрно-оранжевая тема с масками и гримом — уже половина атмосферы, для ночи ужасов прямо в точку. | 1,403 | 264 | 1,667 |
| v8 | Файер-плей, а потом шашлыки с пловом — вечер явно проверит и самообладание, и аппетит) | 1,625 | 358 | 1,983 |
| v9 | Даже демонам после огненных практик, выходит, полагается сначала плов и безалкогольный коктейль — а уж потом снова наводить ужас) | 1,633 | 422 | 2,055 |
| v10 | Мест мало, ночь до утра и всё без алкоголя: даже «я совершенно спокоен» придётся отыгрывать на трезвую голову, пока добрые садисты не проверят эту версию) | 1,666 | 409 | 2,075 |

**Assistant judgement (not independent human grading):** v8 moves away from abstract theme praise toward a concrete contrast; v9 is more playful but still makes food the main joke; v10 develops a more mischievous and role-aware imaginative beat. None are ready to assume conversion value. v10 still overuses quotation marks and is longer/more authored than several user-supplied gold examples. Relative token usage is from single generations, not a normalized cost benchmark.

### Critical source limitation

The saved post's `recent_context[*].text` snapshot has **only 1,200 characters**, ending mid-sentence (at the sentence introducing how the attendance fee is calculated). The initial v7 evaluation script additionally clipped that snapshot to **900 characters**, concealing `БЕЗалкогольное мероприятие`, limited seats, rules, and the beginning of the fee details.

The new bounded research preview now passes up to **2,400** characters of the latest native message (same production composer context cap) and the new one-call research runs preserve as much of the saved record as exists. **This does not restore missing content.** The user-supplied value `4311` was not in the archived source, so any attempt to generate that fee from the record alone would be a hallucination.

**Follow-up engineering gap:** identify where native Telegram messages are truncated before persistence, preserve the full latest post within a sensible single-post cap (Telegram normal text message upper bound) and explicitly carry `text_truncated/source_length` metadata. Do not read multiple older messages just to repair a truncated latest post; refresh that same target if full content is required, under the normal read budget. Avoid relying on model memory or user examples to fabricate absent facts.

## Rollout and validation status

All prompts are in unmerged PR #545. These runs are **research-only**. The three one-shot workflow triggers were reset to `workflow_dispatch` after completion so ordinary pushes cannot spend more model tokens. No Telegram publishing performed. Further testing should use human rating for (1) feels like an actual in-group comment, (2) flirtatious but not creepily forced, (3) factual anchoring, and (4) likelihood that a prospective user would want to interact.
