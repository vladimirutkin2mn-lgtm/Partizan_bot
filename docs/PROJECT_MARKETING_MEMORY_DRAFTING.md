# Project Marketing Memory in action drafting

Status: implemented
Parent memory architecture: `docs/PROJECT_MARKETING_MEMORY.md`

## Purpose

Action drafting now consumes the durable project marketing memory instead of relying only on the static Partizan prompt, ProductProfile and the current opportunity.

The effective composition for LLM-backed drafting is:

`global Partizan rules + marketing guidance + ProductProfile + applicable ProjectMarketingMemory + current play/opportunity/target + conversion brief -> draft`

## Resolution

The composer resolves memory from the current `product_id` and scopes it by:

- the opportunity platform;
- the action type.

Only active global memory and matching scoped memory are included. For example, a Telegram COMMENT playbook does not enter an Instagram COMMENT prompt.

If one product unexpectedly resolves to active memory from more than one project, the memory service fails closed and returns no memory. Drafting therefore never guesses which customer's context should win.

## Prompt placement and authority

Project memory is inserted in the **user context**, not the system prompt.

The memory renderer labels every entry with category, provenance and scope, and explicitly states that the block is project context rather than system instructions. Customer-confirmed preferences guide marketing choices but cannot override safety requirements, law, platform policy, community policy or explicit publication approval.

The global action-drafting system rules remain authoritative.

## Bounded context

The memory service continues to enforce the limits defined by the parent architecture:

- maximum 24 active entries;
- maximum 6,000 rendered characters.

This keeps long-lived projects from turning into unbounded prompt history.

## Mock-provider behavior

The deterministic mock draft path remains deterministic and does not attempt to interpret free-form memory. The memory context is consumed by LLM-backed drafting, where the model can apply the structured preferences and learnings.

This separation keeps local/CI mock output stable while production drafting receives the project-specific context.

## Verification

Regression tests verify that:

- global and matching platform/action memory reaches the draft prompt;
- unrelated channel memory does not leak into the prompt;
- provenance labels remain visible to the model;
- ambiguous product-to-project memory resolution fails closed.

## External-action boundary

This integration only changes draft generation context. It does not approve, apply or publish any external action.

Telegram profile mutations, comments and stories continue to use their existing explicit review/approval gates.
