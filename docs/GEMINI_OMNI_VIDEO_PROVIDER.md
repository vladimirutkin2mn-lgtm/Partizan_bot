# Gemini Omni TikTok video creative provider

Partizan can optionally generate a short vertical TikTok source video with Gemini Omni Flash and then use the existing TikTok provider-finalization layer to upload that source into the TikTok Asset Library.

## Disabled by default

Video generation has external API cost and is fail-closed by default.

Enable it explicitly:

```env
CREATIVE_VIDEO_PROVIDER=gemini_omni
GEMINI_API_KEY=<deployment secret>
CREATIVE_VIDEO_MODEL=gemini-omni-flash-preview
PARTIZAN_PUBLIC_BASE_URL=https://<public-partizan-origin>
```

The existing TikTok paid-provider connection and its token secret must also be configured and ACTIVE before the autonomous flow attempts video generation. This prevents spending money on a source video that cannot be finalized into a real TikTok provider asset.

## Lifecycle

The autonomous paid flow remains bounded:

1. build a confirmed CreativeBrief for the TikTok paid action;
2. ask Gemini Omni for a vertical `9:16` MP4 source creative;
3. persist the MP4 in the existing restart-safe Partizan creative blob store;
4. expose it through the public creative-blob URL;
5. upload that URL to the TikTok Asset Library through the provider-finalization layer;
6. accept readiness only after TikTok returns a real `video_id`;
7. only then proceed to the existing autonomous approval and DISABLED provider staging;
8. paid activation still requires the existing Growth Mandate exact-budget authorization.

Video generation and TikTok video upload do not themselves activate ad delivery.

## Current MVP constraints

- TikTok VIDEO only; Meta IMAGE continues to use the separate OpenAI creative provider.
- Gemini Omni model is configurable but defaults to `gemini-omni-flash-preview`.
- Output request is portrait `9:16`.
- Partizan accepts only `video/mp4` source output.
- The current creative blob store caps each source at 12 MiB.
- Invalid MP4 signatures, invalid base64, missing video output, oversized output, missing API keys, missing public origin, missing TikTok connection/token, or missing TikTok `video_id` all fail closed.
- The Gemini API key is sent only as a request header and is never persisted in CreativeAsset provenance.
- TikTok upload has its own restart-safe ambiguity guard; uncertain provider results are not retried blindly.

## Cost / idempotency boundary

`CreativeGenerationService` reuses READY generated assets by deterministic CreativeBrief fingerprint. Repeated sweeps for the same unchanged brief therefore reuse the existing source/provider asset instead of intentionally requesting another generation.

A future iteration can add URI delivery for larger generated videos or additional video-generation providers without changing the TikTok Asset Library finalization contract.
