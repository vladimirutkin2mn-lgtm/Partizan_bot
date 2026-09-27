# Customer redesign: approved source transfer

This change transfers the approved Partizan prototype into the existing FastAPI,
HTML, CSS and JavaScript customer application. It is a draft for visual review.
It must not be treated as visually approved or deployed until the comparison
below is complete.

## Source

- Approved prototype: https://partizan-design-preview.vladimir-utkin2mn.chatgpt.site
- Handoff: `Partizan_redesign_v2_handoff.zip`, prototype source version 3.
- Reference image in the handoff: `handoff/design/simple-workspace-desktop.jpg`.

The landing HTML is a static export of the approved React page, including the
three illustrative product scenarios. `landing.v1.css` preserves the compiled
prototype CSS. `design.v1.css` preserves `app/redesign.css`, with a provenance
comment. Do not edit these source styles to approximate the design. Keep native
integration overrides in `landing.account.v1.css`, `design.native.v1.css` and
`legal.v1.css` so source changes and product adaptations can be reviewed separately.

The prototype's fake login, timers, sample workspace state and simulated actions
are not application logic. Existing customer APIs and event handlers remain the
source of live state and actions. React, Next.js and Tailwind are not added to the
production runtime. Node/jsdom is used only for development and CI checks.

SHA-256 of the original prototype files:

| Source file | SHA-256 |
| --- | --- |
| `app/page.tsx` | `7f604356337e6f097ca5dac55f25d5cbf3239c9c2ae9bddd78e4e7fb276e04a7` |
| `app/globals.css` | `2595ddc3d5c077271196ca25937f676ddeed685a4245bacd09c21c1e952cc2e7` |
| `app/redesign.css` | `84811e111384db0c719354f9373b9b3987be2c3b9c14865c3e861677126585c8` |
| `app/start/page.tsx` | `1d128721883fcce56f8169475e0b2448f1f8b97dd2cb1def10e7745ea3177b7b` |
| `app/workspace/page.tsx` | `4e2b9757a2bb4c53c782a7040e43635a47b2797da61a21fb099f5b5eabe71696` |
| `app/workspace/sign-in.tsx` | `17765deea43291d6b33cee404919400506538bf56c9e2ee177fa143519eb8e01` |
| `dist/client/_next/static/css/index.DPBv7cA7.css` | `a405f8821271f9f5278893aa3f3647dd0f818f417f5b163b23c11d91ccd7be2f` |

## Product mapping

| Surface | Presentation | Existing behavior retained |
| --- | --- | --- |
| `/` | Approved landing, native tabs, FAQ and fee calculator | Product-link entry, describe entry, session-aware Sign in |
| `/start` | Approved six-step layout, link/description tabs and goal choices | Real preview, clarification, account, research and funding flow |
| `/workspace`, signed out | Approved two-column Sign in | Customer credentials, session and project loading |
| Home | Three live metrics, next move, recent update | Current native activation, channel choice and exact-action review |
| Results | Progress, Research, Tests, History | Finance, research, experiments, learning and action history |
| Channels | Server-provided channel list and detailed controls | Native channel modes, connections and capability restrictions |
| Settings | Budget, spending limits, connected accounts, project | Funding, spending confirmation, OAuth and project editing |
| Privacy, Terms, Security, Contact | Shared light header, typography and footer | Existing policy text and support links |

The internal operator `/app` interface is outside this customer-facing change.
No backend business rules, database schema, execution permissions, publication
rules or payment calculations are changed. Production smoke checks now identify
the entry forms by their stable IDs rather than the old headings.

`start.design.v1.js` adapts the live onboarding controls and progress indicator.
`workspace.design.v1.js` rearranges existing DOM nodes with their handlers intact,
mirrors server-rendered values, and reveals existing controls when navigating.
Opening next-move review does not confirm, approve or execute an action. The
existing explicit confirmation remains required. New source assets participate
in the existing content-based cache revision.

## Verification

Run the existing Python tests and the customer DOM checks with Python dependencies
installed. Use the PostgreSQL service configured in CI for database-backed tests.

```sh
python -m pip install -e '.[dev]'
npm ci --ignore-scripts
npm run test:design
ruff check .
pytest
```

For a virtual environment, set `PYTHON=.venv/bin/python` on the npm command. The
five jsdom tests load the actual FastAPI HTML and all native enhancement scripts
with a mocked transport. They cover entry controls, preview payloads, real login
payloads, navigation, live metric mapping, dynamic channels and exact-action
confirmation. They do not render CSS or replace browser/visual testing.

Local verification on the implementation base (`d21efda`): 65 focused web tests
and all five DOM checks passed. The full Python run had 1,203 passes and six
failures caused by an unavailable local PostgreSQL service. CI includes
PostgreSQL and must provide the full integrated result against current main.

## Required visual review before merge

The available browser session could not open the local implementation, and the
hosted prototype required authentication. No implementation screenshots or
pixel comparisons were produced. Copying source CSS does not prove fidelity of
the adapted native product screens.

Compare the approved prototype and this branch at matching desktop and mobile
widths, with the same font availability and state:

- Landing: headings, line wrapping, section widths and spacing, scenarios, FAQ,
  calculator and mobile navigation.
- Sign in and all onboarding stages: field geometry, step visibility, errors,
  account gate and keyboard navigation.
- Home: the three-metric layout, next move, prepared-action review and empty state.
- Results, Channels and Settings: disclosures, detailed forms, OAuth selection,
  project switching, overflow and small-screen readability.
- Legal pages: shared header/footer and readable policy content.

Also exercise native modal focus/Escape behavior, payment-return navigation and
connection-return navigation in a permitted browser environment. Keep the PR in
draft until visual differences are resolved and the user approves the result.
