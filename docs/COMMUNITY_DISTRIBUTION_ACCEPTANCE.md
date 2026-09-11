# Community Distribution production acceptance evidence

This verifier is the final read-only evidence layer for phases 2–7 of the Community Distribution programme.

It exists because the remaining acceptance gates are real-production gates, not additional publisher implementation. The verifier does not create evidence. It only summarizes production configuration and durable evidence that Partizan already persisted while real customer/research/execution flows ran.

## Safety boundary

`app/community_distribution_acceptance.py` and `tools/report_community_distribution_acceptance.py` are read-only.

They do not:

- call Telegram, Reddit, OpenAI search, or any other provider;
- publish, observe, join, invite, vote, DM, retry, or mutate an external action;
- enable channel readiness or managed inventory;
- read provider secret values;
- expose provider secret references, customer token hashes, account usernames, Telegram target usernames, managed publisher IDs, or partner references;
- close GitHub issues or mark acceptance automatically.

Samples are built from explicit whitelists and contain only internal action/experiment/opportunity IDs, public execution/research URLs, timestamps, state labels, and coarse cost-presence flags.

## Production eligibility

A report can set `production_verified=true` only when all of these are true:

- `APP_ENV=production`;
- `RUNTIME_STORAGE=database`;
- the active `RuntimeStateStore` is non-ephemeral;
- `PARTIZAN_RELEASE_SHA` contains a concrete release.

This prevents local/in-memory synthetic fixtures from being presented as production acceptance evidence even when they contain records that resemble production receipts.

`evidence_complete` is intentionally separate from `production_verified`. Tests may prove that the detector understands an evidence chain, but a MemoryRuntimeStateStore can never turn that into production verification.

## Phase checks

### Phase 2 / #250 — Telegram research

The verifier recognizes persisted native Telegram research only when an opportunity has:

- platform `TELEGRAM`;
- `native_research_status=VERIFIED`;
- a Telegram entity ID;
- a native source-check timestamp;
- a public `t.me` / `telegram.me` URL.

Current Telethon research readiness is reported separately. Historical valid evidence remains evidence even if the provider is later disabled.

### Phase 3 / #251 — Telegram client-owned publish

The required production evidence is a same-action chain:

1. confirmed `EXECUTED` Telegram client-owned receipt;
2. persisted post-publish observation for that exact action.

Current publish readiness and an active scoped customer connection are shown as operational readiness, not substituted for execution evidence.

### Phase 4 / #252 — Reddit research + CommunityPolicy

The required research evidence is a concrete Reddit opportunity with a policy whose:

- source is `indexed_public_research`;
- research status is `VERIFIED`;
- policy evidence is non-empty;
- last-check timestamp is present.

A MANUAL policy does not satisfy real Reddit research verification. Fresh thread-target provenance is reported as an optional additional check because it may not be available for every valid research result.

### Phase 5 / #253 — Reddit client-owned publish

Required evidence is intentionally strict:

- `REDDIT_COMMERCIAL_ACCESS_VERIFIED=true`;
- an active scoped Reddit OAuth connection with `identity`, `read`, and `submit` scopes;
- confirmed Reddit publish receipt;
- outcome observation for the same action;
- downstream `VISIT`, `SIGNUP`, `ACTIVATED`, or `PAID` attribution for its experiment;
- at least one action for which publish + observation + downstream attribution all belong to the same chain.

Current OAuth/publish readiness is reported separately. The verifier never interprets a configured credential as proof of Reddit commercial approval.

### Phase 6 / #254 — Partizan Managed Distribution

The required evidence is:

- a persisted `FULFILLED` managed assignment;
- measurable analytics/platform outcome for the fulfilled action.

Current managed public readiness and eligible inventory are reported separately. Internal managed publisher identity and partner references never appear in the report.

### Phase 7 / #255 — economics + learning

The final Phase 7 chain must belong to one executed experiment and contain:

- positive `OBSERVED` cost evidence, or a fulfilled managed assignment with a real recorded cost;
- persisted funnel/community outcome evidence;
- persisted `STOP`, `CONTINUE`, `MODIFY`, or `SCALE` Growth Manager decision.

`ESTIMATE` and `SYNTHETIC` spend do not count as real cost evidence.

## Running inside production

After this change is deployed, use the manual GitHub Actions workflow **Community distribution acceptance**.

The workflow accepts an optional customer project UUID. When supplied, every phase is scoped to that project's product and its actions/experiments. Evidence from another customer/product cannot satisfy the report.

The workflow:

1. checks the existing production SSH configuration;
2. canonicalizes the optional UUID before using it in the remote command;
3. connects to the production host using the pinned host key;
4. runs the report inside the already-running API container by piping the reporting script to `python -`.

It does not run `docker compose up`, restart/reload a proxy, mutate the database, or invoke a publish endpoint.

The public TLS incident #266 does not prevent this SSH/internal-container report from running. The report therefore lets the team inspect remaining Community Distribution evidence while the shared Caddy certificate/host-route problem is handled separately.

## Operator review

A `VERIFIED` phase means the durable production evidence detector found all required evidence for that phase. It is not an instruction to auto-close the corresponding issue.

Before closing #250–#255, an operator should review the report, the real customer experiment, applicable platform/commercial permissions, and the relevant issue acceptance criteria. The master programme #256 remains the source of truth for final programme completion.
