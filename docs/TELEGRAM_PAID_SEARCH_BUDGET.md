# Paid Telegram web search: operator-estimated budget reservations

The last-resort OpenAI Telegram search is still **off** by default.
To permit a call, three configuration keys must be explicitly set:

```env
TELEGRAM_PAID_DISCOVERY_ENABLED=true
TELEGRAM_PAID_DISCOVERY_MONTHLY_BUDGET_USD=<positive operator allocation>
TELEGRAM_PAID_DISCOVERY_QUERY_RESERVATION_USD=<positive operator per-query estimate>
```

No provider price is embedded in code and no dollar values are assumed.
The per-query estimate is an **operator-set envelope**, **NOT** a
verified API price or a guarantee that the provider cannot charge more.
Do not enable until actual provider pricing, billing alerts and project
allowances have been reviewed. A logical query may cause multiple internal
web-search invocations, so monthly ceilings here bound **planned
reservations**, not an invoice.

## Atomic safeguards

1. Only the genuine `site:t.me` web fallback is gated; the existing
   registry → native Telethon → enabled TGStat checks run first.
2. Before calling the paid provider, a stable per-product UTC-day claim
   permits at most one query. Adaptive rounds and concurrent workers
   cannot bypass the claim.
3. An atomic monthly slot is reserved, sized at the configured
   per-query estimate. The month can have no more than
   `floor(monthly_budget / per_query_estimate)` reservations (maximum 31).
   There is no read-modify-write race in the store.
4. If either estimate is missing, zero, or otherwise invalid, no paid
   request is sent. An explicit denial reason is stored. Exhausted months
   are likewise fail-closed.
5. Failures **retain** the reservation because provider charges may have
   occurred before a timeout. No automatic refund or blind retry.
6. Successful responses record observed token/web-call counts when the
   provider exposes usage. `actual_billed_cost_usd` remains **null** until
   separately verified against actual billing.

## Operational diagnostics

`telegram_paid_discovery_daily_claim`: per-attempt status,
USD operator estimate, provider-error class or observed usage;
`telegram_paid_discovery_month_slot`: stable atomic project-month
reservations; `telegram_paid_discovery_denial`: reasons for
unconfigured/exhausted estimates.

States include `COST_ENVELOPE_NOT_CONFIGURED`,
`MONTHLY_BUDGET_INSUFFICIENT`, `MONTHLY_BUDGET_EXHAUSTED`,
`DAILY_QUERY_LIMIT`, `REQUEST_STARTED`,
`PROVIDER_FAILED_CHARGE_UNKNOWN`, `COMPLETED_BILLING_UNKNOWN`.

No LLM estimates actual billings. Reserving a dollar estimate does not
transfer funds or create an invoice. Provider-side hard account budgets and
reconciliation with billing data are still required to claim **actual
dollar-capped spending** across internal web-search calls.
This PR does not enable any paid requests or FemDom publications.
