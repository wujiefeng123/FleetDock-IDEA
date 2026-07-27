# FleetDock IDEA upstream migration

## Invariants

- The legacy product tree at
  `D:\Git\ControlPlatform\application\jupyterlab-desktop` is read-only input.
- The official JupyterLab Desktop tree at `D:\Git\jupyterlab-desktop` is the
  migration target.
- Upstream dependency versions, build tooling, lock files, and Electron APIs
  remain authoritative.
- FleetDock-specific behavior is implemented in isolated modules and attached
  through small lifecycle hooks.
- Large generated runtimes and simulation binaries are not imported until
  their provenance, packaging need, and repository policy are reviewed.

## Fixed revisions

- Legacy upstream baseline: `701ae47` (JupyterLab 4.4.7)
- Migration target: `fdf1ddd` (JupyterLab Desktop 4.6.2-1 development head)
- Legacy product snapshot: `0baf4c7` plus its existing working tree

## Extension boundary

`src/main/extensions/session-extension.ts` defines a generic per-session
extension contract. `SessionWindow` owns the host and exposes only stable
capabilities:

- the owning Electron window;
- the session `EventManager`;
- read-only accessors for session/workspace state and renderer web contents;
- lifecycle notifications before local server creation and after welcome/lab
  renderer creation;
- deterministic extension disposal before the upstream session is destroyed.

FleetDock implementation code lives under `src/main/fleetdock` and related
domain directories. Core files must not contain device, simulation, publishing,
or FleetDock UI business logic.

## Migration layers

1. Generic extension host and a no-op FleetDock session adapter.
2. Branding and application/workspace services.
3. Device registry, persistence, probing, and sensor services.
4. UAV simulation process, scene, configuration, and media services.
5. FleetDock controllers and IPC registration behind the session adapter.
6. Command-center assets and WebContentsView-compatible renderer injection.
7. Minimal package/build resource configuration based on upstream
   `package.json`.
8. Static checks, unit/contract tests, Electron workflow checks, and packaging
   checks.

## Review gates

Each layer must:

- keep the legacy tree unchanged;
- preserve upstream dependency versions;
- avoid whole-file replacement of upstream core modules;
- keep IPC sender validation intact;
- scope process cleanup to resources created by FleetDock;
- pass the checks relevant to that layer before the next layer is accepted.

## Upstream update procedure

The FleetDock branch is based directly on the official `origin` remote. For a
future JupyterLab Desktop update:

```powershell
git fetch origin
git switch codex/fleetdock-upstream-migration
git merge --no-ff origin/master
```

Resolve conflicts without copying an older upstream file over the newer one.
Keep the new upstream implementation, then reapply only the small extension
hook or FleetDock configuration at the conflict site. The expected review
surface is intentionally limited to:

- `src/main/app.ts`, `src/main/main.ts`, and
  `src/main/sessionwindow/sessionwindow.ts` for lifecycle attachment;
- `src/main/eventtypes.ts`, the two upstream preload entry points, and
  `webpack.preload.js` for explicit IPC exposure;
- `package.json` and the installer/brand files for FleetDock packaging;
- the About dialog for the user-visible product name.

After every upstream merge:

1. Confirm `package.json` dependency and development-dependency versions and
   `yarn.lock` match the new upstream unless a separately reviewed FleetDock
   requirement exists.
2. Run `yarn type-check`, `yarn build`, the unit tests, and the FleetDock
   contract checks in `scripts/check-*`.
3. Inspect the diff from the new upstream revision. FleetDock business logic
   should remain in the isolated directories described above; a conflict must
   not move it into an upstream core module.
4. Build an unpacked application and manually verify application creation,
   device management, simulation start/stop, and command-center injection.

The packaged `skylab-runtime` file set deliberately excludes SDK tests,
examples, CI configuration, and Codex development helpers. Those files remain
in source control for SDK maintenance but are not copied into a FleetDock
desktop release.
