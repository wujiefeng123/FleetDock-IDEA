import time
import unittest

from fleetdock import (
    CapabilityName,
    Drone,
    DronePlan,
    HeadingMode,
    MotionExecutionMode,
    EventType,
    GpsPoint,
    Plan,
    ProductType,
    RobotDog,
    RobotDogPlan,
    Route,
    RouteCompletedEvent,
    RouteCompletionReason,
    RouteContinuity,
    RouteMotion,
    RoutePattern,
    StandardResponse,
    TaskStepHookDecision,
    TaskStepHookPhase,
    TaskStepResult,
    TaskCompletionReason,
    TaskStatus,
    Waypoint,
    WaypointAction,
    WaypointSettledEvent,
)
from fleetdock.tasking.route_execution import RouteExecutionMixin

class FakeTaskDroneClient:
    def __init__(self) -> None:
        self.calls = []

    def connect(self) -> None:
        self.calls.append(("connect",))

    def arm(self, force: bool = False) -> None:
        if force:
            self.calls.append(("arm", True))
        else:
            self.calls.append(("arm",))

    def disarm(self) -> None:
        self.calls.append(("disarm",))

    def takeoff(self, altitude: float) -> None:
        self.calls.append(("takeoff", altitude))

    def land(self) -> None:
        self.calls.append(("land",))

    def return_to_launch(self) -> None:
        self.calls.append(("return_to_launch",))

    def hold(self) -> None:
        self.calls.append(("hold",))

    def goto_location(self, latitude_deg: float, longitude_deg: float, altitude_m: float, yaw_deg: float) -> None:
        self.calls.append(("goto_location", latitude_deg, longitude_deg, altitude_m, yaw_deg))

    def set_velocity(self, x: float, y: float, z: float, speed: float, yaw_deg=None) -> None:
        if yaw_deg is None:
            self.calls.append(("set_velocity", x, y, z, speed))
        else:
            self.calls.append(("set_velocity", x, y, z, speed, yaw_deg))

    def stop(self) -> None:
        self.calls.append(("stop",))

    def emergency_stop(self) -> None:
        self.calls.append(("emergency_stop",))

    def get_status(self):
        self.calls.append(("get_status",))
        return {"calls": list(self.calls)}

class NativeRouteFakeTaskDroneClient(FakeTaskDroneClient):
    def execute_mission(self, mission_items, *, timeout_s, progress_callback=None, guard_callback=None) -> None:
        self.calls.append(("execute_mission", len(mission_items), timeout_s))
        for current in range(1, len(mission_items) + 1):
            if guard_callback is not None:
                guard_callback()
            if progress_callback is not None:
                progress_callback(current, len(mission_items))

class FailingNativeRouteFakeTaskDroneClient(FakeTaskDroneClient):
    def execute_mission(self, mission_items, *, timeout_s, progress_callback=None, guard_callback=None) -> None:
        self.calls.append(("execute_mission", len(mission_items), timeout_s))
        raise RuntimeError("mission denied")


class DurationNativeRouteFakeTaskDroneClient(FakeTaskDroneClient):
    def execute_mission(
        self,
        mission_items,
        *,
        timeout_s,
        max_duration_s=None,
        progress_callback=None,
        guard_callback=None,
    ) -> bool:
        self.calls.append(("execute_mission_for", len(mission_items), timeout_s, max_duration_s))
        if progress_callback is not None:
            progress_callback(0, len(mission_items))
        return False

