# Oracle dogfood runbook

This runbook executes the channel-first Partizan pipeline against the Oracle / AI astrologer product through the real HTTP API.

## 1. Start the runtime

```bash
cp .env.example .env
make runtime-up
```

The runner expects Partizan on `http://127.0.0.1:8000` by default.

## 2. Dry-run to a PREPARED experiment

Pass the real Oracle destination as either `--destination-url` or `--reference-link`.

```bash
partizan-dogfood-oracle \
  --destination-url https://YOUR-ORACLE-DESTINATION.example
```

The default business assumptions are:

- price: `$6.90 / month`;
- first acquisition budget: `$1,000`;
- target max CAC: `$12`;
- audience: English-speaking adults roughly 20–40 interested in astrology, relationships and self-reflection;
- MVP channels: Telegram, Instagram, Reddit and TikTok;
- positioning: entertainment / reflection, not guaranteed prediction or professional advice.

Override them explicitly when needed:

```bash
partizan-dogfood-oracle \
  --destination-url https://YOUR-ORACLE-DESTINATION.example \
  --budget 1500 \
  --max-cac 10 \
  --price 6.90
```

The runner performs:

```text
health
  → Product intake
  → known clarification answers
  → Product confirmation
  → ICP generation
  → Audience Distribution discovery
  → opportunity enrichment
  → Distribution Plays
  → highest-priority READY play
  → auto-prepare DistributionAction
  → tracking/referral output
  → current analytics / learning read
```

If a required setup object is missing, the runner exits with a blocker list instead of fabricating a workaround.

## 3. Filter the first experiment

Examples:

```bash
partizan-dogfood-oracle \
  --destination-url https://YOUR-ORACLE-DESTINATION.example \
  --platform INSTAGRAM \
  --tactic-class PAID_PLATFORM
```

```bash
partizan-dogfood-oracle \
  --destination-url https://YOUR-ORACLE-DESTINATION.example \
  --platform REDDIT \
  --tactic-class COMMUNITY
```

Supported platform filters are `TELEGRAM`, `INSTAGRAM`, `REDDIT`, `TIKTOK`.

Supported tactic filters are `COMMUNITY`, `PAID_PLATFORM`, `OWNED_ORGANIC`.

## 4. Optional external execution

Dry-run is the default. `--execute` explicitly allows the existing approval + execution-adapter boundary:

```bash
export PARTIZAN_OPERATOR_KEY='deployment-secret-if-required'
partizan-dogfood-oracle \
  --destination-url https://YOUR-ORACLE-DESTINATION.example \
  --platform INSTAGRAM \
  --tactic-class PAID_PLATFORM \
  --execute
```

The operator key is read from `PARTIZAN_OPERATOR_KEY` (or `OPERATOR_API_KEY`) environment only. There is no CLI secret argument.

### Paid safety boundary

For Meta/TikTok paid actions, the runner may create provider objects only through the existing execution adapter and stops at `STAGED`.

It never calls:

- paid activation authorization;
- paid activation;
- budget increase/update;
- restart/re-enable.

Real spend must still be activated manually through the existing exact-budget Paid Control UI/API.

## 5. Connect real conversions

In `/app` → Results → `Подключить конверсии`:

1. create the product-bound Event Key;
2. copy it once into the Oracle backend secret store;
3. send stable-idempotency `SIGNUP`, `ACTIVATED` and `PAID` events server-to-server;
4. configure `PARTIZAN_PUBLIC_BASE_URL` if first-click VISIT redirect attribution is desired.

Do not place Product Event Keys in frontend JavaScript or query strings.

## 6. Definition of a successful first dogfood

The first Oracle run is considered commercially useful when:

1. at least one DistributionExperiment reaches RUNNING;
2. real visits/conversions are attributed;
3. spend is reconciled for paid experiments;
4. at least one paid conversion is observed;
5. CAC is calculable;
6. Growth Manager emits a data-backed `SCALE`, `CONTINUE`, `MODIFY` or `STOP` decision;
7. the next portfolio changes based on observed economics.

Until those conditions are met, the runner should expose the missing dependency as a blocker rather than report the milestone complete.
