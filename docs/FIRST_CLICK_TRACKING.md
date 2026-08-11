# First-click tracking redirect

Partizan can record the top of the acquisition funnel without requiring the client product to send a manual `VISIT` event.

## Enable

Set an absolute public origin for the Partizan API:

```env
PARTIZAN_PUBLIC_BASE_URL=https://partizan.example.com
```

The value must be a plain `http://` or `https://` origin with no path, query or fragment.

When this setting is empty, Partizan preserves the existing behavior: `DistributionExperiment.tracking_url` points directly to the product destination with Partizan UTM and `ptz_*` attribution parameters.

When the setting is configured, newly prepared experiments instead expose:

```text
https://partizan.example.com/r/{referral_token}
```

## Redirect flow

```text
external channel / ad / profile
        ↓
Partizan /r/{referral_token}
        ↓
best-effort VISIT event
        ↓
302 redirect
        ↓
original destination + UTM + ptz_experiment + ptz_action + ptz_opportunity
```

The redirect target is reconstructed from the persisted `DistributionAction`, `DistributionExperiment`, `DistributionPlay` and, where applicable, the persisted `CampaignSlot` attribution route.

There is no `url=` query parameter and the endpoint never redirects to a request-supplied destination. Unknown referral tokens return `404`.

## Measurement rules

The public redirect can create only `VISIT` events.

- `DRAFT` / `APPROVED` experiments still redirect, but do not record a measurable visit.
- `RUNNING` / `FINISHED` experiments record a best-effort `VISIT`.
- analytics failure never blocks the redirect to the product.
- every click remains a visit event; a first-party `ptz_vid` cookie provides a stable non-PII visitor actor ID across repeated clicks.

`SIGNUP`, `ACTIVATED`, and `PAID` remain server-to-server product events and require the product-bound `X-Partizan-Event-Key` described in `CONVERSION_EVENTS.md`.

## Privacy

The first-click tracker does not persist:

- IP address;
- raw user-agent;
- cookies from the destination product;
- credentials or provider tokens.

The `ptz_vid` value is a random Partizan-generated identifier stored as an HttpOnly, SameSite=Lax cookie. It is marked Secure when the actual request reaches Partizan over HTTPS.

## Reverse proxy

In production, terminate TLS at a proxy/load balancer that forwards the original request scheme correctly. The application uses the observed request scheme when deciding whether to set the visitor cookie as Secure.
