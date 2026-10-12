# Initial FleetDock collaboration baseline

This record documents the source baseline prepared on 2026-10-12 for
`https://github.com/wujiefeng123/FleetDock-IDEA.git`. It is a collaboration
snapshot, not a release acceptance report.

## Source and remotes

- Product branch: `codex/fleetdock-upstream-migration`.
- Previous FleetDock HEAD: `1aa4967`.
- Official migration base remains `fdf1ddd`; no official update was merged
  while publishing this baseline.
- `origin` is the team repository; `upstream` is official JupyterLab Desktop.
- Existing SDK, desktop, simulation, branding, dependency, and QGroundControl
  source changes are captured in separate source-domain commits.
- Local delivery reports, spreadsheet previews, the untracked repository ZIP,
  generated dependencies/builds, and the unreviewed external UE helper skill
  are excluded from new commits and remain on the local machine.
- Existing Git history is preserved, including the already tracked MediaMTX
  binary (with its source/license record) and an older repository ZIP blob.
  Initial clones therefore include those historical objects.

## Validation

| Check                                                                                                                                                                    | Result                                                                                        |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| Desktop TypeScript `tsc --noEmit`                                                                                                                                        | Passed                                                                                        |
| Desktop `corepack yarn build`                                                                                                                                            | Passed                                                                                        |
| Desktop unit tests                                                                                                                                                       | 515 passed in 42 files                                                                        |
| Electron end-to-end tests                                                                                                                                                | 7 passed; 5 skipped because `JLAB_TEST_PYTHON_PATH` was unset                                 |
| Core Python SDK tests                                                                                                                                                    | 319 tests, OK with 1 skipped                                                                  |
| Python WS server tests                                                                                                                                                   | 39 tests; 1 failed, listed below                                                              |
| JS client `npm ci` followed by `npm test`                                                                                                                                | SDK/web builds passed; 10 tests passed                                                        |
| Desktop ESLint (`src`, `scripts`, `test`)                                                                                                                                | Passed                                                                                        |
| Whole-repository ESLint                                                                                                                                                  | Failed on 4 errors in the independent JS package; also emitted warnings                       |
| Whole-repository Prettier                                                                                                                                                | Failed on existing formatting and unsupported inline type-import syntax in the JS web example |
| New collaboration documents and preload API test formatting                                                                                                              | Passed                                                                                        |
| Physical connection, probe contract, serial discovery, device-manager page, real-device debugger page, Project AirSim sensor, runtime binding, and sensor service checks | Passed                                                                                        |

The Python checks used the repository's documented `fleetdock` Conda environment.
The JS client was tested with its own locked dependencies. Its new Vitest config
prevents accidental inheritance of the desktop's test include/setup rules. The
desktop preload API test now includes the existing `closeGroundStation` API.

## Known limitations and follow-up

1. `test_python_ui_controls_mock_drone_only_through_websocket` fails because
   the mock drone's final X position is `0.0`, while the test expects movement.
   The failure reproduced in an isolated run. Investigate the controller's
   asynchronous command/input lifecycle and test synchronization before treating
   the graphical WS controller as validated.
2. Root ESLint applies desktop interface/import conventions to the separately
   published JS package. Its existing public names and web example produce four
   errors. Review package-specific lint policy without silently changing public
   SDK names merely to satisfy desktop rules.
3. Root Prettier 2.1 cannot parse the web example's inline `type` imports. There
   are also existing formatting warnings in desktop source/test files and the
   demo-template README. Establish a reviewed formatting boundary for independent
   packages and local artifact folders; no whole-repository reformat was performed.
4. Notebook execution and ipywidgets integration need the five skipped Electron
   tests rerun with a valid `JLAB_TEST_PYTHON_PATH` pointing to an environment with
   JupyterLab.
5. No installer, unpacked release, real hardware, or live PX4/Project AirSim/
   QGroundControl flight workflow was built or exercised for this publication.
   Follow `QGROUNDCONTROL_INTEGRATION.md` and `AGENTS.md` before release acceptance.
6. Upstream GitHub workflows currently target `master`. Adapt CI for the product
   branch in a separate change. Configure collaborator access and branch review
   rules in GitHub according to team membership and the repository plan.
