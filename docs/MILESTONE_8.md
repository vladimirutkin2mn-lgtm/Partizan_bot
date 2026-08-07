# Milestone 8 — Dogfood

## Goal

Stop adding abstract architecture and run Partizan Bot against a real digital product.

The first candidate is the Oracle / relationship-reading Telegram product used throughout the
contract tests. The default manifest is intentionally bounded:

- market: US;
- language: English;
- price hypothesis: $9.99 subscription;
- first target: 20 paid users;
- first-cycle budget cap: $100;
- target max CAC: $5.

A live bot/deep-link is **not** committed to the repository. It must be supplied as a deployment
configuration/reference link before external execution.

## Why a dogfood harness exists

Milestone 8 is different from Milestones 0–7: success cannot be proven by mocks alone. We need to
know which parts of a run were real and which were simulated.

`python -m app.dogfood` therefore writes a `DogfoodReport` containing:

- product confirmation state;
- number of ICPs;
- number of concrete channel opportunities;
- number of Growth Plays;
- selected top play and priority;
- execution package / experiment state when requested;
- active provider snapshot;
- explicit blockers preventing a real launch.

The runner does not label mock discovery or mock delivery as a successful live test.

## Default manifest

`dogfood/oracle_us.json`

It contains the founder brief and economics but intentionally has no destination URL/contact.
Secrets and real recipient data must not be committed.

## Modes

### 1. Research dry-run

```bash
python -m app.dogfood dogfood/oracle_us.json --report dogfood_report.json
```

With default local configuration this validates the complete system contract but reports that search
is mock and no live product destination is configured.

### 2. Live discovery

Configure:

```text
SEARCH_PROVIDER=openai
OPENAI_API_KEY=...
```

Then run:

```bash
python -m app.dogfood dogfood/oracle_us.json \
  --require-live-search \
  --report dogfood_report.json
```

This mode must fail fast if search is still mock. Channel opportunities then come from web-search URL
citations instead of deterministic test data.

Before using the output, review the top opportunities and Growth Plays. Channel relevance remains a
hypothesis until conversion data returns.

### 3. Prepare one execution package

Provide a real destination URL/reference link and, for automated email delivery, a real public or
user-supplied business contact. Do not commit those values if they are sensitive.

```bash
python -m app.dogfood /path/to/private_oracle_manifest.json \
  --require-live-search \
  --prepare-execution \
  --report dogfood_report.json
```

This approves the selected Growth Play internally and creates a personalized execution package, but
**does not send it**.

### 4. Approve without sending

```bash
python -m app.dogfood /path/to/private_oracle_manifest.json \
  --require-live-search \
  --prepare-execution \
  --approve-execution
```

The package moves to `APPROVED`, still without delivery.

### 5. Real one-message run

For the first automated delivery path, configure SMTP:

```text
EXECUTION_PROVIDER=smtp
SMTP_HOST=...
SMTP_PORT=587
SMTP_USERNAME=...
SMTP_PASSWORD=...
SMTP_FROM_EMAIL=...
SMTP_STARTTLS=true
```

Then deliberately supply all execution gates:

```bash
python -m app.dogfood /path/to/private_oracle_manifest.json \
  --require-live-search \
  --require-live-delivery \
  --prepare-execution \
  --approve-execution \
  --run-execution
```

This sends **one approved email**. There is no bulk-send dogfood mode.

## After launch

A successful `Run` creates `Experiment=RUNNING`. Feed actual results through the Analytics Loop:

- `VISIT`;
- `SIGNUP`;
- `ACTIVATED`;
- `PAID`;
- actual spend.

Then call Growth Manager. The dogfood result that matters is not “email was sent”, but whether we can
close this loop:

```text
real discovery
  → real approved experiment
  → real users
  → attributed paid conversion
  → observed CAC
  → SCALE / CONTINUE / MODIFY / STOP
  → next experiment
```

## Milestone completion criteria

Code readiness can be completed in the repository. **Milestone 8 itself is not complete until a real
product run produces real attributed acquisition data.**

Required evidence for closing issue #10:

1. live `SEARCH_PROVIDER` was used;
2. at least one real Growth Play was reviewed and launched;
3. at least one real user event was attributed to its Experiment;
4. actual spend/revenue were recorded when applicable;
5. Growth Manager evaluated the actual result;
6. forecast/prior versus observed CAC/conversion was captured in learning memory.

Until a real Oracle bot/deep-link and launch approval are supplied, the repository should report
`READY_FOR_LIVE_TEST`, not claim dogfood success.
