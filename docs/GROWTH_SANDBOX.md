# Isolated Partizan growth sandbox

`partizan-sandbox-run` proves the complete Partizan acquisition/economics/learning loop without an external product or provider mutation.

> **Everything produced by this command is synthetic SANDBOX data. It is not a dogfood result and must never be reported as real acquisition performance.**

## Isolation model

The CLI refuses to run when the parent Partizan configuration has `APP_ENV=production`.

For every run it:

1. chooses a localhost port;
2. creates a temporary working directory with no `.env` file;
3. starts a separate child `uvicorn app.main:app` process;
4. gives the child an explicit allowlisted environment only;
5. forces `RUNTIME_STORAGE=memory`;
6. forces LLM/search/execution providers to `mock` and creative providers to `unavailable`;
7. does not pass database, OpenAI, Gemini, SMTP, operator, Meta or TikTok secrets;
8. performs the proof through the real Partizan HTTP routes;
9. terminates the child process at the end.

The child therefore has no access to the configured production RuntimeStateStore. Product/event/spend/learning facts disappear with the child process.

## Command

```bash
partizan-sandbox-run
```

Machine-readable output:

```bash
partizan-sandbox-run --json
```

The report always contains `mode: SANDBOX` and `external_provider_mutation: false`.

## Deterministic fixture

The sandbox uses a fixed synthetic subscription product:

- market: US;
- language: English;
- price: `$30/month`;
- marketing budget: `$300`;
- target max CAC: `$15`;
- destination: reserved non-routable `https://sandbox.invalid/product`;
- external execution: none.

The runner selects a pair of READY plays sharing one platform+tactic so the second play can prove that the next portfolio incorporates observed economics from the first experiment.

It then creates one experiment and marks it RUNNING through Partizan's existing manual completion boundary. This is an internal sandbox state transition, not a provider call.

## Synthetic funnel and economics

The fixture sends three complete attributed customer funnels through the real Product Event Key ingestion contract:

```text
3 VISIT
  -> 3 SIGNUP
  -> 3 ACTIVATED
  -> 3 PAID
```

Each synthetic PAID event carries `$30` revenue.

Then the real distribution-spend endpoint records `$30` total spend.

Expected deterministic economics:

```text
paid users = 3
spend      = $30
revenue    = $90
CAC        = $10
ROAS       = 3.0
```

With target max CAC `$15`, the real Distribution Growth Manager should produce `SCALE` because CAC is at least 20% below target and three paid users meet the current scale sample threshold.

## What the proof exercises

The sandbox traverses the real Partizan contracts for:

```text
ProductProfile
  -> ICP generation
  -> distribution discovery
  -> Distribution Plays
  -> action + experiment
  -> RUNNING experiment
  -> Product Event Key
  -> VISIT / SIGNUP / ACTIVATED / PAID ingestion
  -> spend
  -> CAC / ROAS
  -> Growth Manager decision
  -> learning memory
  -> next portfolio
```

The run fails if the expected economics differ, if the Growth Manager does not produce the deterministic decision, if learning is not persisted in the isolated store, or if the next portfolio does not reference observed peer economics.

## What it deliberately does not prove

The sandbox does **not** prove:

- real product integration;
- public Partizan routing;
- real Meta/TikTok/Telegram/SMTP execution;
- real users;
- real CAC;
- provider credentials;
- production deployment.

Those remain separate production/dogfood proofs. A sandbox success must never close the real-product dogfood milestone.

## Repository boundary

The sandbox runs entirely from `Partizan_bot`. It does not read, modify, deploy or migrate another product repository.
