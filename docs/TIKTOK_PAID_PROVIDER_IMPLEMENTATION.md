# TikTok paid provider implementation checklist

- Manual Campaign API integration via TikTok API for Business v1.3.
- Product-scoped connection: advertiser ID, token env reference, location IDs, existing video asset/identity, currency/budget settings.
- Stage campaign/ad group/ad in non-delivering state.
- Reuse Partizan exact-budget, one-time paid activation authorization semantics.
- Enable only after explicit authorization.
- Sync campaign status and cumulative spend; ingest deltas into Distribution Analytics.
- Hard pause at approved budget cap.
- Never persist access-token values.
- Reddit Ads campaign management remains unavailable until a confirmed supported Ads API management path is available.
