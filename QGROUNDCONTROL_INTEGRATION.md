# QGroundControl integration

FleetDock IDEA launches QGroundControl (QGC) as a separate Windows application. The right sidebar **地面站** tab discovers the bundled executable, opens QGC, and reports the current simulation's QGC MAVLink output. QGC itself is not modified. Its internal pages are selected in QGC; this integration does not promise deep links to Fly, Plan, or Vehicle Setup.

## Windows layout and packaging

- Development: `dist-resources/win/lib/QGroundControl/bin/QGroundControl.exe`.
- Installed FleetDock IDEA: `<IDE installation>/lib/QGroundControl/bin/QGroundControl.exe`.
- A user-selected external `QGroundControl.exe` can override discovery. The selection is stored in the IDE user data directory as `groundstation.json`. If that file is missing or its executable disappears, discovery falls back to the bundled copy and then standard Program Files directories.
- QGC is staged automatically from the pinned official installer with `corepack yarn stage_qgroundcontrol:win-64`. A verified existing stage is reused. On a fresh machine the script downloads the installer and a pinned 7-Zip extractor, verifies both SHA-256 digests, and extracts the NSIS payload through the project's WSL distro. It does not run the QGC installer or install drivers. The staged directory is ignored by Git and copied by electron-builder into the Windows package. QGC and its helper executables are excluded from FleetDock's code-signing pass to preserve their staged checksums. Do not commit the staged payload.
- The pinned upstream Windows x64 release is recorded in `dist-resources/qgroundcontrol-release.json`. An optional `QGC_SOURCE_DIR` can point to a previously extracted copy, but then `QGC_SOURCE_VERSION=5.1.4` and `QGC_INSTALLER_PATH` must also be supplied; the script verifies the installer SHA-256. The automated path is preferred because it ties the extracted payload directly to the verified download. The project's separate formal release command retains its existing signing and non-RC gates; neither is needed to build or test the QGC integration. Review QGC and bundled third-party license notices before external distribution.

## MAVLink path

For each managed PX4 vehicle, the supervisor configures the existing SDK output to its assigned UDP listener in `14600–14699`, then configures a second PX4 MAVLink output with a distinct local port `14760 + PX4 instance` to the Windows host's UDP `14550`. QGC's UDP auto-connect must be enabled. An unavailable QGC output is shown in the status and log but does not fail the SDK simulation launch. The IDE does not claim exclusive QGC ownership, close QGC when the IDE exits, or arbitrate flight commands between QGC and SDK.

The status `已配置 N 路输出` means the PX4 output commands succeeded. It does not prove that QGC received telemetry. Check QGC's vehicle list and HUD for an end-to-end result. Serial hardware access and flight-control authority policy are outside this phase.

## Acceptance checks

1. Build an unpacked Windows app and check `<unpacked>/lib/QGroundControl/bin/QGroundControl.exe` and `fleetdock-qgc-manifest.json` exist. Confirm the executable hash matches the manifest and the license file is present.
2. In the IDE, select **地面站**. Confirm detection, path, version label, **打开 QGroundControl**, **选择程序**, and **打开目录**. Launch twice and verify no second child is started while the first IDE-launched instance lives. A missing executable should give a clear selection path without blocking the Lab workspace.
3. With QGC open and UDP auto-connect active, start one PX4 simulation. Verify QGC shows that vehicle and the SDK still receives telemetry at its assigned `14600–14699` endpoint. Repeat with two vehicles and verify distinct System IDs, two vehicles in QGC, and independent SDK endpoints.
4. Stop simulation. Confirm the IDE-owned PX4 processes and their outputs stop; QGC remains open. Run again with QGC closed and with 14550 unavailable; SDK simulation must still work while the ground-station status reports the link result.
5. Repeat checks 2–4 in an installed Windows package. Review ownership and command behavior separately before enabling simultaneous SDK and QGC flight control on real hardware.

## Completed local evidence

The pinned QGC 5.1.4 AMD64 installer was downloaded and matched the release SHA-256 `56f5f943df5eb878a67ad73d81fdd8b3d823c60535fe1cc08410545108a49d20`. Its NSIS payload was extracted into an isolated build input, staged with version `5.1.4`, packaged, and launched successfully.

A PX4 SIH instance with System ID 91 was used to exercise the exact dual-output command shape. The SDK listener on UDP 14600 received 1,366 packets / 56,625 bytes from PX4 local port 14750. The QGC output on UDP 14550 received 318 MAVLink 2 packets from local port 14850, all carrying System ID 91. QGC created `ParamCache/91_1.v2`, confirming that it discovered the vehicle and completed parameter retrieval. The test also found that this bundled PX4 rejects `-m normal`; omitting the mode selects its Normal default, so the production command was corrected accordingly. Test-owned QGC and PX4 processes were stopped after verification.
