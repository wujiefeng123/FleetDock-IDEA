"""使用 MAVSDK 通过本地 NED Offboard 完成一米起飞和自动降落。

该文件是直接调用厂商 SDK 的真机验收工具，不是 FleetDock 公共 Demo；其控制闭环已经由 PX4
适配器封装，普通开发者应使用 ``examples/demo/drone_one_meter_takeoff_land.py``。

默认连接 Windows 串口 ``serial://COM3:57600``。脚本不调用
``Action.takeoff()``，也不写入 PX4 ``MIS_TAKEOFF_ALT``；它会先持续发送
原地位置目标，再进入 Offboard、标准解锁、把本地高度目标限制在一米，
最后直接切换到 Land。

实际飞行必须显式传入 ``--execute``：

    python examples/mavsdk_one_meter_takeoff_land.py --execute
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import gc
import json
import math
import os
import time
from typing import Any, Callable


DEFAULT_ENDPOINT = os.getenv("FLEETDOCK_PX4_ENDPOINT", "serial://COM3:57600")
DEFAULT_HEIGHT_M = 1.0
DEFAULT_ASCENT_SPEED_M_S = 0.25
DEFAULT_HOLD_S = 3.0
OFFBOARD_PRESTREAM_S = 1.2
SETPOINT_INTERVAL_S = 0.05
POSITION_STALE_S = 5.0
MAX_HEIGHT_M = 1.35
MAX_HORIZONTAL_ERROR_M = 0.8
MAX_TILT_DEG = 30.0
MIN_FLIGHT_BATTERY_FRACTION = 0.25
MIN_FLIGHT_BATTERY_V = 14.0
POSITION_ONLY_YAW_TYPE_MASK = 2552


def normalize_battery_fraction(value: Any) -> float | None:
    """兼容 MAVSDK 的 0~1 和旧飞控实际返回的 0~100 两种剩余电量格式。"""
    if value is None:
        return None
    remaining = float(value)
    if not math.isfinite(remaining) or remaining < 0.0:
        return None
    if remaining > 1.0:
        if remaining > 100.0:
            return None
        remaining /= 100.0
    return remaining


@dataclass
class VehicleState:
    """保存飞行闭环所需的少量 MAVSDK 遥测。"""

    north_m: float | None = None
    east_m: float | None = None
    down_m: float | None = None
    down_m_s: float | None = None
    position_updated_at_s: float | None = None
    roll_deg: float | None = None
    pitch_deg: float | None = None
    yaw_deg: float | None = None
    armed: bool | None = None
    in_air: bool | None = None
    landed_state: str | None = None
    flight_mode: str | None = None
    local_position_ok: bool | None = None
    armable: bool | None = None
    battery_fraction: float | None = None
    battery_voltage_v: float | None = None
    status_text: list[str] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def notify(self) -> None:
        """通知等待协程有新的遥测状态。"""
        self.changed.set()


@dataclass
class PositionSetpointSender:
    """通过 MAVSDK Offboard 插件或 MavlinkDirect 发送本地 NED 位置目标。"""

    drone: Any
    target_system: int
    target_component: int
    use_raw_mavlink: bool = False

    async def send(
        self,
        north_m: float,
        east_m: float,
        down_m: float,
        yaw_deg: float,
    ) -> None:
        """按当前选定的传输方式发送一个位置和偏航目标。"""
        if not self.use_raw_mavlink:
            from mavsdk.offboard import PositionNedYaw

            await self.drone.offboard.set_position_ned(
                PositionNedYaw(north_m, east_m, down_m, yaw_deg)
            )
            return

        from mavsdk.mavlink_direct import MavlinkMessage

        fields = {
            "time_boot_ms": int(time.monotonic() * 1000.0) & 0xFFFFFFFF,
            "target_system": self.target_system,
            "target_component": self.target_component,
            "coordinate_frame": 1,
            "type_mask": POSITION_ONLY_YAW_TYPE_MASK,
            "x": float(north_m),
            "y": float(east_m),
            "z": float(down_m),
            "vx": 0.0,
            "vy": 0.0,
            "vz": 0.0,
            "afx": 0.0,
            "afy": 0.0,
            "afz": 0.0,
            "yaw": math.radians(float(yaw_deg)),
            "yaw_rate": 0.0,
        }
        await self.drone.mavlink_direct.send_message(
            MavlinkMessage(
                "SET_POSITION_TARGET_LOCAL_NED",
                245,
                190,
                self.target_system,
                self.target_component,
                json.dumps(fields, separators=(",", ":")),
            )
        )


async def _consume_stream(
    stream_factory: Callable[[], Any],
    update: Callable[[Any], None],
    *,
    name: str,
) -> None:
    """持续消费一个 MAVSDK 遥测流，异常时保留流名称。"""
    try:
        async for value in stream_factory():
            update(value)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise RuntimeError(f"MAVSDK telemetry stream '{name}' stopped: {exc}") from exc


def start_telemetry_monitors(drone: Any, state: VehicleState) -> list[asyncio.Task[Any]]:
    """启动位置、姿态、状态和电池遥测任务。"""

    def update_position(value: Any) -> None:
        """更新本地 NED 位置、垂直速度和接收时间。"""
        position = value.position
        velocity = value.velocity
        state.north_m = float(position.north_m)
        state.east_m = float(position.east_m)
        state.down_m = float(position.down_m)
        state.down_m_s = float(velocity.down_m_s)
        state.position_updated_at_s = time.monotonic()
        state.notify()

    def update_attitude(value: Any) -> None:
        """更新机体欧拉角。"""
        state.roll_deg = float(value.roll_deg)
        state.pitch_deg = float(value.pitch_deg)
        state.yaw_deg = float(value.yaw_deg)
        state.notify()

    def update_health(value: Any) -> None:
        """更新本地定位和可解锁健康状态。"""
        state.local_position_ok = bool(value.is_local_position_ok)
        state.armable = bool(value.is_armable)
        state.notify()

    def update_battery(value: Any) -> None:
        """更新电池剩余比例和总电压。"""
        remaining_raw = getattr(value, "remaining_percent", None)
        voltage_raw = getattr(value, "voltage_v", None)
        voltage = float(voltage_raw) if voltage_raw is not None else math.nan
        state.battery_fraction = normalize_battery_fraction(remaining_raw)
        state.battery_voltage_v = voltage if math.isfinite(voltage) else None
        state.notify()

    def update_landed_state(value: Any) -> None:
        """更新 MAVSDK 着陆状态。"""
        state.landed_state = str(getattr(value, "name", value)).lower()
        state.notify()

    def update_flight_mode(value: Any) -> None:
        """更新飞行模式名称。"""
        state.flight_mode = str(getattr(value, "name", value)).lower()
        state.notify()

    def update_status_text(value: Any) -> None:
        """输出并缓存最近的 PX4 状态文字。"""
        text = str(getattr(value, "text", "")).strip()
        if text:
            state.status_text.append(text)
            del state.status_text[:-20]
            print(f"[PX4] {text}", flush=True)
        state.notify()

    streams = (
        ("local_position", drone.telemetry.position_velocity_ned, update_position),
        ("attitude", drone.telemetry.attitude_euler, update_attitude),
        ("health", drone.telemetry.health, update_health),
        ("battery", drone.telemetry.battery, update_battery),
        ("armed", drone.telemetry.armed, lambda value: _set_state(state, "armed", bool(value))),
        ("in_air", drone.telemetry.in_air, lambda value: _set_state(state, "in_air", bool(value))),
        ("landed_state", drone.telemetry.landed_state, update_landed_state),
        ("flight_mode", drone.telemetry.flight_mode, update_flight_mode),
        ("status_text", drone.telemetry.status_text, update_status_text),
    )
    return [
        asyncio.create_task(_consume_stream(factory, update, name=name), name=f"telemetry-{name}")
        for name, factory, update in streams
    ]


def _set_state(state: VehicleState, name: str, value: Any) -> None:
    """更新一个简单状态字段并通知等待者。"""
    setattr(state, name, value)
    state.notify()


async def wait_until(
    state: VehicleState,
    predicate: Callable[[], bool],
    *,
    timeout_s: float,
    description: str,
) -> None:
    """等待状态条件成立，同时检查遥测任务是否已经停止。"""
    deadline = time.monotonic() + timeout_s
    while not predicate():
        remaining_s = deadline - time.monotonic()
        if remaining_s <= 0:
            raise TimeoutError(f"Timed out waiting for {description}.")
        state.changed.clear()
        try:
            await asyncio.wait_for(state.changed.wait(), timeout=min(remaining_s, 0.5))
        except asyncio.TimeoutError:
            continue


async def wait_connected(drone: Any, timeout_s: float) -> None:
    """等待 MAVSDK 发现 PX4。"""

    async def wait_loop() -> None:
        """消费连接状态流，首次连接成功后返回。"""
        async for connection_state in drone.core.connection_state():
            if connection_state.is_connected:
                return

    await asyncio.wait_for(wait_loop(), timeout=timeout_s)


async def wait_preflight_ready(state: VehicleState, timeout_s: float) -> None:
    """等待本地定位、姿态、可解锁状态和电池遥测。"""
    try:
        await wait_until(
            state,
            lambda: (
                state.local_position_ok is True
                and state.north_m is not None
                and state.east_m is not None
                and state.down_m is not None
                and state.yaw_deg is not None
                and state.battery_fraction is not None
            ),
            timeout_s=timeout_s,
            description="local position, attitude, and battery telemetry",
        )
    except TimeoutError as exc:
        raise TimeoutError(
            "Preflight telemetry incomplete: "
            f"local_position_ok={state.local_position_ok}, "
            f"armable={state.armable}, "
            f"ned=({state.north_m}, {state.east_m}, {state.down_m}), "
            f"yaw_deg={state.yaw_deg}, "
            f"battery_fraction={state.battery_fraction}."
        ) from exc
    if state.battery_fraction is not None and state.battery_fraction < 0.10:
        raise RuntimeError(
            f"Battery is only {state.battery_fraction * 100:.0f}%; charge it above 50% before flight."
        )
    if abs(state.roll_deg or 0.0) > 5.0 or abs(state.pitch_deg or 0.0) > 5.0:
        raise RuntimeError(
            f"Vehicle is not level: roll={state.roll_deg:.1f}°, pitch={state.pitch_deg:.1f}°."
        )
    if state.armable is not True:
        print(
            "Warning: MAVSDK health reports is_armable=False. Continuing only to a standard "
            "arm request because Local NED is ready; PX4 still performs and may reject every "
            "preflight check. Force arm is never used.",
            flush=True,
        )


def ensure_flight_safety(
    state: VehicleState,
    *,
    ground_north_m: float,
    ground_east_m: float,
    ground_down_m: float,
) -> None:
    """检查飞行中的遥测新鲜度、姿态、位置、高度和电池边界。"""
    if (
        state.position_updated_at_s is None
        or time.monotonic() - state.position_updated_at_s > POSITION_STALE_S
    ):
        raise RuntimeError("Local-position telemetry is stale.")
    if state.north_m is None or state.east_m is None or state.down_m is None:
        raise RuntimeError("Local-position telemetry is unavailable.")
    height_m = ground_down_m - state.down_m
    horizontal_error_m = math.hypot(
        state.north_m - ground_north_m,
        state.east_m - ground_east_m,
    )
    if height_m > MAX_HEIGHT_M:
        raise RuntimeError(f"Measured height exceeded {MAX_HEIGHT_M:.2f} m.")
    if horizontal_error_m > MAX_HORIZONTAL_ERROR_M:
        raise RuntimeError(
            f"Horizontal deviation exceeded {MAX_HORIZONTAL_ERROR_M:.2f} m."
        )
    if abs(state.roll_deg or 0.0) > MAX_TILT_DEG or abs(state.pitch_deg or 0.0) > MAX_TILT_DEG:
        raise RuntimeError("Vehicle tilt exceeded the flight boundary.")
    if (
        state.battery_fraction is not None
        and state.battery_fraction < MIN_FLIGHT_BATTERY_FRACTION
    ):
        raise RuntimeError("Battery fell below 25%.")
    if state.battery_voltage_v is not None and state.battery_voltage_v < MIN_FLIGHT_BATTERY_V:
        raise RuntimeError("Battery voltage fell below 14.0 V.")


async def send_raw_mode_request(
    drone: Any,
    *,
    target_system: int,
    target_component: int,
) -> None:
    """通过 MAVSDK MavlinkDirect 为旧 PX4 补发一次 Offboard 模式请求。"""
    from mavsdk.mavlink_direct import MavlinkMessage

    sender_system = 245
    sender_component = 190
    command_fields = {
        "target_system": target_system,
        "target_component": target_component,
        "command": 176,
        "confirmation": 0,
        "param1": 1.0,
        "param2": 6.0,
        "param3": 0.0,
        "param4": 0.0,
        "param5": 0.0,
        "param6": 0.0,
        "param7": 0.0,
    }
    set_mode_fields = {
        "target_system": target_system,
        "base_mode": 1,
        "custom_mode": 6 << 16,
    }
    await drone.mavlink_direct.send_message(
        MavlinkMessage(
            "COMMAND_LONG",
            sender_system,
            sender_component,
            target_system,
            target_component,
            json.dumps(command_fields),
        )
    )
    await drone.mavlink_direct.send_message(
        MavlinkMessage(
            "SET_MODE",
            sender_system,
            sender_component,
            target_system,
            0,
            json.dumps(set_mode_fields),
        )
    )


async def start_raw_offboard_fallback(
    drone: Any,
    state: VehicleState,
    sender: PositionSetpointSender,
    *,
    north_m: float,
    east_m: float,
    down_m: float,
    yaw_deg: float,
    target_system: int,
    target_component: int,
) -> None:
    """持续发送原始位置目标并重复请求模式，直到遥测确认 Offboard。"""
    sender.use_raw_mavlink = True
    print(
        "Streaming raw SET_POSITION_TARGET_LOCAL_NED while requesting PX4 Offboard ...",
        flush=True,
    )
    deadline_s = time.monotonic() + 15.0
    next_mode_request_s = 0.0
    while time.monotonic() < deadline_s:
        await sender.send(north_m, east_m, down_m, yaw_deg)
        if state.flight_mode is not None and "offboard" in state.flight_mode:
            return
        now_s = time.monotonic()
        if now_s >= next_mode_request_s:
            await send_raw_mode_request(
                drone,
                target_system=target_system,
                target_component=target_component,
            )
            next_mode_request_s = now_s + 0.8
        await asyncio.sleep(SETPOINT_INTERVAL_S)
    raise TimeoutError(
        "Timed out waiting for PX4 Offboard mode while raw Local NED setpoints "
        "were being streamed."
    )


async def start_offboard(
    drone: Any,
    state: VehicleState,
    sender: PositionSetpointSender,
    *,
    north_m: float,
    east_m: float,
    down_m: float,
    yaw_deg: float,
    target_system: int,
    target_component: int,
) -> bool:
    """启动 MAVSDK Offboard；旧 PX4 拒绝时补发兼容模式消息。"""
    from mavsdk.offboard import OffboardError

    try:
        await drone.offboard.start()
        return True
    except OffboardError as exc:
        print(
            f"MAVSDK offboard.start() failed ({exc}); trying old-PX4 mode fallback.",
            flush=True,
        )
    await start_raw_offboard_fallback(
        drone,
        state,
        sender,
        north_m=north_m,
        east_m=east_m,
        down_m=down_m,
        yaw_deg=yaw_deg,
        target_system=target_system,
        target_component=target_component,
    )
    print("PX4 Offboard confirmed through raw MAVLink setpoint fallback.", flush=True)
    return False


async def ramp_takeoff_setpoint(
    sender: PositionSetpointSender,
    state: VehicleState,
    *,
    ground_north_m: float,
    ground_east_m: float,
    ground_down_m: float,
    yaw_deg: float,
    target_height_m: float,
    ascent_speed_m_s: float,
) -> None:
    """以固定速率移动位置目标，最终将高度限制在目标值。"""
    started_at_s = time.monotonic()
    while True:
        elapsed_s = time.monotonic() - started_at_s
        commanded_height_m = min(target_height_m, elapsed_s * ascent_speed_m_s)
        await sender.send(
            ground_north_m,
            ground_east_m,
            ground_down_m - commanded_height_m,
            yaw_deg,
        )
        ensure_flight_safety(
            state,
            ground_north_m=ground_north_m,
            ground_east_m=ground_east_m,
            ground_down_m=ground_down_m,
        )
        if commanded_height_m >= target_height_m:
            return
        await asyncio.sleep(SETPOINT_INTERVAL_S)


async def maintain_position_setpoint(
    sender: PositionSetpointSender,
    *,
    north_m: float,
    east_m: float,
    down_m: float,
    yaw_deg: float,
) -> None:
    """主动刷新最终位置目标，兼容低带宽链路上的延迟心跳。"""
    while True:
        await sender.send(north_m, east_m, down_m, yaw_deg)
        await asyncio.sleep(SETPOINT_INTERVAL_S)


async def cancel_task(task: asyncio.Task[Any] | None) -> None:
    """取消并回收单个后台任务；空任务和已结束任务均可安全传入。"""
    if task is None:
        return
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def command_land(
    drone: Any,
    *,
    target_system: int,
    target_component: int,
) -> None:
    """直接请求 Land；高层 Action 失败时通过 MavlinkDirect 补发 NAV_LAND。"""
    try:
        await drone.action.land()
        return
    except Exception as exc:
        print(f"MAVSDK action.land() failed ({exc}); sending raw NAV_LAND.", flush=True)

    from mavsdk.mavlink_direct import MavlinkMessage

    fields = {
        "target_system": target_system,
        "target_component": target_component,
        "command": 21,
        "confirmation": 0,
        "param1": 0.0,
        "param2": 0.0,
        "param3": 0.0,
        "param4": None,
        "param5": None,
        "param6": None,
        "param7": None,
    }
    await drone.mavlink_direct.send_message(
        MavlinkMessage(
            "COMMAND_LONG",
            245,
            190,
            target_system,
            target_component,
            json.dumps(fields),
        )
    )


async def wait_landed_and_disarmed(
    drone: Any,
    state: VehicleState,
    *,
    ground_down_m: float,
    timeout_s: float,
) -> None:
    """等待落地；确认近地稳定后才允许发送普通 disarm。"""
    deadline_s = time.monotonic() + timeout_s
    near_ground_since_s: float | None = None
    disarm_sent = False
    while time.monotonic() < deadline_s:
        height_m = (
            ground_down_m - state.down_m
            if state.down_m is not None
            else math.inf
        )
        position_fresh = (
            state.position_updated_at_s is not None
            and time.monotonic() - state.position_updated_at_s <= POSITION_STALE_S
        )
        near_ground = (
            position_fresh
            and abs(height_m) <= 0.15
            and abs(state.down_m_s or 0.0) <= 0.25
        )
        if near_ground:
            near_ground_since_s = near_ground_since_s or time.monotonic()
        else:
            near_ground_since_s = None
        landed_state_confirms_ground = state.landed_state is not None and any(
            marker in state.landed_state for marker in ("ground", "landed")
        )
        near_ground_confirmed = (
            near_ground_since_s is not None
            and time.monotonic() - near_ground_since_s >= 2.0
        )
        if state.armed is False and (
            state.in_air is False
            or landed_state_confirms_ground
            or near_ground_confirmed
        ):
            return
        if (
            not disarm_sent
            and state.armed is True
            and near_ground_confirmed
        ):
            await drone.action.disarm()
            disarm_sent = True
        state.changed.clear()
        try:
            await asyncio.wait_for(state.changed.wait(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
    raise TimeoutError("Landing did not reach a confirmed disarmed state.")


async def fly(args: argparse.Namespace) -> None:
    """执行连接、Offboard 解锁、一米起飞、保持和降落。"""
    from mavsdk import System

    drone = System(sysid=245, compid=190)
    state = VehicleState()
    setpoint_sender = PositionSetpointSender(
        drone,
        target_system=args.target_system,
        target_component=args.target_component,
    )
    monitors: list[asyncio.Task[Any]] = []
    position_keeper_task: asyncio.Task[Any] | None = None
    offboard_started = False
    plugin_offboard_started = False
    arm_command_sent = False
    ground_down_m: float | None = None

    try:
        print(f"Connecting to {args.endpoint} ...", flush=True)
        await drone.connect(system_address=args.endpoint)
        await wait_connected(drone, args.connect_timeout)
        print("PX4 connected. Waiting for local-position preflight state ...", flush=True)

        monitors = start_telemetry_monitors(drone, state)
        await wait_preflight_ready(state, args.preflight_timeout)
        await asyncio.sleep(1.0)

        assert state.north_m is not None
        assert state.east_m is not None
        assert state.down_m is not None
        assert state.yaw_deg is not None
        ground_north_m = state.north_m
        ground_east_m = state.east_m
        ground_down_m = state.down_m
        yaw_deg = state.yaw_deg

        print(
            "Preflight ready: "
            f"NED=({ground_north_m:.2f}, {ground_east_m:.2f}, {ground_down_m:.2f}), "
            f"yaw={yaw_deg:.1f}°, battery={state.battery_fraction * 100:.0f}%.",
            flush=True,
        )

        print(f"Pre-streaming ground hold for {OFFBOARD_PRESTREAM_S:.1f} s ...", flush=True)
        prestream_deadline_s = time.monotonic() + OFFBOARD_PRESTREAM_S
        while time.monotonic() < prestream_deadline_s:
            await setpoint_sender.send(
                ground_north_m,
                ground_east_m,
                ground_down_m,
                yaw_deg,
            )
            await asyncio.sleep(SETPOINT_INTERVAL_S)
        plugin_offboard_started = await start_offboard(
            drone,
            state,
            setpoint_sender,
            north_m=ground_north_m,
            east_m=ground_east_m,
            down_m=ground_down_m,
            yaw_deg=yaw_deg,
            target_system=args.target_system,
            target_component=args.target_component,
        )
        offboard_started = True

        print("Offboard active; sending standard arm and bounded 1 m target ramp.", flush=True)
        arm_task = asyncio.create_task(drone.action.arm(), name="arm")
        arm_command_sent = True
        ramp_task = asyncio.create_task(
            ramp_takeoff_setpoint(
                setpoint_sender,
                state,
                ground_north_m=ground_north_m,
                ground_east_m=ground_east_m,
                ground_down_m=ground_down_m,
                yaw_deg=yaw_deg,
                target_height_m=args.height,
                ascent_speed_m_s=args.ascent_speed,
            ),
            name="takeoff-ramp",
        )
        try:
            await arm_task
            await ramp_task
        except BaseException:
            for task in (arm_task, ramp_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(arm_task, ramp_task, return_exceptions=True)
            raise

        position_keeper_task = asyncio.create_task(
            maintain_position_setpoint(
                setpoint_sender,
                north_m=ground_north_m,
                east_m=ground_east_m,
                down_m=ground_down_m - args.height,
                yaw_deg=yaw_deg,
            ),
            name="position-keeper",
        )
        await wait_until(
            state,
            lambda: (
                state.down_m is not None
                and ground_down_m - state.down_m >= args.height - 0.15
            ),
            timeout_s=args.height_timeout,
            description=f"measured height {args.height:.2f} m",
        )
        print(f"Measured height reached {args.height:.2f} m; holding.", flush=True)

        hold_deadline_s = time.monotonic() + args.hold
        while time.monotonic() < hold_deadline_s:
            ensure_flight_safety(
                state,
                ground_north_m=ground_north_m,
                ground_east_m=ground_east_m,
                ground_down_m=ground_down_m,
            )
            await asyncio.sleep(0.1)

        await cancel_task(position_keeper_task)
        position_keeper_task = None
        print("Requesting Land.", flush=True)
        await command_land(
            drone,
            target_system=args.target_system,
            target_component=args.target_component,
        )
        offboard_started = False
        await wait_landed_and_disarmed(
            drone,
            state,
            ground_down_m=ground_down_m,
            timeout_s=args.land_timeout,
        )
        print("Landing and disarm confirmed.", flush=True)
    except BaseException:
        await cancel_task(position_keeper_task)
        position_keeper_task = None
        if arm_command_sent and ground_down_m is not None:
            print("Flight did not finish normally; requesting Land.", flush=True)
            try:
                await command_land(
                    drone,
                    target_system=args.target_system,
                    target_component=args.target_component,
                )
                offboard_started = False
                await wait_landed_and_disarmed(
                    drone,
                    state,
                    ground_down_m=ground_down_m,
                    timeout_s=args.land_timeout,
                )
            except Exception as landing_exc:
                print(f"Automatic landing confirmation failed: {landing_exc}", flush=True)
        raise
    finally:
        await cancel_task(position_keeper_task)
        for task in monitors:
            task.cancel()
        if monitors:
            await asyncio.gather(*monitors, return_exceptions=True)
        if offboard_started and plugin_offboard_started:
            try:
                await drone.offboard.stop()
            except Exception:
                pass
        stop_server = getattr(drone, "_stop_mavsdk_server", None)
        if callable(stop_server):
            stop_server()
        monitors.clear()
        del drone
        gc.collect()


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="明确允许执行真实解锁和飞行")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="MAVSDK 连接地址")
    parser.add_argument("--height", type=float, default=DEFAULT_HEIGHT_M, help="目标高度，米")
    parser.add_argument(
        "--ascent-speed",
        type=float,
        default=DEFAULT_ASCENT_SPEED_M_S,
        help="位置目标上升速度，米/秒",
    )
    parser.add_argument("--hold", type=float, default=DEFAULT_HOLD_S, help="到达高度后的保持秒数")
    parser.add_argument("--connect-timeout", type=float, default=30.0)
    parser.add_argument("--preflight-timeout", type=float, default=30.0)
    parser.add_argument("--height-timeout", type=float, default=20.0)
    parser.add_argument("--land-timeout", type=float, default=45.0)
    parser.add_argument("--target-system", type=int, default=1)
    parser.add_argument("--target-component", type=int, default=1)
    return parser


def validate_args(args: argparse.Namespace) -> None:
    """拒绝会改变一米低空演示边界的无效参数。"""
    if not 0.2 <= args.height <= 1.0:
        raise SystemExit("--height must be between 0.2 and 1.0 m.")
    if not 0.05 <= args.ascent_speed <= 0.5:
        raise SystemExit("--ascent-speed must be between 0.05 and 0.5 m/s.")
    if not 0.0 <= args.hold <= 10.0:
        raise SystemExit("--hold must be between 0 and 10 seconds.")
    if not 1 <= args.target_system <= 255 or not 1 <= args.target_component <= 255:
        raise SystemExit("MAVLink target system/component must be between 1 and 255.")


def main() -> None:
    """解析配置；只有显式 ``--execute`` 时才启动 MAVSDK。"""
    args = build_parser().parse_args()
    validate_args(args)
    if not args.execute:
        print(
            "Dry run only; no connection or flight command was sent.\n"
            f"Endpoint: {args.endpoint}\n"
            f"Plan: connect -> Offboard prestream -> arm -> {args.height:.2f} m -> "
            f"hold {args.hold:.1f} s -> land\n"
            "Run again with --execute after completing the physical safety check.",
            flush=True,
        )
        return
    try:
        asyncio.run(fly(args))
    except Exception as exc:
        print(f"Flight stopped before completion: {type(exc).__name__}: {exc}", flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
