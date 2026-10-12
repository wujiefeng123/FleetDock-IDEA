# FleetDock IDEA collaboration

The team repository preserves the JupyterLab Desktop Git history. Use `origin`
for FleetDock collaboration and `upstream` for official updates:

| Remote     | Repository                                           | Purpose                         |
| ---------- | ---------------------------------------------------- | ------------------------------- |
| `origin`   | https://github.com/wujiefeng123/FleetDock-IDEA.git   | Team branches and pull requests |
| `upstream` | https://github.com/jupyterlab/jupyterlab-desktop.git | Official updates                |

The shared product branch is `codex/fleetdock-upstream-migration`. The local
`master` branch is an upstream reference, not the FleetDock product branch.

## Join the project

```powershell
git clone --branch codex/fleetdock-upstream-migration https://github.com/wujiefeng123/FleetDock-IDEA.git
cd FleetDock-IDEA
git remote add upstream https://github.com/jupyterlab/jupyterlab-desktop.git
git remote -v
```

Read `AGENTS.md`, `dev.md`, and `FLEETDOCK_MIGRATION.md` before making changes.
Each collaborator should use a separate local clone. Remote configuration is
local to each clone; adding `upstream` on one computer does not configure it for
other collaborators.

## Develop a feature

Start with a clean working tree. Replace `device-manager-improvement` with a
short name for the work:

```powershell
git switch codex/fleetdock-upstream-migration
git pull --ff-only origin codex/fleetdock-upstream-migration
git switch -c codex/device-manager-improvement
```

Make focused commits, stage explicit file paths, and run the relevant checks.
Push the feature branch and open a pull request targeting
`codex/fleetdock-upstream-migration`:

```powershell
git push -u origin codex/device-manager-improvement
```

Configure the product branch as the GitHub default branch and protect it with
pull-request review. Avoid force pushes and history rewrites on shared branches.
Keep generated installers, environments, previews, credentials, and staged
QGroundControl payloads out of commits. Existing upstream workflows target
`master`; FleetDock CI and release automation require a separate review before
being enabled for the product branch.

## Integrate an official update

One maintainer should coordinate each update. Start from the latest team branch
with a clean working tree. Replace the example upgrade name with a unique name:

```powershell
git switch codex/fleetdock-upstream-migration
git pull --ff-only origin codex/fleetdock-upstream-migration
git fetch upstream
git switch -c codex/upgrade-jupyterlab-next
git merge --no-ff upstream/master
```

Use the actual official default branch if it changes. Preserve new upstream
implementations when resolving conflicts, then reapply the minimal extension
hooks. Update the revisions and validation record in `FLEETDOCK_MIGRATION.md`.
Follow its merge checks and the relevant checks in `AGENTS.md`, then push the
upgrade branch and open a pull request to the product branch:

```powershell
git push -u origin codex/upgrade-jupyterlab-next
```

Do not push FleetDock commits to `upstream`. Fetching official updates does not
change the working tree; merging them is a separate, reviewed operation.
