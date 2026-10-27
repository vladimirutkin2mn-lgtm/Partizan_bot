# Connected Telegram account -> existing voice generator

2026-10-27: The customer Telegram account integration already exists; do
**not** ask the user to reconnect solely to improve comment style.

## Existing backend capabilities

- `app/telegram_client_publishing_impl.py` persists an active per-project
  connected user account, with account ID, username and public display name.
  Its `profile_internal(project_id)` reads the LIVE public profile
  (display name / username / bio) from the authorised Telegram session.
- Connected-account profile edit, avatar handling, and conversion-route
  verification are implemented separately. They are **not** changed here.

## New read-only drafting integration

- `app/telegram_author_context.py` resolves precisely one ACTIVE
  customer-owned account for the same product and project. It uses the
  existing service, never reconnects or changes the Telegram profile.
- A bounded, read-only profile fetch adds `actual_publisher_profile`
  to the one-call organic Telegram composition payload. Supply only public
  fields: username, display name, bio and source/status. Never send a
  Telegram session, phone number, profile image reference, project ID or
  API credential to the LLM.
- If the live profile is inaccessible, use the previously connected public
  name/username (without invented bio). If account ownership is ambiguous,
  no account is selected. A project explicitly using
  `PARTIZAN_MANAGED` is not silently associated with a customer-owned user.
- The creative prompt is versioned
  `community_message_v16_1_connected_account_voice`, and explicitly
  requests a voice consistent with the actual profile the reader sees.
  It remains one LLM request per target. No Telegram messages are sent.

## Limitations and follow-ups

- This resolver handles the already connected **customer-owned** account.
  A Partizan-managed identity/assignment needs its own explicit
  project/assignment binding, not reuse of a customer's account.
- The current composer has a product ID but no explicit project ID. When
  multiple active projects refer to the same product, resolution declines
  instead of guessing. A future context-aware route should pass the exact
  authorised project ID.
- The live read adds Telegram profile read time and can time out;
  an authenticated cached public identity still allows a draft.
- Existing profile/bio is not used as fabricated product proof; the
  author identity and product positioning remain separate data fields.
- No live LLM or real-world quality comparison performed by this PR.
