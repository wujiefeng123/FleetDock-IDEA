import time
import unittest
from dataclasses import dataclass

from fleetdock import (
    AvoidanceDimensions,
    AvoidanceManager,
    AvoidancePlanRequest,
    AvoidancePlanResult,
    AvoidancePlanner,
    AvoidancePolicy,
    AvoidanceStrategy,
    CoordinateFrame,
    DeviceCategory,
    DeviceEventStream,
    DeviceMetadata,
    EventSource,
    EventType,
    MotionExecutionMode,
    Obstacle,
    ObstacleCapacityPolicy,
    ObstacleKind,
    ObstacleShape,
    ObstacleSnapshot,
    PlannerStatus,
    Route,
    RouteMotion,
    StandardResponse,
    TaskStatus,
    Vector3,
    Waypoint,
)
from fleetdock.devices import DeviceStateView
from fleetdock.tasking.device import DronePlan
from fleetdock.tasking.trajectory import TrajectoryPath, TrajectoryPoint


class StaticObstacleProvider:
    def __init__(self, obstacle: Obstacle) -> None:
        self.obstacle = obstacle
        self.closed = False

    def snapshot(self) -> ObstacleSnapshot:
        return ObstacleSnapshot(
            provider_name="test.static",
            frame=self.obstacle.frame,
            observed_at_s=time.monotonic(),
            obstacles=(self.obstacle,),
        )

    def close(self) -> None:
        self.closed = True


class OneShotObstacleProvider(StaticObstacleProvider):
    def __init__(self, obstacle: Obstacle) -> None:
        super().__init__(obstacle)
        self._emitted = False

    def snapshot(self) -> ObstacleSnapshot:
        if self._emitted:
            return ObstacleSnapshot(
                provider_name="test.one_shot",
                frame=self.obstacle.frame,
                observed_at_s=time.monotonic(),
                obstacles=(),
            )
        self._emitted = True
        return super().snapshot()


class TransientObstacleProvider(StaticObstacleProvider):
    def __init__(self, obstacle: Obstacle, blocked_snapshots: int = 2) -> None:
        super().__init__(obstacle)
        self._remaining = blocked_snapshots

    def snapshot(self) -> ObstacleSnapshot:
        if self._remaining > 0:
            self._remaining -= 1
            return super().snapshot()
        return ObstacleSnapshot(
            provider_name="test.transient",
            frame=self.obstacle.frame,
            observed_at_s=time.monotonic(),
            obstacles=(),
        )


class ManyObstacleProvider:
    def __init__(self, obstacles: tuple[Obstacle, ...]) -> None:
        self.obstacles = obstacles
        self.snapshot_calls = 0
        self.close_calls = 0

    def snapshot(self) -> ObstacleSnapshot:
        self.snapshot_calls += 1
        return ObstacleSnapshot(
            provider_name="test.many",
            frame=CoordinateFrame.LOCAL_NED,
            observed_at_s=time.monotonic(),
            obstacles=self.obstacles,
        )

    def close(self) -> None:
        self.close_calls += 1


@dataclass
class FakeProfile:
    category: DeviceCategory


class FakeAvoidanceDevice:
    def __init__(self, category: DeviceCategory) -> None:
        self.instance_id = f"test-{category.value}"
        self.metadata = DeviceMetadata(device_id=self.instance_id)
        self.profile = FakeProfile(category)
        self.calls = []
        self.events = DeviceEventStream(
            EventSource(
                device_id=self.instance_id,
                product="test.product",
                device_category=category.value,
                adapter="FakeAdapter",
                instance_id=self.instance_id,
            )
        )

    def hover(self) -> StandardResponse:
        self.calls.append("hover")
        return StandardResponse(True, "hover", self.instance_id, "test.product", message="hover")

    def stop(self) -> StandardResponse:
        self.calls.append("stop")
        return StandardResponse(True, "stop", self.instance_id, "test.product", message="stop")

    def emergency_stop(self) -> StandardResponse:
        self.calls.append("emergency_stop")
        return self.stop()


