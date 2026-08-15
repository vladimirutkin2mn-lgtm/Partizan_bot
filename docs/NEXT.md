# Next

## Active: finish Partizan productionization (#121)

Code-side runtime, Product Integration Kit, generic growth runner and isolated E2E sandbox are complete.

The next unresolved work is real Partizan infrastructure:

1. configure a dedicated Partizan production host/environment and Partizan-specific deployment secrets;
2. complete the first real deploy + migrate + internal smoke from `main`;
3. configure `PARTIZAN_PUBLIC_URL` / `PARTIZAN_PUBLIC_BASE_URL`;
4. pass public HTTPS `/health/live` and `/health/ready` smoke;
5. verify API + workers survive a deployment/restart cycle;
6. only then start real-product dogfood.

Before dogfood, connect the chosen product through the Product Integration Kit. If any external-product repository change is required, obtain explicit permission for that exact project/action before touching it.

`partizan-sandbox-run` is available as a synthetic internal release proof but does not count as real dogfood or acquisition performance.

External products are dependencies, not implicit write targets.
