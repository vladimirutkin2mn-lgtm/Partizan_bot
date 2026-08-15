# Next

## Active: real infrastructure proof for Partizan productionization (#121)

All repository-side production work is complete and merged to `main`:

- production Compose/runtime and migrations;
- fail-closed deployment workflow and smoke scripts;
- deny-by-default production control-plane authentication;
- host-local secret bootstrap and pre-mutation production preflight;
- database-backed worker heartbeats with post-restart verification;
- optional repository-managed Caddy HTTPS edge with real parser validation;
- Product Integration Kit, generic growth runner and isolated E2E sandbox.

There is no remaining honest repository-only implementation step for #121. The next unresolved work requires explicitly selected Partizan infrastructure:

1. provision/select a dedicated Partizan production host;
2. configure Partizan-specific GitHub deployment secrets (`DEPLOY_HOST`, `DEPLOY_SSH_KEY`, `DEPLOY_SSH_KNOWN_HOSTS`, `DEPLOY_PATH`);
3. run the host-local bootstrap and configure the host-owned `.env.prod` with intentionally enabled live providers;
4. choose a Partizan DNS hostname, point it to the host and allow inbound 80/443;
5. configure matching `PARTIZAN_PUBLIC_URL`, `PARTIZAN_PUBLIC_BASE_URL` and `PARTIZAN_PUBLIC_HOST`;
6. run the first real deploy + migrate from `main`;
7. pass public HTTPS `/health/live` and `/health/ready` smoke;
8. prove both recurring worker heartbeats are healthy after an actual deployment/restart;
9. only then start real-product dogfood #10.

Until those host/domain credentials are explicitly supplied, the production workflow intentionally records a safe skipped deployment: it does not start SSH, verify/mutate a host, migrate a database or deploy containers.

Before dogfood, connect the chosen product through the Product Integration Kit. If any external-product repository change is required, obtain explicit permission for that exact project/action before touching it.

`partizan-sandbox-run` remains available as a synthetic internal release proof but does not count as real dogfood or acquisition performance.

External products are dependencies, not implicit write targets.
