# TikTok Ads paid provider

Status: implementation slice in progress.

This provider uses TikTok API for Business / Marketing API v1.3 and plugs into Partizan's existing paid lifecycle:

`PaidCampaignSpec(CREATE_PAUSED) -> provider objects staged/disabled -> explicit spend authorization -> enable -> provider spend/status sync -> hard pause at approved budget cap`.

## Provider boundary

Partizan does not assume that every TikTok advertiser account is authorized for every API feature. A product must have an explicit TikTok paid-provider connection with an advertiser ID and an access-token environment-variable reference. The secret value is never persisted.

The first implementation uses Manual Campaign APIs and existing ad creative assets supplied in the connection. It does not generate or upload a TikTok video as part of paid execution.

## MVP safeguards

- create provider objects disabled/paused first;
- no spend from staging;
- explicit one-time exact-budget activation authorization is required before enabling delivery;
- provider cumulative spend is synced as deltas into Distribution Analytics;
- approved `PaidCampaignSpec.budget_cap` is a hard provider-level pause guardrail;
- no Growth Manager path can autonomously increase budget or reactivate a paused campaign;
- ambiguous provider writes require reconciliation instead of blind retry;
- no secret values in API responses, specs, receipts or control snapshots.

## Reddit Ads note

Reddit remains a first-class paid tactic in the product model, but a campaign-management execution adapter is intentionally not implemented until Partizan has a confirmed supported Reddit Ads API access path. Reddit CAPI/Pixel measurement can be integrated independently.
