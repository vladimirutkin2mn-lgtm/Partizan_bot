# Safe import of Telegram public community catalogs

This stage brings **exported community identities** into the existing
`TelegramCommunityRegistry`. No third-party crawler is executed and no Telegram
credentials, sessions, participants, messages or contacts are sent to a new
provider.

## Supported export shapes

- `chatman-media/telegram-groups-parser`: exported `groups.json` array with
  `username`, `title`, and `type` (group / supergroup / channel).
- `MakazhanAlpamys/Telegram-Chat-Scraper`: `verified_chats.json` object
  with a `groups` array, `username`, `title`, `description`, and
  `is_group=true`. No participant/user exports are accepted.
- `tgscan`: **metadata-only** support for an explicit JSON `results`
  array with `username`, `title`, and `type`. The project has no
  documented stable export contract, so this is a manual normalization format,
  **not** an API integration.

Unknown schemas and invitations are skipped; lists over 200 records or JSON
over 1 MB are rejected. Public usernames are validated and case-normalized.
No catalog-provided numeric chat ID is trusted as a native Telegram entity ID.

## Operator workflow

Start in preview mode (the default):

```bash
python -m app.telegram_catalog_import \
  --product-id <UUID> \
  --source telegram-groups-parser \
  --file /secure/path/groups.json
```

If the preview looks correct, run the same command with `--commit`.
That operation writes **unverified** communities to the product's registry.
They are not automatically audience-fit, published, joined or usable as
comment/reply targets.

Over later refreshes, the existing native Telegram inspector rotates through
known public handles in batches of three. A community is promoted to confirmed
fit only if authoritative native title/about/recent context overlaps the
product-and-ICP search anchor. Native-verified but unrelated catalog entries
remain RESEARCH_ONLY without an action target.

The importer is deliberately separate from the live-discovery service: catalog
exports must be supplied, reviewed and committed explicitly. There is no
periodic scraping, external catalogue requests, session sharing or automatic
expansion into large uncontrolled Telegram searches.

## Third-party code and safety

- `tgscan/tgscan` is GPL-3.0: do not vendor or embed its implementation
  without a separate licensing review.
- `Telegram-Chat-Scraper` publishes an MIT license but its optional participant
  enumeration is **outside scope**. Only community metadata may be imported.
- `telegram-groups-parser` did not advertise a recognized license at the time
  of review; do not copy its implementation into Partizan.
- Imported data are source evidence, **not verified Telegram permission**.
- Preview and unit tests use local synthetic export payloads; they do not run
  any live Telegram search, scraping, publishing or account mutation.

## Pending work

Automated catalog ingestion (with source provenance, refresh interval, source
availability checks, consent/ToS handling and hard budgets) has not been
activated. Do not enable by simply copying an unreviewed third-party crawler.