class DevicePlanTest(unittest.TestCase):
    def test_gps_route_waypoint_converts_to_local_north_east_offset(self) -> None:
        origin = GpsPoint(29.6101273, 106.3051375, 309.2)
        route = Route.spiral(max_radius_m=10.0, spacing_m=5.0, turns=1.0, gps_origin=origin)

        x_m, y_m = RouteExecutionMixin._gps_waypoint_local_xy(
            route.waypoints[-1], origin, 2.0, 3.0
        )

        self.assertAlmostEqual(x_m, 12.0, delta=0.02)
        self.assertAlmostEqual(y_m, 3.0, delta=0.02)

    def make_drone(self, product, managed_object, params):
        drone = Drone.from_managed_object(product, managed_object, params=params)
        self.assertTrue(drone.connect().ok)
        return drone

    def test_plan_entry_returns_device_specific_task_type(self) -> None:
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            FakeTaskDroneClient(),
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        dog = RobotDog(ProductType.UNITREE_GO2, params={"network_interface": "test0"})

        self.assertIsInstance(Plan(drone), DronePlan)
        self.assertIsInstance(Plan(dog), RobotDogPlan)
        drone.close()
        dog.close()

    def test_plan_entry_accepts_interceptors_for_device_specific_task_type(self) -> None:
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            FakeTaskDroneClient(),
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        dog = RobotDog(ProductType.UNITREE_GO2, params={"network_interface": "test0"})

        self.assertIsInstance(Plan(drone, interceptors=[lambda event: None]), DronePlan)
        self.assertIsInstance(Plan(dog, interceptors=[lambda event: None]), RobotDogPlan)
        drone.close()
        dog.close()

    def test_task_chain_runs_steps_in_order_only_after_run(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(armed=True)
        drone.state._update(relative_altitude_m=10.0)

        task = Plan(drone).unlock().takeoff(altitude=10).return_to_launch()

        self.assertEqual(client.calls, [("connect",)])
        result = task.run()

        self.assertTrue(result.ok)
        self.assertEqual(result.status, TaskStatus.SUCCESS)
        self.assertEqual([step.name for step in result.steps], ["unlock", "takeoff", "return_to_launch"])
        self.assertEqual(client.calls, [("connect",), ("arm",), ("takeoff", 10.0), ("return_to_launch",)])
        drone.close()

    def test_plan_move_uses_same_strong_target_as_device(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )

        result = Plan(drone).move(GpsPoint(31.2, 121.5, 120.0)).run()

        self.assertTrue(result.ok)
        self.assertIn(("goto_location", 31.2, 121.5, 120.0, 0.0), client.calls)
        drone.close()

    def test_plan_interceptor_observes_steps_and_can_replace_params_and_result(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(armed=True)
        phases = []

        def interceptor(event):
            phases.append((event.phase, event.step_index, event.step_name))
            if event.phase == TaskStepHookPhase.BEFORE_STEP and event.step_name == "takeoff":
                return TaskStepHookDecision.replace_params({"altitude": 12.0})
            if event.phase == TaskStepHookPhase.AFTER_STEP and event.step_name == "takeoff":
                return TaskStepHookDecision.replace_result(
                    TaskStepResult(
                        event.step_name,
                        True,
                        "intercepted result",
                        response=event.result.response,
                        started_at_s=event.result.started_at_s,
                        finished_at_s=event.result.finished_at_s,
                    )
                )
            return None

        result = (
            Plan(drone, interceptors=[interceptor])
            .require_confirmation(False)
            .unlock()
            .takeoff(altitude=10.0)
            .run()
        )

        self.assertTrue(result.ok)
        self.assertIn(("takeoff", 12.0), client.calls)
        self.assertEqual(result.steps[1].message, "intercepted result")
        self.assertEqual(
            phases,
            [
                (TaskStepHookPhase.BEFORE_STEP, 1, "unlock"),
                (TaskStepHookPhase.AFTER_STEP, 1, "unlock"),
                (TaskStepHookPhase.BEFORE_STEP, 2, "takeoff"),
                (TaskStepHookPhase.AFTER_STEP, 2, "takeoff"),
            ],
        )
        drone.close()

    def test_plan_interceptor_can_skip_or_fail_step(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )

        def interceptor(event):
            if event.phase == TaskStepHookPhase.BEFORE_STEP and event.step_name == "get_status":
                return TaskStepHookDecision.skip("status skipped")
            if event.phase == TaskStepHookPhase.BEFORE_STEP and event.step_name == "stop":
                return TaskStepHookDecision.fail("stop rejected")
            return None

        result = Plan(drone, interceptors=[interceptor]).get_status().stop().run()

        self.assertFalse(result.ok)
        self.assertEqual([step.message for step in result.steps], ["status skipped", "stop rejected"])
        self.assertNotIn(("get_status",), client.calls)
        self.assertNotIn(("stop",), client.calls)
        drone.close()

    def test_takeoff_task_requires_altitude_confirmation_by_default(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(armed=True)

        result = Plan(drone).unlock().takeoff(altitude=10).run(
            step_timeout_s=0.01,
            guard_interval_s=0.001,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.status, TaskStatus.TIMEOUT)
        self.assertEqual(result.failed_step, "takeoff")
        self.assertIn("Altitude did not reach", result.message)
        drone.close()

    def test_task_failure_handler_runs_on_failure(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(armed=True)
        handled = []

        def handle_failed(ctx, result) -> None:
            handled.append((ctx.current_step, result.status, result.failed_step))
            ctx.device.return_to_launch()

        result = (
            Plan(drone)
            .on_failed(handle_failed)

            .unlock()
            .takeoff(altitude=10)
            .run(step_timeout_s=0.01, guard_interval_s=0.001)
        )

        self.assertFalse(result.ok)
        self.assertEqual(handled, [("takeoff", TaskStatus.TIMEOUT, "takeoff")])
        self.assertIn(("return_to_launch",), client.calls)
        drone.close()

    def test_task_failure_handler_error_is_reported_without_hiding_original_failure(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(armed=True)

        def broken_handler(ctx, result) -> None:
            raise RuntimeError("handler failed")

        result = Plan(drone).on_failed(broken_handler).unlock().takeoff(altitude=10).run(
            step_timeout_s=0.01,
            guard_interval_s=0.001,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failed_step, "takeoff")
        self.assertIn("Altitude did not reach", result.message)
        self.assertIn("handler failed", result.message)
        drone.close()

    def test_ensure_return_to_launch_records_cleanup_result_after_failure(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(armed=True)

        result = (
            Plan(drone)
            .unlock()
            .takeoff(altitude=10.0)
            .ensure_return_to_launch(timeout_s=1.0)
            .run(step_timeout_s=0.01, guard_interval_s=0.001)
        )

        self.assertFalse(result.ok)
        self.assertIs(result.completion_reason, TaskCompletionReason.TIMEOUT)
        self.assertIsNotNone(result.cleanup_result)
        assert result.cleanup_result is not None
        self.assertTrue(result.cleanup_result.ok)
        self.assertIn(("return_to_launch",), client.calls)
        drone.close()

    def test_ensure_land_records_cleanup_result_without_home(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "serial://COM3:57600"},
        )
        drone.state._update(armed=False, in_air=False)

        result = (
            Plan(drone)
            .when(lambda _ctx: True, reason="abort before route")
            .ensure_land(timeout_s=1.0)
            .run(step_timeout_s=0.1, guard_interval_s=0.001)
        )

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.cleanup_result)
        assert result.cleanup_result is not None
        self.assertTrue(result.cleanup_result.ok)
        self.assertIn(("land",), client.calls)
        drone.close()

    def test_unlock_waits_for_delayed_armed_telemetry(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )

        def mark_armed(ctx) -> bool:
            if ("arm",) in client.calls:
                ctx.device.state._update(armed=True)
            return False

        result = (
            Plan(drone)

            .when(mark_armed, then=lambda ctx: None, reason="unused")
            .unlock()
            .run(step_timeout_s=1.0, guard_interval_s=0.001)
        )

        self.assertTrue(result.ok)
        self.assertIn(("arm",), client.calls)
        drone.close()

    def test_px4_unlock_confirms_from_command_state_event(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )

        result = Plan(drone).unlock().run(step_timeout_s=0.01, guard_interval_s=0.001)

        self.assertTrue(result.ok)
        self.assertTrue(drone.state.snapshot().armed)
        self.assertIn(("arm",), client.calls)
        drone.close()

    def test_wait_until_ready_runs_before_unlock(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )

        def mark_ready(ctx) -> bool:
            ctx.device.state._update(relative_altitude_m=0.0)
            ctx.device.state._update(local_x_m=0.0)
            ctx.device.state._update(local_y_m=0.0)
            ctx.device.state._update(local_z_m=0.0)
            ctx.device.state._update(home_absolute_altitude_m=100.0)
            ctx.device.state._update(health_local_position_ok=True)
            ctx.device.state._update(health_home_position_ok=True)
            ctx.device.state._update(health_armable=True)
            if ("arm",) in client.calls:
                ctx.device.state._update(armed=True)
            return False

        result = (
            Plan(drone)

            .when(mark_ready, then=lambda ctx: None, reason="unused")
            .wait_until_ready(timeout_s=1.0)
            .unlock()
            .run(step_timeout_s=1.0, guard_interval_s=0.001)
        )

        self.assertTrue(result.ok)
        self.assertEqual([step.name for step in result.steps], ["wait_until_ready", "unlock"])
        self.assertEqual(client.calls, [("connect",), ("get_status",), ("arm",)])
        drone.close()

    def test_px4_takeoff_readiness_accepts_indoor_local_position_without_home(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "serial://COM3:57600"},
        )
        drone.state._update(
            health_local_position_ok=True,
            local_x_m=0.0,
            local_y_m=0.0,
            local_z_m=0.0,
            last_event_updated_at_s=time.monotonic(),
        )

        report = drone.readiness_for(CapabilityName.TAKEOFF)

        self.assertTrue(report.ready, report.issues)
        drone.close()

    def test_px4_takeoff_readiness_accepts_outdoor_global_position_without_local(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(
            health_global_position_ok=True,
            health_home_position_ok=True,
            latitude_deg=31.2,
            longitude_deg=121.5,
            relative_altitude_m=0.0,
            home_absolute_altitude_m=100.0,
            last_event_updated_at_s=time.monotonic(),
        )

        report = drone.readiness_for(CapabilityName.TAKEOFF)

        self.assertTrue(report.ready, report.issues)
        drone.close()

    def test_px4_route_readiness_selects_global_or_local_chain_by_capability(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(
            health_global_position_ok=True,
            health_home_position_ok=True,
            latitude_deg=31.2,
            longitude_deg=121.5,
            relative_altitude_m=5.0,
            home_absolute_altitude_m=100.0,
            last_event_updated_at_s=time.monotonic(),
        )

        self.assertTrue(drone.readiness_for(CapabilityName.ROUTE).ready)
        self.assertTrue(drone.readiness_for(CapabilityName.MISSION).ready)
        self.assertFalse(drone.readiness_for(CapabilityName.OFFBOARD).ready)
        drone.close()

    def test_takeoff_task_uses_altitude_tolerance_for_completion(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(armed=True)
        drone.state._update(relative_altitude_m=9.4)

        result = Plan(drone).unlock().altitude_tolerance(0.5).takeoff(altitude=10).run(
            step_timeout_s=0.01,
            guard_interval_s=0.001,
        )
        self.assertFalse(result.ok)
        self.assertIn("before timeout", result.message)

        drone.state._update(relative_altitude_m=9.5)
        passed = Plan(drone).unlock().altitude_tolerance(0.5).takeoff(altitude=10).run()
        self.assertTrue(passed.ok)
        drone.close()

    def test_takeoff_task_climbs_to_fill_missing_altitude(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(armed=True)
        drone.state._update(relative_altitude_m=8.0)

        def mark_altitude_reached(ctx) -> bool:
            if ("set_velocity", 0.0, 0.0, 1.0, 1.2) in client.calls:
                ctx.device.state._update(relative_altitude_m=10.0)
            return False

        result = (
            Plan(drone)
            .climb_speed(1.2)

            .unlock()
            .when(mark_altitude_reached, then=lambda ctx: None, reason="unused")
            .takeoff(altitude=10)
            .run(step_timeout_s=1.0, guard_interval_s=0.001)
        )

        self.assertTrue(result.ok)
        self.assertIn(("set_velocity", 0.0, 0.0, 1.0, 1.2), client.calls)
        drone.close()

    def test_altitude_confirmation_uses_timeout_as_failure_boundary(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(armed=True)
        drone.state._update(relative_altitude_m=8.0)

        result = (
            Plan(drone)

            .unlock()
            .takeoff(altitude=10)
            .run(step_timeout_s=1.0, guard_interval_s=0.001)
        )

        self.assertFalse(result.ok)
        self.assertIn("before timeout", result.message)
        self.assertGreaterEqual(client.calls.count(("set_velocity", 0.0, 0.0, 1.0, 1.0)), 1)
        drone.close()

    def test_task_guard_can_interrupt_and_return_to_launch(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(battery_remaining_fraction=0.1)

        def low_battery(ctx) -> bool:
            return (
                ctx.state.battery_remaining_fraction is not None
                and ctx.state.battery_remaining_fraction < 0.2
            )

        def return_to_launch(ctx) -> None:
            ctx.device.return_to_launch()

        result = (
            Plan(drone)

            .when(
                low_battery,
                then=return_to_launch,
                reason="low battery",
            )
            .unlock()
            .run()
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.status, TaskStatus.GUARD_TRIGGERED)
        self.assertEqual(result.triggered_guard, "low battery")
        self.assertEqual(client.calls, [("connect",), ("return_to_launch",)])
        drone.close()

    def test_return_to_launch_waits_for_landing_when_in_air(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(in_air=True)

        def mark_landed_after_rtl(ctx) -> bool:
            if ("return_to_launch",) in client.calls:
                ctx.device.state._update(in_air=False)
            return False

        result = (
            Plan(drone)

            .when(mark_landed_after_rtl, then=lambda ctx: None, reason="unused")
            .return_to_launch()
            .run(step_timeout_s=1.0, guard_interval_s=0.001)
        )

        self.assertTrue(result.ok)
        self.assertFalse(drone.state.snapshot().in_air)
        drone.close()

    def test_land_confirmation_accepts_on_ground_when_in_air_is_stale(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(in_air=True, armed=True, landed_state="in_air")

        def mark_on_ground_after_land(ctx) -> bool:
            if ("land",) in client.calls:
                ctx.device.state._update(landed_state="on_ground")
            return False

        result = (
            Plan(drone)
            .when(mark_on_ground_after_land, then=lambda ctx: None, reason="unused")
            .land()
            .run(step_timeout_s=1.0, guard_interval_s=0.001)
        )

        self.assertTrue(result.ok)
        self.assertTrue(drone.state.snapshot().in_air)
        self.assertEqual(drone.state.snapshot().landed_state, "on_ground")
        drone.close()

    def test_land_confirmation_accepts_disarmed_when_in_air_is_stale(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(in_air=True, armed=True, landed_state="in_air")

        def mark_disarmed_after_land(ctx) -> bool:
            if ("land",) in client.calls:
                ctx.device.state._update(armed=False)
            return False

        result = (
            Plan(drone)
            .when(mark_disarmed_after_land, then=lambda ctx: None, reason="unused")
            .land()
            .run(step_timeout_s=1.0, guard_interval_s=0.001)
        )

        self.assertTrue(result.ok)
        self.assertTrue(drone.state.snapshot().in_air)
        self.assertFalse(drone.state.snapshot().armed)
        drone.close()

    def test_task_options_provide_defaults_for_route_steps(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(relative_altitude_m=14.2)
        drone.state._update(local_x_m=0.0)
        drone.state._update(local_y_m=0.0)
        route = Route.polygon(({"x_m": 0.0, "y_m": 0.0}, {"x_m": 0.0, "y_m": 0.0}), close=False)

        result = (
            Plan(drone)

            .speed(4.0)
            .climb_speed(1.5)
            .altitude(14.0)
            .heading(HeadingMode.TANGENT)
            .route(route)
            .run()
        )

        self.assertTrue(result.ok)
        self.assertEqual(client.calls, [("connect",), ("hold",), ("stop",)])
        drone.close()

    def test_routes_adds_multiple_routes_in_order(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(relative_altitude_m=14.0)
        drone.state._update(local_x_m=0.0)
        drone.state._update(local_y_m=0.0)
        route_a = Route.polyline((Waypoint(x_m=0.0, y_m=0.0), Waypoint(x_m=0.0, y_m=0.0)), name="route-a")
        route_b = Route.polyline((Waypoint(x_m=0.0, y_m=0.0), Waypoint(x_m=0.0, y_m=0.0)), name="route-b")

        result = Plan(drone).routes((route_a, route_b), altitude=14.0).run()

        self.assertTrue(result.ok)
        self.assertEqual([step.name for step in result.steps], ["route", "route"])
        self.assertEqual(client.calls, [("connect",), ("hold",), ("stop",), ("hold",), ("stop",)])
        drone.close()

    def test_route_motion_only_applies_to_current_route_step(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        observed_options = []
        route = Route.polyline((Waypoint(x_m=0.0, y_m=0.0), Waypoint(x_m=1.0, y_m=0.0)))

        class RecordingDronePlan(DronePlan):
            def _execute_route(self, route, ctx, guard_interval_s, **kwargs):
                observed_options.append((ctx.options.motion_mode, ctx.options.min_lookahead_m))
                return StandardResponse(True, "route", "test-drone", "mock.drone")

        try:
            result = (
                RecordingDronePlan(drone)
                .route(route, motion=RouteMotion(mode=MotionExecutionMode.SDK, min_lookahead_m=3.5))
                .route(route)
                .run()
            )
        finally:
            drone.close()

        self.assertTrue(result.ok)
        self.assertEqual(
            observed_options,
            [
                (MotionExecutionMode.SDK, 3.5),
                (MotionExecutionMode.AUTO, 1.0),
            ],
        )

    def test_route_step_holds_route_telemetry_for_native_or_sdk_execution(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        active_leases = 0
        acquired = []

        def acquire(capability: CapabilityName):
            nonlocal active_leases
            acquired.append(capability)
            active_leases += 1

            def release() -> None:
                nonlocal active_leases
                active_leases -= 1

            return release

        drone._acquire_capability_telemetry = acquire  # type: ignore[method-assign]
        route = Route.polyline((Waypoint(x_m=0.0, y_m=0.0), Waypoint(x_m=1.0, y_m=0.0)))
        test_case = self

        class RecordingDronePlan(DronePlan):
            def _execute_route(self, route, ctx, guard_interval_s, **kwargs):
                test_case.assertEqual(active_leases, 1)
                return StandardResponse(True, "route", "test-drone", "mock.drone")

        try:
            result = RecordingDronePlan(drone).route(route).run()
        finally:
            drone.close()

        self.assertTrue(result.ok)
        self.assertEqual(acquired, [CapabilityName.ROUTE])
        self.assertEqual(active_leases, 0)

    def test_heading_tangent_sets_route_yaw(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(local_x_m=0.0)
        drone.state._update(local_y_m=0.0)
        route = Route.polygon(({"x_m": 0.0, "y_m": 0.0}, {"x_m": 0.0, "y_m": 10.0}), close=False)

        def mark_target_reached(ctx) -> bool:
            if ("set_velocity", 0.0, 1.0, 0.0, 2.0, 90.0) in client.calls:
                ctx.device.state._update(local_y_m=10.0)
            return False

        result = (
            Plan(drone)

            .when(mark_target_reached, then=lambda ctx: None, reason="unused")
            .speed(2.0)
            .heading(HeadingMode.TANGENT)
            .route(route)
            .run(step_timeout_s=1.0, guard_interval_s=0.001)
        )

        self.assertTrue(result.ok)
        self.assertIn(("set_velocity", 0.0, 1.0, 0.0, 2.0, 90.0), client.calls)
        drone.close()

    def test_polygon_slows_near_target_and_keeps_segment_yaw(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(local_x_m=0.0)
        drone.state._update(local_y_m=9.0)
        route = Route.polygon(({"x_m": 0.0, "y_m": -9.0}, {"x_m": 0.0, "y_m": 1.0}), close=False)

        def mark_target_reached(ctx) -> bool:
            velocity_calls = [call for call in client.calls if call[0] == "set_velocity"]
            if len(velocity_calls) >= 3:
                ctx.device.state._update(local_y_m=10.0)
            return False

        result = (
            Plan(drone)

            .when(mark_target_reached, then=lambda ctx: None, reason="unused")
            .speed(3.0)
            .heading(HeadingMode.TANGENT)
            .route(route)
            .run(step_timeout_s=1.0, guard_interval_s=0.001)
        )

        self.assertTrue(result.ok)
        velocity_calls = [call for call in client.calls if call[0] == "set_velocity"][:3]
        self.assertEqual(len(velocity_calls), 3)
        for call, expected_speed in zip(velocity_calls, (0.2, 0.4, 0.6)):
            self.assertEqual(call[:4], ("set_velocity", 0.0, 1.0, 0.0))
            self.assertAlmostEqual(call[4], expected_speed)
            self.assertEqual(call[5], 90.0)
        self.assertIn(("stop",), client.calls)

    def test_route_generators_create_common_patterns(self) -> None:
        self.assertEqual(len(Route.polyline(({"x_m": 0.0, "y_m": 0.0}, {"x_m": 1.0, "y_m": 0.0})).waypoints), 2)
        circle = Route.circle(radius_m=5.0, action=WaypointAction.PHOTO, acceptance_radius_m=1.0, hold_time_s=0.5)
        self.assertGreaterEqual(len(circle.waypoints), 9)
        self.assertEqual((circle.waypoints[0].x_m, circle.waypoints[0].y_m), (0.0, 0.0))
        self.assertTrue(all(waypoint.action is WaypointAction.PHOTO for waypoint in circle.waypoints[1:]))
        self.assertTrue(all(waypoint.acceptance_radius_m == 1.0 for waypoint in circle.waypoints[1:]))
        self.assertTrue(all(waypoint.hold_time_s == 0.5 for waypoint in circle.waypoints[1:]))
        self.assertGreaterEqual(len(Route.ellipse(radius_x_m=8.0, radius_y_m=4.0).waypoints), 9)
        self.assertGreaterEqual(len(Route.arc(radius_m=5.0, start_deg=0.0, end_deg=90.0).waypoints), 3)
        grid = Route.grid(width_m=10.0, height_m=5.0, spacing_m=2.5, action=WaypointAction.PHOTO)
        self.assertGreaterEqual(len(grid.waypoints), 4)
        self.assertTrue(all(waypoint.action is WaypointAction.PHOTO for waypoint in grid.waypoints))
        survey = Route.survey_area(
            (
                Waypoint(x_m=0.0, y_m=0.0),
                Waypoint(x_m=10.0, y_m=0.0),
                Waypoint(x_m=10.0, y_m=6.0),
                Waypoint(x_m=0.0, y_m=6.0),
            ),
            spacing_m=3.0,
            action=WaypointAction.PHOTO,
        )
        self.assertEqual(survey.pattern, RoutePattern.SURVEY_AREA)
        self.assertGreaterEqual(len(survey.waypoints), 4)
        self.assertTrue(all(waypoint.action is WaypointAction.PHOTO for waypoint in survey.waypoints))
        self.assertGreaterEqual(len(Route.spiral(max_radius_m=10.0, spacing_m=2.0).waypoints), 13)
        entry_spiral = Route.spiral_search(
            target_length_m=120.0,
            max_radius_m=20.0,
            entry_radius_m=8.0,
            center_x_m=15.0,
            center_y_m=-4.0,
            start_angle_deg=90.0,
            acceptance_radius_m=2.0,
        )
        self.assertAlmostEqual(entry_spiral.waypoints[0].x_m or 0.0, 15.0)
        self.assertAlmostEqual(entry_spiral.waypoints[0].y_m or 0.0, -4.0)
        self.assertAlmostEqual(entry_spiral.waypoints[1].x_m or 0.0, 15.0, places=5)
        self.assertAlmostEqual(entry_spiral.waypoints[1].y_m or 0.0, 4.0, places=5)
        self.assertAlmostEqual(entry_spiral.estimated_length_m or 0.0, 120.0, delta=2.0)
        self.assertTrue(all(waypoint.acceptance_radius_m == 2.0 for waypoint in entry_spiral.waypoints))
        self.assertEqual(Route.spiral(max_radius_m=10.0, spacing_m=2.0).continuity, RouteContinuity.REQUIRED)
        self.assertEqual(Route.spiral(max_radius_m=10.0, spacing_m=2.0).geometry.kind, "spiral")
        spiral_search = Route.spiral_search(
            target_length_m=250.0,
            max_radius_m=30.0,
            start_angle_deg=45.0,
            clockwise=True,
        )
        self.assertAlmostEqual(spiral_search.estimated_length_m or 0.0, 250.0, delta=2.0)
        self.assertEqual(spiral_search.geometry.params["start_angle_deg"], 45.0)
        self.assertTrue(spiral_search.geometry.params["clockwise"])
        self.assertGreaterEqual(
            len(Route.corridor(({"x_m": 0.0, "y_m": 0.0}, {"x_m": 10.0, "y_m": 0.0}), width_m=4.0).waypoints),
            5,
        )
        corridor = Route.corridor(({"x_m": 0.0, "y_m": 0.0}, {"x_m": 10.0, "y_m": 0.0}), width_m=4.0)
        self.assertEqual((corridor.waypoints[0].x_m, corridor.waypoints[0].y_m), (0.0, 0.0))

        gps_origin = GpsPoint(31.2, 121.5, 120.0)
        gps_spiral = Route.spiral(
            max_radius_m=10.0,
            spacing_m=2.0,
            gps_origin=gps_origin,
            altitude_m=130.0,
        )
        self.assertTrue(all(isinstance(target, GpsPoint) for target in gps_spiral.targets))
        self.assertTrue(all(target.altitude_m == 130.0 for target in gps_spiral.targets))
        self.assertNotEqual(gps_spiral.targets[-1].latitude_deg, gps_origin.latitude_deg)

    def test_route_publishes_waypoint_settled_after_target_is_stable(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(local_x_m=0.0)
        drone.state._update(local_y_m=0.0)
        drone.state._update(relative_altitude_m=14.0)
        settled_events = []
        unsubscribe = drone.events.on(WaypointSettledEvent, settled_events.append)

        def mark_target_reached(ctx) -> bool:
            if any(call[0] == "set_velocity" for call in client.calls):
                ctx.device.state._update(local_x_m=5.0)
                ctx.device.state._update(velocity_x_m_s=0.0)
                ctx.device.state._update(velocity_y_m_s=0.0)
                ctx.device.state._update(velocity_z_m_s=0.0)
            return False

        route = Route.polyline(
            (
                Waypoint(x_m=0.0, y_m=0.0),
                Waypoint(x_m=5.0, y_m=0.0, action=WaypointAction.PHOTO, metadata={"shot": "front"}),
            ),
            name="photo-line",
        )
        try:
            result = (
                Plan(drone)

                .when(mark_target_reached, then=lambda ctx: None, reason="unused")
                .route(route, altitude=14.0, speed=1.5, heading=HeadingMode.TANGENT, motion=RouteMotion(settle_time_s=0.0))
                .run(step_timeout_s=1.0, guard_interval_s=0.001)
            )
            time.sleep(0.05)
        finally:
            unsubscribe()
            drone.close()

        self.assertTrue(result.ok)
        self.assertEqual(len(settled_events), 1)
        self.assertIs(settled_events[0].type, EventType.WAYPOINT_SETTLED)
        self.assertEqual(settled_events[0].progress.route_name, "photo-line")
        self.assertEqual(settled_events[0].progress.metadata["shot"], "front")
        drone.close()

    def test_route_step_timeout_applies_to_whole_route(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(local_x_m=0.0)
        drone.state._update(local_y_m=0.0)
        started_at = time.monotonic()

        def advance_slowly(ctx) -> bool:
            elapsed_s = time.monotonic() - started_at
            ctx.device.state._update(local_x_m=min(3.0, float(int(elapsed_s / 0.025))))
            return False

        route = Route.polyline(
            (
                Waypoint(x_m=0.0, y_m=0.0),
                Waypoint(x_m=1.0, y_m=0.0),
                Waypoint(x_m=2.0, y_m=0.0),
                Waypoint(x_m=3.0, y_m=0.0),
            ),
            name="whole-route-timeout",
        )
        try:
            result = (
                Plan(drone)
                .when(advance_slowly, then=lambda ctx: None, reason="unused")
                .route(route, altitude=10.0, speed=1.0, motion=RouteMotion(mode=MotionExecutionMode.SDK, settle_time_s=0.0))
                .run(step_timeout_s=0.06, guard_interval_s=0.001)
            )
        finally:
            drone.close()

        self.assertFalse(result.ok)
        self.assertEqual(result.status, TaskStatus.TIMEOUT)
        self.assertEqual(result.failed_step, "route")

    def test_sdk_route_duration_limit_is_successful_completion(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(local_x_m=0.0)
        drone.state._update(local_y_m=0.0)
        completed_events = []
        unsubscribe = drone.events.on(RouteCompletedEvent, completed_events.append)
        route = Route.polyline(
            (Waypoint(x_m=0.0, y_m=0.0), Waypoint(x_m=100.0, y_m=0.0)),
            name="duration-limited-sdk-route",
        )

        try:
            result = (
                Plan(drone)
                .route(route, altitude=10.0, speed=1.0, max_duration_s=0.02, timeout_s=1.0, motion=RouteMotion(mode=MotionExecutionMode.SDK))
                .run(guard_interval_s=0.001)
            )
            drone.events.drain(timeout_s=1.0)
        finally:
            unsubscribe()
            drone.close()

        self.assertTrue(result.ok)
        self.assertEqual(
            result.steps[-1].response.data["route_completion_reason"],
            RouteCompletionReason.DURATION_REACHED.value,
        )
        self.assertEqual(completed_events[-1].progress.completion_reason, RouteCompletionReason.DURATION_REACHED)
        self.assertIn(("stop",), client.calls)

    def test_auto_spiral_uses_sdk_continuous_route_without_midpoint_stop(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(local_x_m=0.0)
        drone.state._update(local_y_m=0.0)
        drone.state._update(relative_altitude_m=10.0)
        route = Route.spiral(max_radius_m=4.0, spacing_m=2.0, turns=1.0, name="smooth-spiral")
        final = route.waypoints[-1]

        def mark_final_reached(ctx) -> bool:
            if len([call for call in client.calls if call[0] == "set_velocity"]) >= 3:
                ctx.device.state._update(local_x_m=float(final.x_m or 0.0))
                ctx.device.state._update(local_y_m=float(final.y_m or 0.0))
                ctx.device.state._update(velocity_x_m_s=0.0)
                ctx.device.state._update(velocity_y_m_s=0.0)
                ctx.device.state._update(velocity_z_m_s=0.0)
            return False

        try:
            result = (
                Plan(drone)
                .when(mark_final_reached, then=lambda ctx: None, reason="unused")
                .route(route, altitude=10.0, speed=1.5, heading=HeadingMode.TANGENT, motion=RouteMotion(mode=MotionExecutionMode.AUTO, settle_time_s=0.0))
                .run(step_timeout_s=1.0, guard_interval_s=0.001)
            )
        finally:
            drone.close()

        self.assertTrue(result.ok)
        self.assertIn("set_velocity", [call[0] for call in client.calls])
        self.assertEqual(client.calls.count(("stop",)), 1)

    def test_robot_dog_route_can_use_continuous_sdk_path(self) -> None:
        dog = RobotDog(ProductType.UNITREE_GO2, params={"network_interface": "test0"})
        self.assertTrue(dog.connect().ok)
        dog.state._update(local_x_m=0.0)
        dog.state._update(local_y_m=0.0)
        route = Route.polyline((Waypoint(x_m=0.0, y_m=0.0), Waypoint(x_m=2.0, y_m=0.0)))

        def mark_target_reached(ctx) -> bool:
            if float(dog.adapter.velocity["speed"]) <= 0.0:
                return False
            ctx.device.state._update(local_x_m=2.0)
            ctx.device.state._update(velocity_x_m_s=0.0)
            ctx.device.state._update(velocity_y_m_s=0.0)
            ctx.device.state._update(velocity_z_m_s=0.0)
            return False

        try:
            result = (
                Plan(dog)
                .stand()
                .when(mark_target_reached, then=lambda ctx: None, reason="unused")
                .route(route, speed=0.5, motion=RouteMotion(mode=MotionExecutionMode.SDK, settle_time_s=0.0))
                .run(step_timeout_s=1.0, guard_interval_s=0.001)
            )
        finally:
            dog.close()

        self.assertTrue(result.ok)

    def test_native_route_duration_limit_is_forwarded_and_observable(self) -> None:
        client = DurationNativeRouteFakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(latitude_deg=31.2)
        drone.state._update(longitude_deg=121.5)
        drone.state._update(relative_altitude_m=14.0)
        completed_events = []
        unsubscribe = drone.events.on(RouteCompletedEvent, completed_events.append)
        route = Route.polyline(
            (Waypoint(x_m=0.0, y_m=0.0), Waypoint(x_m=50.0, y_m=0.0)),
            name="duration-limited-native-route",
        )

        try:
            result = (
                Plan(drone)
                .route(route, altitude=14.0, speed=1.5, max_duration_s=0.25, timeout_s=1.0)
                .run(guard_interval_s=0.001)
            )
            drone.events.drain(timeout_s=1.0)
        finally:
            unsubscribe()
            drone.close()

        self.assertTrue(result.ok)
        self.assertIn(("execute_mission_for", 1, 1.0, 0.25), client.calls)
        self.assertEqual(
            result.steps[-1].response.data["route_completion_reason"],
            RouteCompletionReason.DURATION_REACHED.value,
        )
        self.assertEqual(completed_events[-1].progress.completion_reason, RouteCompletionReason.DURATION_REACHED)

    def test_route_rejects_non_positive_duration_limit(self) -> None:
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            FakeTaskDroneClient(),
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        route = Route.polyline((Waypoint(x_m=0.0, y_m=0.0), Waypoint(x_m=1.0, y_m=0.0)))
        try:
            with self.assertRaises(ValueError):
                Plan(drone).route(route, max_duration_s=0.0)
            with self.assertRaises(ValueError):
                Plan(drone).route(route, timeout_s=0.0)
        finally:
            drone.close()

    def test_auto_motion_uses_native_route_when_adapter_supports_it(self) -> None:
        client = NativeRouteFakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(latitude_deg=31.2)
        drone.state._update(longitude_deg=121.5)
        drone.state._update(relative_altitude_m=14.0)
        settled_events = []
        unsubscribe = drone.events.on(WaypointSettledEvent, settled_events.append)
        route = Route.polyline(
            (
                Waypoint(x_m=0.0, y_m=0.0),
                Waypoint(x_m=5.0, y_m=0.0, action=WaypointAction.PHOTO),
            ),
            name="native-line",
        )

        try:
            result = (
                Plan(drone)

                .route(route, altitude=14.0, speed=1.5, motion=RouteMotion(mode=MotionExecutionMode.AUTO))
                .run(step_timeout_s=1.0, guard_interval_s=0.001)
            )
            drone.events.drain(timeout_s=1.0)
        finally:
            unsubscribe()
            drone.close()

        self.assertTrue(result.ok)
        self.assertIn(("execute_mission", 1, 1.0), client.calls)
        self.assertNotIn("set_velocity", [call[0] for call in client.calls])
        self.assertEqual(len(settled_events), 1)
        self.assertEqual(settled_events[0].progress.route_name, "native-line")

    def test_flight_controller_motion_fails_when_native_route_is_unavailable(self) -> None:
        client = FakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        route = Route.polyline((Waypoint(x_m=0.0, y_m=0.0), Waypoint(x_m=5.0, y_m=0.0)))
        try:
            result = (
                Plan(drone)

                .route(route, altitude=14.0, speed=1.5, motion=RouteMotion(mode=MotionExecutionMode.FLIGHT_CONTROLLER))
                .run(step_timeout_s=1.0, guard_interval_s=0.001)
            )
        finally:
            drone.close()

        self.assertFalse(result.ok)
        self.assertEqual(result.failed_step, "route")
        self.assertIn("native flight-controller route", result.message)

    def test_auto_motion_falls_back_to_sdk_when_native_route_fails(self) -> None:
        client = FailingNativeRouteFakeTaskDroneClient()
        drone = self.make_drone(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": "udpin://0.0.0.0:14550"},
        )
        drone.state._update(latitude_deg=31.2)
        drone.state._update(longitude_deg=121.5)
        drone.state._update(relative_altitude_m=14.0)
        drone.state._update(local_x_m=0.0)
        drone.state._update(local_y_m=0.0)
        route = Route.polyline((Waypoint(x_m=0.0, y_m=0.0), Waypoint(x_m=1.0, y_m=0.0)))

        def mark_target_reached(ctx) -> bool:
            if any(call[0] == "set_velocity" for call in client.calls):
                ctx.device.state._update(local_x_m=1.0)
                ctx.device.state._update(velocity_x_m_s=0.0)
                ctx.device.state._update(velocity_y_m_s=0.0)
                ctx.device.state._update(velocity_z_m_s=0.0)
            return False

        try:
            result = (
                Plan(drone)

                .when(mark_target_reached, then=lambda ctx: None, reason="unused")
                .route(route, altitude=14.0, speed=1.5, motion=RouteMotion(mode=MotionExecutionMode.AUTO, settle_time_s=0.0))
                .run(step_timeout_s=1.0, guard_interval_s=0.001)
            )
        finally:
            drone.close()

        self.assertTrue(result.ok)
        self.assertIn(("execute_mission", 1, 1.0), client.calls)
        self.assertIn("set_velocity", [call[0] for call in client.calls])

if __name__ == "__main__":
    unittest.main()
