# Telegram v6: require one concrete draft when an opportunity is selected

**Date:** 2026-10-23. **Status:** review-only candidate in PR #545, not merged/deployed.

The earlier v5 prompt instructed the model that NO MESSAGE is often better. In practice it drafted nothing on six saved posts, even for a legitimate community event. This contradicted the customer acquisition goal: once a relevant community and actual latest message have been selected, Partizan must attempt ONE grounded, natural contribution, rather than re-run target selection inside the text generator.

## Implemented correction

- `app/community_message_prompt.py` v6 explicitly directs the model to produce **one message on each readable selected target**; it must find a specific topic or ask a genuine grounded question without hallucinating or claiming to be an attendee/customer. Product promotion must never be disguised. Text language follows the source post; prompt instructions are English.
- `app/telegram_message_precheck.py` now rejects only unreadable content or link-only messages. It no longer treats announcements, administrative text, invitations or bare CTAs as automatic reasons to skip a vetted community.
- `app/femdom_research_comment_preview.py` uses a *required nonnull comment field* in the production-facing OpenAI structured-output schema, and a null/abstain no longer silently turns into a successful saved review. This still requires a human to inspect the draft. It **does not create an action or send**.
- Publishing authorization, fresh target checks, anti-duplicate protections, community rules and other safety gates remain separate. A readable post is **not** evidence of a user's permission to publish, nor does the research pilot assert audience fit for all posts.

## Real saved-post demonstration

[Research-only Actions run #40022400009](https://github.com/vladimirutkin2mn-lgtm/Partizan_bot/actions/runs/40022400009). Exactly three previously observed native Telegram snapshots. **3 calls / 3 nonempty Russian drafts**; **3,241 input + 925 output = 4,166 tokens**. Telegram API calls = 0; publication actions = 0.

- `a_sfera/2117` event announcement: **"Подскажите, пожалуйста, на мероприятие нужно записываться заранее? И адрес присылаете после подтверждения участия?"** (The post itself already says the venue address is sent via private messages to participants. The question is not strong enough; it mainly asks about logistics.)
- `fem_tut/76` short invitation: **"А там обсуждения в чате или встречи тоже организуют? Хотелось бы понимать формат до входа."** (Grounded in invitation context, but rather generic.)
- `ZnakomstvaChannel/19710` bare CTA: **"А если после отправки захочется что-то поправить в анкете, можно будет отредактировать её через бота?"** (Grounded in sign-up process but has no clear qualified-client acquisition benefit.)

The v6 pilot **does prove** that previously skipped posts now produce concrete review-only drafts with no extra LLM calls or alternative variants. It **does not prove** that these replies are sufficiently human-sounding or will attract new customers. The next prompt optimization should prioritize meaningful post-specific opinions rather than repetitive polite logistical questions. The model's safety and accuracy guardrails must remain; forcing a draft does not mean forcing publication.

The one-off workflow was converted to manual-only after the experiment, to prevent accidental repeated spend via future PR updates. No PR changes have been merged automatically.
