"""Project AirSim 场景和 Drone1 初始化脚本。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional


def env_path(name: str, default: Optional[Path] = None) -> Path:
    """读取路径环境变量；缺失时优先使用存在的同级仓库默认路径。"""

    value = os.environ.get(name)
    if value:
        return Path(value)
    if default is not None and default.exists():
        return default
    raise SystemExit(f"缺少环境变量 {name}，请先通过 examples\\run_dev.bat 启动。")


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
FLEETDOCK_PROJECTAIRSIM_DIR = Path(__file__).resolve().parent / "projectairsim"
PROJECTAIRSIM_PYTHON_DIR = WORKSPACE_ROOT / "ProjectAirSim" / "client" / "python"
PROJECTAIRSIM_EXAMPLES_DIR = env_path(
    "PROJECTAIRSIM_EXAMPLES_DIR", PROJECTAIRSIM_PYTHON_DIR / "example_user_scripts"
)
PROJECTAIRSIM_SRC_DIR = env_path(
    "PROJECTAIRSIM_SRC_DIR", PROJECTAIRSIM_PYTHON_DIR / "projectairsim" / "src"
)
CONDA_NAME = os.environ.get("CONDA_NAME", "fleetdock")

SCENE_NAME = os.environ.get("PROJECTAIRSIM_SCENE_NAME", "scene_fleetdock_px4_single.generated.jsonc")
ROBOT_PROFILE = os.environ.get("PROJECTAIRSIM_ROBOT_PROFILE", "basic").strip().lower()
SENSOR_PROFILES_SPEC = os.environ.get("PROJECTAIRSIM_SENSOR_PROFILES", "").strip()
ROBOT_CONFIG_PATH = os.environ.get("PROJECTAIRSIM_ROBOT_CONFIG_PATH", "").strip()
SCENE_CONFIG_PATH = os.environ.get("PROJECTAIRSIM_SCENE_CONFIG_PATH", "").strip()
MULTI_SENSOR_DRONES = os.environ.get("MULTI_PROJECTAIRSIM_SENSOR_DRONES", "Drone1").strip()
MULTI_SENSOR_PROFILES_SPEC = os.environ.get(
    "MULTI_PROJECTAIRSIM_SENSOR_PROFILES", ""
).strip()
DRONE_NAME = os.environ.get("PROJECTAIRSIM_DRONE_NAME", "Drone1")
DRONE_NAMES = [
    name.strip()
    for name in os.environ.get("PROJECTAIRSIM_DRONE_NAMES", DRONE_NAME).split(",")
    if name.strip()
]
POSE_TIMEOUT_S = 30.0
SIMULATOR_TCP_PORT_BASE = 4560
CONTROL_PORT_BASE = 14580
DRONE_SPAWN_SPACING_M = float(os.environ.get("MULTI_PROJECTAIRSIM_SPAWN_SPACING_M", "10.0"))
DEFAULT_FOLLOW_VIEW_SPAWN_OFFSETS_M = (
    (0.0, 0.0),
    (12.0, -4.0),
    (12.0, 4.0),
)
HOME_GEO_ENV = {
    "latitude": "PROJECTAIRSIM_HOME_LATITUDE_DEG",
    "longitude": "PROJECTAIRSIM_HOME_LONGITUDE_DEG",
    "altitude": "PROJECTAIRSIM_HOME_ALTITUDE_M",
}
ROBOT_PROFILE_NAMES = {"basic", "video", "depth", "stereo", "lidar", "radar"}


class LoadedWorldShim:
    """复用已经加载好的 Project AirSim 场景。"""

    def __init__(self, scene_config: dict[str, Any]) -> None:
        self._scene_config = scene_config
        self.parent_topic = f"/Sim/{scene_config['id']}"
        self.home_geo_point = scene_config.get("home-geo-point", {})

    def get_configuration(self) -> dict[str, Any]:
        """返回本地 JSON 配置内容的副本，供仿真客户端读取。"""
        return self._scene_config


def log(message: str) -> None:
    """只打印到控制台，避免初始化脚本额外生成日志文件。"""

    encoding = sys.stdout.encoding or "utf-8"
    safe_message = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
    safe_message = safe_message.replace("\x00", "")
    print(safe_message, flush=True)


def configure_projectairsim_console_logging() -> None:
    """让 Project AirSim 只向控制台输出日志，不写 projectairsim_client.log。"""

    logger = logging.getLogger("projectairsim")
    formatter = logging.Formatter(
        "%(asctime)s:%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    for handler in list(logger.handlers):
        if isinstance(handler, logging.FileHandler):
            logger.removeHandler(handler)
            handler.close()

    if not logger.handlers:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    logger.setLevel(logging.INFO)
    logger.propagate = False


def import_projectairsim() -> tuple[Any, Any, Any, Any]:
    """导入 Project AirSim 类型；必要时使用显式配置的源码目录。"""
    configure_projectairsim_console_logging()

    if PROJECTAIRSIM_SRC_DIR.exists() and str(PROJECTAIRSIM_SRC_DIR) not in sys.path:
        sys.path.insert(0, str(PROJECTAIRSIM_SRC_DIR))

    try:
        from projectairsim import Drone, ProjectAirSimClient, World
        from projectairsim.utils import load_scene_config_as_dict
    except ImportError:
        print(f"未安装 projectairsim。请使用 {CONDA_NAME} conda 环境运行本脚本。", file=sys.stderr)
        raise SystemExit(2)
    return ProjectAirSimClient, World, Drone, load_scene_config_as_dict


def home_geo_override() -> Optional[dict[str, float]]:
    """从环境变量读取 Project AirSim Home 经纬度覆盖；未配置时返回 None。"""

    if not any(os.environ.get(env_name) for env_name in HOME_GEO_ENV.values()):
        return None
    missing = [env_name for env_name in HOME_GEO_ENV.values() if not os.environ.get(env_name)]
    if missing:
        raise ValueError(f"Home 经纬度覆盖缺少环境变量：{', '.join(missing)}")
    return {
        key: float(os.environ[env_name])
        for key, env_name in HOME_GEO_ENV.items()
    }


def apply_home_geo_override(scene_config: dict[str, Any]) -> None:
    """把环境变量中的 Home 经纬度写入场景配置，确保 GPS 起点可复现。"""

    override = home_geo_override()
    if override is None:
        return
    scene_config["home-geo-point"] = override
    log(
        "已设置 Project AirSim Home 经纬度："
        f"lat={override['latitude']}, lon={override['longitude']}, altitude={override['altitude']} m"
    )


def load_jsonc(path: Path) -> dict[str, Any]:
    """读取 UTF-8 JSON/JSONC 配置。"""

    try:
        import commentjson
    except ImportError:
        print(f"未安装 commentjson，请使用 {CONDA_NAME} conda 环境运行本脚本。", file=sys.stderr)
        raise SystemExit(2)

    with path.open(encoding="utf-8") as file:
        return commentjson.load(file)


def write_generated_config(path: Path, config: dict[str, Any]) -> None:
    """以 UTF-8 无 BOM 和 LF 换行写入生成配置。"""

    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)
        file.write("\n")


def generated_scene_path() -> Path:
    """返回受限的场景输出路径，避免覆盖 Project AirSim 上游配置。"""

    if Path(SCENE_NAME).name != SCENE_NAME or not SCENE_NAME.endswith(".generated.jsonc"):
        raise ValueError("PROJECTAIRSIM_SCENE_NAME 必须是以 .generated.jsonc 结尾的纯文件名")
    return PROJECTAIRSIM_EXAMPLES_DIR / "sim_config" / SCENE_NAME


def resolve_config_path(value: str, fallback_dir: Path) -> Path:
    """解析绝对路径，或相对于指定配置目录解析文件名。"""

    path = Path(value)
    return path if path.is_absolute() else fallback_dir / path


def load_scene_template() -> dict[str, Any]:
    """加载 FleetDock 场景模板，并展开其 Project AirSim 基础场景。"""

    projectairsim_config_dir = PROJECTAIRSIM_EXAMPLES_DIR / "sim_config"
    profile_path = (
        Path(SCENE_CONFIG_PATH)
        if SCENE_CONFIG_PATH
        else FLEETDOCK_PROJECTAIRSIM_DIR / "scenes" / "scene_px4_neighborhood.jsonc"
    )
    profile = load_jsonc(profile_path)
    base_name = profile.pop("base-scene-config", None)
    if base_name is None:
        return profile
    base_path = resolve_config_path(str(base_name), projectairsim_config_dir)
    scene_config = load_jsonc(base_path)
    scene_config.update(profile)
    return scene_config


def parse_sensor_profiles(value: str, *, separator: str = ",") -> tuple[str, ...]:
    """解析传感器 Profile 组合，保留声明顺序并拒绝空组合、未知值和重复项。"""

    profile_names = tuple(
        profile_name.strip().lower()
        for profile_name in value.split(separator)
        if profile_name.strip()
    )
    if not profile_names:
        raise ValueError("传感器 Profile 组合不能为空")
    unknown_names = set(profile_names).difference(ROBOT_PROFILE_NAMES)
    if unknown_names:
        choices = ", ".join(sorted(ROBOT_PROFILE_NAMES))
        raise ValueError(
            "未知 Project AirSim 传感器 Profile："
            f"{', '.join(sorted(unknown_names))}；可选值：{choices}"
        )
    duplicate_names = sorted(
        {profile_name for profile_name in profile_names if profile_names.count(profile_name) > 1}
    )
    if duplicate_names:
        raise ValueError(f"传感器 Profile 组合包含重复项：{', '.join(duplicate_names)}")
    return profile_names


def configured_sensor_profiles() -> tuple[str, ...]:
    """返回默认传感器 Profile 组合；新组合变量未配置时兼容旧单 Profile 变量。"""

    return parse_sensor_profiles(SENSOR_PROFILES_SPEC or ROBOT_PROFILE)


def collect_profile_sensors(
    profile: dict[str, Any],
    *,
    include_stack: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """递归展开 Profile 引用并返回附加传感器；循环引用会被明确拒绝。"""

    additional_sensors = list(profile.pop("additional-sensors", []))
    for included_name in profile.pop("include-profiles", []):
        if included_name not in ROBOT_PROFILE_NAMES:
            raise ValueError(f"机器人 Profile 引用了未知 Profile：{included_name}")
        if included_name in include_stack:
            chain = " -> ".join((*include_stack, included_name))
            raise ValueError(f"机器人 Profile 存在循环引用：{chain}")
        included_path = (
            FLEETDOCK_PROJECTAIRSIM_DIR / "robots" / f"robot_px4_{included_name}.jsonc"
        )
        included_profile = load_jsonc(included_path)
        additional_sensors.extend(
            collect_profile_sensors(
                included_profile,
                include_stack=(*include_stack, included_name),
            )
        )
    return additional_sensors


def append_profile_sensors(
    robot_config: dict[str, Any],
    additional_sensors: list[dict[str, Any]],
) -> None:
    """把传感器追加到机器人配置，并拒绝基础机型或组合 Profile 中的重复 ID。"""

    sensors = robot_config.setdefault("sensors", [])
    existing_ids = {sensor["id"] for sensor in sensors}
    duplicate_ids: list[str] = []
    for sensor in additional_sensors:
        sensor_id = sensor["id"]
        if sensor_id in existing_ids:
            duplicate_ids.append(sensor_id)
        existing_ids.add(sensor_id)
    if duplicate_ids:
        raise ValueError(f"机器人 Profile 包含重复传感器 ID：{', '.join(duplicate_ids)}")
    sensors.extend(additional_sensors)


def apply_profile_sensor_overrides(
    robot_config: dict[str, Any],
    sensor_overrides: list[dict[str, Any]],
) -> None:
    """按传感器 ID 覆盖基础机型配置；上游模板不存在该传感器时忽略。"""

    sensors_by_id = {sensor["id"]: sensor for sensor in robot_config.get("sensors", [])}
    for override in sensor_overrides:
        override_fields = dict(override)
        sensor_id = override_fields.pop("id")
        sensor = sensors_by_id.get(sensor_id)
        if sensor is None:
            continue
        sensor.update(override_fields)


def load_robot_profile(profile_name: str, *, use_custom_path: bool = True) -> dict[str, Any]:
    """加载单个机器人 Profile，并递归合并其附加传感器到 Project AirSim 基础机型。"""

    profile_name = parse_sensor_profiles(profile_name)[0]

    if use_custom_path and ROBOT_CONFIG_PATH:
        profile_path = Path(ROBOT_CONFIG_PATH)
    else:
        profile_path = FLEETDOCK_PROJECTAIRSIM_DIR / "robots" / f"robot_px4_{profile_name}.jsonc"
    profile = load_jsonc(profile_path)
    base_name = profile.pop("base-robot-config", None)
    sensor_overrides = list(profile.pop("sensor-overrides", []))
    additional_sensors = collect_profile_sensors(profile, include_stack=(profile_name,))
    if base_name is None:
        robot_config = profile
    else:
        base_path = resolve_config_path(
            str(base_name), PROJECTAIRSIM_EXAMPLES_DIR / "sim_config"
        )
        robot_config = load_jsonc(base_path)
        robot_config.update(profile)

    apply_profile_sensor_overrides(robot_config, sensor_overrides)
    append_profile_sensors(robot_config, additional_sensors)
    return robot_config


def load_robot_profiles(
    profile_names: tuple[str, ...],
    *,
    use_custom_path: bool = True,
) -> dict[str, Any]:
    """组合多个传感器 Profile，并返回带完整基础机型和传感器列表的机器人配置。

    ``profile_names`` 按声明顺序叠加；每个 Profile 可以继续通过 ``include-profiles`` 递归组合。
    自定义机器人配置只支持旧式单 Profile 入口，避免无法判断自定义文件应作为基础机型还是传感器层。
    """

    normalized_names = parse_sensor_profiles(",".join(profile_names))
    if len(normalized_names) == 1:
        return load_robot_profile(normalized_names[0], use_custom_path=use_custom_path)
    if use_custom_path and ROBOT_CONFIG_PATH:
        raise ValueError(
            "PROJECTAIRSIM_ROBOT_CONFIG_PATH 不能与多 Profile 组合同时使用；"
            "请把组合写入自定义 JSONC 的 include-profiles"
        )

    robot_config = load_robot_profile("basic", use_custom_path=False)
    for profile_name in normalized_names:
        profile_path = (
            FLEETDOCK_PROJECTAIRSIM_DIR / "robots" / f"robot_px4_{profile_name}.jsonc"
        )
        profile = load_jsonc(profile_path)
        profile.pop("base-robot-config", None)
        sensor_overrides = list(profile.pop("sensor-overrides", []))
        additional_sensors = collect_profile_sensors(profile, include_stack=(profile_name,))
        apply_profile_sensor_overrides(robot_config, sensor_overrides)
        append_profile_sensors(robot_config, additional_sensors)
    return robot_config


def parse_multi_sensor_profiles() -> dict[str, tuple[str, ...]]:
    """解析逐机传感器组合，格式为 ``Drone1=stereo+lidar;Drone2=depth``。"""

    assignments: dict[str, tuple[str, ...]] = {}
    for assignment in MULTI_SENSOR_PROFILES_SPEC.split(";"):
        assignment = assignment.strip()
        if not assignment:
            continue
        drone_name, separator, profile_spec = assignment.partition("=")
        drone_name = drone_name.strip()
        if not separator or not drone_name:
            raise ValueError(
                "MULTI_PROJECTAIRSIM_SENSOR_PROFILES 格式应为 "
                "Drone1=stereo+lidar;Drone2=depth"
            )
        if drone_name in assignments:
            raise ValueError(f"逐机传感器组合重复声明无人机：{drone_name}")
        assignments[drone_name] = parse_sensor_profiles(profile_spec, separator="+")
    unknown_names = set(assignments).difference(DRONE_NAMES)
    if not assignments:
        raise ValueError("MULTI_PROJECTAIRSIM_SENSOR_PROFILES 不能为空")
    if unknown_names:
        raise ValueError(f"逐机传感器组合包含未知无人机：{', '.join(sorted(unknown_names))}")
    return assignments


def selected_profiles_for_drone(drone_name: str) -> tuple[str, ...]:
    """返回指定无人机的传感器 Profile 组合；未指定的多机成员使用 ``basic``。"""

    default_profiles = configured_sensor_profiles()
    if len(DRONE_NAMES) == 1:
        return default_profiles
    if MULTI_SENSOR_PROFILES_SPEC:
        return parse_multi_sensor_profiles().get(drone_name, ("basic",))
    if default_profiles == ("basic",):
        return default_profiles
    if MULTI_SENSOR_DRONES.lower() == "all":
        return default_profiles
    selected_names = {name.strip() for name in MULTI_SENSOR_DRONES.split(",") if name.strip()}
    unknown_names = selected_names.difference(DRONE_NAMES)
    if unknown_names:
        raise ValueError(f"感知无人机列表包含未知名称：{', '.join(sorted(unknown_names))}")
    return default_profiles if drone_name in selected_names else ("basic",)


def generate_robot_config(drone_name: str, index: int) -> str:
    """生成带独立 PX4 端口的机器人配置，并返回生成文件名。"""

    profile_names = selected_profiles_for_drone(drone_name)
    robot_config = load_robot_profiles(
        profile_names,
        use_custom_path=profile_names == configured_sensor_profiles(),
    )
    px4_settings = robot_config["controller"]["px4-settings"]
    px4_settings["tcp-port"] = SIMULATOR_TCP_PORT_BASE + index
    px4_settings["control-port-remote"] = CONTROL_PORT_BASE + index
    px4_settings["control-port-local"] = CONTROL_PORT_BASE + index
    windows_host_ip = os.environ.get("PX4_WINDOWS_HOST_IP")
    if windows_host_ip:
        px4_settings["local-host-ip"] = windows_host_ip

    robot_config_name = f"robot_fleetdock_px4_{index + 1}.generated.jsonc"
    write_generated_config(PROJECTAIRSIM_EXAMPLES_DIR / "sim_config" / robot_config_name, robot_config)
    log(
        f"已生成 {drone_name} 机器人配置："
        f"Profiles={'+'.join(profile_names)}，文件={robot_config_name}"
    )
    return robot_config_name


def prepare_single_drone_scene() -> None:
    """根据公共场景和机器人 Profile 生成单机 Project AirSim 场景。"""

    if len(DRONE_NAMES) > 1:
        return

    scene_config = load_scene_template()
    apply_home_geo_override(scene_config)
    actor = json.loads(json.dumps(scene_config["actors"][0]))
    actor.update(
        {
            "type": "robot",
            "name": DRONE_NAMES[0],
            "robot-config": generate_robot_config(DRONE_NAMES[0], 0),
        }
    )
    scene_config["id"] = "SceneFleetDock1Drone"
    scene_config["actors"] = [actor]
    output_path = generated_scene_path()
    write_generated_config(output_path, scene_config)
    log(f"已生成 Project AirSim 单机场景：{output_path}")


def prepare_multi_drone_scene() -> None:
    """按无人机列表和 Profile 生成 Project AirSim 多机场景。"""

    if len(DRONE_NAMES) <= 1:
        return

    scene_path = generated_scene_path()
    scene_config = load_scene_template()
    apply_home_geo_override(scene_config)

    actors = []
    for index, drone_name in enumerate(DRONE_NAMES):
        if index < len(DEFAULT_FOLLOW_VIEW_SPAWN_OFFSETS_M):
            forward_offset_m, lateral_offset_m = DEFAULT_FOLLOW_VIEW_SPAWN_OFFSETS_M[index]
        else:
            forward_offset_m = 12.0 + (index - len(DEFAULT_FOLLOW_VIEW_SPAWN_OFFSETS_M) + 1) * 8.0
            lateral_offset_m = -index * DRONE_SPAWN_SPACING_M
        actors.append(
            {
                "type": "robot",
                "name": drone_name,
                "origin": {"xyz": f"{forward_offset_m:.1f} {lateral_offset_m:.1f} -4.0", "rpy-deg": "0 0 0"},
                "robot-config": generate_robot_config(drone_name, index),
            }
        )

    scene_config["id"] = f"SceneFleetDock{len(DRONE_NAMES)}Drones"
    scene_config["actors"] = actors
    write_generated_config(scene_path, scene_config)
    log(f"已生成 {len(DRONE_NAMES)} 架无人机的 Project AirSim 场景：{scene_path}")


def pose_position(pose: dict[str, Any]) -> dict[str, float]:
    """从仿真器 pose 映射中提取并返回 x/y/z 米制位置。"""
    return pose.get("position") or pose.get("translation") or {}


def load_or_attach_world(client: Any) -> Any:
    """加载场景；如果场景已经存在，则直接挂接。"""

    _, World, _, load_scene_config_as_dict = import_projectairsim()
    config_dir = str(PROJECTAIRSIM_EXAMPLES_DIR / "sim_config") + os.sep
    scene_config, _ = load_scene_config_as_dict(SCENE_NAME, sim_config_path=config_dir)
    actual_pose_topics = [
        f"/Sim/{scene_config['id']}/robots/{drone_name}/actual_pose" for drone_name in DRONE_NAMES
    ]

    client.get_topic_info()
    if any(topic in client.topics for topic in actual_pose_topics):
        log(f"复用已加载场景：{scene_config['id']}")
        return LoadedWorldShim(scene_config)

    log(f"加载 Project AirSim 场景：{SCENE_NAME}")
    try:
        return World(client, SCENE_NAME, delay_after_load_sec=2.0, sim_config_path=config_dir)
    except RuntimeError as err:
        if "ImportNEDTrajectory" not in str(err):
            raise
        log("场景已加载，忽略当前版本不支持的 ImportNEDTrajectory 服务。")
        return LoadedWorldShim(scene_config)


async def wait_for_air_sim_pose(drone: Any) -> None:
    """等到 Project AirSim 开始发布无人机真实位姿。"""

    deadline = time.monotonic() + POSE_TIMEOUT_S
    last_error: Optional[Exception] = None

    while time.monotonic() < deadline:
        try:
            pose = drone.get_ground_truth_pose()
            position = pose_position(pose)
            log(
                "Project AirSim 无人机位姿已就绪："
                f"x={position.get('x')}, y={position.get('y')}, z={position.get('z')}"
            )
            return
        except Exception as err:
            last_error = err
            await asyncio.sleep(0.5)

    raise TimeoutError(f"等待 Project AirSim 无人机位姿超时：{last_error}")


async def prepare_projectairsim_drone() -> tuple[Any, list[Any]]:
    """连接 Project AirSim，加载世界，创建一个或多个无人机对象。"""

    prepare_single_drone_scene()
    prepare_multi_drone_scene()
    ProjectAirSimClient, _, AirSimDrone, _ = import_projectairsim()
    client = ProjectAirSimClient()
    client.connect()
    log("已连接 Project AirSim client。")

    try:
        world = load_or_attach_world(client)
        drones = []
        for drone_name in DRONE_NAMES:
            log(f"初始化 Project AirSim 无人机：{drone_name}")
            drone = AirSimDrone(client, world, drone_name)
            await wait_for_air_sim_pose(drone)
            drones.append(drone)
        return client, drones
    except Exception:
        client.disconnect()
        raise


async def main_async() -> None:
    """连接 Project AirSim，加载场景并准备指定无人机。"""
    client, _ = await prepare_projectairsim_drone()
    try:
        log("Project AirSim 世界和无人机已加载完成。")
    finally:
        client.disconnect()
        log("Project AirSim client 已断开。")


def main() -> int:
    """运行异步仿真准备入口；成功返回 0，失败返回非零状态。"""
    asyncio.run(main_async())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
