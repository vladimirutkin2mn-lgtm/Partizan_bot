# Repository boundary

Partizan may depend on external products for dogfood, conversion delivery or provider setup, but that dependency does not authorize changes outside this repository.

## Rule

While working on Partizan:

- read/write scope is `vladimirutkin2mn-lgtm/Partizan_bot` by default;
- external repositories are treated as immutable dependencies;
- before modifying, deploying, migrating or opening a PR in another repository, obtain explicit owner permission for that exact external project/action;
- if permission is absent, record the dependency/blocker in Partizan and continue with work that can be completed inside Partizan.

## Integration design consequence

Partizan should expose self-service contracts instead of requiring its developers to patch customer products:

- public tracking URLs;
- server-to-server conversion Event Keys;
- documented `SIGNUP`, `ACTIVATED`, `PAID` semantics;
- integration verification;
- SDK/snippet guidance;
- explicit provider/account configuration.

This boundary applies equally to dogfood products owned by the same person.
