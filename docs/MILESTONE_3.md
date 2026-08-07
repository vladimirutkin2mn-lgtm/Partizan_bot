# Milestone 3 — Channel Hunter

## Goal

Turn the top ranked ICP hypotheses into concrete online distribution opportunities with URLs,
evidence and relevance scores.

The first source classes are intentionally narrow:

1. public communities / Reddit;
2. creators;
3. newsletters and niche websites.

## Flow

```text
Top 3 ranked ICPs
        ↓
3 source adapters
        ↓
2 search queries per ICP × source class
        ↓
SearchProvider (OpenAI web search or deterministic mock)
        ↓
URL normalization + platform detection
        ↓
relevance scoring
        ↓
deduplication per ICP + evidence merge
        ↓
30–60 concrete ChannelOpportunity objects
```

## Why search is a separate provider

Channel discovery is external evidence retrieval, not normal LLM generation. It therefore has a
separate `SearchProvider` boundary.

Configured providers:

- `SEARCH_PROVIDER=mock` — deterministic local/CI behavior, no network;
- `SEARCH_PROVIDER=openai` — Responses API with the `web_search` tool and URL citation extraction.

`SEARCH_MODEL` defaults to `gpt-5.6-terra` to keep discovery separate from the main reasoning model.
The same `OPENAI_API_KEY` is reused.

The production provider creates opportunities only from URL citations returned by web search. It
does not parse arbitrary model-written URLs from prose.

## Source adapters

### CommunityAdapter

Searches for public communities and discussion spaces around the ICP pain and trigger. The first
queries bias toward Reddit plus broader forums.

### CreatorAdapter

Searches for concrete creator/channel profiles around the ICP identity, problem and category.

### NewsletterSiteAdapter

Searches for newsletters, niche publications and specialist websites serving the same audience.

## Relevance score

The first relevance score is deterministic and deliberately simple:

- overlap between ICP signals and retrieved evidence;
- source/domain fit bonus;
- evidence/snippet availability.

The score is bounded to 0–100. It is a discovery prior, not a claim that the channel will produce
a specific CAC.

Later outcomes should calibrate this score using real experiment data.

## Evidence contract

Every `ChannelOpportunity` contains one or more evidence records:

- search query;
- retrieved title;
- cited URL;
- snippet / response context.

If multiple queries find the same canonical URL for the same ICP, the opportunity is deduplicated
and its evidence is merged.

Tracking parameters and URL fragments are removed during canonicalization.

## API

- `POST /v1/products/{product_id}/channels/discover`;
- `GET /v1/products/{product_id}/channels`.

Channel discovery requires a previously generated ICP result. The service uses the top 3 ranked
ICPs by default.

## Persistence contract

`ChannelOpportunity` now stores:

- ICP reference;
- platform;
- source type;
- title;
- canonical URL;
- relevance score;
- rationale;
- evidence;
- contact/acquisition fields reserved for later milestones;
- creation timestamp.

Alembic migration: `20260807_0004_channel_hunter.py`.

## Definition of Done

- top ICPs produce at least 30 concrete opportunities;
- all opportunities have a URL, source type, relevance score and rationale;
- evidence is stored for every opportunity;
- community, creator and newsletter/site adapters are exercised;
- URLs are normalized and duplicates merged;
- discovery can run with OpenAI web search in production configuration;
- deterministic mock makes local development and CI network-independent;
- Ruff and pytest pass in CI.

## Next handoff

Milestone 4 / Growth Play Generator should combine `ICP × ChannelOpportunity` into an executable
acquisition hypothesis. It should prefer high-scoring ICPs and high-relevance opportunities but
preserve diversity so the first experiment portfolio does not collapse into one channel type.
