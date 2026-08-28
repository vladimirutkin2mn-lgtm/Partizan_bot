# Security Policy

Partizan may handle customer account sessions, product/project data, provider connections,
attribution events and acquisition-budget controls. Security reports should therefore avoid
public disclosure of credentials or customer data.

## Reporting a vulnerability

1. Prefer GitHub's private vulnerability-reporting path on the repository **Security** tab
   when it is available to you.
2. Do **not** put access tokens, passwords, payment information, private customer data,
   exploit payloads or reproducible sensitive details in a normal public issue.
3. If private reporting is unavailable, open a minimal public issue requesting private
   security follow-up without including the sensitive details.

Security page: https://partizanlabs.com/security

## Important execution boundary

A connected account or funded acquisition budget is not, by itself, authorization to spend.
Paid execution must remain fail-closed unless the required integration, project limits,
channel permissions, settlement/spend rail and reconciliation checks are ready.

## Scope

Reports involving the Partizan repository, customer workspace, authentication/session
ownership, provider credentials, execution safety, attribution integrity or billing/spend
controls are in scope.

Partizan does not currently claim SOC 2, ISO 27001 or a completed independent penetration
test unless and until those claims are explicitly published with supporting evidence.
