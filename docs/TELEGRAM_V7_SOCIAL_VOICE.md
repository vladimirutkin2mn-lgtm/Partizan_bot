# Telegram prompt v7: conversational social-media voice

**Status:** candidate in [PR #545](https://github.com/vladimirutkin2mn-lgtm/Partizan_bot/pull/545); not merged or deployed.
**Prior experiment:** v6 produced three nonempty Telegram drafts on three previously saved posts in a nonpublishing research run, but all three leaned toward polite logistical questions. This document describes a prompt edit, **not** a new live-model quality result.

## Problem and correction

The v6 prompt prioritized specificity but kept producing stiff, over-polite replies such as “Подскажите, пожалуйста…”. The new English-language v7 guidance retains **one output, one model call** for a verified selected target and adds constraints addressing the language itself:

- Short everyday phrasing; light slang or dry humour only where it fits the selected community.
- Sentence length variation without mechanical long/short alternation.
- No AI or corporate clichés (Russian examples and target-language equivalents), formulaic compliments, compulsory questions or moralizing.
- No lists, sign-offs, excessive punctuation, decorative quotations, long em dashes or recurring emojis.
- Grounded opinion or observation, or an honest question, about a real detail in the post.
- One contrastive “robotic vs natural” example, explicitly not a reusable comment template.
- **No fabricated first-hand experience or undisclosed product promotion.** “Pretend you are a real consumer” was not adopted because Partizan must not disguise promotional interests or misrepresent endorsements.

The prompt is roughly the same size as v6 rather than appending long extra instructions, to keep per-call token spend controlled. Shared versioning covers both the ordinary Telegram action composer and restricted research preview.

## What the tests establish

`tests/test_community_message_prompt.py` checks the instruction contract for one message, channel/group scope, language, forbidden clichés, style, transparency, and compact length. Integration tests check the shared prompt reaches the existing composer. They **do not** establish that model outputs will pass a human naturalness test. No new Telegram reads or posts were required to implement this change.

## Next quality check

Replay v7 against the same saved on-topic posts as v6, compare the actual texts side by side using independent human rating, record input/output tokens, and never send a draft before explicit permission. Consider ranking responses that have an actual observation above polite questions about logistics; do not quietly relax ad-disclosure, factuality or community posting restrictions.
