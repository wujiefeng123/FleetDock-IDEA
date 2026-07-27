"""任务航线与平滑运动执行实现。"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from fleetdock.avoidance import _AvoidanceAction, AvoidanceBlockedError, AvoidanceDecision, AvoidancePolicy
from fleetdock.adapters.base import NativeRouteCallbacks, NativeRouteRequest
from fleetdock.events import CoordinateFrame, EventCategory, EventType, RouteCompletionReason, RouteProgress, WaypointProgress
from fleetdock.events import Vector3
from fleetdock.models import StandardResponse
from fleetdock.movement import GpsPoint, gps_offset_m
from fleetdock.tasking.models import HeadingMode, MotionExecutionMode, RouteContinuity, RouteMotion, TaskContext
from fleetdock.tasking.motion import clamp, resolve_approach_speed, resolve_profile_speed
from fleetdock.tasking.routes import Route, Waypoint, WaypointAction
from fleetdock.tasking.trajectory import TrajectoryPath, TrajectoryPoint

if TYPE_CHECKING:
    from fleetdock.tasking.core import _TaskStep
    from fleetdock.tasking.models import TaskOptions

logger = logging.getLogger(__name__)


class _RouteDurationReached(RuntimeError):
    """内部控制流：航线达到调用方声明的正常执行时长上限。"""


class RouteExecutionMixin:
    """为单设备任务提供航线、航点事件和 SDK 平滑运动执行能力。"""

    _device: Any

    if TYPE_CHECKING:
        @staticmethod
        def _normalize_heading(heading: HeadingMode | str | None) -> HeadingMode: ...

        @staticmethod
        def _normalize_motion_mode(mode: MotionExecutionMode | str | None) -> MotionExecutionMode: ...

        def _raise_if_guard_triggered(self, ctx: TaskContext) -> None: ...

    def _route(self, step: _TaskStep, ctx: TaskContext, guard_interval_s: float) -> StandardResponse:
        route_ctx = self._with_route_motion(ctx, step.params.get("motion"))
        return self._execute_route(
            step.params["route"],
            route_ctx,
            guard_interval_s,
            altitude=step.params["altitude"],
            speed=step.params["speed"],
            heading=step.params["heading"],
            avoidance=step.params.get("avoidance"),
            max_duration_s=step.params.get("max_duration_s"),
            timeout_s=step.params.get("timeout_s"),
        )

    @staticmethod
    def _with_route_motion(ctx: TaskContext, motion: RouteMotion | None) -> TaskContext:
        if motion is None:
            return ctx
        options = replace(
            ctx.options,
            motion_mode=MotionExecutionMode(motion.mode),
            acceleration_m_s2=motion.acceleration_m_s2,
            deceleration_m_s2=motion.deceleration_m_s2,
            lookahead_time_s=motion.lookahead_time_s,
            min_lookahead_m=motion.min_lookahead_m,
            settle_time_s=motion.settle_time_s,
            settle_velocity_m_s=motion.settle_velocity_m_s,
        )
        return replace(ctx, options=options)

    def _execute_route(
        self,
        route: Route,
        ctx: TaskContext,
        guard_interval_s: float,
        *,
        altitude: Optional[float],
        speed: Optional[float],
        heading: Optional[HeadingMode],
        avoidance: Optional[AvoidancePolicy] = None,
        max_duration_s: Optional[float] = None,
        timeout_s: Optional[float] = None,
    ) -> StandardResponse:
        if len(route.waypoints) < 2:
            raise ValueError("route requires at least two waypoints.")
        base_speed = float(speed if speed is not None else ctx.options.speed_m_s)
        base_altitude = altitude if altitude is not None else ctx.options.altitude_m
        base_heading = self._normalize_heading(heading or ctx.options.heading)
        if base_speed <= 0:
            raise ValueError("speed must be greater than 0.")
        if max_duration_s is not None and max_duration_s <= 0:
            raise ValueError("max_duration_s must be greater than 0.")
        if timeout_s is not None and timeout_s <= 0:
            raise ValueError("timeout_s must be greater than 0.")
        route_timeout_s = float(timeout_s if timeout_s is not None else ctx.options.step_timeout_s)

        active_avoidance = self._active_route_avoidance(avoidance)
        native_response = self._try_execute_native_route(
            route,
            ctx,
            altitude=base_altitude,
            speed=base_speed,
            heading=base_heading,
            avoidance=active_avoidance,
            max_duration_s=max_duration_s,
            timeout_s=route_timeout_s,
        )
        if native_response is not None:
            return native_response

        origin_x, origin_y = self._require_local_xy(ctx)
        global_route = all(
            waypoint.latitude_deg is not None and waypoint.longitude_deg is not None
            for waypoint in route.waypoints
        )
        gps_origin: Optional[GpsPoint] = None
        if global_route:
            if ctx.state.latitude_deg is None or ctx.state.longitude_deg is None:
                raise TimeoutError("Global position telemetry is required for GPS route execution.")
            if ctx.state.home_absolute_altitude_m is None:
                raise TimeoutError("Home absolute altitude is required for GPS route execution.")
            gps_origin = GpsPoint(
                latitude_deg=float(ctx.state.latitude_deg),
                longitude_deg=float(ctx.state.longitude_deg),
                altitude_m=float(ctx.state.absolute_altitude_m or ctx.state.home_absolute_altitude_m),
            )
        self._publish_route_event(EventType.ROUTE_STARTED, route)
        last_response: Optional[StandardResponse] = None
        route_deadline_s = time.monotonic() + route_timeout_s
        duration_deadline_s = time.monotonic() + max_duration_s if max_duration_s is not None else None
        if (active_avoidance is not None and not active_avoidance.disabled_for_route) or self._should_use_continuous_sdk_route(route, ctx):
            try:
                last_response = self._execute_continuous_route(
                    route,
                    ctx,
                    origin_x=origin_x,
                    origin_y=origin_y,
                    gps_origin=gps_origin,
                    global_route=global_route,
                    base_altitude=base_altitude,
                    base_speed=base_speed,
                    heading=base_heading,
                    avoidance=active_avoidance,
                    guard_interval_s=guard_interval_s,
                    timeout_s=route_timeout_s,
                    duration_deadline_s=duration_deadline_s,
                )
            except _RouteDurationReached:
                logger.debug(
                    "航线达到正常执行时长上限: device_id=%s route=%s max_duration_s=%.3f",
                    getattr(ctx.device, "instance_id", None),
                    route.name or route.route_id,
                    max_duration_s,
                )
                return self._finish_route(route, RouteCompletionReason.DURATION_REACHED)
            if last_response is not None and not last_response.ok:
                return last_response
            return self._finish_route(
                route,
                RouteCompletionReason.ROUTE_FINISHED,
                use_hover=last_response is None,
                final_response=last_response,
            )

        try:
            first_index = 0 if global_route else 1
            for index in range(first_index, len(route.waypoints)):
                self._raise_if_route_duration_reached(duration_deadline_s)
                self._raise_if_guard_triggered(ctx)
                remaining_timeout_s = route_deadline_s - time.monotonic()
                if remaining_timeout_s <= 0:
                    raise TimeoutError(f"Route {route.name or route.route_id} did not complete before timeout.")
                waypoint = route.waypoints[index]
                self._publish_waypoint_event(EventType.WAYPOINT_STARTED, route, waypoint, index, None)
                if global_route:
                    if index == 0:
                        start_x, start_y = origin_x, origin_y
                    else:
                        start_x, start_y = self._gps_waypoint_local_xy(
                            route.waypoints[index - 1], gps_origin, origin_x, origin_y
                        )
                    target_x, target_y = self._gps_waypoint_local_xy(
                        waypoint, gps_origin, origin_x, origin_y
                    )
                else:
                    start = route.waypoints[index - 1]
                    start_x = origin_x + float(start.x_m or 0.0)
                    start_y = origin_y + float(start.y_m or 0.0)
                    target_x = origin_x + float(waypoint.x_m or 0.0)
                    target_y = origin_y + float(waypoint.y_m or 0.0)
                target_altitude = waypoint.z_m if waypoint.z_m is not None else waypoint.altitude_m
                if global_route and target_altitude is not None:
                    target_altitude = float(target_altitude) - float(ctx.state.home_absolute_altitude_m)
                if target_altitude is None:
                    target_altitude = base_altitude
                waypoint_speed = float(waypoint.speed_m_s if waypoint.speed_m_s is not None else base_speed)
                segment_dx = target_x - start_x
                segment_dy = target_y - start_y
                segment_distance = math.hypot(segment_dx, segment_dy)
                segment_yaw_deg = waypoint.yaw_deg
                if segment_yaw_deg is None and segment_distance > 0:
                    segment_yaw_deg = self._resolve_path_yaw_deg(base_heading, segment_dx / segment_distance, segment_dy / segment_distance)
                acceptance_radius_m = waypoint.acceptance_radius_m or ctx.options.position_tolerance_m
                settle_time_s = ctx.options.settle_time_s if waypoint.settle_time_s is None else waypoint.settle_time_s
                last_response = self._move_along_local_segment(
                    ctx,
                    start_x=start_x,
                    start_y=start_y,
                    target_x=target_x,
                    target_y=target_y,
                    target_altitude=target_altitude,
                    speed=waypoint_speed,
                    heading=base_heading,
                    guard_interval_s=guard_interval_s,
                    yaw_deg=segment_yaw_deg,
                    acceptance_radius_m=acceptance_radius_m,
                    settle_time_s=settle_time_s,
                    timeout_s=remaining_timeout_s,
                    duration_deadline_s=duration_deadline_s,
                )
                current_x, current_y = self._require_local_xy(ctx)
                distance_m = math.hypot(target_x - current_x, target_y - current_y)
                self._publish_waypoint_event(EventType.WAYPOINT_REACHED, route, waypoint, index, distance_m)
                self._publish_waypoint_event(EventType.WAYPOINT_SETTLED, route, waypoint, index, distance_m)
                self._run_waypoint_action(route, waypoint, index, distance_m)
                self._wait_route_hold(
                    waypoint.hold_time_s,
                    ctx,
                    guard_interval_s=guard_interval_s,
                    duration_deadline_s=duration_deadline_s,
                )
        except _RouteDurationReached:
            logger.debug(
                "航线达到正常执行时长上限: device_id=%s route=%s max_duration_s=%.3f",
                getattr(ctx.device, "instance_id", None),
                route.name or route.route_id,
                max_duration_s,
            )
            return self._finish_route(route, RouteCompletionReason.DURATION_REACHED)
        return self._finish_route(route, RouteCompletionReason.ROUTE_FINISHED, use_hover=last_response is None)

    @staticmethod
    def _gps_waypoint_local_xy(
        waypoint: Waypoint,
        gps_origin: Optional[GpsPoint],
        origin_x: float,
        origin_y: float,
    ) -> tuple[float, float]:
        """把 GPS 航点换算为相对当前本地原点的北向、东向米制坐标。"""

        if gps_origin is None or waypoint.latitude_deg is None or waypoint.longitude_deg is None:
            raise ValueError("GPS route waypoint requires latitude and longitude.")
        north_m, east_m = gps_offset_m(
            gps_origin,
            GpsPoint(
                latitude_deg=float(waypoint.latitude_deg),
                longitude_deg=float(waypoint.longitude_deg),
                altitude_m=float(waypoint.altitude_m or gps_origin.altitude_m),
            ),
        )
        return origin_x + north_m, origin_y + east_m

    def _try_execute_native_route(
        self,
        route: Route,
        ctx: TaskContext,
        *,
        altitude: Optional[float],
        speed: float,
        heading: HeadingMode,
        avoidance: Optional[AvoidancePolicy],
        max_duration_s: Optional[float],
        timeout_s: float,
    ) -> Optional[StandardResponse]:
        mode = self._normalize_motion_mode(ctx.options.motion_mode)
        if avoidance is not None and not avoidance.disabled_for_route:
            if mode in (MotionExecutionMode.NATIVE, MotionExecutionMode.FLIGHT_CONTROLLER):
                raise RuntimeError("Avoidance requires FleetDock SDK route execution; native route mode cannot monitor obstacles.")
            logger.debug(
                "航线启用避障，跳过原生航线执行: device_id=%s route=%s strategy=%s",
                getattr(ctx.device, "instance_id", None),
                route.name or route.route_id,
                avoidance.strategy.value,
            )
            return None
        if mode == MotionExecutionMode.SDK:
            return None
        continuity = self._normalize_route_continuity(route)
        if continuity is RouteContinuity.REQUIRED:
            if mode in (MotionExecutionMode.NATIVE, MotionExecutionMode.FLIGHT_CONTROLLER):
                raise RuntimeError("Route requires SDK continuous execution and cannot use native route mode.")
            logger.debug(
                "航线要求连续平滑执行，跳过原生航线: device_id=%s route=%s mode=%s",
                getattr(ctx.device, "instance_id", None),
                route.name or route.route_id,
                mode.value,
            )
            return None

        adapter = getattr(ctx.device, "adapter", None)
        def guard_callback() -> None:
            self._raise_if_guard_triggered(ctx)

        def waypoint_event_callback(
            event_type: Any,
            waypoint: Waypoint,
            index: int,
            distance_m: float | None = None,
        ) -> None:
            self._publish_waypoint_event(event_type, route, waypoint, index, distance_m)

        def route_event_callback(event_type: Any) -> None:
            self._publish_route_event(event_type, route)

        def completion_callback(reason: str) -> None:
            self._publish_route_event(
                EventType.ROUTE_COMPLETED,
                route,
                completion_reason=RouteCompletionReason(reason),
            )

        def waypoint_action_callback(
            waypoint: Waypoint,
            index: int,
            distance_m: float | None = None,
        ) -> None:
            self._run_waypoint_action(route, waypoint, index, distance_m)

        request = NativeRouteRequest(
            route=route,
            state=ctx.state,
            altitude_m=altitude,
            speed_m_s=speed,
            heading=heading,
            timeout_s=timeout_s,
            max_duration_s=max_duration_s,
            callbacks=NativeRouteCallbacks(
                guard=guard_callback,
                waypoint_event=waypoint_event_callback,
                route_event=route_event_callback,
                completion=completion_callback,
                waypoint_action=waypoint_action_callback,
            ),
        )
        supports_route = getattr(adapter, "supports_route", None)
        native_supported = bool(callable(supports_route) and supports_route(request))
        if not native_supported:
            if mode is MotionExecutionMode.FLIGHT_CONTROLLER:
                raise RuntimeError("Current adapter does not support native flight-controller route execution.")
            if mode is MotionExecutionMode.NATIVE:
                raise RuntimeError("Current adapter does not support native route execution.")
            logger.debug(
                "原生航线不可用，回退到 SDK 平滑: device_id=%s route=%s mode=%s",
                getattr(ctx.device, "instance_id", None),
                route.name,
                mode.value,
            )
            return None

        execute_route = getattr(adapter, "execute_route", None)
        if not callable(execute_route):
            if mode in (MotionExecutionMode.NATIVE, MotionExecutionMode.FLIGHT_CONTROLLER):
                raise RuntimeError("Current adapter cannot execute native routes.")
            return None

        logger.debug(
            "使用原生飞控航线执行: device_id=%s route=%s mode=%s waypoint_count=%d",
            getattr(ctx.device, "instance_id", None),
            route.name,
            mode.value,
            len(route.waypoints),
        )
        response = execute_route(request)
        if response.ok or mode in (MotionExecutionMode.NATIVE, MotionExecutionMode.FLIGHT_CONTROLLER):
            return response
        logger.debug(
            "原生航线执行失败，AUTO 回退到 SDK 平滑: device_id=%s route=%s message=%s",
            getattr(ctx.device, "instance_id", None),
            route.name,
            response.message,
        )
        return None

    def _execute_continuous_route(
        self,
        route: Route,
        ctx: TaskContext,
        *,
        origin_x: float,
        origin_y: float,
        gps_origin: Optional[GpsPoint],
        global_route: bool,
        base_altitude: Optional[float],
        base_speed: float,
        heading: HeadingMode,
        avoidance: Optional[AvoidancePolicy],
        guard_interval_s: float,
        timeout_s: float,
        duration_deadline_s: Optional[float],
    ) -> StandardResponse:
        path = self._build_trajectory_path(
            route,
            ctx,
            origin_x=origin_x,
            origin_y=origin_y,
            gps_origin=gps_origin,
            global_route=global_route,
            base_altitude=base_altitude,
            base_speed=base_speed,
        )
        logger.debug(
            "使用 SDK 连续航迹执行: device_id=%s route=%s length_m=%.3f waypoint_count=%d",
            getattr(ctx.device, "instance_id", None),
            route.name or route.route_id,
            path.length_m,
            len(route.waypoints),
        )
        started_indexes: set[int] = set()
        reached_indexes: set[int] = set()
        final_index = len(route.waypoints) - 1
        final_waypoint = route.waypoints[final_index]
        final_acceptance_radius_m = final_waypoint.acceptance_radius_m or ctx.options.position_tolerance_m
        current_speed_m_s = 0.0
        last_response: Optional[StandardResponse] = None
        stable_since_s: Optional[float] = None
        stopped_for_final_settle = False
        path_progress_m = 0.0
        replan_count = 0
        replan_wait_started_s: Optional[float] = None
        validated_detour_step_pending = False
        last_loop_at_s = time.monotonic()
        last_debug_at_s = last_loop_at_s
        deadline_s = last_loop_at_s + timeout_s

        while time.monotonic() < deadline_s:
            self._raise_if_route_duration_reached(duration_deadline_s)
            self._raise_if_guard_triggered(ctx)
            now_s = time.monotonic()
            dt_s = max(now_s - last_loop_at_s, guard_interval_s, 0.2)
            last_loop_at_s = now_s
            current_x, current_y = self._require_local_xy(ctx)
            lookahead_m = max(ctx.options.min_lookahead_m, current_speed_m_s * ctx.options.lookahead_time_s)
            progress_window_m = max(lookahead_m * 4.0, base_speed * max(dt_s, guard_interval_s) * 6.0, ctx.options.min_lookahead_m * 4.0)
            projected_progress_m = path.project_near(current_x, current_y, path_progress_m, progress_window_m)
            progress_m = max(path_progress_m, projected_progress_m)
            path_progress_m = progress_m
            remaining_m = max(0.0, path.length_m - progress_m)
            sample = path.sample(min(path.length_m, progress_m + lookahead_m))
            target_sample = path.sample(path.length_m)
            target_altitude = target_sample.z_m
            z = self._resolve_altitude_velocity(ctx, target_altitude)
            final_distance_m = math.hypot(target_sample.x_m - current_x, target_sample.y_m - current_y)
            speed_remaining_m = max(remaining_m, final_distance_m)
            avoidance_frame = self._route_avoidance_frame(ctx)
            current_altitude_up_m = self._current_altitude_up_m(ctx, target_sample.z_m)
            sample_altitude_up_m = sample.z_m if sample.z_m is not None else current_altitude_up_m
            if validated_detour_step_pending:
                decision = AvoidanceDecision(
                    _AvoidanceAction.ALLOW,
                    message="execute one control step on the validated local detour",
                    frame=avoidance_frame,
                )
                validated_detour_step_pending = False
            else:
                decision = self._evaluate_route_avoidance(
                    route,
                    current_x=current_x,
                    current_y=current_y,
                    current_z=self._altitude_up_to_frame_z(current_altitude_up_m, avoidance_frame),
                    sample=sample,
                    sample_z=self._altitude_up_to_frame_z(float(sample_altitude_up_m), avoidance_frame),
                    frame=avoidance_frame,
                    speed_m_s=max(current_speed_m_s, base_speed),
                    avoidance=avoidance,
                    replan_count=replan_count,
                )
            if decision.action is _AvoidanceAction.REPLAN and decision.detour_points_m:
                replan_wait_started_s = None
                replan_count += 1
                path = self._rebuild_path_with_detour(
                    route,
                    path,
                    current_x=current_x,
                    current_y=current_y,
                    current_z=current_altitude_up_m,
                    progress_m=progress_m,
                    detour_points=self._detours_to_route_altitude(decision.detour_points_m, decision.frame),
                    base_speed=base_speed,
                )
                path_progress_m = 0.0
                validated_detour_step_pending = True
                self._publish_route_replanned_event(route, decision)
                logger.debug(
                    "航线已插入避障绕行点: device_id=%s route=%s replan_count=%d detour_count=%d",
                    getattr(ctx.device, "instance_id", None),
                    route.name or route.route_id,
                    replan_count,
                    len(decision.detour_points_m),
                )
                time.sleep(guard_interval_s)
                continue
            if decision.action is _AvoidanceAction.ALLOW:
                replan_wait_started_s = None
            elif (
                avoidance is not None
                and avoidance.requests_local_replan
                and replan_count < avoidance.max_replans_per_route
                and avoidance.replan_retry_timeout_s > 0.0
            ):
                if replan_wait_started_s is None:
                    replan_wait_started_s = now_s
                    hover_decision = AvoidanceDecision(
                        _AvoidanceAction.HOVER,
                        risk=decision.risk,
                        message="local replan temporarily unavailable; waiting for a fresh obstacle snapshot",
                        frame=decision.frame,
                    )
                    response = ctx.device.avoidance.execute_action(hover_decision)
                    if response is not None and not response.ok:
                        return response
                if now_s - replan_wait_started_s < avoidance.replan_retry_timeout_s:
                    time.sleep(max(guard_interval_s, avoidance.replan_retry_interval_s))
                    continue
            if decision.action not in (_AvoidanceAction.ALLOW, _AvoidanceAction.REPLAN):
                response = ctx.device.avoidance.execute_action(decision)
                if response is not None and not response.ok:
                    return response
                raise AvoidanceBlockedError(decision)
            if now_s - last_debug_at_s >= 5.0:
                logger.debug(
                    "SDK 连续航迹进度: device_id=%s route=%s progress_m=%.2f length_m=%.2f final_distance_m=%.2f speed_m_s=%.2f z_cmd=%.2f",
                    getattr(ctx.device, "instance_id", None),
                    route.name or route.route_id,
                    progress_m,
                    path.length_m,
                    final_distance_m,
                    current_speed_m_s,
                    z,
                )
                last_debug_at_s = now_s

            for index, waypoint in enumerate(route.waypoints):
                waypoint_distance_m = path.waypoint_distance_m(index)
                acceptance_radius_m = waypoint.acceptance_radius_m or ctx.options.position_tolerance_m
                waypoint_sample = path.sample(waypoint_distance_m)
                distance_m = math.hypot(waypoint_sample.x_m - current_x, waypoint_sample.y_m - current_y)
                passed = progress_m + acceptance_radius_m >= waypoint_distance_m or distance_m <= acceptance_radius_m
                if passed and index not in started_indexes:
                    self._publish_waypoint_event(EventType.WAYPOINT_STARTED, route, waypoint, index, distance_m)
                    started_indexes.add(index)
                if not passed or index in reached_indexes:
                    continue
                self._publish_waypoint_event(EventType.WAYPOINT_REACHED, route, waypoint, index, distance_m)
                reached_indexes.add(index)
                if index != final_index and self._requires_stop_at_waypoint(waypoint):
                    stop_response = self._settle_at_waypoint(
                        route,
                        waypoint,
                        index,
                        distance_m,
                        ctx,
                        guard_interval_s=guard_interval_s,
                        duration_deadline_s=duration_deadline_s,
                    )
                    if not stop_response.ok:
                        return stop_response
                    last_response = stop_response

            if final_distance_m <= final_acceptance_radius_m and z == 0.0:
                if not stopped_for_final_settle:
                    last_response = self._device.stop()
                    if not last_response.ok:
                        hover = getattr(ctx.device, "hover", None)
                        if callable(hover):
                            last_response = hover()
                    stopped_for_final_settle = True
                    if not last_response.ok:
                        return last_response
                    current_speed_m_s = 0.0
                if self._is_motion_stable(ctx):
                    stable_since_s = stable_since_s or now_s
                else:
                    stable_since_s = None
                if stable_since_s is not None and now_s - stable_since_s >= ctx.options.settle_time_s:
                    if final_index not in started_indexes:
                        self._publish_waypoint_event(EventType.WAYPOINT_STARTED, route, final_waypoint, final_index, final_distance_m)
                    if final_index not in reached_indexes:
                        self._publish_waypoint_event(EventType.WAYPOINT_REACHED, route, final_waypoint, final_index, final_distance_m)
                    self._publish_waypoint_event(EventType.WAYPOINT_SETTLED, route, final_waypoint, final_index, final_distance_m)
                    self._run_waypoint_action(route, final_waypoint, final_index, final_distance_m)
                    self._wait_route_hold(
                        final_waypoint.hold_time_s,
                        ctx,
                        guard_interval_s=guard_interval_s,
                        duration_deadline_s=duration_deadline_s,
                    )
                    return last_response or self._device.hover()
                time.sleep(guard_interval_s)
                continue

            stopped_for_final_settle = False
            direction_x = sample.x_m - current_x
            direction_y = sample.y_m - current_y
            direction_length_m = math.hypot(direction_x, direction_y)
            if direction_length_m <= 0:
                direction_x = sample.tangent_x
                direction_y = sample.tangent_y
                direction_length_m = math.hypot(direction_x, direction_y) or 1.0
            unit_x = direction_x / direction_length_m
            unit_y = direction_y / direction_length_m
            target_speed_m_s = float(sample.speed_m_s if sample.speed_m_s is not None else base_speed)
            current_speed_m_s = self._resolve_profile_speed(
                current_speed_m_s,
                target_speed_m_s,
                speed_remaining_m,
                dt_s,
                ctx.options,
            )
            move_yaw_deg = self._resolve_path_yaw_deg(heading, sample.tangent_x, sample.tangent_y)
            last_response = self._move_with_optional_yaw(
                x=unit_x,
                y=unit_y,
                z=z,
                speed=current_speed_m_s,
                yaw_deg=move_yaw_deg,
            )
            if not last_response.ok:
                return last_response
            time.sleep(guard_interval_s)
        raise TimeoutError(f"Route {route.name or route.route_id} did not complete before timeout.")

    def _build_trajectory_path(
        self,
        route: Route,
        ctx: TaskContext,
        *,
        origin_x: float,
        origin_y: float,
        gps_origin: Optional[GpsPoint],
        global_route: bool,
        base_altitude: Optional[float],
        base_speed: float,
    ) -> TrajectoryPath:
        points: list[TrajectoryPoint] = []
        for index, waypoint in enumerate(route.waypoints):
            if global_route:
                x_m, y_m = self._gps_waypoint_local_xy(waypoint, gps_origin, origin_x, origin_y)
            else:
                x_m = origin_x + float(waypoint.x_m or 0.0)
                y_m = origin_y + float(waypoint.y_m or 0.0)
            z_m = waypoint.z_m if waypoint.z_m is not None else waypoint.altitude_m
            if global_route and z_m is not None:
                z_m = float(z_m) - float(ctx.state.home_absolute_altitude_m)
            if z_m is None:
                z_m = base_altitude
            points.append(
                TrajectoryPoint(
                    x_m=x_m,
                    y_m=y_m,
                    z_m=z_m,
                    waypoint=waypoint,
                    waypoint_index=index,
                    speed_m_s=float(waypoint.speed_m_s if waypoint.speed_m_s is not None else base_speed),
                )
            )
        return TrajectoryPath(points)

    def _active_route_avoidance(self, avoidance: Optional[AvoidancePolicy]) -> Optional[AvoidancePolicy]:
        """解析当前航线实际使用的避障策略。

        显式传入 ``avoidance`` 时以单次航线参数为准；未传入时读取 ``device.avoidance.configure(...)``
        保存的设备默认策略。返回 ``None`` 表示本次航线不启用 SDK 避障。
        """

        if avoidance is not None:
            return avoidance
        manager = getattr(self._device, "avoidance", None)
        if manager is None:
            return None
        policy = manager.policy
        return policy if manager.enabled and not policy.disabled_for_route else None

    @staticmethod
    def _route_avoidance_frame(ctx: TaskContext) -> CoordinateFrame:
        frame = ctx.state.local_position_frame
        if frame in (CoordinateFrame.LOCAL_NED, CoordinateFrame.LOCAL_ENU):
            return frame
        return CoordinateFrame.UNKNOWN

    @staticmethod
    def _current_altitude_up_m(ctx: TaskContext, fallback_up_m: Optional[float]) -> float:
        if ctx.state.relative_altitude_m is not None:
            return float(ctx.state.relative_altitude_m)
        if ctx.state.local_z_m is not None:
            local_z_m = float(ctx.state.local_z_m)
            if ctx.state.local_position_frame is CoordinateFrame.LOCAL_NED:
                return -local_z_m
            return local_z_m
        return float(fallback_up_m or 0.0)

    @staticmethod
    def _altitude_up_to_frame_z(altitude_up_m: float, frame: CoordinateFrame) -> float:
        if frame is CoordinateFrame.LOCAL_NED:
            return -float(altitude_up_m)
        return float(altitude_up_m)

    @classmethod
    def _frame_z_to_route_altitude(cls, z_m: float, frame: CoordinateFrame) -> float:
        if frame is CoordinateFrame.LOCAL_NED:
            return -float(z_m)
        return float(z_m)

    @classmethod
    def _detours_to_route_altitude(cls, detours: tuple[Vector3, ...], frame: CoordinateFrame) -> tuple[Vector3, ...]:
        return tuple(
            Vector3(detour.x, detour.y, cls._frame_z_to_route_altitude(detour.z, frame))
            for detour in detours
        )

    def _evaluate_route_avoidance(
        self,
        route: Route,
        *,
        current_x: float,
        current_y: float,
        current_z: float,
        sample: TrajectoryPoint | Any,
        sample_z: float,
        frame: CoordinateFrame,
        speed_m_s: float,
        avoidance: Optional[AvoidancePolicy],
        replan_count: int,
    ):
        """检查当前 route 前视段的避障风险并返回决策。"""
        manager = getattr(self._device, "avoidance", None)
        if manager is None:
            return AvoidanceDecision(_AvoidanceAction.ALLOW, message="avoidance manager missing")
        active_policy = avoidance or manager.policy
        if active_policy is None or active_policy.disabled_for_route:
            return AvoidanceDecision(_AvoidanceAction.ALLOW, message="avoidance disabled")
        if avoidance is None and not getattr(manager, "enabled", False):
            return AvoidanceDecision(_AvoidanceAction.ALLOW, message="avoidance disabled")
        return manager.evaluate_segment(
            start_m=Vector3(current_x, current_y, current_z),
            target_m=Vector3(sample.x_m, sample.y_m, sample_z),
            frame=frame,
            speed_m_s=speed_m_s,
            policy=active_policy,
            allow_replan=active_policy.requests_local_replan,
            replan_count=replan_count,
        )

    def _rebuild_path_with_detour(
        self,
        route: Route,
        path: TrajectoryPath,
        *,
        current_x: float,
        current_y: float,
        current_z: float,
        progress_m: float,
        detour_points: tuple[Vector3, ...],
        base_speed: float,
    ) -> TrajectoryPath:
        """把当前点、临时绕行点和绕行终点前方的原路线拼成新的 SDK 执行路径。"""
        current_index = path.source_waypoint_index_at_or_before(progress_m)
        anchor_waypoint = route.waypoints[current_index] if current_index is not None else route.waypoints[-1]
        points = [
            TrajectoryPoint(
                x_m=current_x,
                y_m=current_y,
                z_m=current_z,
                waypoint=anchor_waypoint,
                waypoint_index=current_index,
                speed_m_s=base_speed,
            )
        ]
        for detour in detour_points:
            points.append(
                TrajectoryPoint(
                    x_m=detour.x,
                    y_m=detour.y,
                    z_m=detour.z,
                    waypoint=anchor_waypoint,
                    waypoint_index=None,
                    speed_m_s=base_speed,
                )
            )
        rejoin_progress_m = progress_m
        if detour_points:
            detour_end = detour_points[-1]
            detour_length_m = 0.0
            previous_x, previous_y = current_x, current_y
            for detour in detour_points:
                detour_length_m += math.hypot(detour.x - previous_x, detour.y - previous_y)
                previous_x, previous_y = detour.x, detour.y
            rejoin_progress_m = max(
                progress_m,
                path.project_near(
                    detour_end.x,
                    detour_end.y,
                    progress_m,
                    max(detour_length_m * 1.5, 1.0),
                ),
            )
        points.extend(path.source_points_after(rejoin_progress_m))
        if len(points) < 2:
            final = path.points[-1]
            points.append(final)
        return TrajectoryPath(points)

    def _publish_route_replanned_event(self, route: Route, decision: Any) -> None:
        """发布 SDK 局部重规划事件。"""
        risk = getattr(decision, "risk", None)
        obstacle = getattr(risk, "obstacle", None) if risk is not None else None
        self._device.events.publish(
            EventType.ROUTE_REPLANNED,
            EventCategory.STATE,
            {
                "route_id": route.route_id,
                "route_name": route.name,
                "reason": decision.message,
                "detour_count": len(decision.detour_points_m),
                "metadata": {
                    "obstacle_id": getattr(obstacle, "obstacle_id", None),
                    "action": decision.action.value,
                },
            },
        )

    def _settle_at_waypoint(
        self,
        route: Route,
        waypoint: Waypoint,
        waypoint_index: int,
        distance_m: Optional[float],
        ctx: TaskContext,
        *,
        guard_interval_s: float,
        duration_deadline_s: Optional[float],
    ) -> StandardResponse:
        response = self._device.stop()
        if not response.ok:
            return response
        settle_time_s = ctx.options.settle_time_s if waypoint.settle_time_s is None else waypoint.settle_time_s
        stable_since_s: Optional[float] = None
        while True:
            self._raise_if_route_duration_reached(duration_deadline_s)
            self._raise_if_guard_triggered(ctx)
            now_s = time.monotonic()
            if self._is_motion_stable(ctx):
                stable_since_s = stable_since_s or now_s
            else:
                stable_since_s = None
            if stable_since_s is not None and now_s - stable_since_s >= settle_time_s:
                break
            time.sleep(guard_interval_s)
        self._publish_waypoint_event(EventType.WAYPOINT_SETTLED, route, waypoint, waypoint_index, distance_m)
        self._run_waypoint_action(route, waypoint, waypoint_index, distance_m)
        self._wait_route_hold(
            waypoint.hold_time_s,
            ctx,
            guard_interval_s=guard_interval_s,
            duration_deadline_s=duration_deadline_s,
        )
        return response

    @staticmethod
    def _should_use_continuous_sdk_route(route: Route, ctx: TaskContext) -> bool:
        mode = ctx.options.motion_mode
        if not isinstance(mode, MotionExecutionMode):
            mode = MotionExecutionMode(str(mode))
        continuity = RouteExecutionMixin._normalize_route_continuity(route)
        if mode == MotionExecutionMode.SDK:
            return continuity in (RouteContinuity.PREFERRED, RouteContinuity.REQUIRED)
        if mode == MotionExecutionMode.AUTO:
            return continuity is RouteContinuity.REQUIRED
        return False

    @staticmethod
    def _normalize_route_continuity(route: Route) -> RouteContinuity:
        if isinstance(route.continuity, RouteContinuity):
            return route.continuity
        return RouteContinuity(str(route.continuity))

    @staticmethod
    def _requires_stop_at_waypoint(waypoint: Waypoint) -> bool:
        action = waypoint.action.value if isinstance(waypoint.action, Enum) else str(waypoint.action)
        return action != WaypointAction.NONE.value or waypoint.hold_time_s > 0.0

    def _move_along_local_segment(
        self,
        ctx: TaskContext,
        *,
        start_x: float,
        start_y: float,
        target_x: float,
        target_y: float,
        target_altitude: Any,
        speed: float,
        heading: HeadingMode,
        guard_interval_s: float,
        yaw_deg: Optional[float] = None,
        acceptance_radius_m: Optional[float] = None,
        settle_time_s: Optional[float] = None,
        timeout_s: Optional[float] = None,
        duration_deadline_s: Optional[float] = None,
    ) -> StandardResponse:
        segment_x = target_x - start_x
        segment_y = target_y - start_y
        segment_length_m = math.hypot(segment_x, segment_y)
        if segment_length_m <= 0:
            return self._move_to_local_target(
                ctx,
                target_x=target_x,
                target_y=target_y,
                target_altitude=target_altitude,
                speed=speed,
                heading=heading,
                guard_interval_s=guard_interval_s,
                yaw_deg=yaw_deg,
                timeout_s=timeout_s,
            )

        unit_segment_x = segment_x / segment_length_m
        unit_segment_y = segment_y / segment_length_m
        position_tolerance_m = float(acceptance_radius_m if acceptance_radius_m is not None else ctx.options.position_tolerance_m)
        required_settle_time_s = float(settle_time_s if settle_time_s is not None else ctx.options.settle_time_s)
        current_speed_m_s = 0.0
        last_response: Optional[StandardResponse] = None
        stable_since_s: Optional[float] = None
        stopped_for_settle = False
        last_loop_at_s = time.monotonic()
        deadline = last_loop_at_s + float(timeout_s if timeout_s is not None else ctx.options.step_timeout_s)
        while time.monotonic() < deadline:
            self._raise_if_route_duration_reached(duration_deadline_s)
            self._raise_if_guard_triggered(ctx)
            now_s = time.monotonic()
            dt_s = max(now_s - last_loop_at_s, guard_interval_s, 0.2)
            last_loop_at_s = now_s
            current_x, current_y = self._require_local_xy(ctx)
            from_start_x = current_x - start_x
            from_start_y = current_y - start_y
            progress_m = self._clamp(
                from_start_x * unit_segment_x + from_start_y * unit_segment_y,
                0.0,
                segment_length_m,
            )
            remaining_m = segment_length_m - progress_m
            end_distance_m = math.hypot(target_x - current_x, target_y - current_y)
            z = self._resolve_altitude_velocity(ctx, target_altitude)
            at_target = end_distance_m <= position_tolerance_m and z == 0.0
            if at_target:
                if not stopped_for_settle:
                    last_response = self._device.stop()
                    stopped_for_settle = True
                    if not last_response.ok:
                        return last_response
                if self._is_motion_stable(ctx):
                    stable_since_s = stable_since_s or now_s
                else:
                    stable_since_s = None
                if stable_since_s is not None and now_s - stable_since_s >= required_settle_time_s:
                    return last_response or self._device.hover()
                current_speed_m_s = 0.0
                time.sleep(guard_interval_s)
                continue

            stable_since_s = None
            stopped_for_settle = False
            lookahead_m = max(ctx.options.min_lookahead_m, current_speed_m_s * ctx.options.lookahead_time_s)
            follow_progress_m = min(segment_length_m, progress_m + lookahead_m)
            follow_x = start_x + unit_segment_x * follow_progress_m
            follow_y = start_y + unit_segment_y * follow_progress_m
            direction_x = follow_x - current_x
            direction_y = follow_y - current_y
            direction_length_m = math.hypot(direction_x, direction_y)
            if direction_length_m <= 0:
                direction_x = unit_segment_x
                direction_y = unit_segment_y
                direction_length_m = 1.0
            unit_x = direction_x / direction_length_m
            unit_y = direction_y / direction_length_m
            current_speed_m_s = self._resolve_profile_speed(
                current_speed_m_s,
                speed,
                remaining_m,
                dt_s,
                ctx.options,
            )
            move_yaw_deg = yaw_deg if yaw_deg is not None else self._resolve_path_yaw_deg(heading, unit_x, unit_y)
            last_response = self._move_with_optional_yaw(
                x=unit_x,
                y=unit_y,
                z=z,
                speed=current_speed_m_s,
                yaw_deg=move_yaw_deg,
            )
            if not last_response.ok:
                return last_response
            time.sleep(guard_interval_s)
        raise TimeoutError(f"Position did not reach x={target_x:.1f} m y={target_y:.1f} m before timeout.")

    def _move_to_local_target(
        self,
        ctx: TaskContext,
        *,
        target_x: float,
        target_y: float,
        target_altitude: Any,
        speed: float,
        heading: HeadingMode,
        guard_interval_s: float,
        yaw_deg: Optional[float] = None,
        timeout_s: Optional[float] = None,
    ) -> StandardResponse:
        last_response: Optional[StandardResponse] = None
        deadline = time.monotonic() + float(timeout_s if timeout_s is not None else ctx.options.step_timeout_s)
        while time.monotonic() < deadline:
            self._raise_if_guard_triggered(ctx)
            current_x, current_y = self._require_local_xy(ctx)
            remaining_x = target_x - current_x
            remaining_y = target_y - current_y
            distance_m = math.hypot(remaining_x, remaining_y)
            z = self._resolve_altitude_velocity(ctx, target_altitude)
            if distance_m <= ctx.options.position_tolerance_m and z == 0.0:
                if last_response is not None:
                    return last_response
                return self._device.hover()
            if distance_m > 0:
                unit_x = remaining_x / distance_m
                unit_y = remaining_y / distance_m
            else:
                unit_x = 0.0
                unit_y = 0.0
            move_yaw_deg = yaw_deg if yaw_deg is not None else self._resolve_path_yaw_deg(heading, unit_x, unit_y)
            move_speed = self._resolve_approach_speed(speed, distance_m, ctx.options.position_tolerance_m)
            last_response = self._move_with_optional_yaw(
                x=unit_x,
                y=unit_y,
                z=z,
                speed=move_speed,
                yaw_deg=move_yaw_deg,
            )
            if not last_response.ok:
                return last_response
            time.sleep(guard_interval_s)
        raise TimeoutError(f"Position did not reach x={target_x:.1f} m y={target_y:.1f} m before timeout.")

    def _publish_route_event(
        self,
        event_type: EventType,
        route: Route,
        *,
        completion_reason: Optional[RouteCompletionReason] = None,
    ) -> None:
        self._device.events.publish(
            event_type,
            EventCategory.STATE,
            RouteProgress(
                route_id=route.route_id,
                route_name=route.name,
                waypoint_count=len(route.waypoints),
                pattern=route.pattern.value if isinstance(route.pattern, Enum) else str(route.pattern),
                completion_reason=completion_reason,
            ),
        )

    def _finish_route(
        self,
        route: Route,
        reason: RouteCompletionReason,
        *,
        use_hover: bool = False,
        final_response: Optional[StandardResponse] = None,
    ) -> StandardResponse:
        """安全结束航线并把正常完成原因写入事件和标准响应。"""
        if final_response is not None:
            response = final_response
        else:
            hover = getattr(self._device, "hover", None)
            response = hover() if use_hover and callable(hover) else self._device.stop()
        self._publish_route_event(EventType.ROUTE_COMPLETED, route, completion_reason=reason)
        return replace(
            response,
            message=f"Route completed: {reason.value}.",
            data={**response.data, "route_completion_reason": reason.value},
        )

    def _wait_route_hold(
        self,
        hold_time_s: float,
        ctx: TaskContext,
        *,
        guard_interval_s: float,
        duration_deadline_s: Optional[float],
    ) -> None:
        """等待航点停留时间，同时保持时长上限和任务守卫可响应。"""
        deadline_s = time.monotonic() + max(0.0, hold_time_s)
        interval_s = min(0.2, max(0.001, guard_interval_s))
        while time.monotonic() < deadline_s:
            self._raise_if_route_duration_reached(duration_deadline_s)
            self._raise_if_guard_triggered(ctx)
            time.sleep(min(interval_s, max(0.0, deadline_s - time.monotonic())))

    @staticmethod
    def _raise_if_route_duration_reached(duration_deadline_s: Optional[float]) -> None:
        if duration_deadline_s is not None and time.monotonic() >= duration_deadline_s:
            raise _RouteDurationReached()

    def _publish_waypoint_event(
        self,
        event_type: EventType,
        route: Route,
        waypoint: Waypoint,
        waypoint_index: int,
        distance_m: Optional[float],
    ) -> None:
        self._device.events.publish(
            event_type,
            EventCategory.STATE,
            WaypointProgress(
                route_id=route.route_id,
                route_name=route.name,
                waypoint_id=waypoint.waypoint_id,
                waypoint_index=waypoint_index,
                waypoint_count=len(route.waypoints),
                x_m=waypoint.x_m,
                y_m=waypoint.y_m,
                z_m=waypoint.z_m,
                latitude_deg=waypoint.latitude_deg,
                longitude_deg=waypoint.longitude_deg,
                altitude_m=waypoint.altitude_m,
                distance_m=distance_m,
                action=waypoint.action.value if isinstance(waypoint.action, Enum) else str(waypoint.action),
                metadata=dict(waypoint.metadata),
            ),
        )

    def _run_waypoint_action(
        self,
        route: Route,
        waypoint: Waypoint,
        waypoint_index: int,
        distance_m: Optional[float],
    ) -> None:
        action = waypoint.action.value if isinstance(waypoint.action, Enum) else str(waypoint.action)
        if action == WaypointAction.NONE.value:
            return
        self._publish_waypoint_event(EventType.WAYPOINT_ACTION_REQUESTED, route, waypoint, waypoint_index, distance_m)
        if action == WaypointAction.HOVER.value:
            self._device.hover()

    def _is_motion_stable(self, ctx: TaskContext) -> bool:
        x = ctx.state.velocity_x_m_s
        y = ctx.state.velocity_y_m_s
        z = ctx.state.velocity_z_m_s
        if x is None or y is None or z is None:
            return True
        return math.sqrt(float(x) ** 2 + float(y) ** 2 + float(z) ** 2) <= ctx.options.settle_velocity_m_s

    @staticmethod
    def _resolve_approach_speed(speed_m_s: float, distance_m: float, tolerance_m: float) -> float:
        return resolve_approach_speed(speed_m_s, distance_m, tolerance_m)

    @staticmethod
    def _resolve_profile_speed(
        current_speed_m_s: float,
        max_speed_m_s: float,
        remaining_m: float,
        dt_s: float,
        options: TaskOptions,
    ) -> float:
        return resolve_profile_speed(current_speed_m_s, max_speed_m_s, remaining_m, dt_s, options)

    @staticmethod
    def _clamp(value: float, lower: float, upper: float) -> float:
        return clamp(value, lower, upper)

    @staticmethod
    def _require_local_xy(ctx: TaskContext) -> tuple[float, float]:
        if ctx.state.local_x_m is None or ctx.state.local_y_m is None:
            raise TimeoutError("Local position telemetry is required for target-based route confirmation.")
        return float(ctx.state.local_x_m), float(ctx.state.local_y_m)

    @staticmethod
    def _resolve_altitude_velocity(ctx: TaskContext, target_altitude: Any) -> float:
        if target_altitude is None or ctx.state.relative_altitude_m is None:
            return 0.0
        altitude = float(ctx.state.relative_altitude_m)
        target = float(target_altitude)
        tolerance = ctx.options.altitude_tolerance_m
        if altitude < target - tolerance:
            return 1.0
        if altitude > target + tolerance:
            return -1.0
        return 0.0

    def _move_with_optional_yaw(
        self,
        *,
        x: float,
        y: float,
        z: float,
        speed: float,
        yaw_deg: Optional[float],
    ) -> StandardResponse:
        if yaw_deg is None:
            return self._device.set_velocity(x=x, y=y, z=z, speed=speed)
        return self._device.set_velocity(x=x, y=y, z=z, speed=speed, yaw_deg=yaw_deg)

    @staticmethod
    def _resolve_path_yaw_deg(heading: HeadingMode, x: float, y: float) -> Optional[float]:
        if heading == HeadingMode.TANGENT:
            return math.degrees(math.atan2(y, x))
        if heading == HeadingMode.FIXED:
            return 0.0
        return None

    @staticmethod
    def _resolve_circle_yaw_deg(heading: HeadingMode, angle_rad: float, x: float, y: float) -> Optional[float]:
        if heading == HeadingMode.TANGENT:
            return math.degrees(math.atan2(y, x))
        if heading == HeadingMode.CENTER:
            center_x = -math.cos(angle_rad)
            center_y = -math.sin(angle_rad)
            return math.degrees(math.atan2(center_y, center_x))
        if heading == HeadingMode.FIXED:
            return 0.0
        return None

