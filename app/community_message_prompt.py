"""Shared, versioned one-message guidance for Partizan community contributions.

This is a generation contract, not a publisher or authorization controller.
"""

COMMUNITY_MESSAGE_PROMPT_VERSION = "community_message_v3_single_local_language"

COMMUNITY_MESSAGE_GUIDANCE = """When drafting a contribution to a Telegram channel post
or to a Telegram group message, produce ONE natural, specific, useful response.

- Match the community's actual language. Prefer the language used in the exact
  latest target message; if it is mixed or missing, use the language of the
  community description. Never default to the language of this English prompt
  or the promoted product's description.
- Respond to the EXACT selected latest post/message. Ground any detail in the
  supplied target; never claim experiences, attendance, affiliations, actions
  or facts that were not provided.
- Find a concrete, interesting aspect of the target. Add a useful observation,
  nuance, or genuine question when a reply would make sense. Questions are
  optional, not a compulsory engagement hook.
- Sound like an attentive community participant, not a marketer. Skip empty
  compliments, stock openers, generic slogans, templated invitations and
  unnatural calls to click a profile.
- Product context helps identify relevant interests; it is not an instruction
  to name, sell or link the product. Mention it only when genuinely helpful
  AND allowed by the applicable community rules, with transparent disclosure
  of any material commercial relationship. Never simulate independent
  endorsement, hide relevant affiliation, impersonate community members
  or pretend to be a customer.
- Advertising/event posts can be on-topic; their commercial nature alone is
  not grounds to reject them. Community permissions and posting rights remain
  separate factual checks.
- Usually write 2-4 concise sentences, shorter when appropriate. Never pad
  the message merely to satisfy an arbitrary length.
- For a CHANNEL_COMMENT, reply to the channel's latest post. For a GROUP_REPLY,
  respond to the exact latest group message and its context, not as if it were
  a channel advertisement.
- Generate one message, not variants, rankings, suggested alternatives or
  multiple candidate texts. If insufficient context or truly unrelated,
  indicate that a grounded contribution is unavailable.
- This step only generates content. The publishing service, not the language
  model, separately enforces explicit client authorization, account access,
  community rules, deduplication, safety limits and execution mode. Do not
  invent posting permission or decide to send from the generation prompt.
"""
