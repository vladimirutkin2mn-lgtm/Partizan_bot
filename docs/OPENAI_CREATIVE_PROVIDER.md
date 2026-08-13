# OpenAI creative provider

Partizan can generate action-level paid creatives with OpenAI for both supported paid channels:

- Instagram/Meta: generated image -> Partizan public creative URL -> Meta PAUSED staging.
- TikTok: Sora vertical MP4 -> TikTok Creative Library upload -> provider-confirmed `video_id` -> TikTok DISABLED staging.

## Enable

Set all of the following in the production environment:

- `CREATIVE_PROVIDER=openai`
- `OPENAI_API_KEY=<secret>`
- `PARTIZAN_PUBLIC_BASE_URL=https://<public Partizan origin>`

For TikTok, the product must also have an ACTIVE TikTok paid-provider connection and the configured `access_token_env` secret must be present in the worker environment.

Optional image settings:

- `CREATIVE_IMAGE_MODEL=gpt-image-2`
- `CREATIVE_IMAGE_QUALITY=medium` (`low`, `medium`, or `high`)

Optional video settings:

- `CREATIVE_VIDEO_MODEL=sora-2` (`sora-2` or `sora-2-pro`)
- `CREATIVE_VIDEO_SECONDS=8` (`4`, `8`, or `12`)
- `CREATIVE_VIDEO_SIZE=720x1280` (`720x1280` or `1024x1792`)

The default remains `CREATIVE_PROVIDER=unavailable`, so generation never creates OpenAI API cost unless explicitly enabled.

## Meta lifecycle

1. The autonomous paid worker prepares the DistributionAction and PaidCampaignSpec.
2. CreativeGenerationService builds/reuses the deterministic CreativeBrief.
3. OpenAI generates a 1024x1536 PNG.
4. Image bytes are stored restart-safely in RuntimeStateStore.
5. A READY CreativeAsset receives a public URL at `/v1/public/creative-blobs/{id}`.
6. The creative-aware Meta adapter uses that action-level URL while creating the existing PAUSED campaign/ad-set/creative/ad stack.
7. Exact-budget paid activation remains a separate Growth Mandate authorization step.

## TikTok lifecycle

1. The same strict creative preflight builds/reuses a TikTok VIDEO CreativeBrief.
2. Sora creates and completes a vertical video job.
3. Partizan downloads the rendered MP4 and stores it restart-safely for preview/audit.
4. Partizan uploads the exact MP4 bytes to TikTok Creative Library through `/file/video/ad/upload/` with the advertiser ID and file signature.
5. Partizan queries `/file/video/ad/info/` and requires TikTok to return the exact uploaded `video_id` as ad-usable.
6. Only after that confirmation does CreativeGenerationService register a READY asset with `provider_asset_id=<video_id>`.
7. The existing creative-aware TikTok adapter substitutes that action-level `video_id` while creating the campaign/ad group/ad in DISABLE state.
8. Exact-budget paid activation remains a separate Growth Mandate authorization step.

A Sora-rendered MP4 without a TikTok-confirmed `video_id` is never considered provider-ready.

## Safety and failure behavior

- Missing OpenAI key, public base URL, TikTok connection, or TikTok secret returns `UNAVAILABLE` before paid-provider staging.
- Failed Sora jobs/downloads become FAILED creative attempts, not READY assets.
- Ambiguous or rejected TikTok upload/info responses become FAILED creative attempts and never expose a `provider_asset_id` to staging.
- Generated image/video blobs are bounded to 64 MiB and limited to PNG/JPEG/WebP/MP4 MIME types.
- Stored blobs include SHA-256 integrity metadata and are verified on read.
- API keys and provider credentials are never copied into CreativeAsset provenance or public responses.
- The public creative endpoint has no operator authentication because ad providers may need to fetch preview/creative media. Blob IDs are unguessable UUIDs; generated paid creatives should be treated as public campaign assets.
- Creative generation/upload cannot activate Meta/TikTok delivery and cannot authorize spend. Those remain downstream Growth Mandate operations.
