# Telegram: one newest post for each qualified audience

The project has two distinct stages of Telegram research. An unknown or
unverified public community can still require a short native message sample
to assess whether its audience is relevant. **A community with confirmed
audience fit should fetch only its single most recent Telegram message.**

Since #522 the connector supports `inspect_latest_known` with a one-message
native fetch. The first implementation selected it only when *all* communities
in the three-peer refresh batch had previously been confirmed. Mixed batches
therefore continued reading four posts even from confirmed communities.

This change partitions **each existing rotating batch** by audience fit:

- confirmed audience → `inspect_latest_known` (one native message per group);
- new/unverified audience → `inspect_known` (short onboarding sample);
- no re-search of community identities, no global Telegram search or paid
  web fallback introduced by this change;
- native `TelegramFloodWaitError` stops the interrupted batch, persists the
  shared Telegram cooldown and retains the rotation cursor;
- a transient inspection error in one subgroup does not erase successful
  results in the other subgroup;
- cursor records `last_batch_latest_handles` and
  `last_batch_context_handles` for straightforward diagnostics.

The latest message is selected by highest Telegram message ID. If
it has a missing URL or its reply/comment surface is unavailable, the
existing target-selection policy takes effect; it never quietly falls
back to older posts. No search-result fit alone proves account-specific
comment permission. Existing action drafting uses the selected native
target context, while preflight and the FemDom DEBUG_REVIEW publishing
approval remain separate requirements.

Read-only synthetic tests use a fake transport to assert that mixed
groups cause limit=1 and limit=4 calls separately, with no global search,
and that failures/FloodWait are handled safely. No Telegram login, post
submission, profile changes or paid queries are performed in tests.
