---
name: Productionization task
title: "Production: "
about: Track Partizan-only production runtime work
labels: ""
assignees: ""
---

## Scope

Changes must remain inside `Partizan_bot` unless explicit permission is given for another repository.

## Goal


## Acceptance criteria

- [ ] production runtime change is fail-closed;
- [ ] no deployment secret is committed;
- [ ] migrations run before traffic is considered ready;
- [ ] liveness/readiness/smoke are validated;
- [ ] CI is green;
- [ ] external products are not modified.
