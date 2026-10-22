# Telegram audience fit: low-cost semantic assessment

## Why
The 2026-10-21 FemDom production snapshot retained nine native Telegram messages,
but only two communities passed a lexical English/Russian overlap check and neither
of those allowed comments on the newest post. A lexical failure is not evidence
that no relevant audience exists.

## Behavior
- Product-scoped `TelegramAudienceSemanticFit` uses **gpt-6-luna** via the
  existing OpenAI credential and usage logger, at most twelve newly evaluated
  communities per product per UTC day. Existing duplicate/cached calls do not
  consume the daily cap. No web search, embeddings or Telegram history scan.
- Cache key: product ID + Telegram handle; hash of product name, description,
  product problem, known audience, ICP title and pain, community title and about;
  prompt version; TTL 60 days. Newest post content is evidence but **not** part
  of the fingerprint, so each daily post refresh does not spend new tokens.
- User/source text is treated as untrusted data. The model returns a strict
  Pydantic verdict: DIRECT, ADJACENT, UNRELATED or INSUFFICIENT, confidence,
  rationale and at most four evidence snippets.
- DIRECT is accepted only at confidence >= 0.85 AND when one evidence snippet
  appears in the supplied native/product data; otherwise it stays unconfirmed.
  ADJACENT remains research-only; no commercial action rights inferred.
- LLM call exceptions, missing key or exhausted daily reservations **fail closed**.
  Calls are opt-in via `TELEGRAM_AUDIENCE_SEMANTIC_ENABLED=true` for routine
  discovery. The one-time FemDom pilot can evaluate stored registry snapshots
  without enabling routine paid inference for other products.

## Why this model
Published list price on 2026-10-22: gpt-6-luna input $0.05 / 1M tokens,
output $0.25 / 1M tokens. Example nine evaluations at 1,000 input and 200
output tokens cost about $0.00090 (before any discounts or other charges);
the actual API usage appears in an artifact and OpenAI usage logs. **Price
does not prove model classification quality.** Do not enable large-scale
classification until a human reviews the nine pilot outcomes and ambiguity.

## Promotional context
A commercial / promotional latest post is no longer disqualified solely
because it advertises an event or has an outbound link. The algorithm still
requires a topical target and the **newest** post, and records the
`PROMOTIONAL_POST` signal into live opportunity metadata. Existing community
restrictions, per-account permission preflight, spam controls and explicit
human confirmation of any send still apply.

## Learning, not instant reinforcement
Record promotional vs nonpromotional context for later AutoResearch evaluation:
link each *actually sent* comment to its source-post category, publisher
identity, attribution route, time, product, audience and campaign/experiment.
Compare conversion outcomes across representative samples rather than
overreacting to one post; exclude placements forbidden by community policy
regardless of apparent conversion. The post-type field is only the first
measurement hook; no self-training or publishing permission is introduced
by this change.
