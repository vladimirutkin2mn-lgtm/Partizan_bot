# Partizan Telegram v6 versus v7: actual saved-post A/B review

**Date:** 2026-10-24. **Mode:** offline drafts on already-saved exact posts only.
**Experiment:** [GitHub Actions run 40079412182](https://github.com/vladimirutkin2mn-lgtm/Partizan_bot/actions/runs/40079412182), short-retention private artifact `telegram-v7-saved-post-review-PR545`.
**Status:** v7 remains an unmerged research candidate in PR #545. No Telegram API requests, joins, sends or publications. Both prompt versions made exactly one draft per post; 3 v6 outputs were taken from the earlier private run #40022400009 rather than regenerated.

## Reproducibility and constraints

Both runs used `gpt-6-luna`, the same structured output schema, the same frozen product/community payload shape, and **exactly the same three target post text bytes**. The v7 replay compared SHA-256 fingerprints of all three saved excerpts to the v6 source before making any paid LLM calls. This is a three-pair observational model comparison; it does not quantify engagement or conversions, and random variability is not controlled with multiple samples.

## Side-by-side actual output

| Community / target | v6 historical draft | v7 candidate draft | Assistant qualitative judgement |
| --- | --- | --- | --- |
| [a_sfera/2117](https://t.me/a_sfera/2117) | Подскажите, пожалуйста, на мероприятие нужно записываться заранее? И адрес присылаете после подтверждения участия? | Чёрно-оранжевая тема с масками и гримом — уже половина атмосферы, для ночи ужасов прямо в точку. | v7 is less stilted and more anchored in details, but merely praises the theme; a long em dash violates v7 editing instructions and acquisition value is unclear. v6 asked about the address even though it was specified in the post. |
| [fem_tut/76](https://t.me/fem_tut/76) | А там обсуждения в чате или встречи тоже организуют? Хотелось бы понимать формат до входа. | Формулировка сразу задаёт очень прямой тон — тут без интриги. | v7 removes the forced admin question but just observes the ad's tone; no reason for a qualified prospect to engage. Em dash persists. |
| [ZnakomstvaChannel/19710](https://t.me/ZnakomstvaChannel/19710) | А если после отправки захочется что-то поправить в анкете, можно будет отредактировать её через бота? | Главное, чтобы анкета не превратилась в сочинение на свободную тему 😄 | v7 reads like a casual quip instead of tech support, but it could fit any form and has no topic/product fit. v7 model's own reason explicitly says there is no FemDom relationship. **This is a discovery/target selection mismatch; do not treat a generated draft as authorization to publish.** |

All comments are unposted research outputs, not recommended publications.

## Exact token accounting

| Metric | v6 | v7 | Difference |
| --- | ---: | ---: | ---: |
| Successful LLM calls | 3 | 3 | 0 |
| Nonempty drafts | 3 | 3 | 0 |
| Input tokens | 3,241 | 3,385 | +144 (+4.44%) |
| Output tokens | 925 | 982 | +57 (+6.16%) |
| Total tokens | 4,166 | 4,367 | +201 (+4.82%) |
| Telegram reads/sends | 0 / 0 | 0 / 0 | 0 |

Token results are usage counts, not pricing and not controlled repeated-model cost estimates. v7 per-case input/output: `a_sfera` 1403/264, `fem_tut` 957/228, `znakomstvachannel` 1025/490.

## Assessment and next decisions

The social-voice rules **moved replies away from formulaic “Подскажите, пожалуйста…” questions**, but v7 still generates formulaic agreement, content-free meta-observations, and off-topic quips. A polished conversational surface is not sufficient for Partizan's customer acquisition goal. **No causal naturalness uplift or conversion lift is proven by three assistant-reviewed samples.**

The next prompt candidate should prefer an interesting post-specific reaction that can meaningfully advance conversation, and discourage commenting solely on the post's writing style. Keep **one generation and one draft** per vetted target; do not revert to blanket abstention after the audience has been selected. Correct the **upstream product-to-community/topic matching** so that unrelated `ZnakomstvaChannel` prompts don't become eligible targets. Reuse cached discovery and the latest exact post only, without paying for duplicate research. Existing explicit permission and publication-review checks are unchanged.

The one-shot GitHub workflow was set back to **manual-only `workflow_dispatch`** after the successful v7 run, preventing unplanned future LLM spend. **Do not merge or deploy v7 based only on this evidence.**
