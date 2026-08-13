# OpenAI creative provider

Partizan can generate action-level paid creatives with OpenAI for both supported paid channels.

## Enable

Set all of the following in the production environment:

- `CREATIVE_PROVIDER=openai`
- `OPENAI_API_KEY=<secret>`
- `PARTIZAN_PUBLIC_BASE_URL=https://<public Partizan origin>`

Optional image settings:

- `CREATIVE_IMAGE_MODEL=gpt-image-2`
- `CREATIVE_IMAGE_QUALITY=medium` (`low`, `medium`, or `high`)

Optional TikTok source-video settings:

- `CREATIVE_VIDEO_MODEL=sora-2` (`sora-2` or `sora-2-pro`)
- `CREATIVE_VIDEO_SECONDS=8` (`4`, `8`, or `12`)
- `CREATIVE_VIDEO_SIZE=720x1280` (`720x1280` or `1024x1792`)

The default remains `CREATIVE_PROVIDER=unavailable`, so OpenAI creative generation never creates API cost unless explicitly enabled.

## Meta lifecycle

1. The autonomous paid worker prepares the DistributionAction and PaidCampaignSpec.
2. CreativeGenerationService builds/reuses the deterministic CreativeBrief.
3. OpenAI generates a 1024x1536 PNG for Instagram/Meta.
4. Image bytes are stored restart-safely in RuntimeStateStore.
5. A READY CreativeAsset receives a public URL at `/v1/public/creative-blobs/{id}`.
6. The creative-aware Meta adapter uses that action-level URL while creating the existing PAUSED campaign/ad-set/creative/ad stack.
7. Exact-budget paid activation remains a separate Growth Mandate authorization step.

## TikTok lifecycle

TikTok uses two deliberately separate boundaries:

1. Sora source generation creates and polls a vertical video job.
2. Partizan downloads the completed MP4 and stores it restart-safely in the dedicated video blob store.
3. The generated source receives a stable HTTPS URL at `/v1/public/creative-video-blobs/{id}` but **no TikTok provider ID is invented**.
4. The existing ProviderAwareCreativeGenerationService sees the URL-addressable TikTok VIDEO source and invokes the restart-safe TikTok provider finalizer.
5. The finalizer uploads the source URL into the TikTok Asset Library and requires a real provider-returned `video_id`.
6. Only the promoted provider asset with that real `video_id` satisfies TikTok CreativeReadiness.
7. The existing creative-aware TikTok adapter then creates campaign/ad group/ad with delivery DISABLED.
8. Exact-budget paid activation remains a separate Growth Mandate authorization step.

A Sora MP4 by itself is source-ready but not provider-ready. Autonomous paid staging still requires the separate TikTok finalization step to succeed.

## Safety and failure behavior

- Missing OpenAI key or public base URL returns `UNAVAILABLE` before source generation.
- Failed or incomplete Sora jobs never create a READY source asset.
- Sora video bytes use a dedicated restart-safe MP4 store with a 64 MiB limit and SHA-256 integrity verification.
- TikTok upload remains handled by the existing restart/reconciliation-safe finalizer; ambiguous provider mutations are never retried blindly.
- API keys and provider credentials are never copied into CreativeAsset provenance or public responses.
- Public creative endpoints intentionally have no operator authentication because ad providers need to fetch the generated media. Blob IDs are unguessable UUIDs; generated paid creatives should be treated as public campaign assets.
- Creative generation/finalization cannot activate Meta/TikTok delivery and cannot authorize spend. Those remain downstream Growth Mandate operations.
