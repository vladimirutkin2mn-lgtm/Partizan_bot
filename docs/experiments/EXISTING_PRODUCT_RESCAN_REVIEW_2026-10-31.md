# Existing workspace product rescan — 31 October 2026

## Why

On the existing FemDom project, the linked Telegram invite did not provide a
readable public product description. The stored card mostly describes an
acquisition asset; it does not establish what a visitor gains or who benefits.
Changing the LLM's character prompt cannot repair missing customer facts.

## User journey

Go to an existing project → **Settings** → **Product understanding** →
**Rescan product**. Partizan reads the saved public source when available and
creates an **unconfirmed draft**. When it cannot read the source (private
Telegram invite, inaccessible page), the draft says so rather than inventing
a value proposition. It retains verified saved customer details where safe.

The user reviews and corrects just four product facts: name, description,
what people receive, and target audience. **Confirm product details** applies
the change; Cancel does not apply it. Unconfirmed drafts are recoverable when
returning to Settings.

The functionality works for:
- Existing account projects that already have a confirmed ProductProfile;
- Projects created with "New project" that previously had no linked
  `product_id` (a product is created only after explicit confirmation).

## Technical safety

- Customer session and project ownership are validated on all GET/POST
  endpoints; no raw customer tokens are placed into browser JavaScript.
- Public source retrieval uses the same SSRF/host-safe product link reader
  as first-time onboarding. No LLM, Telegram login/join/send or paid call.
- A source scan writes only an independent `customer_product_rescan_proposals`
  record, not the confirmed project/product or marketing memory.
- Confirmation requires a matching scan ID, the same unchanged product and
  project baseline hashes, and explicit nonempty customer-value/audience
  facts. CAS protects against overwriting a newer product.
- Each successful review writes a project-scoped immutable
  `customer_product_revision_history` entry with the old/new product.
  ProductId remains stable for existing products.
- Previously saved author voice, live campaigns, Telegram sessions,
  publications, subscription settings and conversion research are not
  automatically changed by a rescan. Project
  `voice_refresh_status=REVIEW_REQUIRED` marks a separate follow-on
  author-style evaluation against the changed facts.

## Scope of automatic source reading

This is public-source evidence retrieval plus human review, **not**
autonomous deep crawling of an invite-only Telegram community and not
auto-learning from private messages. A private invite cannot safely establish
the product offering. An accessible, authorized source or the founder's
missing basic facts are required. Product knowledge changes do not auto
trigger voice re-generation.

## Next

Use the existing account project in the browser, review the source findings,
confirm actual user-facing value and audience, then independently create a
new inactive product-derived author character. Compare it with the saved
baseline using the same writer/model and reviewed saved native posts before
promoting or enabling any publication.
