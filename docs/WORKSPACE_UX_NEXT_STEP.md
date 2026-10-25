# Workspace next step and pending reviews

This iteration makes Home answer three questions: what stage is the project at,
whose turn is it, and which review should the customer open next?

Approved community actions are surfaced on Home. The first action takes the
main next-step card; any other pending actions appear below it. Their buttons
forward to the existing native review dialog or channel setup navigation.
Opening them never confirms or publishes an action. Completed actions remain
in Results / History and leave the pending list after the existing refresh.

The starting-move and execution-request renderers supply presentation metadata
for channel choice, draft review, setup, preparation, final review and results.
Confirmation and operator approval remain distinct from completed execution.
The next-step dialog starts with the current card; "Review earlier steps" keeps
the other native controls accessible. Review opens at the content heading,
rather than focusing a publication or confirmation button.

All metrics still come from the existing workspace response. Source prototype
CSS is unchanged; layout additions are in `design.native.v1.css`. There are no
new customer API endpoints, database changes, payment changes or publication
permissions. Pending reviews reuse live DOM controls and are scoped to the
current project; stale refreshes cannot replace a newer project's review.

## Validation

`PYTHON=.venv/bin/python npm run test:design` covers native navigation, login,
metrics, channel choice, draft/setup stages, preparation/confirmation/approval/
execution states, pending action review, explicit publication confirmation,
refresh removal and project isolation. API transport is mocked throughout.

The focused Python workspace tests verify existing UI, asset-cache and mutation
contracts. DOM checks do not render CSS and are not visual approval.

## Offline visual review

```sh
PYTHON=.venv/bin/python node tools/preview_workspace_ux.cjs /tmp/Partizan_workspace_UX_preview.html
```

The generator loads actual FastAPI HTML and native scripts with synthetic GET
responses, then exports six DOM snapshots with inline product styles. Exported
snapshots contain only offline navigation controls: product scripts are removed
and there is no API transport, authentication, payment or publishing. The data
is explicitly labeled as demonstration data. The review file is not production.

Before merge, inspect the six states at desktop and mobile widths, open the
review dialogs, expand earlier steps, and check long text and focus visibility.
Use the authenticated product environment for the final integration check.
