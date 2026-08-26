# Partizan Self-Research Loop

This is the Phase 8 / B3 operational contract for offline Partizan self-improvement.
It follows the core autoresearch pattern: hold evaluation fixed, change one bounded surface,
measure the candidate against the same benchmark, keep improvements and discard regressions.

## Research program

The autonomous loop may optimize only the scoring behavior represented by
`app/distribution_play_planner.py`. The first implementation uses a structured
`PlannerScoringSpec` rather than arbitrary source-code generation. One trial changes exactly
one of these bounded dimensions:

- aggregate priority-score weight;
- evidence/provenance bonus;
- evidence-count weight;
- community tactic bonus;
- owned-organic tactic bonus;
- paid-platform tactic bonus.

The proposal catalog is deterministic and each candidate is reviewable as `before -> after`.
The loop suppresses already-tried candidate specs for the current benchmark version.

## Immutable exam

The Phase 7 benchmark, evaluator and path policy are protected. The self-research loop cannot
edit them. It cannot tune on the TEST holdout. Autonomous iterations use TRAIN or DEV only.
A future human-reviewed production adoption can separately evaluate a research champion on
TEST without modifying the evaluation harness.

The candidate ranker never reads observed spend, CAC, conversions, revenue or the recorded
winner while generating predictions. Those fields are evaluation ground truth only.

## Trial lifecycle

1. load the benchmark version and current research champion;
2. evaluate the champion on the selected TRAIN/DEV split;
3. propose one unseen, one-dimensional scoring mutation;
4. verify the target is explicitly editable and not protected;
5. rank benchmark candidates without reading outcome economics;
6. run the fixed Phase 7 evaluator;
7. compare baseline and candidate using fixed safety/reliability vetoes;
8. persist `KEEP`, `DISCARD`, `VETO`, `BLOCKED`, or `EXHAUSTED`;
9. on `KEEP`, persist the candidate only as a research champion artifact.

`KEEP` does not edit the production planner file. It does not commit code, push a branch,
merge a PR, deploy, call a provider, spend money or mutate a customer project.

## Running bounded iterations

The offline CLI is:

```bash
python -m app.self_research_cli \
  --dataset-version <version> \
  --split DEV \
  --iterations 10
```

`--iterations` is capped at 100. TEST is not a valid autonomous CLI split.

## Production adoption

A useful research champion is evidence for a normal engineering change. A human-reviewed PR
must translate the accepted scoring spec into production planner behavior, run the protected
TEST evaluation and the repository CI, and then follow the normal explicit merge/deploy path.