class RouteFakeDrone(FakeAvoidanceDevice):
    def __init__(self) -> None:
        super().__init__(DeviceCategory.DRONE)
        self.state = DeviceStateView()
        self.state._update(
            connected=True,
            armed=True,
            in_air=True,
            relative_altitude_m=5.0,
            local_x_m=0.0,
            local_y_m=0.0,
            local_z_m=5.0,
            velocity_x_m_s=0.0,
            velocity_y_m_s=0.0,
            velocity_z_m_s=0.0,
        )
        self.avoidance = AvoidanceManager(self)

    def set_velocity(
        self,
        *,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        speed: float = 0.0,
        yaw_deg=None,
    ) -> StandardResponse:
        self.calls.append(("set_velocity", round(x, 3), round(y, 3), round(z, 3), round(speed, 3)))
        step_m = max(0.05, speed * 0.2)
        state = self.state.snapshot()
        relative_altitude_m = max(0.0, float(state.relative_altitude_m or 0.0) + z * 0.2)
        self.state._update(
            local_x_m=float(state.local_x_m or 0.0) + x * step_m,
            local_y_m=float(state.local_y_m or 0.0) + y * step_m,
            relative_altitude_m=relative_altitude_m,
            local_z_m=relative_altitude_m,
            velocity_x_m_s=x * speed,
            velocity_y_m_s=y * speed,
            velocity_z_m_s=z * speed,
        )
        return StandardResponse(True, "velocity", self.instance_id, "test.product", message="velocity")

    def stop(self) -> StandardResponse:
        self.calls.append("stop")
        self.state._update(velocity_x_m_s=0.0, velocity_y_m_s=0.0, velocity_z_m_s=0.0)
        return StandardResponse(True, "stop", self.instance_id, "test.product", message="stop")

    def return_to_launch(self) -> StandardResponse:
        self.calls.append("return_to_launch")
        self.state._update(local_x_m=0.0, local_y_m=0.0)
        return StandardResponse(True, "rtl", self.instance_id, "test.product", message="rtl")


class NativeRouteAdapter:
    def __init__(self, device: RouteFakeDrone) -> None:
        self.device = device
        self.executed = 0

    def supports_route(self, request) -> bool:
        return True

    def execute_route(self, request) -> StandardResponse:
        self.executed += 1
        return StandardResponse(True, "native_route", self.device.instance_id, "test.product", message="native")


class ExternalPlannerBackend:
    def __init__(self) -> None:
        self.requests: list[AvoidancePlanRequest] = []

    def plan(self, request: AvoidancePlanRequest) -> AvoidancePlanResult:
        self.requests.append(request)
        return AvoidancePlanResult(
            PlannerStatus.REPLAN,
            detour_points_m=(Vector3(request.start_m.x, request.start_m.y + 2.0, request.start_m.z),),
            message="external test backend",
        )


class NoSafePathBackend:
    def plan(self, request: AvoidancePlanRequest) -> AvoidancePlanResult:
        return AvoidancePlanResult(PlannerStatus.NO_SAFE_PATH, message="temporarily blocked")


