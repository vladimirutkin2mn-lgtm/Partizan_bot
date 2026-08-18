# Production delivery contract

Partizan production deploys are expected to be fail-closed.

A successful `Deploy production` workflow means all of the following are true:

1. Production SSH deployment secrets are configured.
2. The exact source checked out by the workflow was synchronized to the host.
3. The production image was rebuilt and services restarted.
4. Health and worker probes passed.
5. The public `/start` route is serving the current customer onboarding HTML without cache reuse.
6. The public Goal dropdown CSS/JS bytes exactly match the release source.

If deployment credentials are missing, the deployment workflow must fail instead of reporting a successful skipped deployment.
