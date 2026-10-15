# FleetDock UI visual refresh

## Scope and recovery point

The visual refresh starts from commit `1df6615` on 2026-10-12. The same commit is
preserved on branch `codex/ui-baseline-20261012` and annotated tag
`fleetdock-ui-baseline-20261012`, both backed up to the team `origin` repository.
Development continues on `codex/ui-vscode-refresh`; the original product branch
is retained.

The target is the restrained visual language of VS Code: neutral surfaces,
clear text hierarchy, thin borders, small corner radii, and blue accents. Existing
buttons, navigation, view structure, IPC, device behavior, simulation behavior,
and user data remain outside the scope of the refresh. The concept mockup's
additional navigation, commands, or moved panels are not implementation targets.

## Restore the previous UI

Close the development application first. Check for uncommitted work:

```powershell
git status --short
```

When the working tree is clean, switch to the preserved baseline and rebuild:

```powershell
git switch codex/ui-baseline-20261012
corepack yarn build
corepack yarn start
```

This switches the source version without deleting the refresh branch or its
commits. Rebuilding is required because generated `build/` files are not managed
by Git. No bundled runtime reinstall or user-data cleanup is required for this
visual change.

To return to the refreshed source:

```powershell
git switch codex/ui-vscode-refresh
corepack yarn build
corepack yarn start
```

If the baseline branch is missing on another computer, recreate it from the tag
after fetching the team repository:

```powershell
git fetch origin --tags
git switch -c codex/ui-baseline-20261012 fleetdock-ui-baseline-20261012
```

Save or commit any uncommitted work before switching versions. Do not use a hard
reset or delete local application data to restore the UI. The tag preserves Git
source, not generated installers, dependencies, or runtime environments.

## Implemented appearance

- Shared light and dark color tokens across the welcome page, desktop titlebar,
  JupyterLab workspace, command center, real-device debugger, and standalone HUD.
- Neutral surfaces, thin borders, smaller corner radii, quieter shadows and
  typography, blue primary actions, and distinct success/warning/error states.
- Existing local desktop dialogs receive isolated CSS; the upstream shadow
  titlebar accepts generic color variables with its original fallback values.
- Existing renderer scripts, controls, IPC, navigation and layout dimensions are
  retained. Instrument graphics and model geometry remain unchanged.
- Dialog injection handles repeated attachment, navigation, destruction, failed
  insertion and late asynchronous completion without blocking dialog startup.

## Verification on 2026-10-12

- Type checking and the development build passed.
- Unit tests passed: 521 tests across 43 files, including six dialog lifecycle
  checks. End-to-end tests passed: seven tests; five Python environment cases were
  skipped because `JLAB_TEST_PYTHON_PATH` was not configured.
- Targeted ESLint and Prettier checks passed for changed files. CSS AST comparison
  confirmed that the five existing stylesheets retained their layout properties.
  No asset renderer JavaScript or IPC handler was changed.
- Device-manager and real-device-debugger page checks passed.
- Isolated Electron verification exercised welcome creation/cancellation,
  directory selection, application creation, JupyterLab file-browser loading,
  notebook opening, and the settings dialog's original close button. Light and
  dark screenshots were captured outside the repository.
- The preserved baseline was compiled in a separate temporary directory with the
  same dependencies, confirming that the recovery source remains buildable.

Notebook execution could not be validated: the isolated runtime lost its Jupyter
server connection when starting a kernel (`Failed to fetch`,
`ERR_CONNECTION_REFUSED`). The same failure was reproduced from the preserved
baseline through the same operation. It remains a runtime investigation rather
than a verified regression from this visual change. Real-device connections,
simulation startup/stop and a release installer were not exercised or rebuilt;
their implementation and bundled runtime were not changed.

After building and restarting, review both themes with a real workspace: open a
Notebook and terminal, check the existing device and simulation controls, and
open application settings. Confirm keyboard focus, disabled states and warning
messages. Treat successful kernel execution as pending until the runtime issue
is diagnosed separately.

The isolated dark workspace also reported a startup `removeChild` renderer
exception. The same exception was reproduced on the preserved baseline; file
loading and notebook opening still completed. Its source is not established by
this appearance task and should be investigated separately.

## Titlebar theme synchronization on 2026-10-15

JupyterLab can change its theme independently of the desktop's startup theme.
An isolated Lab observer now reports only theme-attribute changes through
`fleetdock:sync-titlebar-theme`. The session accepts boolean payloads only from
its current Lab WebContents and updates its own titlebar. Welcome views restore
the desktop theme; no persistent theme preference is overwritten.

The actual bundled preload was verified in Electron for initial dark rendering,
live light/dark switching, duplicate observer injection, rejection of foreign
senders and malformed payloads, observer/controller cleanup, and the original
close-button IPC. Unit tests passed (525 tests). This checks the same renderer
theme attributes used by JupyterLab without starting a Python server or changing
user settings. Restart the desktop application to load the updated preload.

The native BrowserWindow background is also synchronized with the titlebar
palette. The upstream non-macOS titlebar inset exposes a one-pixel strip of this
background, so leaving it at the startup color produces a light edge after
switching a light desktop session to a dark Lab theme. The inset and window
resizing geometry are retained.
An isolated real application session verified maximized dark/light/dark
switching and the native background color. A transparent root-renderer fixture
confirmed that the exposed backing surface follows the same palette.

## Maximized titlebar edge follow-up

The background-only change did not resolve the reported border on the user's
window. Maximized non-macOS windows now use a zero titlebar inset, covering the
top and side client edges directly. Restored windows retain the original
one-pixel inset for edge resizing. This small generic geometry correction is
marked in `SessionWindow`; no native frame flags or system settings are changed.

Build and 527 unit tests passed, including maximized/restored bounds on Windows
and Linux. Native capture of an isolated application is used to inspect the
composited window, since root WebContents captures do not include child views or
native window decorations. The user's original white line could not be reproduced
in that isolated session, so confirmation on the affected desktop remains pending.

## Initial workspace theme ordering

LabView previously reported readiness while desktop theme synchronization was
still pending. JupyterLab's theme command can resolve after saving its setting,
before the asynchronous theme load updates the body's theme attributes. This
allowed a persisted dark web theme to appear before a light desktop theme.

The view now remains hidden during initialization. Readiness waits for the theme
command and the target theme attributes; the attribute observer is cleaned up on
completion or a ten-second timeout. Failure logs a warning and reveals the web
theme instead of blocking the workspace. Disabled synchronization preserves the
saved web theme. Disposal/navigation guards prevent stale initialization from
revealing a different document. Folder opens keep their transition until this
readiness point, and FleetDock titlebar observers ignore intermediate themes.

The build, scoped lint/format checks, and 535 unit tests passed. An isolated real
Electron/JupyterLab session seeded with a dark saved theme and a light desktop
theme showed only the final light theme when LabView became visible. Tests use
temporary settings/workspaces; existing user preferences are not edited.
The opposite mismatch (saved light, desktop dark) also revealed only the final
dark theme. Disabling synchronization retained the saved dark theme despite a
light desktop preference. A stalled theme command has a ten-second startup limit
and falls back to revealing the web theme.
