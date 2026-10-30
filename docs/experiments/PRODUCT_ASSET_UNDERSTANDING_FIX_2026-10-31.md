# Product Understanding: campaign asset versus product (2026-10-31)

## Verified root cause

The read-only source audit from PR #586 reported that the existing FemDom
product was saved as an acquisition-asset/campaign summary, rather than a
description of the actual customer offering. The product link is a
**Telegram invite**, not an ordinary public product page. Its public
landing page cannot be trusted to describe what members receive.
The value proposition duplicated the campaign description; structured
audience, product type, use cases and user need were absent.

Do not infer that the offering is a conversational agent or an ordinary
public channel from the campaign label alone. No private invite token,
publisher identity or product information is added to this documentation.

## Fix in PR #589

1. Input quality identifies explicit campaign-asset labels and operational
   budget fields independently of any LLM, while avoiding changes to normal
   product descriptions.
2. The Product Intake fallback no longer manufactures a value proposition
   from the campaign brief. For such sources it prioritizes questions about
   the customer-facing value and the real audience, **not** personality.
   Confirmed clarification answers survive fallback without extra calls.
3. Public Telegram channels/groups with sparse descriptions receive a
   relevant community-value question; obvious bot handles keep the bot
   question. Telegram invite links return insufficient public evidence
   without pretending to fetch a readable public product page.
4. Initial author-voice job reservation checks source readiness first.
   The saved author character is not overwritten. A generic Telegram
   asset type cannot by itself override missing product value.
5. Test normal commercial products as controls to avoid unnecessary
   clarification or degradation of their intake.

## Existing confirmed product

This PR intentionally does **not** mutate the already confirmed
`product_intake` row or project history and does not guess missing facts.
A separate controlled product re-understanding flow must recover evidence
from an authorized, accessible product/channel source or seek the one
missing customer-value fact and real audience. The founder does not need
to configure the author voice: LLM will continue generating that after
product facts have enough support.

Only after the confirmed snapshot changes, re-run the zero-cost
source audit and create one new, inactive v3 challenger. Compare it with
the previous character at the exact **same** writer model, reasoning
settings, selected posts and conversion policy. Bizarre's no-profile-
promotion case remains a policy-negative test, not an acquisition test.

No paid model calls, Telegram joins, sending, publishing, automatic
voice promotion or current-account configuration change in this PR.
