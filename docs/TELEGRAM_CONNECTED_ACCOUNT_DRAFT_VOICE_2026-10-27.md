# Telegram profile is a conversion destination, not a source of message voice

Architectural correction: 2026-10-28. Supersedes the per-draft profile
injection introduced by PR #573 (2026-10-27). The customer account was
**already connected**; neither re-login nor a new profile integration is
necessary.

## Decision

The direction of dependency is:

Product positioning + audience + community + latest target
  -> product-derived communication voice
  -> one message
  -> reader's existing Telegram profile
  -> attributable CTA route -> product
  -> measured outcome for AutoResearch.

**Do not** infer product voice from existing Telegram display name, username,
bio or avatar. A bad historical bio must not determine all future copy.
The writing path must not fetch live Telegram profile fields before generating
a draft. Brand/persona style is inferred in the same LLM call from product
positioning, target audience and community context. Voice variants remain
available to AutoResearch as a bounded experimental parameter.

## Existing conversion path checks — kept unchanged

- `app/customer_telegram_profile_setup.py` explicitly reads
  `customer_telegram_client_publish_service.profile_internal(project_id)`
  during profile setup/verification and checks that the stable profile
  CTA URL actually appears in the Telegram bio. It can detect CTA drift
  when revalidated. No such read is needed for every new comment.
- `app/conversion_path.py::ConversionPathValidator` validates
  `PROFILE_CLICK` and `REPLY_ENGAGEMENT` routes: identity exists, slot
  exists, tracked destination exists, correct profile route is configured
  and verified. Failing checks produce `SETUP_REQUIRED`.
- `app/distribution_execution_service.py` requires conversion route
  `READY` before approving an action that specifies a conversion
  mechanism. This enforcement remains separate from creative drafting.
- `app/telegram_profile_conversion_pack.py` already supports
  approved and applied profile treatments with their own comparisons.
- `app/telegram_profile_learning.py` compares attributable downstream
  metrics by profile treatment. It does not yet prove an automatic
  end-to-end AutoResearch optimization loop.

## Implementation change

- Remove `ConnectedTelegramAuthorContext` and its per-comment profile
  call from `app/action_drafting.py`; remove `actual_publisher_profile`
  from the LLM payload; delete now-unused module and tests.
- Version the creative message prompt as
  `community_message_v16_2_product_led_voice`.
- Keep one LLM call per Telegram opportunity; avoid any additional
  Telethon read on the draft path.
- Keep the already connected account, CTA verification, profile pack
  functionality, conversion-path gate, metrics and publication permissions
  unchanged.

## Not claimed

This PR does not redesign or automatically rewrite the current profile and
does not prove better conversion. The next controlled evaluation should compare
message voice and, separately, profile treatment against attributable
registrations/activations/payments, without confusing profile effects with
message effects.
