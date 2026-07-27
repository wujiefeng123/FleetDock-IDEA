import threading
import time
import unittest
from dataclasses import replace

from fleetdock import (
    AdapterFactory,
    AdapterNotFoundError,
    AvoidanceDimensions,
    AvoidancePlanner,
    AvoidancePolicy,
    AvoidanceStrategy,
    CapabilityName,
    CommandName,
    CoordinateFrame,
    DeviceFactory,
    DeviceMetadata,
    Drone,
    ErrorCode,
    EventHub,
    FirmwareVersion,
    Fleet,
    FleetDockValidationError,
    FollowController,
    FollowExecutionOptions,
    FollowLostAction,
    FollowPolicy,
    FollowStopAction,
    GpsPoint,
    ObstacleCapacityPolicy,
    Plan,
    ProductRegistry,
    ProductType,
    RelativeMove2D,
    RelativeMove3D,
    RelativePoint2D,
    RelativePoint3D,
    RobotDog,
    StandardCommand,
    TaskCancelMode,
    TaskResult,
    TaskSessionState,
    TaskStatus,
    TaskStepHookDecision,
    TaskStepHookPhase,
    default_registry,
)
from fleetdock.adapters.mock import MockDroneAdapter
from fleetdock.runtime import ManagedWorker, WorkerState
from fleetdock.tasking.models import TaskCancellationToken
from fleetdock.tasking.session import TaskSession


def mock_drone_factory() -> AdapterFactory:
    profile = default_registry.get(ProductType.PX4_GENERIC)
    profile = replace(profile, capabilities=profile.capabilities - {CapabilityName.MISSION})
    registry = ProductRegistry()
    registry.register(profile, adapters={profile.default_firmware: MockDroneAdapter})
    return AdapterFactory(registry)


class MockDroneDepthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.drone = Drone(
            ProductType.PX4_GENERIC,
            params={"endpoint": "mock://px4"},
            metadata=DeviceMetadata(device_id="mock-drone-depth"),
            adapter_factory=mock_drone_factory(),
        )

    def tearDown(self) -> None:
        self.drone.close()

    def test_full_command_lifecycle(self) -> None:
        self.assertFalse(self.drone.takeoff(2.0).ok)
        self.assertTrue(self.drone.connect().ok)
        self.assertFalse(self.drone.takeoff(0.0).ok)
        self.assertEqual(self.drone.takeoff(0.0).code, ErrorCode.VALIDATION_ERROR)
        self.assertTrue(self.drone.unlock().ok)
        self.assertTrue(self.drone.takeoff(4.0).ok)
        self.assertTrue(self.drone.hover().ok)
        self.assertTrue(self.drone.set_velocity(x=1.0, y=-1.0, z=0.5, speed=1.0).ok)
        self.assertTrue(self.drone.stop().ok)
        self.assertTrue(self.drone.lock().ok)
        self.assertTrue(self.drone.unlock().ok)
        self.assertTrue(self.drone.land().ok)
        self.assertTrue(self.drone.return_to_launch().ok)
        self.assertTrue(self.drone.emergency_stop().ok)
        self.assertTrue(self.drone.get_status().ok)

    def test_all_supported_move_target_shapes(self) -> None:
        self.assertTrue(self.drone.connect().ok)
        self.assertTrue(self.drone.move(GpsPoint(30.0, 110.0, 120.0, 15.0)).ok)
        self.assertTrue(self.drone.move(RelativePoint3D(1.0, 2.0, 3.0, 20.0)).ok)
        self.assertTrue(self.drone.move(RelativeMove3D(forward_m=2.0, right_m=1.0, up_m=1.0, yaw_deg=30.0)).ok)

        response = self.drone.adapter.execute(
            StandardCommand(
                CommandName.MOVE,
                self.drone.instance_id,
                self.drone.product,
                params={"target_kind": "relative_point_2d", "target": {"x_m": 1.0, "y_m": 2.0}},
            )
        )
        self.assertEqual(response.code, ErrorCode.UNSUPPORTED_COMMAND)

    def test_move_convenience_method_and_event_state(self) -> None:
        self.assertNotIn("goto_global", {command.value for command in CommandName})
        self.assertNotIn("goto_local", {command.value for command in CommandName})
        self.assertNotIn("goto_global", {capability.value for capability in CapabilityName})
        self.assertNotIn("goto_local", {capability.value for capability in CapabilityName})
        self.assertTrue(self.drone.connect().ok)
        response = self.drone.goto_local(3.0, 4.0, 5.0)
        self.assertTrue(response.ok)
        self.assertEqual(response.data["position"]["z"], 5.0)
        global_response = self.drone.goto_global(30.0, 110.0, 120.0, 15.0)
        self.assertTrue(global_response.ok)
        self.assertEqual(global_response.data["target_kind"], "gps_point")
        plan_result = (
            Plan(self.drone)
            .goto_global(30.1, 110.1, 125.0, 20.0)
            .goto_local(6.0, 7.0, 8.0)
            .run()
        )
        self.assertTrue(plan_result.ok)
        self.assertEqual(tuple(step.name for step in plan_result.steps), ("move", "move"))
        self.assertTrue(self.drone.unlock().ok)
        self.assertTrue(self.drone.takeoff(6.0).ok)
        self.drone.events.drain(timeout_s=1.0)
        self.assertTrue(self.drone.state.snapshot().in_air)


class MockRobotDogDepthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dog = RobotDog(
            ProductType.UNITREE_GO2,
            params={"network_interface": "mock0"},
            metadata=DeviceMetadata(device_id="mock-dog-depth"),
        )

    def tearDown(self) -> None:
        self.dog.close()

    def test_posture_move_stop_and_emergency_paths(self) -> None:
        self.assertTrue(self.dog.connect().ok)
        self.assertFalse(self.dog.move(RelativeMove2D(forward_m=1.0)).ok)
        self.assertTrue(self.dog.stand().ok)
        self.assertTrue(self.dog.move(RelativePoint2D(2.0, 3.0, 15.0)).ok)
        self.assertTrue(self.dog.move(RelativeMove2D(forward_m=1.0, right_m=1.0, yaw_deg=30.0)).ok)
        self.assertTrue(self.dog.set_velocity(x=1.0, speed=0.5).ok)
        self.assertTrue(self.dog.stop().ok)
        self.assertTrue(self.dog.sit().ok)
        self.assertTrue(self.dog.emergency_stop().ok)
        self.assertTrue(self.dog.get_status().ok)


class TaskSessionDepthTests(unittest.TestCase):
    def test_success_failure_and_exception_states(self) -> None:
        success = TaskSession(lambda token: TaskResult(True, TaskStatus.SUCCESS, "ok"))
        self.assertTrue(success.wait(1.0).ok)
        self.assertEqual(success.state, TaskSessionState.SUCCEEDED)

        failed = TaskSession(lambda token: TaskResult(False, TaskStatus.FAILED, "failed"))
        self.assertFalse(failed.wait(1.0).ok)
        self.assertEqual(failed.state, TaskSessionState.FAILED)

        raised = TaskSession(lambda token: (_ for _ in ()).throw(LookupError("boom")))
        with self.assertRaisesRegex(LookupError, "boom"):
            raised.wait(1.0)
        self.assertIsInstance(raised.exception, LookupError)

    def test_cancel_timeout_and_validation_paths(self) -> None:
        started = threading.Event()
        safe_stopped = threading.Event()

        def runner(token):
            started.set()
            while not token.cancelled:
                time.sleep(0.001)
            return TaskResult(False, TaskStatus.CANCELLED, "cancelled")

        session = TaskSession(runner, safe_stop=safe_stopped.set)
        self.assertTrue(started.wait(1.0))
        with self.assertRaises(TimeoutError):
            session.wait(0.001)
        self.assertTrue(session.cancel(TaskCancelMode.SAFE_STOP))
        self.assertFalse(session.cancel())
        self.assertEqual(session.wait(1.0).status, TaskStatus.CANCELLED)
        self.assertTrue(safe_stopped.is_set())
        with self.assertRaises(ValueError):
            session.wait(-1.0)

    def test_safe_stop_error_does_not_mask_cancellation(self) -> None:
        def runner(token):
            while not token.cancelled:
                time.sleep(0.001)
            return TaskResult(False, TaskStatus.CANCELLED, "cancelled")

        def broken_stop() -> None:
            raise RuntimeError("stop failed")

        session = TaskSession(runner, safe_stop=broken_stop)
        self.assertTrue(session.cancel())
        self.assertEqual(session.wait(1.0).status, TaskStatus.CANCELLED)


