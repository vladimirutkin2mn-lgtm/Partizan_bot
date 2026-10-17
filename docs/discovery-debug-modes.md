# Discovery execution modes

Partizan separates production discovery from diagnostic and replay execution.

| Mode | Network | Working-state writes | Learning writes | External actions |
|---|---:|---:|---:|---:|
| `RUNTIME` | yes | yes | yes | yes, subject to existing mandate/governance |
| `DEBUG_TRACE` | yes | no | no | no |
| `REPLAY` | no | no | no | no |

`DEBUG_TRACE` is allowed to perform an explicitly requested bounded live read, but it must not persist Telegram communities, negative learning, publications, profile changes, or other working-state mutations.

A live diagnostic OpenAI call is correlated as `run_id → step_id → query_id → response_id`. The trace captures input, cached input, output and reasoning tokens, internal web-search call count and duration. Reasoning tokens stay a separate diagnostic field and are not added to output tokens again.

Dollar fields are deliberately separate from provider usage. Until an explicit pricing source/version is configured, `estimated_cost_usd` is `null` with `PRICING_NOT_CONFIGURED`. The OpenAI response usage does not expose the final billed dollar amount, so `actual_billed_cost_usd` stays `null` with `NOT_AVAILABLE_FROM_RESPONSE_USAGE` instead of being guessed.

`REPLAY` reprocesses a saved discovery trace without network access or working-state mutations. Run it with:

```bash
python -m app.discovery_trace_replay path/to/discovery-trace.json
```

The replay uses the current deterministic Telegram URL normalization and the current saved-input relevance decision path. It reports the input fingerprint, verdict changes, normalization changes and an explicit zero-external-effects summary. It does not call web search, an LLM, native Telegram research or the runtime store.

Replay is intentionally limited by the source trace. If an old trace did not save a native Telegram field or source-specific observation, replay reports the limitation rather than reconstructing or inventing the missing evidence.

Every diagnostic run has a `run_id`. Search operations receive linked `query_id` and `step_id` values so later trace stages can be joined without relying on ordering or free-form log text.

This document describes execution safety only. It does not change the production community qualification, latest-post rule, publishing permissions, or AutoResearch policy.
