"""PX4 多机编队示例；展示队形槽位、跟随执行和 Project AirSim 观察视角。"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
import time

from _projectairsim_logging import configure_projectairsim_console_logging

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = REPO_ROOT.parent

from fleetdock import (
    DeviceFactory,
    DeviceMetadata,
    Drone,
    Fleet,
    FormationLayout,
    Plan,
    Route,
    SafetyAction,
    SafetyPolicy,
    Waypoint,
)

from drone_plan import TAKEOFF_ALTITUDE_M, TASK_ROUTE_MOTION
from fleet_plan import (
    FleetDemoSubscriptions,
    configure_drone_plan,
    ensure_connect_ok,
    prepare_and_takeoff_fleet,
    print_route_parameters,
    print_task_result,
    print_task_step,
)


DEFAULT_DRONES = (
    ("drone-a", "A-队形 leader", "udpin://0.0.0.0:14555"),
    ("drone-b", "B-左后 follower", "udpin://0.0.0.0:14556"),
    ("drone-c", "C-右后 follower", "udpin://0.0.0.0:14557"),
)
FORMATION_SPACING_M = 8.0
FORMATION_SPEED_M_S = 3.5
PROJECTAIRSIM_SCENE_ID = "SceneFleetDock3Drones"
PROJECTAIRSIM_DRONE_NAMES = ("Drone1", "Drone2", "Drone3")
PROJECTAIRSIM_SCENE_WAIT_TIMEOUT_S = 90.0
PROJECTAIRSIM_SCENE_WAIT_INTERVAL_S = 2.0


def wait_for_projectairsim_multi_scene(timeout_s: float = PROJECTAIRSIM_SCENE_WAIT_TIMEOUT_S) -> None:
    """等待 Project AirSim 当前世界加载三机队形 demo 所需的三个机器人。

    该检查只读取仿真服务器 topic，不会主动加载或切换场景。`examples/run_dev_multi.bat`
    会异步准备 Project AirSim 多机场景；如果 demo 启动得更快，这里会等待 `Drone1`、
    `Drone2` 和 `Drone3` 的真实位姿 topic 出现，避免脚本在默认单机场景中继续执行。
    """
    projectairsim_src_dir = Path(
        os.environ.get(
            "PROJECTAIRSIM_SRC_DIR",
            WORKSPACE_ROOT / "ProjectAirSim" / "client" / "python" / "projectairsim" / "src",
        )
    )
    if projectairsim_src_dir.exists() and str(projectairsim_src_dir) not in sys.path:
        sys.path.insert(0, str(projectairsim_src_dir))

    configure_projectairsim_console_logging(logging.ERROR)
    try:
        from projectairsim import ProjectAirSimClient
    except ImportError as exc:
        raise SystemExit(
            "无法导入 Project AirSim Python client。请先使用包含 Project AirSim 依赖的 fleetdock "
            "conda 环境运行本 demo。"
        ) from exc

    expected_topics = {
        f"/Sim/{PROJECTAIRSIM_SCENE_ID}/robots/{drone_name}/actual_pose"
        for drone_name in PROJECTAIRSIM_DRONE_NAMES
    }
    deadline = time.monotonic() + timeout_s
    last_pose_topics: tuple[str, ...] = ()
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        client = ProjectAirSimClient()
        try:
            client.connect()
            client.get_topic_info()
            topics = set(client.topics)
            last_pose_topics = tuple(sorted(topic for topic in topics if "/robots/" in topic and "actual_pose" in topic))
            if expected_topics.issubset(topics):
                print("[Project AirSim] 已确认三机场景 SceneFleetDock3Drones。", flush=True)
                return
        except Exception as exc:  # pragma: no cover - depends on local simulator state.
            last_error = exc
        finally:
            try:
                client.disconnect()
            except Exception:
                pass
        time.sleep(PROJECTAIRSIM_SCENE_WAIT_INTERVAL_S)

    if last_pose_topics:
        current_topics = "\n  ".join(last_pose_topics)
        detail = f"当前 actual_pose topics:\n  {current_topics}"
    elif last_error is not None:
        detail = f"最后一次连接 Project AirSim 失败：{last_error}"
    else:
        detail = "没有发现任何机器人 actual_pose topic。"
    raise SystemExit(
        "Project AirSim 尚未加载三机队形场景，已停止 demo，避免只在单机场景中驱动 PX4。\n"
        "请先运行 examples\\run_dev_multi.bat，并等待 prepare projectairsim drones 窗口完成。\n"
        f"{detail}"
    )


def build_demo_drone(device_id: str, name: str, endpoint: str) -> Drone:
    """创建一架连接 PX4 SITL 的队形跟随示例无人机。"""
    return (
        DeviceFactory.create()
        .px4_generic()
        .params(endpoint=endpoint, timeout_s=30.0)
        .metadata(DeviceMetadata(device_id=device_id, name=name, tags=("formation-demo",)))
        .build()
    )


def build_fleet() -> Fleet:
    """创建与 ``examples/run_dev_multi.bat`` 默认端口对应的 A/B/C 三机 Fleet。"""
    return Fleet(
        [build_demo_drone(device_id, name, endpoint) for device_id, name, endpoint in DEFAULT_DRONES],
        name="PX4 三机队形跟随示例",
    )


def formation_safety() -> SafetyPolicy:
    """返回队形跟随示例使用的安全策略。"""
    return SafetyPolicy(
        max_relative_altitude_m=80.0,
        max_distance_from_leader_m=120.0,
        # 当前 PX4 多实例的本地坐标原点可能随各机 Home 点变化，demo 先不启用跨机本地间距判断。
        min_separation_m=None,
        telemetry_timeout_s=3.0,
        battery_return_fraction=0.18,
        battery_emergency_fraction=0.08,
        action_on_violation=SafetyAction.HOVER,
    )


def leader_route() -> Route:
    """返回 leader 执行的短距离本地航线，followers 由 FollowSession 保持 V 字队形。"""
    return Route.polyline(
        (
            Waypoint(x_m=0.0, y_m=0.0),
            Waypoint(x_m=24.0, y_m=0.0),
            Waypoint(x_m=24.0, y_m=18.0),
            Waypoint(x_m=0.0, y_m=18.0),
        ),
        name="formation-leader-box",
    )


def print_formation_session(session) -> None:
    """打印队形槽位分配和当前 follower 策略，便于确认 layout 已由 SDK 生成策略。"""
    print("[队形槽位]", flush=True)
    for assignment in session.assignments:
        print(
            f"  slot={assignment.slot_id} device={assignment.device_id} state={assignment.state.value}",
            flush=True,
        )
    for follower in session.followers:
        policy = session.policy_for(follower)
        print(
            f"  follower={follower.metadata.device_id} offset=({policy.x_m:.1f}, {policy.y_m:.1f}, {policy.z_m:.1f}) m",
            flush=True,
        )


def main() -> None:
    """运行 PX4 SITL 三机队形跟随 Demo。"""
    wait_for_projectairsim_multi_scene()
    fleet = build_fleet()
    leader, drone_b, drone_c = fleet.devices
    session = fleet.follow(
        leader=leader,
        followers=[drone_b, drone_c],
        layout=FormationLayout.v_shape(
            spacing_m=FORMATION_SPACING_M,
            count=2,
            altitude_step_m=1.0,
        ),
        safety_policy=formation_safety(),
    )
    print_formation_session(session)

    try:
        with FleetDemoSubscriptions(fleet):
            connect_result = fleet.connect()
            ensure_connect_ok(connect_result)
            if not prepare_and_takeoff_fleet(fleet, altitude_m=TAKEOFF_ALTITUDE_M):
                raise SystemExit(1)

            session.start(
                interval_s=0.2,
                default_max_speed_m_s=FORMATION_SPEED_M_S,
                stop_action="hover",
            )
            print(
                f"[队形跟随参数] interval=0.2 s speed={FORMATION_SPEED_M_S:.1f} m/s "
                f"spacing={FORMATION_SPACING_M:.1f} m",
                flush=True,
            )
            route = leader_route()
            print_route_parameters(
                route,
                label="leader 队形航线",
                altitude_m=TAKEOFF_ALTITUDE_M,
                motion_mode="auto",
            )
            route_result = configure_drone_plan(
                Plan(leader, interceptors=[print_task_step])
            ).route(
                route,
                altitude=TAKEOFF_ALTITUDE_M,
                motion=TASK_ROUTE_MOTION,
                timeout_s=120.0,
            ).run()
            print("[队形 leader 航线结果]")
            print_task_result(route_result)
            follow_status = session.status()
            print(f"[队形跟随状态] state={follow_status.state.value} message={follow_status.message}", flush=True)
            for follower_id, follower_state in follow_status.followers.items():
                print(
                    f"  follower={follower_id} mode={follower_state.mode.value} "
                    f"telemetry_ok={follower_state.telemetry_ok} "
                    f"distance_to_leader={follower_state.distance_to_leader_m} "
                    f"command_failures={follower_state.command_failures} "
                    f"message={follower_state.message}",
                    flush=True,
                )

            session.transition_to(FormationLayout.line(spacing_m=10.0, count=2), duration_s=5.0)
            print("[队形切换] 已从 V 字队形切换到横队目标。", flush=True)

        rtl_result = fleet.drones().return_to_launch()
        print(f"[Fleet 返航结果] {rtl_result.message}", flush=True)
    finally:
        session.stop()
        print(f"\n队形跟随已停止，活动会话数量：{len(fleet.follow_sessions)}", flush=True)
        fleet.events.drain(timeout_s=5.0)
        fleet.close()


if __name__ == "__main__":
    main()
