# FemDom Telegram flywheel rollout — 2026-10-08

Project: `124cdd70-49b0-48b3-aee0-a037dda4bcd0`
Product: `52d7e59e-5ac5-4363-b829-c93e33e48272`

Initial rollout policy:

- fresh opportunity discovery may run automatically on the normal autonomous worker;
- default discovery refresh cadence is 6 hours;
- customer-facing live opportunities are updated from concrete native Telegram targets;
- every exact Telegram COMMENT target must pass a live, read-only publisher preflight using the connected FemDom account before it can become execution-ready;
- posts with no discussion, missing targets, no write access, or failed publisher verification are removed from the active acquisition queue;
- `JOIN_REQUIRED` may remain visible as a recoverable opportunity, but it is not execution-ready until membership is handled through a separately authorized operation;
- autonomous Telegram execution remains capped by the existing governance configuration;
- initial intended publish cap is 1 confirmed comment per 24 hours;
- comments only for the first slice; no DMs, replies, profile edits or story edits;
- linked-discussion joins are not delegated by this policy and remain a separate permission;
- ambiguous publish outcomes must be reconciled before any retry.

This file records rollout intent and boundaries only. It is not an execution authorization and performs no external mutation.