class AvoidanceManagerTest(unittest.TestCase):
    def tearDown(self) -> None:
        for device in getattr(self, "_devices", ()):
            device.events.close()

    def _device(self, category: DeviceCategory) -> FakeAvoidanceDevice:
        device = FakeAvoidanceDevice(category)
        self._devices = (*getattr(self, "_devices", ()), device)
        return device

    def test_policy_uses_strategy_enum_and_explicit_disabled_placeholder(self) -> None:
        stop_policy = AvoidancePolicy(
            dimensions=AvoidanceDimensions.TWO_D,
            strategy=AvoidanceStrategy.STOP,
        )
        replan = AvoidancePolicy(
            dimensions=AvoidanceDimensions.THREE_D,
            strategy=AvoidanceStrategy.LOCAL_REPLAN,
            fallback_strategy=AvoidanceStrategy.RETURN_TO_LAUNCH,
            replan_lateral_clearance_m=3.0,
            max_replans_per_route=2,
            replan_retry_timeout_s=4.0,
            replan_retry_interval_s=0.25,
        )
        disabled = AvoidancePolicy.disabled()

        self.assertIs(stop_policy.dimensions, AvoidanceDimensions.TWO_D)
        self.assertIs(stop_policy.strategy, AvoidanceStrategy.STOP)
        self.assertIs(stop_policy.planner, AvoidancePlanner.TRAJECTORY_ROLLOUT)
        self.assertFalse(stop_policy.requests_local_replan)
        self.assertEqual(stop_policy.fallback_action.value, "stop")
        self.assertIs(replan.strategy, AvoidanceStrategy.LOCAL_REPLAN)
        self.assertIs(replan.planner, AvoidancePlanner.TRAJECTORY_ROLLOUT)
        self.assertIs(replan.fallback_strategy, AvoidanceStrategy.RETURN_TO_LAUNCH)
        self.assertTrue(replan.requests_local_replan)
        self.assertEqual(replan.fallback_action.value, "return_to_launch")
        self.assertEqual(replan.replan_lateral_clearance_m, 3.0)
        self.assertEqual(replan.max_replans_per_route, 2)
        self.assertEqual(replan.replan_retry_timeout_s, 4.0)
        self.assertEqual(replan.replan_retry_interval_s, 0.25)
        self.assertTrue(disabled.disabled_for_route)

    def test_manager_enable_and_disable_are_runtime_switches(self) -> None:
        device = self._device(DeviceCategory.DRONE)
        manager = AvoidanceManager(device)
        manager.add_provider(
            StaticObstacleProvider(
                Obstacle(
                    obstacle_id="runtime-switch",
                    kind=ObstacleKind.STATIC,
                    frame=CoordinateFrame.LOCAL_NED,
                    position_m=Vector3(2.0, 0.0, 0.0),
                    radius_m=0.5,
                    observed_at_s=time.monotonic(),
                    ttl_s=2.0,
                )
            )
        )
        policy = AvoidancePolicy(
            dimensions=AvoidanceDimensions.THREE_D,
            min_clearance_m=1.0,
            device_radius_m=0.5,
            strategy=AvoidanceStrategy.HOLD_POSITION,
        )

        self.assertFalse(manager.status().enabled)
        disabled_decision = manager.evaluate_segment(
            start_m=Vector3(0.0, 0.0, 0.0),
            target_m=Vector3(5.0, 0.0, 0.0),
            speed_m_s=1.0,
        )
        manager.configure(policy)
        enabled_decision = manager.evaluate_segment(
            start_m=Vector3(0.0, 0.0, 0.0),
            target_m=Vector3(5.0, 0.0, 0.0),
            speed_m_s=1.0,
        )
        manager.disable()
        paused_decision = manager.evaluate_segment(
            start_m=Vector3(0.0, 0.0, 0.0),
            target_m=Vector3(5.0, 0.0, 0.0),
            speed_m_s=1.0,
        )
        manager.enable()
        resumed_decision = manager.evaluate_segment(
            start_m=Vector3(0.0, 0.0, 0.0),
            target_m=Vector3(5.0, 0.0, 0.0),
            speed_m_s=1.0,
        )

        self.assertEqual(disabled_decision.action.value, "allow")
        self.assertEqual(enabled_decision.action.value, "hover")
        self.assertEqual(paused_decision.action.value, "allow")
        self.assertEqual(resumed_decision.action.value, "hover")
        self.assertTrue(manager.status().enabled)

    def test_provider_registration_is_idempotent_and_capacity_is_enforced(self) -> None:
        device = self._device(DeviceCategory.DRONE)
        now_s = time.monotonic()
        provider = ManyObstacleProvider(
            (
                Obstacle(
                    obstacle_id="duplicate",
                    position_m=Vector3(1.0, 0.0, 0.0),
                    confidence=0.2,
                    observed_at_s=now_s,
                    ttl_s=2.0,
                ),
                Obstacle(
                    obstacle_id="duplicate",
                    position_m=Vector3(2.0, 0.0, 0.0),
                    confidence=0.9,
                    observed_at_s=now_s,
                    ttl_s=2.0,
                ),
                Obstacle(
                    obstacle_id="expired",
                    position_m=Vector3(3.0, 0.0, 0.0),
                    observed_at_s=now_s - 5.0,
                    ttl_s=0.1,
                ),
                Obstacle(
                    obstacle_id="kept",
                    kind=ObstacleKind.DYNAMIC,
                    position_m=Vector3(4.0, 0.0, 0.0),
                    observed_at_s=now_s,
                    ttl_s=2.0,
                ),
            )
        )
        manager = AvoidanceManager(device).configure_capacity(
            ObstacleCapacityPolicy(max_obstacles=2, max_obstacles_per_provider=4)
        )
        manager.add_provider(provider).add_provider(provider)

        snapshot = manager.snapshot()
        manager.close()

        self.assertEqual(provider.snapshot_calls, 1)
        self.assertEqual(provider.close_calls, 1)
        self.assertEqual(len(snapshot.obstacles), 2)
        self.assertEqual({item.obstacle_id for item in snapshot.obstacles}, {"duplicate", "kept"})
        duplicate = next(item for item in snapshot.obstacles if item.obstacle_id == "duplicate")
        self.assertEqual(duplicate.position_m.x, 2.0)
        self.assertEqual(manager.status().last_aggregation_stats.dropped_stale_count, 1)
        self.assertEqual(manager.status().last_aggregation_stats.dropped_duplicate_count, 1)

    def test_auto_dimensions_selects_3d_for_drone_and_2d_for_ground_device(self) -> None:
        obstacle = Obstacle(
            obstacle_id="high-obstacle",
            position_m=Vector3(2.0, 0.0, 20.0),
            radius_m=0.5,
            observed_at_s=time.monotonic(),
            ttl_s=2.0,
        )
        drone_manager = AvoidanceManager(self._device(DeviceCategory.DRONE))
        dog_manager = AvoidanceManager(self._device(DeviceCategory.ROBOT_DOG))
        drone_manager.add_provider(StaticObstacleProvider(obstacle))
        dog_manager.add_provider(StaticObstacleProvider(obstacle))
        policy = AvoidancePolicy(
            dimensions=AvoidanceDimensions.AUTO,
            min_clearance_m=1.0,
            device_radius_m=0.5,
        )

        drone_decision = drone_manager.evaluate_segment(
            start_m=Vector3(0.0, 0.0, 0.0),
            target_m=Vector3(5.0, 0.0, 0.0),
            speed_m_s=1.0,
            policy=policy,
        )
        dog_decision = dog_manager.evaluate_segment(
            start_m=Vector3(0.0, 0.0, 0.0),
            target_m=Vector3(5.0, 0.0, 0.0),
            speed_m_s=1.0,
            policy=policy,
        )

        self.assertEqual(drone_decision.action.value, "allow")
        self.assertEqual(dog_decision.action.value, "hover")

    def test_blocking_risk_maps_hover_to_robot_dog_stop(self) -> None:
        device = self._device(DeviceCategory.ROBOT_DOG)
        manager = AvoidanceManager(device)
        manager.add_provider(
            StaticObstacleProvider(
                Obstacle(
                    obstacle_id="box",
                    kind=ObstacleKind.STATIC,
                    frame=CoordinateFrame.LOCAL_NED,
                    position_m=Vector3(2.0, 0.0, 0.0),
                    radius_m=0.5,
                    observed_at_s=time.monotonic(),
                    ttl_s=2.0,
                )
            )
        )
        policy = AvoidancePolicy(
            dimensions=AvoidanceDimensions.TWO_D,
            min_clearance_m=1.0,
            device_radius_m=0.5,
            strategy=AvoidanceStrategy.HOLD_POSITION,
        )

        decision = manager.evaluate_segment(
            start_m=Vector3(0.0, 0.0, 0.0),
            target_m=Vector3(5.0, 0.0, 0.0),
            speed_m_s=1.0,
            policy=policy,
        )
        manager.execute_action(decision)

        self.assertEqual(decision.action.value, "hover")
        self.assertEqual(device.calls, ["stop"])

    def test_local_replan_returns_detour_point_and_event(self) -> None:
        device = self._device(DeviceCategory.DRONE)
        events = []
        unsubscribe = device.events.subscribe(events.append)
        manager = AvoidanceManager(device)
        manager.add_provider(
            StaticObstacleProvider(
                Obstacle(
                    obstacle_id="moving-box",
                    kind=ObstacleKind.DYNAMIC,
                    frame=CoordinateFrame.LOCAL_NED,
                    position_m=Vector3(4.0, 0.0, 10.0),
                    velocity_m_s=Vector3(0.0, 0.0, 0.0),
                    radius_m=1.0,
                    observed_at_s=time.monotonic(),
                    ttl_s=2.0,
                )
            )
        )
        policy = AvoidancePolicy(
            dimensions=AvoidanceDimensions.THREE_D,
            strategy=AvoidanceStrategy.LOCAL_REPLAN,
            min_clearance_m=2.0,
            device_radius_m=0.5,
        )

        decision = manager.evaluate_segment(
            start_m=Vector3(0.0, 0.0, 10.0),
            target_m=Vector3(8.0, 0.0, 10.0),
            speed_m_s=2.0,
            policy=policy,
        )
        device.events.drain()
        unsubscribe()

        self.assertEqual(decision.action.value, "replan")
        self.assertGreaterEqual(len(decision.detour_points_m), 1)
        self.assertNotEqual(decision.detour_points_m[0].y, 0.0)
        self.assertIn(EventType.COLLISION_RISK_RAISED, {event.type for event in events})
        self.assertIn(EventType.AVOIDANCE_ACTION_TRIGGERED, {event.type for event in events})

    def test_segment_can_escape_initial_clearance_but_cannot_move_deeper(self) -> None:
        device = self._device(DeviceCategory.DRONE)
        manager = AvoidanceManager(device)
        manager.add_provider(
            StaticObstacleProvider(
                Obstacle(
                    obstacle_id="near-start",
                    kind=ObstacleKind.STATIC,
                    shape=ObstacleShape.BOX,
                    frame=CoordinateFrame.LOCAL_NED,
                    position_m=Vector3(0.0, -2.0, -10.0),
                    size_m=Vector3(1.0, 1.0, 1.0),
                    observed_at_s=time.monotonic(),
                    ttl_s=2.0,
                )
            )
        )
        policy = AvoidancePolicy(
            dimensions=AvoidanceDimensions.TWO_D,
            strategy=AvoidanceStrategy.HOLD_POSITION,
            min_clearance_m=2.0,
            device_radius_m=0.5,
        )

        escaping = manager.evaluate_segment(
            start_m=Vector3(0.0, 0.0, -10.0),
            target_m=Vector3(10.0, 5.0, -10.0),
            speed_m_s=2.0,
            policy=policy,
        )
        approaching = manager.evaluate_segment(
            start_m=Vector3(0.0, 0.0, -10.0),
            target_m=Vector3(0.0, -10.0, -10.0),
            speed_m_s=2.0,
            policy=policy,
        )

        self.assertEqual(escaping.action.value, "allow")
        self.assertEqual(approaching.action.value, "hover")

    def test_trajectory_rollout_uses_vertical_candidate_when_lateral_path_is_blocked(self) -> None:
        device = self._device(DeviceCategory.DRONE)
        manager = AvoidanceManager(device)
        now_s = time.monotonic()
        for obstacle in (
            Obstacle(
                obstacle_id="front-wall",
                kind=ObstacleKind.STATIC,
                shape=ObstacleShape.BOX,
                frame=CoordinateFrame.LOCAL_NED,
                position_m=Vector3(4.0, 0.0, -10.0),
                size_m=Vector3(1.0, 2.5, 3.0),
                observed_at_s=now_s,
                ttl_s=2.0,
            ),
            Obstacle(
                obstacle_id="left-blocker",
                kind=ObstacleKind.STATIC,
                frame=CoordinateFrame.LOCAL_NED,
                position_m=Vector3(4.0, 3.4, -10.0),
                radius_m=1.2,
                observed_at_s=now_s,
                ttl_s=2.0,
            ),
            Obstacle(
                obstacle_id="right-blocker",
                kind=ObstacleKind.STATIC,
                frame=CoordinateFrame.LOCAL_NED,
                position_m=Vector3(4.0, -3.4, -10.0),
                radius_m=1.2,
                observed_at_s=now_s,
                ttl_s=2.0,
            ),
            Obstacle(
                obstacle_id="roof",
                kind=ObstacleKind.STATIC,
                shape=ObstacleShape.BOX,
                frame=CoordinateFrame.LOCAL_NED,
                position_m=Vector3(4.0, 0.0, -12.2),
                size_m=Vector3(10.0, 10.0, 0.8),
                observed_at_s=now_s,
                ttl_s=2.0,
            ),
        ):
            manager.add_provider(StaticObstacleProvider(obstacle))
        policy = AvoidancePolicy(
            dimensions=AvoidanceDimensions.THREE_D,
            strategy=AvoidanceStrategy.LOCAL_REPLAN,
            min_clearance_m=0.8,
            device_radius_m=0.3,
            replan_lateral_clearance_m=3.4,
            replan_vertical_clearance_m=3.0,
        )

        decision = manager.evaluate_segment(
            start_m=Vector3(0.0, 0.0, -10.0),
            target_m=Vector3(8.0, 0.0, -10.0),
            frame=CoordinateFrame.LOCAL_NED,
            speed_m_s=2.0,
            policy=policy,
        )

        self.assertEqual(decision.action.value, "replan", decision.message)
        self.assertTrue(any(point.z > -10.0 for point in decision.detour_points_m))
        self.assertFalse(any(point.z < -11.0 for point in decision.detour_points_m))

    def test_trajectory_rollout_expands_lateral_search_around_wide_obstacles(self) -> None:
        device = self._device(DeviceCategory.DRONE)
        manager = AvoidanceManager(device)
        now_s = time.monotonic()
        for index, y_m in enumerate((-4.0, -2.0, 0.0, 2.0, 4.0)):
            manager.add_provider(
                StaticObstacleProvider(
                    Obstacle(
                        obstacle_id=f"wall-{index}",
                        kind=ObstacleKind.STATIC,
                        shape=ObstacleShape.BOX,
                        frame=CoordinateFrame.LOCAL_NED,
                        position_m=Vector3(8.0, y_m, -10.0),
                        size_m=Vector3(2.0, 2.0, 2.0),
                        observed_at_s=now_s,
                        ttl_s=2.0,
                    )
                )
            )
        policy = AvoidancePolicy(
            dimensions=AvoidanceDimensions.TWO_D,
            strategy=AvoidanceStrategy.LOCAL_REPLAN,
            min_clearance_m=1.0,
            device_radius_m=0.5,
            replan_lateral_clearance_m=3.0,
        )

        decision = manager.evaluate_segment(
            start_m=Vector3(0.0, 0.0, -10.0),
            target_m=Vector3(20.0, 0.0, -10.0),
            speed_m_s=2.0,
            policy=policy,
        )

        self.assertEqual(decision.action.value, "replan", decision.message)
        self.assertGreater(abs(decision.detour_points_m[0].y), 4.0)

    def test_custom_planner_backend_can_replace_builtin_replan(self) -> None:
        device = self._device(DeviceCategory.DRONE)
        manager = AvoidanceManager(device)
        backend = ExternalPlannerBackend()
        manager.register_planner_backend(AvoidancePlanner.EXTERNAL_PROCESS, backend)
        manager.add_provider(
            StaticObstacleProvider(
                Obstacle(
                    obstacle_id="external-blocker",
                    frame=CoordinateFrame.LOCAL_NED,
                    position_m=Vector3(2.0, 0.0, 5.0),
                    radius_m=0.5,
                    observed_at_s=time.monotonic(),
                    ttl_s=2.0,
                )
            )
        )
        policy = AvoidancePolicy(
            dimensions=AvoidanceDimensions.THREE_D,
            strategy=AvoidanceStrategy.LOCAL_REPLAN,
            planner=AvoidancePlanner.EXTERNAL_PROCESS,
            min_clearance_m=1.0,
            device_radius_m=0.3,
        )

        decision = manager.evaluate_segment(
            start_m=Vector3(0.0, 0.0, 5.0),
            target_m=Vector3(4.0, 0.0, 5.0),
            frame=CoordinateFrame.LOCAL_NED,
            speed_m_s=1.0,
            policy=policy,
        )

        self.assertEqual(decision.action.value, "replan")
        self.assertEqual(decision.detour_points_m, (Vector3(0.0, 2.0, 5.0),))
        self.assertEqual(len(backend.requests), 1)
        self.assertIs(backend.requests[0].policy.planner, AvoidancePlanner.EXTERNAL_PROCESS)

    def test_expired_obstacle_is_ignored_and_provider_is_closed(self) -> None:
        device = self._device(DeviceCategory.DRONE)
        provider = StaticObstacleProvider(
            Obstacle(
                obstacle_id="expired",
                position_m=Vector3(1.0, 0.0, 0.0),
                radius_m=1.0,
                observed_at_s=time.monotonic() - 5.0,
                ttl_s=0.1,
            )
        )
        manager = AvoidanceManager(device)
        manager.add_provider(provider)

        decision = manager.evaluate_segment(
            start_m=Vector3(0.0, 0.0, 0.0),
            target_m=Vector3(3.0, 0.0, 0.0),
            speed_m_s=1.0,
            policy=AvoidancePolicy(),
        )
        manager.close()

        self.assertEqual(decision.action.value, "allow")
        self.assertTrue(provider.closed)

    def test_route_blocking_and_local_replan_execute_in_sdk_loop(self) -> None:
        route = Route.polyline(
            (
                Waypoint(x_m=0.0, y_m=0.0, z_m=5.0, acceptance_radius_m=0.5),
                Waypoint(x_m=4.0, y_m=0.0, z_m=5.0, acceptance_radius_m=0.6),
            ),
            name="avoidance-test-line",
        )
        motion = RouteMotion(
            mode=MotionExecutionMode.SDK,
            lookahead_time_s=0.5,
            min_lookahead_m=0.5,
            settle_time_s=0.0,
            settle_velocity_m_s=0.1,
        )

        blocking_drone = RouteFakeDrone()
        self._devices = (*getattr(self, "_devices", ()), blocking_drone)
        blocking_drone.avoidance.add_provider(
            StaticObstacleProvider(
                Obstacle(
                    obstacle_id="blocker",
                    position_m=Vector3(2.5, 0.0, 5.0),
                    radius_m=0.6,
                    observed_at_s=time.monotonic(),
                    ttl_s=5.0,
                )
            )
        )
        blocking_policy = AvoidancePolicy(
            min_clearance_m=1.0,
            device_radius_m=0.3,
            strategy=AvoidanceStrategy.HOLD_POSITION,
        )

        blocking_result = (
            DronePlan(blocking_drone)
            .route(route, speed=1.5, motion=motion, avoidance=blocking_policy, timeout_s=5.0)
            .run(step_timeout_s=5.0, guard_interval_s=0.01)
        )

        self.assertEqual(blocking_result.status, TaskStatus.FAILED)
        self.assertIn("hover", blocking_drone.calls)

        replan_drone = RouteFakeDrone()
        self._devices = (*getattr(self, "_devices", ()), replan_drone)
        replan_events = []
        unsubscribe = replan_drone.events.subscribe(replan_events.append)
        replan_drone.avoidance.add_provider(
            OneShotObstacleProvider(
                Obstacle(
                    obstacle_id="detour",
                    position_m=Vector3(0.5, 0.0, 5.0),
                    radius_m=0.1,
                    observed_at_s=time.monotonic(),
                    ttl_s=5.0,
                )
            )
        )
        replan_policy = AvoidancePolicy(
            strategy=AvoidanceStrategy.LOCAL_REPLAN,
            min_clearance_m=0.3,
            device_radius_m=0.2,
            replan_lateral_clearance_m=1.2,
            max_replans_per_route=2,
        )

        replan_result = (
            DronePlan(replan_drone)
            .route(route, speed=2.5, motion=motion, avoidance=replan_policy, timeout_s=20.0)
            .run(step_timeout_s=20.0, guard_interval_s=0.01)
        )
        replan_drone.events.drain()
        unsubscribe()

        self.assertEqual(replan_result.status, TaskStatus.SUCCESS, replan_result.message)
        self.assertIn(EventType.ROUTE_REPLANNED, {event.type for event in replan_events})

    def test_route_waits_for_fresh_snapshot_before_replan_fallback(self) -> None:
        route = Route.polyline(
            (
                Waypoint(x_m=0.0, y_m=0.0, z_m=5.0),
                Waypoint(x_m=4.0, y_m=0.0, z_m=5.0, acceptance_radius_m=0.6),
            )
        )
        drone = RouteFakeDrone()
        self._devices = (*getattr(self, "_devices", ()), drone)
        drone.avoidance.add_provider(
            TransientObstacleProvider(
                Obstacle(
                    obstacle_id="transient-blocker",
                    position_m=Vector3(0.5, 0.0, 5.0),
                    radius_m=0.2,
                    observed_at_s=time.monotonic(),
                    ttl_s=5.0,
                )
            )
        )
        drone.avoidance.register_planner_backend(AvoidancePlanner.EXTERNAL_PROCESS, NoSafePathBackend())
        policy = AvoidancePolicy(
            strategy=AvoidanceStrategy.LOCAL_REPLAN,
            planner=AvoidancePlanner.EXTERNAL_PROCESS,
            fallback_strategy=AvoidanceStrategy.RETURN_TO_LAUNCH,
            min_clearance_m=0.3,
            device_radius_m=0.2,
            replan_retry_timeout_s=0.5,
            replan_retry_interval_s=0.01,
        )

        result = (
            DronePlan(drone)
            .route(
                route,
                speed=2.5,
                motion=RouteMotion(mode=MotionExecutionMode.SDK, min_lookahead_m=0.5, settle_time_s=0.0),
                avoidance=policy,
                timeout_s=10.0,
            )
            .run(step_timeout_s=10.0, guard_interval_s=0.01)
        )

        self.assertEqual(result.status, TaskStatus.SUCCESS, result.message)
        self.assertIn("hover", drone.calls)
        self.assertNotIn("return_to_launch", drone.calls)

    def test_route_avoidance_uses_device_default_policy_before_native_route(self) -> None:
        route = Route.polyline(
            (
                Waypoint(x_m=0.0, y_m=0.0, z_m=5.0, acceptance_radius_m=0.5),
                Waypoint(x_m=4.0, y_m=0.0, z_m=5.0, acceptance_radius_m=0.6),
            ),
            name="device-default-avoidance",
        )
        drone = RouteFakeDrone()
        adapter = NativeRouteAdapter(drone)
        drone.adapter = adapter
        self._devices = (*getattr(self, "_devices", ()), drone)
        drone.avoidance.add_provider(
            StaticObstacleProvider(
                Obstacle(
                    obstacle_id="device-default-blocker",
                    position_m=Vector3(1.0, 0.0, 5.0),
                    radius_m=0.5,
                    observed_at_s=time.monotonic(),
                    ttl_s=5.0,
                )
            )
        )
        drone.avoidance.configure(
            AvoidancePolicy(
                min_clearance_m=1.0,
                device_radius_m=0.3,
                strategy=AvoidanceStrategy.HOLD_POSITION,
            )
        )

        result = DronePlan(drone).route(route, speed=1.5, timeout_s=5.0).run(
            step_timeout_s=5.0,
            guard_interval_s=0.01,
        )

        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(adapter.executed, 0)
        self.assertIn("hover", drone.calls)

    def test_route_avoidance_converts_local_ned_altitude_to_down_axis(self) -> None:
        route = Route.polyline(
            (
                Waypoint(x_m=0.0, y_m=0.0, z_m=5.0, acceptance_radius_m=0.5),
                Waypoint(x_m=4.0, y_m=0.0, z_m=5.0, acceptance_radius_m=0.6),
            ),
            name="ned-altitude-avoidance",
        )
        drone = RouteFakeDrone()
        drone.state._update(local_position_frame=CoordinateFrame.LOCAL_NED, local_z_m=-5.0)
        self._devices = (*getattr(self, "_devices", ()), drone)
        drone.avoidance.add_provider(
            StaticObstacleProvider(
                Obstacle(
                    obstacle_id="ned-blocker",
                    frame=CoordinateFrame.LOCAL_NED,
                    position_m=Vector3(1.0, 0.0, -5.0),
                    radius_m=0.5,
                    observed_at_s=time.monotonic(),
                    ttl_s=5.0,
                )
            )
        )

        result = (
            DronePlan(drone)
            .route(
                route,
                speed=1.5,
                motion=RouteMotion(mode=MotionExecutionMode.SDK, settle_time_s=0.0),
                avoidance=AvoidancePolicy(
                    min_clearance_m=1.0,
                    device_radius_m=0.3,
                    strategy=AvoidanceStrategy.HOLD_POSITION,
                ),
                timeout_s=5.0,
            )
            .run(step_timeout_s=5.0, guard_interval_s=0.01)
        )

        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertIn("hover", drone.calls)

    def test_rebuilt_detour_path_preserves_remaining_waypoint_indexes(self) -> None:
        route = Route.polyline(
            (
                Waypoint(x_m=0.0, y_m=0.0, z_m=5.0),
                Waypoint(x_m=4.0, y_m=0.0, z_m=5.0),
                Waypoint(x_m=8.0, y_m=0.0, z_m=5.0),
            ),
            name="detour-waypoint-indexes",
        )
        path = TrajectoryPath(
            tuple(
                TrajectoryPoint(
                    x_m=float(waypoint.x_m or 0.0),
                    y_m=float(waypoint.y_m or 0.0),
                    z_m=waypoint.z_m,
                    waypoint=waypoint,
                    waypoint_index=index,
                    speed_m_s=2.0,
                )
                for index, waypoint in enumerate(route.waypoints)
            )
        )

        rebuilt = DronePlan(RouteFakeDrone())._rebuild_path_with_detour(
            route,
            path,
            current_x=1.0,
            current_y=0.0,
            current_z=5.0,
            progress_m=1.0,
            detour_points=(Vector3(1.0, 2.0, 5.0),),
            base_speed=2.0,
        )

        self.assertEqual([point.waypoint_index for point in rebuilt.points], [0, None, 1, 2])
        self.assertLess(rebuilt.waypoint_distance_m(1), rebuilt.length_m)
        self.assertEqual(rebuilt.waypoint_distance_m(2), rebuilt.length_m)

    def test_rebuilt_detour_path_rejoins_ahead_of_obstacle(self) -> None:
        route = Route.polyline(
            tuple(Waypoint(x_m=float(x_m), y_m=0.0, z_m=5.0) for x_m in (0, 3, 6, 9, 12)),
            name="detour-forward-rejoin",
        )
        path = TrajectoryPath(
            tuple(
                TrajectoryPoint(
                    x_m=float(waypoint.x_m or 0.0),
                    y_m=float(waypoint.y_m or 0.0),
                    z_m=waypoint.z_m,
                    waypoint=waypoint,
                    waypoint_index=index,
                    speed_m_s=2.0,
                )
                for index, waypoint in enumerate(route.waypoints)
            )
        )

        rebuilt = DronePlan(RouteFakeDrone())._rebuild_path_with_detour(
            route,
            path,
            current_x=1.0,
            current_y=0.0,
            current_z=5.0,
            progress_m=1.0,
            detour_points=(
                Vector3(1.0, 3.0, 5.0),
                Vector3(8.0, 3.0, 5.0),
                Vector3(8.0, 0.0, 5.0),
            ),
            base_speed=2.0,
        )

        self.assertEqual([point.waypoint_index for point in rebuilt.points], [0, None, None, None, 3, 4])
        self.assertNotIn(1, {point.waypoint_index for point in rebuilt.points})
        self.assertNotIn(2, {point.waypoint_index for point in rebuilt.points})


if __name__ == "__main__":
    unittest.main()
