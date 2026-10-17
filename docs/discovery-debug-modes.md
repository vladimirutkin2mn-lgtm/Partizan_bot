# Discovery execution modes

Partizan separates production discovery from diagnostic and replay execution.

| Mode | Network | Working-state writes | Learning writes | External actions |
|---|---:|---:|---:|---:|
| `RUNTIME` | yes | yes | yes | yes, subject to existing mandate/governance |
| `DEBUG_TRACE` | yes | no | no | no |
| `REPLAY` | no | no | no | no |

`DEBUG_TRACE` is allowed to perform an explicitly requested bounded live read, but it must not persist Telegram communities, negative learning, publications, profile changes, or other working-state mutations.

`REPLAY` reprocesses a saved discovery trace without network access or working-state mutations. Run it with:

```bash
python -m app.discovery_trace_replay path/to/discovery-trace.json
```

The replay uses the current deterministic Telegram URL normalization and the current saved-input relevance decision path. It reports the input fingerprint, verdict changes, normalization changes and an explicit zero-external-effects summary. It does not call web search, an LLM, native Telegram research or the runtime store.

Replay is intentionally limited by the source trace. If an old trace did not save a native Telegram field or source-specific observation, replay reports the limitation rather than reconstructing or inventing the missing evidence.

Every diagnostic run has a `run_id`. Search operations receive linked `query_id` and `step_id` values so later trace stages can be joined without relying on ordering or free-form log text.

This document describes execution safety only. It does not change the production community qualification, latest-post rule, publishing permissions, or AutoResearch policy.
