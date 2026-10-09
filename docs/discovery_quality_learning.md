# Acquisition discovery quality learning

Partizan discovery is deliberately broad, but broad recall must not turn into unrelated acquisition targets. The production acquisition loop therefore applies three reusable safeguards after public research and before execution planning.

## Multi-signal relevance gate

A candidate is kept only when observed public evidence supports the customer ICP through more than a single generic keyword. The gate uses the existing research-signal families: ICP fit, pain, trigger, alternatives, demand/commercial intent, independent evidence count and confidence. Search-query text remains provenance and is not treated as evidence.

This gate is generic. It reads the current product and ICPs; it contains no FemDom- or customer-specific topic list.

## Negative discovery learning

Rejected candidates feed product-scoped negative memory for 30 days.

- An exact platform/community candidate is suppressed only after two separate weak observations. A single weak post does not permanently hide a community that later produces strong evidence.
- If the same community is weak for one ICP but valid for another ICP in the same discovery batch, Partizan does not learn it as a negative community.
- Theme terms become negative only after appearing across at least two independently rejected communities.
- Product and ICP vocabulary is excluded from negative-theme learning so the system does not learn away the customer's own category.
- Negative theme memory is applied before adaptive queries are built, so later search rounds stop spending query budget on hypotheses already learned to be irrelevant.
- Memory is isolated by product, so learning for one customer's acquisition job does not leak into another customer's discovery.

The adaptive Telegram loop can therefore broaden its search while using failure evidence to become narrower and more relevant over subsequent rounds.

## Telegram community restriction memory

A successful read-only membership preflight is not stronger than a real failed write. If Telegram returns a hard write restriction such as `ACCOUNT_BANNED_IN_COMMUNITY` or `WRITE_FORBIDDEN`, Partizan records that restriction for the specific customer project and public community handle.

Future preflight checks for any post under that handle immediately return `NO_WRITE_ACCESS`. Read-only access checks also classify direct Telegram ban/write-forbidden RPC signals as `NO_WRITE_ACCESS` instead of a generic precheck failure. No join or publish is attempted by this check. Restrictions are project-scoped because different customers may connect different Telegram accounts.

The restriction can be explicitly cleared by an operator if the account is later unbanned.
