# [UAV-IDE MODIFY] Long-running IDE-owned Project AirSim client used by the UAV simulation supervisor.
import argparse
import json
import os
import signal
import sys


_running = True
_COMMAND_PROTOCOL_VERSION = 1
_COMMAND_RESULT_PREFIX = "SKYLAB_COMMAND_RESULT "
_COMMAND_CAPABILITIES = (
    "view.next",
    "simulation.pause.toggle",
    "weather.set",
    "trace.toggle",
    "markers.clear",
    "simulation.snapshot",
)


def _handle_stop(_signum, _frame):
    global _running
    _running = False


def _write_result(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as target:
        json.dump(payload, target, ensure_ascii=False, indent=2)


def _command_result(command, ok, code, message, data=None):
    result = {
        "id": str(command.get("id", "")),
        "runId": str(command.get("runId", "")),
        "action": str(command.get("action", "")),
        "ok": bool(ok),
        "code": code,
        "message": message,
    }
    if data is not None:
        result["data"] = data
    return result


def _available_capabilities(world, runtime_drones=None):
    required_methods = {
        "view.next": ("switch_streaming_view",),
        "simulation.pause.toggle": ("pause", "resume", "is_paused"),
        "weather.set": (
            "enable_weather_visual_effects",
            "disable_weather_visual_effects",
            "reset_weather_effects",
            "set_weather_visual_effects_param",
        ),
        "trace.toggle": ("toggle_trace",),
        "markers.clear": ("flush_persistent_markers",),
        "simulation.snapshot": ("get_sim_time", "is_paused"),
    }
    capabilities = [
        action
        for action, methods in required_methods.items()
        if all(callable(getattr(world, method, None)) for method in methods)
    ]
    if "simulation.snapshot" in capabilities and not all(
        callable(getattr(item.get("client"), "get_ground_truth_pose", None))
        for item in (runtime_drones or [])
    ):
        capabilities.remove("simulation.snapshot")
    return capabilities


def _pose_payload(pose):
    translation = pose.get("translation", {})
    rotation = pose.get("rotation", {})
    return {
        "frameId": str(pose.get("frame_id", "")),
        "translation": {
            "x": float(translation.get("x", 0.0)),
            "y": float(translation.get("y", 0.0)),
            "z": float(translation.get("z", 0.0)),
        },
        "rotation": {
            "w": float(rotation.get("w", 1.0)),
            "x": float(rotation.get("x", 0.0)),
            "y": float(rotation.get("y", 0.0)),
            "z": float(rotation.get("z", 0.0)),
        },
    }


def _execute_command(
    world, vehicles, command, expected_run_id="", runtime_drones=None
):
    if command.get("version") != _COMMAND_PROTOCOL_VERSION:
        return _command_result(
            command, False, "PROTOCOL_MISMATCH", "Unsupported command protocol."
        )
    if expected_run_id and str(command.get("runId", "")) != expected_run_id:
        return _command_result(
            command, False, "RUN_MISMATCH", "Command belongs to a different run."
        )
    action = str(command.get("action", ""))
    if action not in _COMMAND_CAPABILITIES:
        return _command_result(
            command, False, "UNSUPPORTED_ACTION", "Unsupported simulation command."
        )
    try:
        if action == "view.next":
            accepted = world.switch_streaming_view()
            message = "Streaming view switched."
            data = {"accepted": bool(accepted)}
        elif action == "trace.toggle":
            accepted = world.toggle_trace()
            message = "Trace visibility toggled."
            data = {"accepted": bool(accepted)}
        elif action == "markers.clear":
            accepted = world.flush_persistent_markers()
            message = "Persistent debug markers cleared."
            data = {"accepted": bool(accepted)}
        elif action == "simulation.pause.toggle":
            was_paused = bool(world.is_paused())
            if was_paused:
                world.resume()
            else:
                world.pause()
            paused = bool(world.is_paused())
            accepted = paused != was_paused
            message = "Simulation paused." if paused else "Simulation resumed."
            data = {"paused": paused}
        elif action == "weather.set":
            # [UAV-IDE MODIFY] Use official Project AirSim weather parameters so each preset is visible in-scene.
            from projectairsim.types import WeatherParameter

            params = command.get("params")
            params = params if isinstance(params, dict) else {}
            preset = str(params.get("preset", "")).strip().lower()
            weather_presets = {
                "clear": (),
                "rain": (
                    (WeatherParameter.RAIN, 0.7),
                    (WeatherParameter.ROAD_WETNESS, 0.5),
                ),
                "snow": (
                    (WeatherParameter.SNOW, 0.7),
                    (WeatherParameter.ROAD_SNOW, 0.5),
                ),
                "fog": ((WeatherParameter.FOG, 0.65),),
                "dust": ((WeatherParameter.DUST, 0.55),),
            }
            if preset not in weather_presets:
                return _command_result(
                    command, False, "INVALID_PARAMS", "Unsupported weather preset."
                )
            steps = [world.reset_weather_effects()]
            if preset == "clear":
                steps.append(world.disable_weather_visual_effects())
            else:
                steps.append(world.enable_weather_visual_effects())
                steps.extend(
                    world.set_weather_visual_effects_param(param, intensity)
                    for param, intensity in weather_presets[preset]
                )
            accepted = all(step is not False for step in steps)
            message = "Weather preset applied."
            data = {"weatherPreset": preset}
        else:
            accepted = True
            message = "Simulation snapshot returned."
            poses = []
            for runtime_drone in runtime_drones or []:
                pose_item = {
                    "droneId": runtime_drone.get("droneId", ""),
                    "vehicleName": runtime_drone.get("vehicleName", ""),
                }
                try:
                    pose_item["pose"] = _pose_payload(
                        runtime_drone["client"].get_ground_truth_pose()
                    )
                except Exception as exc:
                    pose_item["error"] = str(exc)
                poses.append(pose_item)
            data = {
                "simTimeNanos": int(world.get_sim_time()),
                "paused": bool(world.is_paused()),
                "vehicles": [
                    {
                        "droneId": vehicle.get("droneId", ""),
                        "vehicleName": vehicle.get("vehicleName", ""),
                        "loaded": bool(vehicle.get("loaded", False)),
                    }
                    for vehicle in vehicles
                ],
                "poses": poses,
            }
        if accepted is False:
            return _command_result(
                command, False, "API_REJECTED", "Project AirSim rejected the command."
            )
        return _command_result(command, True, "OK", message, data)
    except Exception as exc:
        return _command_result(command, False, "API_ERROR", str(exc))


def _serve_commands(world, vehicles, expected_run_id, runtime_drones=None):
    # [UAV-IDE MODIFY] Process one allowlisted command at a time on the client thread.
    while _running:
        line = sys.stdin.readline()
        if not line:
            break
        text = line.strip()
        if not text:
            continue
        try:
            command = json.loads(text)
            if not isinstance(command, dict):
                raise ValueError("Command payload must be an object.")
            response = _execute_command(
                world, vehicles, command, expected_run_id, runtime_drones
            )
        except Exception as exc:
            response = _command_result({}, False, "INVALID_REQUEST", str(exc))
        print(
            _COMMAND_RESULT_PREFIX
            + json.dumps(response, ensure_ascii=True, separators=(",", ":")),
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Load and hold a Project AirSim world.")
    parser.add_argument("--launch", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--runtime-root", required=True)
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    with open(args.launch, "r", encoding="utf-8") as source:
        launch = json.load(source)

    # [UAV-IDE MODIFY] Load the IDE transport package and the same pinned FleetDock source used by kernels.
    runtime_root = os.path.abspath(args.runtime_root)
    fleetdock_root = os.path.join(runtime_root, "fleetdock-src")
    for import_root in (runtime_root, fleetdock_root):
        if import_root not in sys.path:
            sys.path.insert(0, import_root)

    scene_file = launch["files"]["sceneFile"]
    # [UAV-IDE MODIFY] Project AirSim resolves robot-config paths from sim_config_path, so point it at the generated run directory.
    scene_config_dir = os.path.dirname(scene_file)
    scene_config_name = os.path.basename(scene_file)
    drones = launch.get("drones", [])
    result = {
        "ok": False,
        "sceneFile": scene_file,
        "protocolVersion": _COMMAND_PROTOCOL_VERSION,
        "capabilities": [],
        "vehicles": [],
        "sensorService": {"available": False},
        "error": "",
    }

    manifest_path = str(launch.get("files", {}).get("sensorManifestFile", ""))
    if manifest_path:
        _write_result(
            manifest_path,
            {
                "active": False,
                "runId": str(launch.get("runId", "")),
                "protocolVersion": 1,
            },
        )

    client = None
    sensor_service = None
    rtsp_bridges = []

    try:
        from projectairsim import ProjectAirSimClient
        from projectairsim.utils import projectairsim_log
        from projectairsim.world import World
        from projectairsim.drone import Drone
        from fleetdock_ide_runtime.sensor_service import (
            SdkSensorService,
            create_projectairsim_backends,
        )
        from fleetdock import ProjectAirSimRtspBridge, SensorKind

        projectairsim_log().info("FleetDock IDEA UAV simulation client connecting.")
        client = ProjectAirSimClient()
        client.connect()
        world = World(
            client,
            scene_config_name,
            delay_after_load_sec=2,
            sim_config_path=scene_config_dir,
        )
        loaded_drones = []
        runtime_drones = []
        for drone in drones:
            vehicle_name = drone["vehicleName"]
            drone_client = Drone(client, world, vehicle_name)
            loaded_drones.append(drone_client)
            runtime_drones.append(
                {
                    "droneId": drone["droneId"],
                    "vehicleName": vehicle_name,
                    "cameraStreamUri": str(drone.get("cameraStreamUri", "")),
                    "cameraStreams": list(drone.get("cameraStreams", [])),
                    "client": drone_client,
                }
            )
            result["vehicles"].append(
                {
                    "droneId": drone["droneId"],
                    "vehicleName": vehicle_name,
                    "systemId": drone["systemId"],
                    "px4Instance": drone["px4Instance"],
                    "simulatorPort": drone["simulatorPort"],
                    "mavlinkPort": drone["mavlinkPort"],
                    "loaded": True,
                }
            )
        result["capabilities"] = _available_capabilities(world, runtime_drones)

        try:
            # [UAV-IDE MODIFY] Activate the official streaming view before waiting for SDK camera frames.
            world.switch_streaming_view()
        except Exception as exc:
            projectairsim_log().warning(
                "Unable to switch Project AirSim streaming view: %s", exc
            )

        # [UAV-IDE MODIFY] SDK adapters own discovery and conversion while every robot shares the IDE-owned client.
        backends = create_projectairsim_backends(
            client, [item["vehicleName"] for item in runtime_drones]
        )
        rtsp_streams = []
        for runtime_drone in runtime_drones:
            vehicle_name = runtime_drone["vehicleName"]
            descriptors = {
                descriptor.sensor_id: descriptor
                for descriptor in backends[vehicle_name].list_sensors()
                if descriptor.kind is SensorKind.RGB_CAMERA
            }
            camera_streams = runtime_drone.get("cameraStreams") or []
            if not camera_streams and descriptors:
                sensor_id = next(iter(descriptors))
                camera_streams = [{
                    "id": f"{vehicle_name}:{sensor_id}",
                    "sensorId": sensor_id,
                    "label": sensor_id,
                    "streamUri": runtime_drone.get("cameraStreamUri", ""),
                }]
            for camera in camera_streams:
                sensor_id = str(camera.get("sensorId", ""))
                if sensor_id not in descriptors:
                    projectairsim_log().warning(
                        "FleetDock RGB camera is not available: vehicle=%s sensor=%s",
                        vehicle_name,
                        sensor_id,
                    )
                    continue
                bridge = ProjectAirSimRtspBridge(
                    robot_name=vehicle_name,
                    sensor_id=sensor_id,
                    rtsp_uri=str(camera.get("streamUri", "")) or None,
                    projectairsim_client=client,
                )
                stream_uri = bridge.start()
                rtsp_bridges.append(bridge)
                rtsp_streams.append({
                    "id": str(camera.get("id", f"{vehicle_name}:{sensor_id}")),
                    "vehicleName": vehicle_name,
                    "sensorId": sensor_id,
                    "label": str(camera.get("label", sensor_id)),
                    "uri": stream_uri,
                })
            for vehicle_result in result["vehicles"]:
                if vehicle_result.get("vehicleName") == vehicle_name:
                    vehicle_result["cameraStreams"] = [
                        stream for stream in rtsp_streams
                        if stream["vehicleName"] == vehicle_name
                    ]
                    break
        sensor_service = SdkSensorService(
            run_id=str(launch.get("runId", "")),
            backends=backends,
            manifest_path=manifest_path,
            rtsp_streams=rtsp_streams,
        )
        result["sensorService"] = sensor_service.start()

        result["ok"] = True
        _write_result(args.result, result)
        _serve_commands(
            world,
            result["vehicles"],
            str(launch.get("runId", "")),
            runtime_drones,
        )

        return 0
    except Exception as exc:
        result["error"] = str(exc)
        for drone in drones:
            result["vehicles"].append(
                {
                    "droneId": drone.get("droneId", ""),
                    "vehicleName": drone.get("vehicleName", ""),
                    "loaded": False,
                    "error": str(exc),
                }
            )
        _write_result(args.result, result)
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        # [UAV-IDE MODIFY] Release SDK subscriptions before disconnecting the sole Project AirSim client.
        if sensor_service is not None:
            try:
                sensor_service.close()
            except Exception:
                pass
        for bridge in reversed(rtsp_bridges):
            try:
                bridge.close()
            except Exception:
                pass
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
