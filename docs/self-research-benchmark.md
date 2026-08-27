# Partizan Self-Research Benchmark

This benchmark is the fixed offline evaluation harness for AutoResearch Track B / Phase 7.
It is intentionally separate from future candidate-generation or code-editing agents.

## Data contract

A benchmark case is built from one ProductProfile plus its generated DistributionPlays and
measurable RUNNING/FINISHED DistributionExperiments. The exported case is allowlisted and
contains only:

- market, language, goal, budget and max CAC;
- stable pseudonymous case/candidate keys;
- platform, tactic class/id, action type and opportunity kind;
- planner priority score and READY/executable state;
- evidence count / provenance-present flag;
- aggregate visits, signups, activated users, paid users, spend, revenue, CAC and ROAS.

The builder never exports raw product briefs, product names/descriptions, reference URLs,
audience/competitor free text, analytics event properties, actor IDs, provider metadata,
credentials, access tokens, payment data or customer-account identifiers.

## Versioning and split

The dataset version is SHA-256 over canonical schema-v1 case contents, excluding creation
time. Rebuilding from identical runtime facts therefore produces the same dataset version.

Each pseudonymous case ID is deterministically assigned by hash bucket:

- 70% TRAIN
- 15% DEV
- 15% TEST

Phase 8 may tune only against TRAIN/DEV. A candidate intended for promotion must be evaluated
on the fixed TEST split without modifying benchmark/evaluator files.

## Ground-truth objective

A case becomes decision-grade only when at least two measured candidates satisfy the same
minimum-evidence tier. Winner selection is deterministic:

1. CAC when at least two candidates have >=2 paid users and defined CAC;
2. paid users when at least two candidates have >=2 paid users but CAC is unavailable;
3. activation rate when at least two candidates have >=5 activations and >=10 signups;
4. signup rate when at least two candidates have >=10 signups and >=50 visits;
5. otherwise the case is retained for dataset coverage but is not used as winner ground truth.

## Fixed evaluation metrics

For decision-grade cases on one selected split, the evaluator calculates:

- winner hit@1;
- winner hit@3;
- normalized regret against observed winner economics;
- top recommendation executable rate;
- top recommendation provenance coverage;
- unknown recommendation rate;
- safety violation rate (top choice absent or not executable);
- complexity penalty for returning more than three recommendations;
- a documented fixed headline score combining the above.

The evaluator is deterministic for a fixed dataset, evaluator version and prediction payload.
Evaluation results are persisted in `runtime_snapshots` under the self-research evaluation
namespace.

## Promotion vetoes

A higher headline score does not automatically win. Candidate-vs-baseline comparison returns
`VETO` when the candidate materially regresses safety/reliability, including:

- safety violation rate > baseline by more than 1 percentage point;
- unknown recommendation rate > baseline by more than 1 percentage point;
- executable recommendation rate falls by more than 2 percentage points;
- provenance coverage falls by more than 5 percentage points.

Only a positive headline delta with no veto regression returns `KEEP`.

## Protected surface

`app/self_research_policy.py` is the machine-readable boundary for Phase 8. The benchmark
builder, schemas, evaluator, policy file, deployment/CI workflow and production safety/spend
controls are protected. The initial autonomous edit allowlist is intentionally only
`app/distribution_play_planner.py` and may expand only through a human-reviewed change to the
protected policy file.
