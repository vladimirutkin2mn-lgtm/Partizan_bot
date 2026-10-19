# Telegram: one newest post for a qualified audience

The customer-level growth objective identifies relevant **communities** first. Once
the community audience is verified, Partizan does **not** search tens of
messages for a perfectly matching post.

## Native read budget

- **Confirmed community**: read **one latest native message** per peer.
  `TelegramResearchConnector.inspect_latest_known` reuses the same bounded
  public-peer transport and Telegram FloodWait discipline, with
  `recent_message_limit=1`.
- **New/unverified community**: retain the current short research sample
  (normally four messages) while evaluating whether the community itself
  reaches the correct audience.
- **Mixed registered refresh batch**: retain the normal short evidence
  sample for safety; only entirely confirmed batches take the one-message path.
- The registered-community cursor remains 3 peers per pass. This PR does not
  increase the Telegram request budget or use a new global community search.

## Content preparation

After confirming community audience fit, `select_telegram_participation_target`
already chooses the **newest** message by Telegram message ID, not an older
post with more convenient keywords. `ActionTargetSelector` also verifies
that the selected target is the exact newest native post; it cannot silently
fall back to an old enriched search hit.

New composer guidance uses the latest post's own text. A comment should
naturally respond to that post, and may reference the product only if
there is a genuinely useful connection. Do not fabricate a sales bridge or
advertise a product under an unrelated post. If the newest post has no
readable text, drafting fails closed rather than preparing a generic reply.

**Surface availability is not publisher authorization.** A linked discussion
or group reply capability still requires the separate actual publisher
preflight and applicable Growth Mandate/review approval. No Telegram
account change, send, join or comment is performed by this PR. FemDom
publication still requires explicit operator instruction `публикуй`.

## What this avoids

No 50-message scan or older-post fallback. No new paid searches. No promotion
just because a group is relevant, and no claim that a research snapshot proves
write permissions.
