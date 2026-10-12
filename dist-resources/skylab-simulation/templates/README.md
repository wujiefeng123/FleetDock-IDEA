# FleetDock IDEA UAV Simulation Templates

[UAV-IDE MODIFY] This directory is reserved for packaged Project AirSim scene and robot templates used by the isolated UAV simulation subsystem.

The first implementation generates per-run `scene.jsonc` and `robot-drone-*.jsonc` files dynamically under the current workspace:

```text
.jupyter/simulation/runs/<runId>/
```

The packaged PX4 SITL contracts are:

| FleetDock airframe |                            PX4 POSIX autostart | Project AirSim template           |                    Controller outputs |
| ------------------ | ---------------------------------------------: | --------------------------------- | ------------------------------------: |
| Quadrotor          |                                        `10016` | `robot_quadrotor_px4.jsonc`       |                              4 rotors |
| Hexarotor          | `10016` plus `PX4_PARAM_*` allocator overrides | `robot_hexarotor_px4.jsonc`       |                              6 rotors |
| VTOL tailsitter    |                                         `1041` | `robot_vtol_tailsitter_px4.jsonc` |      4 rotors, unused slot, 2 elevons |
| VTOL tiltrotor     |                                         `1042` | `robot_vtol_tiltrotor_px4.jsonc`  | 4 rotors, 2 tilts, 3 control surfaces |

FleetDock launches the existing `px4_sitl_default` binary. Selecting a vehicle
does not modify PX4 sources or build a new binary. The generator rejects a
template whose rotor count or actuator order differs from its profile before
starting Project AirSim.
