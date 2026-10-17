# Discovery execution modes

Partizan separates production discovery from diagnostic and replay execution.

| Mode | Network | Working-state writes | Learning writes | External actions |
|---|---:|---:|---:|---:|
| `RUNTIME` | yes | yes | yes | yes, subject to existing mandate/governance |
| `DEBUG_TRACE` | yes | no | no | no |
| `REPLAY` | no | no | no | no |

`DEBUG_TRACE` is allowed to perform an explicitly requested bounded live read, but it must not persist Telegram communities, negative learning, publications, profile changes, or other working-state mutations.

`REPLAY` is the offline mode for reprocessing saved provider responses and Telegram snapshots. Its network prohibition is encoded in `DiscoveryExecutionContext`; the saved-response provider/CLI is implemented separately so that replay can be tested without weakening the production path.

Every diagnostic run has a `run_id`. Search operations receive linked `query_id` and `step_id` values so later trace stages can be joined without relying on ordering or free-form log text.

This document describes execution safety only. It does not change the production community qualification, latest-post rule, publishing permissions, or AutoResearch policy.
