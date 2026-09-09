# Reddit client-owned publishing

Phase 5 adds a customer-owned Reddit execution path without turning Reddit research into a publishing credential or enabling autonomous posting.

## Safety and provider boundary

Reddit publishing is fail-closed by default. Production execution is unavailable unless all of the following are true:

```text
REDDIT_CLIENT_PUBLISH_PROVIDER=oauth
REDDIT_CLIENT_PUBLISH_PUBLIC_READY=true
REDDIT_COMMERCIAL_ACCESS_VERIFIED=true
REDDIT_CLIENT_PUBLISH_CLIENT_ID=...
REDDIT_CLIENT_PUBLISH_CLIENT_SECRET=...
REDDIT_CLIENT_PUBLISH_USER_AGENT=...
PROVIDER_SECRET_ENCRYPTION_KEY=...
PARTIZAN_PUBLIC_BASE_URL=https://...
```

`REDDIT_COMMERCIAL_ACCESS_VERIFIED=true` is an operational attestation. It must not be enabled merely because the code exists or an OAuth application can be created. Partizan must first have the Reddit commercial/API permission required for the production use case and retain evidence of that approval outside this repository.

The code currently models an OAuth/Data API transport boundary because it gives the application an explicit, testable capability contract. The production mechanism must use a Reddit-approved execution mechanism for the actual Partizan use case. If Reddit requires Devvit/User Actions or another approved integration path, the transport implementation must be replaced or adapted before public readiness is enabled.

## Customer OAuth and secret isolation

The customer connection requests only the scopes needed by this slice:

- `identity` — verify the connected Reddit identity;
- `read` — observe the exact published object;
- `submit` — create the approved post/comment/reply.

OAuth state is short-lived and stored server-side. Access and refresh tokens are bundled and encrypted at rest using the existing `ProviderSecretStore` / Fernet key. Customer tokens, refresh tokens and the Reddit OAuth client secret are never returned by customer-facing status, receipt or observation models.

A Reddit research result cannot create a Reddit publishing connection, cannot populate customer OAuth secrets and cannot make `PUBLISH` ready.

## Explicit user action

Partizan does not treat an approved `DistributionAction` as permission to execute it automatically on Reddit.

Every customer-facing publish request must include:

```json
{
  "confirm_publish": true
}
```

Without that explicit confirmation, the request fails closed. There is no Reddit background scheduler, autonomous as-user execution path or endpoint that bypasses this gate.

## Approval and current-policy gates

The external action must already be `APPROVED`. At publish time the service re-reads the current `CommunityPolicy`; approval does not freeze or bypass later policy changes.

The current policy must still be fresh and must permit the requested action type. The publisher also re-checks:

- commercial participation;
- comments/replies vs standalone posts;
- direct product links;
- product mentions;
- required disclosure;
- AI-content constraints;
- special promotion constraints.

A required disclosure is represented by explicit approved action metadata. The publisher never silently edits the approved content to add one at execution time. If policy changes after approval and the approved action no longer satisfies it, publishing is blocked and the action must be edited/re-approved through the normal workflow.

`AI_CONTENT_PROHIBITED` blocks AI drafting. `AI_CONTENT_DISCLOSURE_REQUIRED` requires an explicit disclosure flag on the approved action. Special promotion constraints require explicit confirmation metadata rather than inferred compliance.

## Targets and titles

For `COMMENT` and `REPLY`, the target must:

- be a public `reddit.com` thread/comment URL;
- belong to the same subreddit as the selected persistent subreddit opportunity;
- reference a fresh thread still present in the opportunity's bounded research enrichment;
- provide the expected Reddit post/comment identifier shape.

For `STANDALONE_POST`, the approved action must carry a first-class title. Title and body are part of the normal prepare/edit/approve contract; production code does not mutate private execution-service state to manufacture a title.

## Guardrails

Client-owned Reddit publishing has conservative local guards:

- duplicate normalized target+content blocked for 24 hours;
- minimum two-minute interval between confirmed publishes for a project;
- maximum five confirmed publishes per project per 24 hours;
- maximum body length of 10,000 characters;
- maximum title length of 300 characters;
- one process-local `asyncio.Lock` around the publish attempt.

The transport intentionally exposes no vote/upvote/downvote, unsolicited DM, subscribe/join, moderation or ban-evasion methods.

For horizontally scaled multi-worker production, duplicate/cooldown/day-limit reservations should be moved to an atomic datastore-level reservation before this path is used at meaningful concurrency.

## Receipts and outcomes

A publish attempt stores an `IN_PROGRESS` receipt before the provider call. A confirmed provider result stores a safe `EXECUTED` receipt containing the Reddit fullname/permalink and marks the existing `DistributionAction` executed through the shared execution service.

Provider failures are sanitized. Raw provider exception text and OAuth tokens are not persisted in receipts.

Post-publish observation performs an exact-object read only. It records:

- `PRESENT`, `REMOVED`, `INACCESSIBLE` or `UNKNOWN`;
- score when available;
- reply/comment count when available;
- bounded restriction/removal signals;
- observation timestamp.

The latest safe observation summary is also attached to `DistributionAction.operational_metadata.external_observations.reddit`, so downstream learning can consume the outcome without accessing OAuth secrets.

A `REMOVED` observation only means Reddit no longer returns the object as present (or marks it removed/deleted). It does not infer moderator intent, spam classification or causality.

## Attribution

The publisher preserves the shared `DistributionAction` / `DistributionExperiment` attribution identifiers and existing tracking URL. The receipt records whether the approved body already contained the tracking URL. Publishing never injects a tracking URL after approval because doing so would change customer-approved content and could violate the current community policy.

## Production acceptance

Code and tests are not sufficient to close Phase 5. Before production Reddit client-owned publishing is called ready, Partizan still needs all of the following:

1. documented Reddit commercial/API authorization for this production use case;
2. a production-approved Reddit application/integration mechanism;
3. deployment secrets installed without exposing them in chat, logs or source control;
4. one real customer-authorized production connection;
5. one explicit, policy-permitted, approved and manually confirmed real publish;
6. a real post-publish observation showing the remote result and safe outcome capture;
7. verification that the production deployment remains unable to perform automated as-user actions, voting, unsolicited messaging or ban evasion.

Until those checks are complete, production must keep Reddit client-owned publish readiness disabled.
