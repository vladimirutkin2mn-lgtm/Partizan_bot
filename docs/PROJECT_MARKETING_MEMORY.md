# Project Marketing Memory

Status: implemented foundation  
Purpose: durable, provenance-aware marketing memory for one customer project

## Why this exists

Partizan already persists product understanding, research, opportunities, experiments, actions and analytics. What was missing was a single durable layer for the conclusions that should influence future marketing work on the same project.

The memory is **not** a saved giant prompt. It is structured project knowledge that is assembled into a bounded prompt context when needed.

Examples:

- the customer explicitly prefers a short feminine/neutral Telegram profile name;
- a certain visual or tone was rejected by the customer;
- an experiment showed that one CTA converted better than another;
- Telegram comments should be less lecture-like for this project;
- a current profile strategy is still only a hypothesis and must not be treated as a fact.

## Data model

Each memory entry contains:

- `project_id` and, when available, `product_id`;
- a stable semantic `key`;
- one category;
- one statement;
- provenance/source;
- confidence;
- optional platform and action-type scope;
- optional source reference and tags;
- lifecycle status and supersession links;
- timestamps.

### Categories

- `FACT`
- `BRAND_TONE`
- `CONSTRAINT`
- `CUSTOMER_PREFERENCE`
- `HYPOTHESIS`
- `EXPERIMENT_LEARNING`
- `CHANNEL_PLAYBOOK`
- `CURRENT_STRATEGY`

### Provenance

From weakest to strongest:

1. `AI_HYPOTHESIS`
2. `OBSERVED`
3. `EXPERIMENT_RESULT`
4. `CUSTOMER_CONFIRMED`

A weaker source cannot silently replace an active stronger source with the same semantic key and scope.

Example:

- AI hypothesis: `profile-name-style = keep current name`
- customer confirmation: `profile-name-style = prefer short feminine/neutral name`

The customer-confirmed entry becomes active. The old hypothesis remains in history as `SUPERSEDED`.

This preserves both auditability and the distinction between preference, observation and measured evidence.

## Scope

Entries may be global or scoped by:

- platform, for example `TELEGRAM`;
- action type, for example `COMMENT`.

Prompt assembly includes global memory plus entries that match the current platform/action.

A Telegram-comment playbook therefore does not leak into an unrelated Reddit action.

## Prompt builder

The prompt builder renders only active applicable entries and labels every statement with category and provenance.

The resulting block explicitly says that memory is project context, not system instructions. Customer-confirmed preferences guide marketing choices but cannot override:

- safety requirements;
- law;
- platform policy;
- community policy;
- execution approval requirements.

The builder is bounded to 24 entries and 6,000 characters so project history cannot grow into an unbounded prompt.

If one `product_id` is unexpectedly associated with active memory from more than one project, automatic product-based prompt resolution fails closed and returns no memory rather than mixing customer contexts.

## Customer API

Authenticated customer workspace endpoints:

- `GET /customer/workspace/{project_id}/marketing-memory`
- `POST /customer/workspace/{project_id}/marketing-memory`
- `DELETE /customer/workspace/{project_id}/marketing-memory/{entry_id}`
- `GET /customer/workspace/{project_id}/marketing-memory/prompt-preview`

A memory entry created through the customer API is always recorded as `CUSTOMER_CONFIRMED` with confidence `1.0`. The customer cannot claim that a preference is an experiment result through this endpoint.

Internal services use `record_internal(...)` with an explicit provenance source when later wiring automated observations and experiment learnings.

## Persistence

Entries are stored in `RuntimeStateStore` under namespace:

`project_marketing_memory`

Therefore database-mode production keeps the project memory across process restarts in the same durable snapshot mechanism already used by the rest of Partizan runtime state.

## Prompt integration boundary

This PR deliberately separates **memory storage / conflict resolution / prompt assembly** from automatic mutation of historical product facts.

The action-drafting integration consumes the bounded prompt context rather than storing bespoke prompts per customer. The intended composition is:

`global Partizan rules + ProductProfile + ProjectMarketingMemory + current opportunity + experiment strategy -> action draft`

No memory entry can authorize external publication. Telegram comment/story publication remains governed by its existing explicit approval flows.

## Next capture hooks

The service is designed for follow-up automatic writes from:

- customer approve/reject feedback -> `CUSTOMER_CONFIRMED`;
- provider/profile observation -> `OBSERVED`;
- statistically accepted experiment outcome -> `EXPERIMENT_RESULT`;
- planner-generated candidate idea -> `AI_HYPOTHESIS`.

Those hooks should write structured entries, not append free-form prompt history.
