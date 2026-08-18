# Meta OAuth production request

This document is the handoff for configuring the Meta application used by Partizan self-service customer onboarding.

Do not guess values from screenshots or old environments. The application and production host must match the contract below.

## Exact Partizan OAuth contract

Production origin:

```text
https://partizanlabs.com
```

Valid OAuth redirect URI — use this value character-for-character:

```text
https://partizanlabs.com/v1/customer-meta/oauth/callback
```

Partizan requests these Meta permissions during customer OAuth:

```text
ads_management
ads_read
```

The Graph API version must be pinned explicitly in `vNN.N` form, for example:

```text
v25.0
```

Do not use aliases such as `latest`.

## Values required from the Meta app

The production operator needs exactly these three values from the configured Meta app:

```text
META_OAUTH_APP_ID=<numeric app id>
META_OAUTH_APP_SECRET=<secret>
META_OAUTH_API_VERSION=<vNN.N>
```

`META_OAUTH_APP_ID` must be numeric. `META_OAUTH_API_VERSION` must match `vNN.N`.

The app secret is confidential. Do not send it in chat, tickets, pull requests, GitHub issues, screenshots, shell command arguments, or documentation. Install it directly into the production secret/environment process using protected stdin or another non-logging secret-input mechanism.

## Meta-side setup checklist

Before calling the Partizan production configuration complete:

1. Create or select the Meta Developer app owned by the Partizan business/operator.
2. Enable the Meta product/capability required for Facebook Login and Marketing API access.
3. Add the exact redirect URI shown above to the app's valid OAuth redirect URIs.
4. Ensure the app can request `ads_management` and `ads_read`.
5. Pin the Graph API version that Partizan will use and return it as `META_OAUTH_API_VERSION`.
6. For owner dogfood, confirm the operator can connect the intended test ad account and Facebook Page.
7. Before onboarding unrelated customer accounts, make the app available to non-role users and complete any Meta App Review, access-level, or business-verification requirements shown by the current Meta developer dashboard for the requested advertising permissions.

Do not assume public customer access is ready merely because OAuth works for app administrators or developers.

## Partizan production configuration

These existing production values are also required for the public customer flow:

```text
PROVIDER_SECRET_ENCRYPTION_KEY=<Fernet-compatible key>
STRIPE_AUTOPILOT_PRICE_ID=<Stripe recurring Price ID>
```

The Meta values must be installed in the host-owned `.env.prod` alongside them:

```text
META_OAUTH_APP_ID=...
META_OAUTH_APP_SECRET=...
META_OAUTH_API_VERSION=...
```

Keep `.env.prod` mode `600`. Never commit it.

After installing the values, run:

```bash
PARTIZAN_REQUIRE_PUBLIC_URL=true bash tools/preflight_prod_host.sh .env.prod
```

The preflight reports all configuration errors it can validate in one run. Fix every reported error before deploying.

## Expected Partizan behavior

The customer clicks **Connect Meta** inside Autopilot. Partizan redirects to Meta OAuth with the two advertising scopes above. After callback, Partizan exchanges and extends the access token, encrypts it at rest, lists available ad accounts, and lists Pages that can be promoted from the selected account.

The customer then selects the exact ad account and Page. Partizan stores the provider connection using an opaque secret reference; the browser does not receive the stored access token.

Meta remains a customer-owned advertising account. Partizan acts through scoped access.

## Growth Balance / payment rail is separate

Meta OAuth configuration does not enable the Partizan-funded payment rail by itself.

Keep:

```text
GROWTH_BALANCE_SETTLEMENT_PROVIDER=unavailable
```

until Stripe Issuing is fully configured according to `docs/GROWTH_BALANCE_ISSUING.md`.

When Stripe Issuing is enabled later, the Partizan project card must be bound to the exact Meta ad account before live paid Autopilot can activate.

## Acceptance criteria

Meta setup is ready for the first owner dogfood only when all of the following are true:

- production preflight passes;
- the production deploy succeeds;
- `https://partizanlabs.com/version` reports the release SHA that the deployment intended to publish;
- **Connect Meta** completes OAuth without redirect mismatch;
- the intended test ad account is returned;
- the intended Facebook Page is returned and can be selected;
- no Meta access token or app secret appears in browser responses, application logs, GitHub, or chat.

Public onboarding of third-party customer accounts has an additional gate: the Meta app must have whatever live/review/access status the current Meta platform requires for those users and permissions.
