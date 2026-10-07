# FemDom marketing memory and Telegram review v2

Status: review-only production preparation

This document records the customer-confirmed feedback captured after the first FemDom Telegram review bundle and explains how it is used without publishing anything.

## Customer-confirmed memory

The reviewed seed is stored in:

`ops/femdom/marketing_memory_2026-10-07.json`

It records only preferences the customer explicitly stated:

- do not keep the male-presenting Telegram profile identity for this campaign;
- use either a native link alone or a stronger concise bio CTA instead of the weak `FemDom ↓` label;
- include a reviewed story in the first experiment;
- avoid dry, lecture-like and safety-checklist comments;
- do not reuse or closely paraphrase the previously rejected safety/documentation comment;
- use a sensual editorial, light-erotic but non-explicit avatar direction appropriate to the target community.

These entries are written as `CUSTOMER_CONFIRMED` memory with confidence `1.0`.

The seed is applied by the generic `app.project_marketing_memory_seed` CLI. A durable seed marker makes `customer-review-2026-10-07-v1` idempotent, so later deployments cannot repeatedly re-apply the same historical feedback.

## Proposal v2

The next exact proposal is stored separately from confirmed memory:

`ops/femdom/telegram_review_v2.json`

This distinction is important. The proposal contains concrete choices that are still awaiting human review, including:

- display name `Nika`;
- bio CTA line `То, что не пишу в комментариях ↓`;
- a first-experiment story proposal;
- a dark premium sensual-editorial avatar/story visual direction;
- `PROFILE_CLICK / non_obvious_lens` as the recommended comment variant.

These values are **not** automatically promoted to customer-confirmed memory merely because they exist in the proposal file.

## Production order

After an eligible production deployment, the FemDom preview workflow executes in this order:

1. verify the deployed release is still current `main`;
2. apply the reviewed, idempotent FemDom memory seed;
3. prepare new comment variants using the current Project Marketing Memory;
4. keep every generated action in `PREPARED` state;
5. run workspace diagnostics.

The follow-up review-bundle workflow then reads `telegram_review_v2.json` and prepares the exact profile, story and comment proposal for human review.

## Memory-aware preview invalidation

Telegram preview schema v5 stores a SHA-256 fingerprint of the rendered Telegram/COMMENT marketing memory plus the exact memory entry ids.

A cached preview is reused only when its memory fingerprint still matches the current project memory. If the memory changes, the next preview run generates a new set of comment drafts instead of silently returning stale copy.

The review bundle verifies the same fingerprint before it can be built.

## Publication boundary

This flow performs **no** Telegram publication and no profile mutation.

It may:

- write reviewed project memory to Partizan's database;
- generate draft comments;
- read the current Telegram profile;
- prepare a proposed profile/story/comment bundle.

It may not:

- change the Telegram display name, bio or avatar;
- publish a story;
- publish a comment.

Those external actions remain behind the explicit user approval gate. For the current FemDom campaign, the required phrase remains `публикуй` after the user has seen the exact final material.
