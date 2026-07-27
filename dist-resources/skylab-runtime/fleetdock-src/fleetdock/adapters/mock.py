"""Mock adapters for local development and tests."""

from __future__ import annotations

import math
from typing import Any, Dict

from fleetdock.adapters.base import DeviceAdapter
from fleetdock.events import CoordinateFrame, EventCategory, EventType
from fleetdock.models import ErrorCode, StandardCommand, StandardResponse
from fleetdock.movement import MoveTargetKind


class MockDroneAdapter(DeviceAdapter):
    """本地测试用无人机适配器。

    该适配器不连接真实飞控，只在内存中维护解锁、飞行和本地位置状态，并把标准命令
    转换为可预测的 ``StandardResponse`` 与标准事件，供示例、单元测试和 SDK 调用链
    验证使用。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.unlocked = False
        self.flying = False
        self.position = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.yaw_deg = 0.0
        self.global_position = {"latitude_deg": 0.0, "longitude_deg": 0.0, "altitude_m": 0.0}

    def _handle_connect(self, command: StandardCommand) -> StandardResponse:
        self.connected = True
        self.publish_standard_event(
            EventType.CONNECTION_CHANGED,
            EventCategory.LIFECYCLE,
            {"previous": "disconnected", "current": "connected"},
            correlation_id=command.command_id,
        )
        return StandardResponse.success(command, "Drone connected.", {"params": dict(self.connection.params)})

    def _handle_unlock(self, command: StandardCommand) -> StandardResponse:
        self.unlocked = True
        self.publish_standard_event(
            EventType.ARMED_CHANGED,
            EventCategory.STATE,
            {"previous": False, "current": True},
            correlation_id=command.command_id,
        )
        return StandardResponse.success(command, "Drone unlocked.", {"unlocked": self.unlocked})

    def _handle_lock(self, command: StandardCommand) -> StandardResponse:
        self.unlocked = False
        self.publish_standard_event(
            EventType.ARMED_CHANGED,
            EventCategory.STATE,
            {"previous": True, "current": False},
            correlation_id=command.command_id,
        )
        return StandardResponse.success(command, "Drone locked.", {"unlocked": self.unlocked})

    def _handle_takeoff(self, command: StandardCommand) -> StandardResponse:
        altitude_param = command.params.get("altitude", 5.0)
        altitude = 5.0 if altitude_param is None else float(altitude_param)
        if altitude <= 0:
            raise ValueError("altitude must be greater than 0.")
        if not self.unlocked:
            return StandardResponse.failure(command, ErrorCode.DEVICE_STATE_ERROR, "Drone must be unlocked before takeoff.")
        self.flying = True
        self.position["z"] = altitude
        self.publish_standard_event(
            EventType.IN_AIR_CHANGED,
            EventCategory.STATE,
            {"previous": False, "current": True},
            correlation_id=command.command_id,
        )
        self.publish_standard_event(
            EventType.GLOBAL_POSITION_UPDATED,
            EventCategory.TELEMETRY,
            {
                "latitude_deg": 0.0,
                "longitude_deg": 0.0,
                "absolute_altitude_m": altitude,
                "relative_altitude_m": altitude,
            },
            correlation_id=command.command_id,
        )
        self.publish_standard_event(
            EventType.LOCAL_POSITION_UPDATED,
            EventCategory.TELEMETRY,
            {
                "frame": CoordinateFrame.LOCAL_ENU.value,
                "position_m": {
                    "x": self.position["x"],
                    "y": self.position["y"],
                    "z": self.position["z"],
                },
            },
            correlation_id=command.command_id,
        )
        return StandardResponse.success(command, "Drone takeoff completed.", {"altitude": altitude})

    def _handle_land(self, command: StandardCommand) -> StandardResponse:
        self.flying = False
        self.position["z"] = 0.0
        self.publish_standard_event(
            EventType.IN_AIR_CHANGED,
            EventCategory.STATE,
            {"previous": True, "current": False},
            correlation_id=command.command_id,
        )
        return StandardResponse.success(command, "Drone landed.", {"position": dict(self.position)})

    def _handle_return_to_launch(self, command: StandardCommand) -> StandardResponse:
        previous_flying = self.flying
        self.flying = False
        self.position = {"x": 0.0, "y": 0.0, "z": 0.0}
        if previous_flying:
            self.publish_standard_event(
                EventType.IN_AIR_CHANGED,
                EventCategory.STATE,
                {"previous": True, "current": False},
                correlation_id=command.command_id,
            )
        return StandardResponse.success(command, "Drone returned to launch.", {"position": dict(self.position)})

    def _handle_hover(self, command: StandardCommand) -> StandardResponse:
        return StandardResponse.success(command, "Drone hovering.", {"position": dict(self.position)})

    def _handle_move(self, command: StandardCommand) -> StandardResponse:
        if command.params.get("control_mode") == "body_velocity":
            forward_m_s = float(command.params.get("forward_m_s", 0.0))
            right_m_s = float(command.params.get("right_m_s", 0.0))
            up_m_s = float(command.params.get("up_m_s", 0.0))
            yaw_rate_deg_s = float(command.params.get("yaw_rate_deg_s", 0.0))
            yaw_rad = math.radians(self.yaw_deg)
            self.position["x"] += forward_m_s * math.cos(yaw_rad) - right_m_s * math.sin(yaw_rad)
            self.position["y"] += forward_m_s * math.sin(yaw_rad) + right_m_s * math.cos(yaw_rad)
            self.position["z"] = max(0.0, self.position["z"] + up_m_s)
            self.yaw_deg = (self.yaw_deg + yaw_rate_deg_s) % 360.0
            self.publish_standard_event(
                EventType.LOCAL_POSITION_UPDATED,
                EventCategory.TELEMETRY,
                {
                    "frame": CoordinateFrame.LOCAL_ENU.value,
                    "position_m": {
                        "x": self.position["x"],
                        "y": self.position["y"],
                        "z": self.position["z"],
                    },
                },
                correlation_id=command.command_id,
            )
            return StandardResponse.success(
                command,
                "Drone body velocity updated.",
                {"position": dict(self.position), "yaw_deg": self.yaw_deg},
            )
        if command.params.get("control_mode") == "velocity":
            speed = float(command.params.get("speed", 0.0))
            self.position["x"] += float(command.params.get("x", 0.0)) * speed
            self.position["y"] += float(command.params.get("y", 0.0)) * speed
            self.position["z"] = max(
                0.0,
                self.position["z"] + float(command.params.get("z", 0.0)) * speed,
            )
            return StandardResponse.success(command, "Drone velocity updated.", {"position": dict(self.position)})

        target_kind = MoveTargetKind(str(command.params["target_kind"]))
        target = dict(command.params["target"])
        if target_kind is MoveTargetKind.GPS_POINT:
            self.global_position = {
                "latitude_deg": float(target["latitude_deg"]),
                "longitude_deg": float(target["longitude_deg"]),
                "altitude_m": float(target["altitude_m"]),
            }
            self.yaw_deg = float(target["yaw_deg"]) if target.get("yaw_deg") is not None else self.yaw_deg
            self.publish_standard_event(
                EventType.GLOBAL_POSITION_UPDATED,
                EventCategory.TELEMETRY,
                {
                    "latitude_deg": self.global_position["latitude_deg"],
                    "longitude_deg": self.global_position["longitude_deg"],
                    "absolute_altitude_m": self.global_position["altitude_m"],
                    "relative_altitude_m": self.position["z"],
                },
                correlation_id=command.command_id,
            )
        elif target_kind is MoveTargetKind.RELATIVE_POINT_3D:
            self.position = {
                "x": float(target["x_m"]),
                "y": float(target["y_m"]),
                "z": float(target["z_m"]),
            }
            self.yaw_deg = float(target["yaw_deg"]) if target.get("yaw_deg") is not None else self.yaw_deg
        elif target_kind is MoveTargetKind.RELATIVE_MOVE_3D:
            yaw_rad = math.radians(self.yaw_deg)
            forward_m = float(target["forward_m"])
            right_m = float(target["right_m"])
            self.position["x"] += forward_m * math.cos(yaw_rad) - right_m * math.sin(yaw_rad)
            self.position["y"] += forward_m * math.sin(yaw_rad) + right_m * math.cos(yaw_rad)
            self.position["z"] = max(0.0, self.position["z"] + float(target["up_m"]))
            self.yaw_deg += float(target["yaw_deg"])
        else:
            return StandardResponse.failure(
                command,
                ErrorCode.UNSUPPORTED_COMMAND,
                f"Mock drone does not support move target '{target_kind.value}'.",
            )
        return StandardResponse.success(
            command,
            "Drone move target applied.",
            {
                "target_kind": target_kind.value,
                "position": dict(self.position),
                "global_position": dict(self.global_position),
                "yaw_deg": self.yaw_deg,
            },
        )

    def _handle_stop(self, command: StandardCommand) -> StandardResponse:
        return StandardResponse.success(command, "Drone stopped.", {"position": dict(self.position)})

    def _handle_emergency_stop(self, command: StandardCommand) -> StandardResponse:
        previous_flying = self.flying
        previous_unlocked = self.unlocked
        self.flying = False
        self.unlocked = False
        if previous_flying:
            self.publish_standard_event(
                EventType.IN_AIR_CHANGED,
                EventCategory.STATE,
                {"previous": True, "current": False},
                correlation_id=command.command_id,
            )
        if previous_unlocked:
            self.publish_standard_event(
                EventType.ARMED_CHANGED,
                EventCategory.STATE,
                {"previous": True, "current": False},
                correlation_id=command.command_id,
            )
        return StandardResponse.success(command, "Drone emergency stop completed.", {"unlocked": False, "flying": False})

    def _handle_get_status(self, command: StandardCommand) -> StandardResponse:
        return StandardResponse.success(command, "Drone status.", self.get_vendor_state())

    def get_vendor_state(self) -> Dict[str, Any]:
        """返回 Mock 无人机内部状态快照。

        返回值包含 ``unlocked``、``flying``、``position`` 和 ``connected`` 等字段，
        仅用于调试或测试断言，不代表真实厂商 SDK 的状态结构。
        """
        return {
            "connected": self.connected,
            "unlocked": self.unlocked,
            "flying": self.flying,
            "position": dict(self.position),
            "global_position": dict(self.global_position),
            "yaw_deg": self.yaw_deg,
        }


class MockRobotDogAdapter(DeviceAdapter):
    """本地测试用机器狗适配器。

    该适配器不连接真实 Unitree 或其他厂商 SDK，只在内存中维护站立状态、姿态和位置，
    用于验证机器狗标准命令、事件和 Fleet 批量流程。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pose = "sit"
        self.velocity = {"x": 0.0, "y": 0.0, "z": 0.0, "speed": 0.0}
        self.position = {"x": 0.0, "y": 0.0}
        self.yaw_deg = 0.0

    def _handle_connect(self, command: StandardCommand) -> StandardResponse:
        self.connected = True
        self.publish_standard_event(
            EventType.CONNECTION_CHANGED,
            EventCategory.LIFECYCLE,
            {"previous": "disconnected", "current": "connected"},
            correlation_id=command.command_id,
        )
        return StandardResponse.success(command, "Robot dog connected.", {"params": dict(self.connection.params)})

    def _handle_stand(self, command: StandardCommand) -> StandardResponse:
        previous = self.pose
        self.pose = "stand"
        if previous != self.pose:
            self.publish_standard_event(
                EventType.POSTURE_CHANGED,
                EventCategory.STATE,
                {"previous": previous, "current": self.pose},
                correlation_id=command.command_id,
            )
        return StandardResponse.success(command, "Robot dog standing.", {"pose": self.pose})

    def _handle_sit(self, command: StandardCommand) -> StandardResponse:
        previous = self.pose
        self.pose = "sit"
        self.velocity = {"x": 0.0, "y": 0.0, "z": 0.0, "speed": 0.0}
        if previous != self.pose:
            self.publish_standard_event(
                EventType.POSTURE_CHANGED,
                EventCategory.STATE,
                {"previous": previous, "current": self.pose},
                correlation_id=command.command_id,
            )
        return StandardResponse.success(command, "Robot dog sitting.", {"pose": self.pose})

    def _handle_move(self, command: StandardCommand) -> StandardResponse:
        if self.pose != "stand":
            return StandardResponse.failure(command, ErrorCode.DEVICE_STATE_ERROR, "Robot dog must stand before moving.")
        if command.params.get("control_mode") == "velocity":
            self.velocity = {
                "x": float(command.params.get("x", 0.0)),
                "y": float(command.params.get("y", 0.0)),
                "z": float(command.params.get("z", 0.0)),
                "speed": float(command.params.get("speed", 0.2)),
            }
            return self._publish_velocity(command)

        target_kind = MoveTargetKind(str(command.params["target_kind"]))
        target = dict(command.params["target"])
        if target_kind is MoveTargetKind.RELATIVE_POINT_2D:
            self.position = {"x": float(target["x_m"]), "y": float(target["y_m"])}
            self.yaw_deg = float(target["yaw_deg"]) if target.get("yaw_deg") is not None else self.yaw_deg
        elif target_kind is MoveTargetKind.RELATIVE_MOVE_2D:
            yaw_rad = math.radians(self.yaw_deg)
            forward_m = float(target["forward_m"])
            right_m = float(target["right_m"])
            self.position["x"] += forward_m * math.cos(yaw_rad) - right_m * math.sin(yaw_rad)
            self.position["y"] += forward_m * math.sin(yaw_rad) + right_m * math.cos(yaw_rad)
            self.yaw_deg += float(target["yaw_deg"])
        else:
            return StandardResponse.failure(
                command,
                ErrorCode.UNSUPPORTED_COMMAND,
                f"Mock robot dog does not support move target '{target_kind.value}'.",
            )
        self.velocity = {"x": 0.0, "y": 0.0, "z": 0.0, "speed": 0.0}
        return StandardResponse.success(
            command,
            "Robot dog move target applied.",
            {
                "target_kind": target_kind.value,
                "position": dict(self.position),
                "yaw_deg": self.yaw_deg,
            },
        )

    def _publish_velocity(self, command: StandardCommand) -> StandardResponse:
        self.publish_standard_event(
            EventType.VELOCITY_UPDATED,
            EventCategory.TELEMETRY,
            {
                "frame": CoordinateFrame.BODY.value,
                "linear_velocity_m_s": {
                    "x": self.velocity["x"] * self.velocity["speed"],
                    "y": self.velocity["y"] * self.velocity["speed"],
                    "z": self.velocity["z"] * self.velocity["speed"],
                },
            },
            correlation_id=command.command_id,
        )
        return StandardResponse.success(command, "Robot dog moving.", {"velocity": dict(self.velocity)})

    def _handle_stop(self, command: StandardCommand) -> StandardResponse:
        self.velocity = {"x": 0.0, "y": 0.0, "z": 0.0, "speed": 0.0}
        return StandardResponse.success(command, "Robot dog stopped.", {"velocity": dict(self.velocity)})

    def _handle_emergency_stop(self, command: StandardCommand) -> StandardResponse:
        self.velocity = {"x": 0.0, "y": 0.0, "z": 0.0, "speed": 0.0}
        self.pose = "sit"
        return StandardResponse.success(command, "Robot dog emergency stop completed.", self.get_vendor_state())

    def _handle_get_status(self, command: StandardCommand) -> StandardResponse:
        return StandardResponse.success(command, "Robot dog status.", self.get_vendor_state())

    def get_vendor_state(self) -> Dict[str, Any]:
        """返回 Mock 机器狗内部状态快照。

        返回值包含 ``standing``、``position`` 和 ``connected`` 等字段，仅供调试和测试
        使用，不应作为真实厂商协议字段依赖。
        """
        return {
            "connected": self.connected,
            "pose": self.pose,
            "velocity": dict(self.velocity),
            "position": dict(self.position),
            "yaw_deg": self.yaw_deg,
        }