class ManagedWorkerDepthTests(unittest.TestCase):
    def test_failure_callback_and_restart_boundaries(self) -> None:
        errors = []
        failed = ManagedWorker(
            "failed-worker",
            lambda: (_ for _ in ()).throw(RuntimeError("worker failed")),
            on_error=errors.append,
        ).start()
        self.assertTrue(failed.join(1.0))
        self.assertEqual(failed.status().state, WorkerState.FAILED)
        self.assertEqual(len(errors), 1)
        with self.assertRaises(RuntimeError):
            failed.start()

        completed = ManagedWorker("restart-worker", lambda: None).start()
        self.assertTrue(completed.join(1.0))
        self.assertEqual(completed.status().state, WorkerState.STOPPED)
        self.assertIs(completed.start(), completed)
        self.assertTrue(completed.join(1.0))


class ValidationAndBuilderDepthTests(unittest.TestCase):
    def test_avoidance_policy_validation_and_derived_properties(self) -> None:
        invalid_values = (
            {"dimensions": "3d"},
            {"strategy": "stop"},
            {"planner": "trajectory_rollout"},
            {"fallback_strategy": "stop"},
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(TypeError):
                AvoidancePolicy(**values)
        for values in (
            {"fallback_strategy": AvoidanceStrategy.LOCAL_REPLAN},
            {"lookahead_s": 0.0},
            {"min_clearance_m": -1.0},
            {"replan_lateral_clearance_m": -1.0},
            {"replan_vertical_clearance_m": -1.0},
            {"stale_obstacle_timeout_s": -1.0},
            {"max_replans_per_route": -1},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                AvoidancePolicy(**values)

        policy = AvoidancePolicy(
            dimensions=AvoidanceDimensions.THREE_D,
            strategy=AvoidanceStrategy.LOCAL_REPLAN,
            planner=AvoidancePlanner.TRAJECTORY_ROLLOUT,
            fallback_strategy=AvoidanceStrategy.STOP,
            min_clearance_m=2.0,
            device_radius_m=0.5,
        )
        self.assertTrue(policy.requests_local_replan)
        self.assertEqual(policy.fallback_action.value, "stop")
        self.assertEqual(policy.safety_radius_m, 2.5)
        self.assertTrue(AvoidancePolicy.disabled().disabled_for_route)

    def test_obstacle_capacity_validation(self) -> None:
        for values in (
            {"max_obstacles": 0},
            {"max_obstacles_per_provider": 0},
            {"default_ttl_s": -1.0},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ObstacleCapacityPolicy(**values)

    def test_typed_builders_and_failure_boundaries(self) -> None:
        with self.assertRaises(FleetDockValidationError):
            DeviceFactory.create().px4_generic().build()

        dog = (
            DeviceFactory.create()
            .unitree_g1()
            .params(network_interface="mock0", robot_ip="192.0.2.1")
            .firmware(FirmwareVersion.UNITREE_SDK2)
            .event_hub(EventHub())
            .metadata(DeviceMetadata(device_id="builder-g1"))
            .build()
        )
        try:
            self.assertEqual(dog.metadata.device_id, "builder-g1")
        finally:
            dog.close()

        with self.assertRaises(AdapterNotFoundError):
            (
                DeviceFactory.create()
                .dji_matrice_350_rtk()
                .params("msdk", app_key="key", app_secret="secret", mqtt_host="host", workspace_id="workspace")
                .build()
            )
        with self.assertRaises(AdapterNotFoundError):
            DeviceFactory.create().dji_mavic_3_enterprise().params("cloud_api", mqtt_host="host").build()


class FollowControllerDepthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = FollowController(
            FollowExecutionOptions(min_separation_m=2.0, target_tolerance_m=0.5, default_max_speed_m_s=3.0)
        )

    def test_relative_chase_and_separation_branches(self) -> None:
        separation = self.controller.compute((0.0, 0.0), (1.0, 0.0), FollowPolicy.chase(distance_m=3.0))
        self.assertEqual(separation.reason, "separation guard")
        self.assertLess(separation.x, 0.0)

        relative = self.controller.compute((0.0, 0.0), (5.0, 5.0), FollowPolicy.relative(x_m=1.0, y_m=-1.0))
        self.assertEqual(relative.reason, "relative")
        held = self.controller.compute((6.0, 4.0), (5.0, 5.0), FollowPolicy.relative(x_m=1.0, y_m=-1.0))
        self.assertEqual(held.reason, "relative hold")

        chase_hold = self.controller.compute((0.0, 0.0), (5.0, 0.0), FollowPolicy.chase(distance_m=5.0))
        self.assertEqual(chase_hold.reason, "chase hold")
        chase_back = self.controller.compute((0.0, 0.0), (3.0, 0.0), FollowPolicy.chase(distance_m=5.0))
        self.assertLess(chase_back.x, 0.0)

    def test_trail_camera_and_band_branches(self) -> None:
        now = time.monotonic()
        history = ((now - 3.0, 1.0, 1.0), (now, 6.0, 1.0))
        trail = self.controller.compute((0.0, 0.0), (6.0, 1.0), FollowPolicy.trail(delay_s=2.0), history)
        self.assertEqual(trail.reason, "trail")
        trail_fallback = self.controller.compute((0.0, 0.0), (8.0, 0.0), FollowPolicy.trail(distance_m=2.0))
        self.assertEqual(trail_fallback.reason, "trail")

        camera = self.controller.compute((0.0, 0.0), (6.0, 1.0), FollowPolicy.camera(), history)
        self.assertEqual(camera.reason, "camera")
        camera_fallback = self.controller.compute((0.0, 0.0), (8.0, 0.0), FollowPolicy.camera())
        self.assertEqual(camera_fallback.reason, "camera")

        band_hold = self.controller.compute(
            (0.0, 0.0), (10.0, 0.0), FollowPolicy.band(min_distance_m=8.0, max_distance_m=12.0)
        )
        self.assertEqual(band_hold.reason, "band hold")
        self.assertGreater(
            self.controller.compute(
                (0.0, 0.0), (20.0, 0.0), FollowPolicy.band(min_distance_m=8.0, max_distance_m=12.0)
            ).x,
            0.0,
        )
        self.assertLess(
            self.controller.compute(
                (0.0, 0.0), (3.0, 0.0), FollowPolicy.band(min_distance_m=4.0, max_distance_m=12.0)
            ).x,
            0.0,
        )


class FollowExecutorDepthTests(unittest.TestCase):
    def build_dog(self, device_id: str) -> RobotDog:
        dog = RobotDog(
            ProductType.UNITREE_GO2,
            params={"network_interface": "mock0"},
            metadata=DeviceMetadata(device_id=device_id),
        )
        dog.connect()
        dog.stand()
        dog.state._update(local_position_frame=CoordinateFrame.LOCAL_NED)
        dog.state._update(local_position_updated_at_s=time.monotonic())
        return dog

    def test_executor_start_pause_resume_tick_and_stop(self) -> None:
        leader = self.build_dog("leader")
        follower = self.build_dog("follower")
        leader.state._update(local_x_m=10.0, local_y_m=0.0)
        follower.state._update(local_x_m=0.0, local_y_m=0.0)
        fleet = Fleet([leader, follower])
        try:
            session = fleet.follow(leader=leader, followers=[follower], policy=FollowPolicy.chase(distance_m=4.0))
            session.start(
                interval_s=0.005,
                telemetry_timeout_s=2.0,
                min_separation_m=1.0,
                stop_action=FollowStopAction.HOLD_VELOCITY_ZERO,
            )
            time.sleep(0.03)
            self.assertTrue(session.status().followers["follower"].telemetry_ok)
            session.pause()
            self.assertEqual(session.status().state.value, "paused")
            session.resume()
            time.sleep(0.01)
            session.stop_execution(wait_s=1.0)
            self.assertEqual(session.status().state.value, "stopped")
        finally:
            fleet.close()

    def test_executor_handles_missing_leader_telemetry(self) -> None:
        leader = self.build_dog("lost-leader")
        follower = self.build_dog("safe-follower")
        leader.state._update(local_x_m=None)
        leader.state._update(local_y_m=None)
        follower.state._update(local_x_m=0.0, local_y_m=0.0)
        fleet = Fleet([leader, follower])
        try:
            session = fleet.follow(leader=leader, followers=[follower], policy=FollowPolicy.relative())
            session.start(
                interval_s=0.005,
                on_leader_lost=FollowLostAction.HOVER,
                stop_action=FollowStopAction.NONE,
            )
            time.sleep(0.02)
            self.assertIn("leader", session.status().message)
            session.stop_execution(wait_s=1.0)
        finally:
            fleet.close()


class FleetPlanDepthTests(unittest.TestCase):
    def build_fleet(self, *, connect: bool = True) -> Fleet:
        dogs = [
            RobotDog(
                ProductType.UNITREE_GO2,
                params={"network_interface": "mock0"},
                metadata=DeviceMetadata(device_id=f"plan-dog-{index}"),
            )
            for index in range(2)
        ]
        if connect:
            for dog in dogs:
                dog.connect()
        return Fleet(dogs)

    def test_empty_success_and_filtered_subplan(self) -> None:
        empty = Fleet([])
        try:
            self.assertTrue(Plan(empty).run().ok)
        finally:
            empty.close()

        fleet = self.build_fleet()
        try:
            result = Plan(fleet).for_robot_dogs().stand().move(RelativeMove2D(forward_m=1.0)).sit().run()
            self.assertTrue(result.ok)
            self.assertEqual(len(result.succeeded_devices), 2)
            self.assertEqual(len(result.steps), 3)
        finally:
            fleet.close()

    def test_failure_guard_cancellation_and_hooks(self) -> None:
        disconnected = self.build_fleet(connect=False)
        try:
            failed = Plan(disconnected).for_robot_dogs().stand().run()
            self.assertFalse(failed.ok)
            self.assertEqual(len(failed.failed_devices), 2)
        finally:
            disconnected.close()

        guarded = self.build_fleet()
        try:
            result = Plan(guarded).when(lambda ctx: True, reason="guarded").stop().run()
            self.assertFalse(result.ok)
            self.assertEqual(result.triggered_guard, "guarded")
        finally:
            guarded.close()

        cancelled = self.build_fleet()
        try:
            token = TaskCancellationToken()
            token.cancel()
            result = Plan(cancelled).stop().run(_cancellation_token=token)
            self.assertEqual(result.status, TaskStatus.CANCELLED)
        finally:
            cancelled.close()

        hooked = self.build_fleet()
        try:

            def skip_stop(event):
                if event.phase is TaskStepHookPhase.BEFORE_STEP:
                    return TaskStepHookDecision.skip("skip")
                return None

            result = Plan(hooked, interceptors=[skip_stop]).stop().run()
            self.assertTrue(result.ok)
            self.assertEqual(result.steps[0].message, "skip")
        finally:
            hooked.close()


class FleetRelationshipDepthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.devices = [
            RobotDog(
                ProductType.UNITREE_GO2,
                params={"network_interface": "mock0"},
                metadata=DeviceMetadata(device_id=f"relation-dog-{index}"),
            )
            for index in range(3)
        ]
        self.fleet = Fleet(self.devices)

    def tearDown(self) -> None:
        self.fleet.close()

    def test_follow_relationship_validation_paths(self) -> None:
        leader, follower, outsider = self.devices
        session = self.fleet.follow(leader=leader, followers=[follower], policy=FollowPolicy.relative())
        self.assertIs(session.update_leader(leader), session)
        with self.assertRaises(ValueError):
            session.add(leader)
        with self.assertRaises(ValueError):
            session.remove(outsider)
        with self.assertRaises(ValueError):
            session.set_policy(outsider, FollowPolicy.chase())
        with self.assertRaises(ValueError):
            session.assign(follower, "missing-layout")
        with self.assertRaises(ValueError):
            self.fleet.unfollow("missing-session")

        session.remove(follower)
        session.add(outsider, policy=FollowPolicy.band())
        self.assertEqual(session.policy_for(outsider).mode.value, "band")
        session.stop()
        with self.assertRaises(ValueError):
            session.add(follower)

    def test_bulk_operation_exception_is_standardized(self) -> None:
        def broken_connect():
            raise RuntimeError("connection exploded")

        self.devices[0].connect = broken_connect
        result = self.fleet.connect()
        self.assertFalse(result.ok)
        self.assertIn("relation-dog-0", result.failed_devices)
        self.assertEqual(result.device_results["relation-dog-0"].code, ErrorCode.UNKNOWN_ERROR)
        self.assertIn("failed", result.message)


if __name__ == "__main__":
    unittest.main()
