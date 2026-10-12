import unittest
from threading import Barrier
import time

from fleetdock import (
    BatteryState,
    CapabilityName,
    CoordinateFrame,
    DeviceCategory,
    DeviceEvent,
    DeviceMetadata,
    DeviceEventHandlerError,
    Drone,
    DroneFleet,
    DroneFleetPlan,
    EventCategory,
    EventType,
    Fleet,
    FleetOperationResult,
    HandlerErrorPolicy,
    Obstacle,
    ObstacleKind,
    ObstacleSnapshot,
    FollowExecutionOptions,
    FleetPlan,
    FormationLayout,
    FormationSlotState,
    FollowConflict,
    FollowMode,
    FollowPolicy,
    FollowSessionState,
    FollowSession,
    ParallelTaskResult,
    Plan,
    ProductType,
    RobotDog,
    RobotDogFleet,
    RobotDogFleetPlan,
    SafetyAction,
    SafetyPolicy,
    SafetyActionTriggeredEvent,
    TaskStepHookPhase,
    TaskStatus,
    Vector3,
)


class FakeFleetDroneClient:
    def __init__(self) -> None:
        self.calls = []

    def connect(self) -> None:
        self.calls.append(("connect",))

    def arm(self, force: bool = False) -> None:
        if force:
            self.calls.append(("arm", True))
        else:
            self.calls.append(("arm",))

    def takeoff(self, altitude: float) -> None:
        self.calls.append(("takeoff", altitude))

    def return_to_launch(self) -> None:
        self.calls.append(("return_to_launch",))

    def land(self) -> None:
        self.calls.append(("land",))

    def set_velocity(self, x: float, y: float, z: float, speed: float, yaw_deg=None) -> None:
        self.calls.append(("set_velocity", x, y, z, speed, yaw_deg))

    def stop(self) -> None:
        self.calls.append(("stop",))

    def hover(self) -> None:
        self.calls.append(("hover",))

    def get_status(self):
        self.calls.append(("get_status",))
        return {"calls": list(self.calls)}


class ConcurrentFleetDroneClient(FakeFleetDroneClient):
    def __init__(self, barrier: Barrier) -> None:
        super().__init__()
        self.barrier = barrier

    def get_status(self):
        self.calls.append(("get_status_entered",))
        self.barrier.wait(timeout=1.0)
        self.calls.append(("get_status_released",))
        return {"calls": list(self.calls)}


class FleetStaticObstacleProvider:
    def __init__(self, obstacle: Obstacle) -> None:
        self.obstacle = obstacle
        self.close_calls = 0

    def snapshot(self) -> ObstacleSnapshot:
        return ObstacleSnapshot(
            provider_name="test.fleet.static",
            frame=self.obstacle.frame,
            observed_at_s=time.monotonic(),
            obstacles=(self.obstacle,),
        )

    def close(self) -> None:
        self.close_calls += 1


class FleetTest(unittest.TestCase):
    def make_drone(self, device_id: str, name: str, endpoint: str = "udpin://0.0.0.0:14550"):
        client = FakeFleetDroneClient()
        drone = Drone.from_managed_object(
            ProductType.PX4_GENERIC,
            client,
            params={"endpoint": endpoint},
            metadata=DeviceMetadata(
                device_id=device_id,
                name=name,
                tags=("inspection",),
                local_frame_id="fleet-test-map",
            ),
        )
        return drone, client

    def test_fleet_filters_return_new_fleet_views(self) -> None:
        drone, _ = self.make_drone("drone-001", "A区无人机")
        dog = RobotDog(
            ProductType.UNITREE_GO2,
            params={"network_interface": "test0"},
            metadata=DeviceMetadata(device_id="dog-001", name="A区机器狗"),
        )
        fleet = Fleet([drone, dog], name="A区编队")
        drone_fleet = fleet.drones()
        dog_fleet = fleet.robot_dogs()

        self.assertEqual(fleet.device_ids, ("drone-001", "dog-001"))
        self.assertIsInstance(drone_fleet, DroneFleet)
        self.assertIsInstance(dog_fleet, RobotDogFleet)
        self.assertEqual(drone_fleet.device_ids, ("drone-001",))
        self.assertEqual(dog_fleet.device_ids, ("dog-001",))
        self.assertEqual(fleet.by_product(ProductType.PX4_GENERIC).device_ids, ("drone-001",))
        self.assertEqual(fleet.by_capability(CapabilityName.TAKEOFF).device_ids, ("drone-001",))
        self.assertEqual(fleet.by_id("dog-001").device_ids, ("dog-001",))
        self.assertEqual(fleet.by_name("A区无人机").device_ids, ("drone-001",))
        self.assertEqual(fleet.by_category(DeviceCategory.DRONE).device_ids, ("drone-001",))
        self.assertEqual(fleet.with_tag("inspection").device_ids, ("drone-001",))
        self.assertIsInstance(drone_fleet.where(lambda device: True), DroneFleet)
        self.assertIsInstance(dog_fleet.where(lambda device: True), RobotDogFleet)
        self.assertIs(drone_fleet.drones(), drone_fleet)
        self.assertIs(dog_fleet.robot_dogs(), dog_fleet)
        self.assertIsInstance(drone_fleet.robot_dogs(), RobotDogFleet)
        self.assertEqual(len(drone_fleet.robot_dogs()), 0)
        self.assertIsInstance(dog_fleet.drones(), DroneFleet)
        self.assertEqual(len(dog_fleet.drones()), 0)

        fleet.close()

    def test_fleet_events_forward_same_device_event(self) -> None:
        drone, _ = self.make_drone("drone-001", "A区无人机")
        fleet = Fleet([drone])
        received = []
        device_received = []

        unsubscribe = fleet.events.on(EventType.ROUTE_STARTED, received.append)
        unsubscribe_device = drone.events.on(EventType.ROUTE_STARTED, device_received.append)
        published = drone.events.publish(
            EventType.ROUTE_STARTED,
            EventCategory.STATE,
            {"route_id": "route-001", "route_name": "demo", "waypoint_count": 1},
        )
        self.assertTrue(fleet.events.drain(timeout_s=2.0))
        unsubscribe()
        unsubscribe_device()

        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], DeviceEvent)
        self.assertIs(received[0], published)
        self.assertIs(device_received[0], published)
        self.assertEqual(received[0].source.device_id, "drone-001")
        self.assertEqual(received[0].source.instance_id, drone.instance_id)
        self.assertEqual(received[0].progress.route_name, "demo")
        fleet.close()

    def test_fleet_events_can_filter_source_devices(self) -> None:
        drone1, _ = self.make_drone("drone-001", "Drone1")
        drone2, _ = self.make_drone("drone-002", "Drone2")
        fleet = Fleet([drone1, drone2])
        received = []

        unsubscribe = fleet.events.subscribe(
            EventType.ROUTE_STARTED,
            received.append,
            device=drone2,
        )
        for drone in (drone1, drone2):
            drone.events.publish(
                EventType.ROUTE_STARTED,
                EventCategory.STATE,
                {"route_id": "route-001", "route_name": "demo", "waypoint_count": 1},
            )
        self.assertTrue(fleet.events.drain(timeout_s=2.0))
        unsubscribe()

        self.assertEqual([event.source.device_id for event in received], ["drone-002"])
        fleet.close()

    def test_fleet_events_keep_single_underlying_subscription_per_device(self) -> None:
        drone, _ = self.make_drone("drone-001", "Drone1")
        fleet = Fleet([drone])

        before = drone.events.status().subscriber_count
        unsubscribe_all = fleet.events.subscribe(lambda event: None)
        unsubscribe_battery = fleet.events.subscribe(EventType.BATTERY_UPDATED, lambda event: None)
        after = drone.events.status().subscriber_count

        unsubscribe_all()
        unsubscribe_battery()

        self.assertEqual(before, 1)
        self.assertEqual(after, 2)
        fleet.close()

    def test_fleet_events_filter_device_type_and_product_in_mixed_fleet(self) -> None:
        drone, _ = self.make_drone("drone-001", "Drone1")
        dog = RobotDog(
            ProductType.UNITREE_GO2,
            params={"network_interface": "test0"},
            metadata=DeviceMetadata(device_id="dog-001", name="Dog1"),
        )
        fleet = Fleet([drone, dog])
        received = []

        unsubscribe = fleet.events.subscribe(
            received.append,
            device_type=DeviceCategory.ROBOT_DOG,
            product=ProductType.UNITREE_GO2,
            event_name=EventType.BATTERY_UPDATED,
        )
        drone.events.publish(
            EventType.BATTERY_UPDATED,
            EventCategory.TELEMETRY,
            BatteryState(remaining_fraction=0.5),
        )
        dog.events.publish(
            EventType.BATTERY_UPDATED,
            EventCategory.TELEMETRY,
            BatteryState(remaining_fraction=0.4),
        )
        self.assertTrue(fleet.events.drain(timeout_s=2.0))
        unsubscribe()

        self.assertEqual([event.source.device_id for event in received], ["dog-001"])
        self.assertEqual(received[0].source.device_category, DeviceCategory.ROBOT_DOG.value)
        fleet.close()

    def test_fleet_events_subscribe_event_with_predicate_filters_threshold(self) -> None:
        drone, _ = self.make_drone("drone-001", "Drone1")
        fleet = Fleet([drone])
        received = []

        unsubscribe = fleet.events.subscribe(
            EventType.BATTERY_UPDATED,
            received.append,
            predicate=lambda event: event.battery.remaining_fraction <= 0.25,
        )
        drone.events.publish(
            EventType.BATTERY_UPDATED,
            EventCategory.TELEMETRY,
            BatteryState(remaining_fraction=0.6),
        )
        drone.events.publish(
            EventType.BATTERY_UPDATED,
            EventCategory.TELEMETRY,
            BatteryState(remaining_fraction=0.2),
        )
        self.assertTrue(fleet.events.drain(timeout_s=2.0))
        unsubscribe()

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].battery.remaining_fraction, 0.2)
        fleet.close()

    def test_fleet_events_subscribe_event_requires_handler(self) -> None:
        drone, _ = self.make_drone("drone-001", "Drone1")
        fleet = Fleet([drone])

        with self.assertRaises(TypeError):
            fleet.events.subscribe(EventType.BATTERY_UPDATED)

        fleet.close()

    def test_fleet_events_use_device_error_policy_semantics(self) -> None:
        drone, _ = self.make_drone("drone-001", "Drone1")
        fleet = Fleet([drone])
        fleet.events.on(
            EventType.BATTERY_UPDATED,
            lambda event: (_ for _ in ()).throw(RuntimeError("failed")),
            error_policy=HandlerErrorPolicy.RAISE_ON_DRAIN,
        )
        drone.events.publish(
            EventType.BATTERY_UPDATED,
            EventCategory.TELEMETRY,
            BatteryState(remaining_fraction=0.5),
        )

        with self.assertRaises(DeviceEventHandlerError):
            fleet.events.drain(timeout_s=2.0)
        fleet.close()

    def test_fleet_events_can_filter_source_device_and_event_name(self) -> None:
        drone, _ = self.make_drone("drone-001", "Drone1")
        dog = RobotDog(
            ProductType.UNITREE_GO2,
            params={"network_interface": "test0"},
            metadata=DeviceMetadata(device_id="dog-001", name="Dog1"),
        )
        fleet = Fleet([drone, dog])
        received = []

        unsubscribe = fleet.events.subscribe(
            EventType.BATTERY_UPDATED,
            received.append,
            device=dog,
        )
        drone.events.publish(
            EventType.BATTERY_UPDATED,
            EventCategory.TELEMETRY,
            BatteryState(remaining_fraction=0.3),
        )
        dog.events.publish(
            EventType.BATTERY_UPDATED,
            EventCategory.TELEMETRY,
            BatteryState(remaining_fraction=0.7),
        )
        self.assertTrue(fleet.events.drain(timeout_s=2.0))
        unsubscribe()

        self.assertEqual([event.source.device_id for event in received], ["dog-001"])
        fleet.close()

    def test_plan_fleet_runs_common_steps_for_all_devices(self) -> None:
        drone1, client1 = self.make_drone("drone-001", "Drone1")
        drone2, client2 = self.make_drone("drone-002", "Drone2")
        fleet = Fleet([drone1, drone2])

        connect_result = fleet.connect()
        task = Plan(fleet)
        self.assertIsInstance(task, DroneFleetPlan)
        result = task.get_status().run()

        self.assertIsInstance(connect_result, FleetOperationResult)
        self.assertTrue(connect_result.ok)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, TaskStatus.SUCCESS)
        self.assertEqual(result.succeeded_devices, ("drone-001", "drone-002"))
        self.assertIn(("connect",), client1.calls)
        self.assertIn(("connect",), client2.calls)
        fleet.close()

    def test_plan_entry_accepts_interceptors_for_filtered_and_mixed_fleet(self) -> None:
        drone, _ = self.make_drone("drone-001", "Drone1")
        dog = RobotDog(
            ProductType.UNITREE_GO2,
            params={"network_interface": "test0"},
            metadata=DeviceMetadata(device_id="dog-001", name="Dog1"),
        )
        fleet = Fleet([drone, dog])

        self.assertIsInstance(Plan(fleet.drones(), interceptors=[lambda event: None]), DroneFleetPlan)
        self.assertIsInstance(Plan(fleet.robot_dogs(), interceptors=[lambda event: None]), RobotDogFleetPlan)
        self.assertIsInstance(Plan(fleet, interceptors=[lambda event: None]), FleetPlan)
        fleet.close()

    def test_mixed_fleet_plan_scopes_share_root_queue(self) -> None:
        drone, client = self.make_drone("drone-001", "Drone1")
        dog = RobotDog(
            ProductType.UNITREE_GO2,
            params={"network_interface": "test0"},
            metadata=DeviceMetadata(device_id="dog-001", name="Dog1"),
        )
        fleet = Fleet([drone, dog])
        self.assertTrue(fleet.connect().ok)
        root_plan = Plan(fleet)
        drone_plan = root_plan.for_drones()
        dog_plan = drone_plan.unlock().root().for_robot_dogs()

        self.assertIsInstance(drone_plan, DroneFleetPlan)
        self.assertIsInstance(dog_plan, RobotDogFleetPlan)
        self.assertIs(drone_plan.root(), root_plan)
        self.assertIs(dog_plan.root(), root_plan)

        result = dog_plan.stand().root().run()

        self.assertTrue(result.ok)
        self.assertEqual([step.name for step in result.steps], ["unlock", "stand"])
        self.assertIn(("arm",), client.calls)
        self.assertNotIn("dog-001", result.device_results["drone-001"].message)
        self.assertTrue(result.device_results["dog-001"].ok)
        self.assertEqual(dog.get_status().data["pose"], "stand")
        fleet.close()

    def test_plan_fleet_interceptor_receives_fleet_and_device_steps(self) -> None:
        drone1, _ = self.make_drone("drone-001", "Drone1")
        drone2, _ = self.make_drone("drone-002", "Drone2")
        fleet = Fleet([drone1, drone2])
        self.assertTrue(fleet.connect().ok)
        events = []

        def interceptor(event):
            events.append((event.scope, event.device_id, event.phase, event.step_name))

        result = Plan(fleet, interceptors=[interceptor]).get_status().run()

        self.assertTrue(result.ok)
        self.assertEqual(events[0], ("fleet", None, TaskStepHookPhase.BEFORE_STEP, "get_status"))
        self.assertEqual(events[-1], ("fleet", None, TaskStepHookPhase.AFTER_STEP, "get_status"))
        device_events = [event for event in events if event[0] == "device"]
        self.assertEqual(
            {event[1] for event in device_events if event[2] == TaskStepHookPhase.BEFORE_STEP},
            {"drone-001", "drone-002"},
        )
        fleet.close()

    def test_plan_filtered_fleet_runs_drone_steps(self) -> None:
        drone1, client1 = self.make_drone("drone-001", "Drone1")
        drone2, client2 = self.make_drone("drone-002", "Drone2")
        fleet = Fleet([drone1, drone2])

        self.assertTrue(fleet.connect().ok)
        result = (
            Plan(fleet.drones())
            .require_confirmation(False)
            .unlock()
            .takeoff(altitude=12.0)
            .return_to_launch()
            .run()
        )

        self.assertTrue(result.ok)
        self.assertIn(("takeoff", 12.0), client1.calls)
        self.assertIn(("takeoff", 12.0), client2.calls)
        fleet.close()

    def test_drone_fleet_takeoff_readiness_accepts_indoor_members_without_home(self) -> None:
        drone1, _ = self.make_drone("drone-001", "Drone1", endpoint="serial://COM3:57600")
        drone2, _ = self.make_drone("drone-002", "Drone2", endpoint="serial://COM4:57600")
        fleet = Fleet([drone1, drone2])
        self.assertTrue(fleet.connect().ok)
        for drone in (drone1, drone2):
            drone.state._update(
                health_local_position_ok=True,
                local_x_m=0.0,
                local_y_m=0.0,
                local_z_m=0.0,
                last_event_updated_at_s=time.monotonic(),
            )

        result = Plan(fleet.drones()).wait_until_ready(timeout_s=0.1).run(
            guard_interval_s=0.001
        )

        self.assertTrue(result.ok, result.message)
        self.assertEqual(set(result.device_results), {"drone-001", "drone-002"})
        fleet.close()

    def test_drone_fleet_ensure_land_preserves_cleanup_results(self) -> None:
        drone1, client1 = self.make_drone("drone-001", "Drone1", endpoint="serial://COM3:57600")
        drone2, client2 = self.make_drone("drone-002", "Drone2", endpoint="serial://COM4:57600")
        fleet = Fleet([drone1, drone2])
        self.assertTrue(fleet.connect().ok)
        for drone in (drone1, drone2):
            drone.state._update(armed=False, in_air=False)

        result = (
            Plan(fleet.drones())
            .when(lambda _ctx: True, reason="abort fleet route")
            .ensure_land(timeout_s=1.0)
            .run(guard_interval_s=0.001)
        )

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.cleanup_result)
        assert result.cleanup_result is not None
        self.assertTrue(result.cleanup_result.ok)
        self.assertIn(("land",), client1.calls)
        self.assertIn(("land",), client2.calls)
        fleet.close()

    def test_drone_fleet_unlock_can_force_arm_members(self) -> None:
        drone1, client1 = self.make_drone("drone-001", "Drone1")
        drone2, client2 = self.make_drone("drone-002", "Drone2")
        fleet = Fleet([drone1, drone2])

        self.assertTrue(fleet.connect().ok)
        result = fleet.drones().unlock(force=True)

        self.assertTrue(result.ok)
        self.assertEqual(client1.calls, [("connect",), ("arm", True)])
        self.assertEqual(client2.calls, [("connect",), ("arm", True)])
        self.assertTrue(result.device_results["drone-001"].data["force"])
        fleet.close()

    def test_follow_defaults_to_all_other_fleet_members(self) -> None:
        leader, _ = self.make_drone("drone-001", "Leader")
        follower1, _ = self.make_drone("drone-002", "Follower1")
        follower2, _ = self.make_drone("drone-003", "Follower2")
        fleet = Fleet([leader, follower1, follower2])
        policy = FollowPolicy.relative(x_m=-3.0, y_m=2.0, z_m=0.0, max_speed_m_s=4.0)

        session = fleet.follow(leader=leader, policy=policy)

        self.assertIsInstance(session, FollowSession)
        self.assertEqual(session.follower_ids, ("drone-002", "drone-003"))
        self.assertEqual(session.policy_for(follower1), policy)
        self.assertEqual(fleet.follow_sessions, (session,))
        fleet.close()

    def test_follow_policies_expose_modes_and_are_stored_in_session(self) -> None:
        leader, _ = self.make_drone("drone-001", "Leader")
        follower1, _ = self.make_drone("drone-002", "Follower1")
        follower2, _ = self.make_drone("drone-003", "Follower2")
        fleet = Fleet([leader, follower1, follower2])
        camera_policy = FollowPolicy.camera(distance_m=15.0, side_m=4.0)
        trail_policy = FollowPolicy.trail(delay_s=2.5, distance_m=9.0)

        session = fleet.follow(leader=leader, followers=[follower1], policy=camera_policy)
        session.add(follower2, policy=trail_policy)

        self.assertEqual(FollowPolicy.relative().mode, FollowMode.RELATIVE)
        self.assertEqual(FollowPolicy.chase().mode, FollowMode.CHASE)
        self.assertEqual(FollowPolicy.trail().mode, FollowMode.TRAIL)
        self.assertEqual(FollowPolicy.camera().mode, FollowMode.CAMERA)
        self.assertEqual(FollowPolicy.band().mode, FollowMode.BAND)
        self.assertEqual(session.policy_for(follower1), camera_policy)
        self.assertEqual(session.policy_for(follower2), trail_policy)
        fleet.close()

    def test_follow_policy_factory_creates_typed_policies(self) -> None:
        camera_policy = FollowPolicy.camera(distance_m=15.0, side_m=4.0)
        chase_policy = FollowPolicy.chase(distance_m=9.0)
        relative_policy = FollowPolicy.relative(x_m=-3.0, y_m=2.0)

        self.assertEqual(camera_policy.mode, FollowMode.CAMERA)
        self.assertEqual(chase_policy.mode, FollowMode.CHASE)
        self.assertEqual(relative_policy.mode, FollowMode.RELATIVE)
        self.assertEqual(camera_policy.distance_m, 15.0)
        self.assertEqual(chase_policy.distance_m, 9.0)
        self.assertEqual(relative_policy.x_m, -3.0)

    def test_follow_can_start_empty_session_and_add_later(self) -> None:
        leader, _ = self.make_drone("drone-001", "Leader")
        follower, _ = self.make_drone("drone-002", "Follower")
        fleet = Fleet([leader, follower])

        session = fleet.follow(leader=leader, followers=[])
        session.add(follower, policy=FollowPolicy.chase(distance_m=8.0))

        self.assertEqual(session.follower_ids, ("drone-002",))
        self.assertEqual(session.policy_for(follower).mode, FollowMode.CHASE)
        fleet.close()

    def test_follow_accepts_per_follower_policies_at_creation(self) -> None:
        leader, _ = self.make_drone("drone-001", "Leader")
        follower1, _ = self.make_drone("drone-002", "Follower1")
        follower2, _ = self.make_drone("drone-003", "Follower2")
        fleet = Fleet([leader, follower1, follower2])

        session = fleet.follow(
            leader=leader,
            follower_policies={
                follower1: FollowPolicy.chase(distance_m=8.0),
                follower2: FollowPolicy.camera(distance_m=12.0, side_m=3.0),
            },
        )

        self.assertEqual(session.follower_ids, ("drone-002", "drone-003"))
        self.assertEqual(session.policy_for(follower1).mode, FollowMode.CHASE)
        self.assertEqual(session.policy_for(follower2).mode, FollowMode.CAMERA)
        fleet.close()

    def test_fleet_avoidance_shares_obstacles_and_fleet_members_as_dynamic_obstacles(self) -> None:
        drone, _ = self.make_drone("drone-001", "Drone1")
        dog = RobotDog(
            ProductType.UNITREE_GO2,
            params={"network_interface": "test0"},
            metadata=DeviceMetadata(device_id="dog-001", name="Dog1"),
        )
        drone.state._update(local_x_m=0.0)
        drone.state._update(local_y_m=0.0)
        drone.state._update(local_z_m=5.0)
        dog.state._update(local_x_m=2.0)
        dog.state._update(local_y_m=0.0)
        dog.state._update(local_z_m=5.0)
        provider = FleetStaticObstacleProvider(
            Obstacle(
                obstacle_id="shared-wall",
                kind=ObstacleKind.STATIC,
                position_m=Vector3(4.0, 0.0, 5.0),
                radius_m=0.5,
                observed_at_s=time.monotonic(),
                ttl_s=2.0,
            )
        )
        fleet = Fleet([drone, dog])

        fleet.avoidance.add_provider(provider).configure(assume_shared_local_origin=True)
        snapshot = fleet.avoidance.snapshot()
        drone_snapshot = drone.avoidance.snapshot()
        decision = drone.avoidance.evaluate_segment(
            start_m=Vector3(0.0, 0.0, 5.0),
            target_m=Vector3(5.0, 0.0, 5.0),
            speed_m_s=1.0,
        )
        fleet.close()

        self.assertIn("shared-wall", {obstacle.obstacle_id for obstacle in snapshot.obstacles})
        self.assertIn("fleet.device.dog-001", {obstacle.obstacle_id for obstacle in drone_snapshot.obstacles})
        self.assertNotIn("fleet.device.drone-001", {obstacle.obstacle_id for obstacle in drone_snapshot.obstacles})
        self.assertEqual(decision.action.value, "hover")
        self.assertEqual(provider.close_calls, 1)

    def test_follow_layout_compiles_to_relative_policies(self) -> None:
        leader, _ = self.make_drone("drone-001", "Leader")
        follower1, _ = self.make_drone("drone-002", "Follower1")
        follower2, _ = self.make_drone("drone-003", "Follower2")
        fleet = Fleet([leader, follower1, follower2])

        session = fleet.follow(
            leader=leader,
            followers=[follower1, follower2],
            layout=FormationLayout.v_shape(spacing_m=8.0, count=2, altitude_step_m=1.0),
        )

        self.assertEqual(session.layout.name, "v_shape")
        self.assertEqual(session.policy_for(follower1).mode, FollowMode.RELATIVE)
        self.assertEqual(session.policy_for(follower1).x_m, -8.0)
        self.assertEqual(session.policy_for(follower1).y_m, -8.0)
        self.assertEqual(session.policy_for(follower1).z_m, 0.0)
        self.assertEqual(session.policy_for(follower2).z_m, 1.0)
        self.assertEqual(session.assignments[0].slot_id, "left-1")
        self.assertEqual(session.assignments[0].device_id, "drone-002")
        fleet.close()

    def test_follow_layout_rejects_policy_sources_that_conflict(self) -> None:
        leader, _ = self.make_drone("drone-001", "Leader")
        follower, _ = self.make_drone("drone-002", "Follower")
        fleet = Fleet([leader, follower])

        with self.assertRaises(ValueError):
            fleet.follow(
                leader=leader,
                followers=[follower],
                layout=FormationLayout.line(count=1),
                policy=FollowPolicy.chase(distance_m=8.0),
            )

        fleet.close()

    def test_follow_session_can_transition_layout(self) -> None:
        leader, _ = self.make_drone("drone-001", "Leader")
        follower1, _ = self.make_drone("drone-002", "Follower1")
        follower2, _ = self.make_drone("drone-003", "Follower2")
        fleet = Fleet([leader, follower1, follower2])
        session = fleet.follow(
            leader=leader,
            followers=[follower1, follower2],
            layout=FormationLayout.v_shape(spacing_m=8.0, count=2),
        )

        session.transition_to(FormationLayout.line(spacing_m=10.0, count=2))

        self.assertEqual(session.layout.name, "line")
        self.assertEqual(session.policy_for(follower1).x_m, -8.0)
        self.assertEqual(session.policy_for(follower1).y_m, -5.0)
        self.assertEqual(session.policy_for(follower2).y_m, 5.0)
        fleet.close()

    def test_follow_session_assign_rejects_occupied_slot(self) -> None:
        leader, _ = self.make_drone("drone-001", "Leader")
        follower1, _ = self.make_drone("drone-002", "Follower1")
        follower2, _ = self.make_drone("drone-003", "Follower2")
        fleet = Fleet([leader, follower1, follower2])
        session = fleet.follow(
            leader=leader,
            followers=[follower1, follower2],
            layout=FormationLayout.line(spacing_m=8.0, count=2),
        )

        with self.assertRaises(ValueError):
            session.assign(follower2, "line-1")

        fleet.close()

    def test_follow_session_add_failure_does_not_reserve_layout_slot(self) -> None:
        leader, _ = self.make_drone("drone-001", "Leader")
        follower, _ = self.make_drone("drone-002", "Follower")
        outsider, _ = self.make_drone("drone-999", "Outsider")
        fleet = Fleet([leader, follower])
        session = fleet.follow(
            leader=leader,
            followers=[],
            layout=FormationLayout.line(spacing_m=8.0, count=1),
        )

        with self.assertRaises(ValueError):
            session.add(outsider)

        self.assertEqual(len(session.assignments), 1)
        self.assertEqual(session.assignments[0].slot_id, "line-1")
        self.assertIsNone(session.assignments[0].device_id)
        self.assertIs(session.assignments[0].state, FormationSlotState.VACANT)

        session.add(follower)

        self.assertEqual(session.assignments[0].device_id, "drone-002")
        self.assertIs(session.assignments[0].state, FormationSlotState.ASSIGNED)
        fleet.close()

    def test_follow_session_start_runs_executor_and_reports_status(self) -> None:
        leader, _ = self.make_drone("drone-001", "Leader")
        follower, client = self.make_drone("drone-002", "Follower")
        leader.state._update(local_x_m=20.0)
        leader.state._update(local_y_m=0.0)
        follower.state._update(local_x_m=0.0)
        follower.state._update(local_y_m=0.0)
        fleet = Fleet([leader, follower])
        self.assertTrue(fleet.connect().ok)
        session = fleet.follow(
            leader=leader,
            followers=[follower],
            policy=FollowPolicy.chase(distance_m=10.0, max_speed_m_s=3.0),
        )

        session.start(FollowExecutionOptions(interval_s=0.01, stop_action="hold_velocity_zero"))
        time.sleep(0.05)
        status = session.status()
        session.stop()

        move_calls = [call for call in client.calls if call[0] == "set_velocity"]
        self.assertTrue(move_calls)
        self.assertEqual(status.state, FollowSessionState.RUNNING)
        self.assertEqual(status.followers["drone-002"].mode, FollowMode.CHASE)
        self.assertEqual(fleet.follow_sessions, ())
        fleet.close()

    def test_follow_start_rejects_different_unidentified_local_origins(self) -> None:
        leader = Drone.from_managed_object(
            ProductType.PX4_GENERIC,
            FakeFleetDroneClient(),
            metadata=DeviceMetadata(device_id="leader"),
        )
        follower = Drone.from_managed_object(
            ProductType.PX4_GENERIC,
            FakeFleetDroneClient(),
            metadata=DeviceMetadata(device_id="follower"),
        )
        for device in (leader, follower):
            device.state._update(
                local_x_m=0.0,
                local_y_m=0.0,
                local_position_frame=CoordinateFrame.LOCAL_NED,
            )
        fleet = Fleet([leader, follower])
        session = fleet.follow(leader=leader, followers=[follower])

        with self.assertRaisesRegex(RuntimeError, "local_frame_id"):
            session.start(FollowExecutionOptions(stop_action="none"))

        fleet.close()

    def test_follow_uses_global_common_frame_when_local_origins_differ(self) -> None:
        leader_client = FakeFleetDroneClient()
        follower_client = FakeFleetDroneClient()
        leader = Drone.from_managed_object(
            ProductType.PX4_GENERIC,
            leader_client,
            metadata=DeviceMetadata(device_id="leader", local_frame_id="leader-ekf"),
        )
        follower = Drone.from_managed_object(
            ProductType.PX4_GENERIC,
            follower_client,
            metadata=DeviceMetadata(device_id="follower", local_frame_id="follower-ekf"),
        )
        updated_at_s = time.monotonic()
        leader.state._update(
            latitude_deg=30.0001,
            longitude_deg=120.0,
            absolute_altitude_m=20.0,
            global_position_updated_at_s=updated_at_s,
            local_x_m=100.0,
            local_y_m=50.0,
            local_z_m=20.0,
            local_position_updated_at_s=updated_at_s,
            local_position_frame=CoordinateFrame.LOCAL_NED,
        )
        follower.state._update(
            latitude_deg=30.0,
            longitude_deg=120.0,
            absolute_altitude_m=20.0,
            global_position_updated_at_s=updated_at_s,
            local_x_m=-40.0,
            local_y_m=10.0,
            local_z_m=20.0,
            local_position_updated_at_s=updated_at_s,
            local_position_frame=CoordinateFrame.LOCAL_NED,
        )
        fleet = Fleet([leader, follower])
        self.assertTrue(fleet.connect().ok)
        session = fleet.follow(
            leader=leader,
            followers=[follower],
            policy=FollowPolicy.chase(distance_m=5.0),
        )

        session.start(FollowExecutionOptions(interval_s=0.01, stop_action="none"))
        time.sleep(0.04)
        status = session.status()
        session.stop()

        self.assertEqual(status.state, FollowSessionState.RUNNING)
        self.assertTrue(
            [call for call in follower_client.calls if call[0] == "set_velocity"],
            status,
        )
        fleet.close()

    def test_follow_session_can_update_policy_while_running(self) -> None:
        leader, _ = self.make_drone("drone-001", "Leader")
        follower, _ = self.make_drone("drone-002", "Follower")
        leader.state._update(local_x_m=20.0)
        leader.state._update(local_y_m=0.0)
        follower.state._update(local_x_m=0.0)
        follower.state._update(local_y_m=0.0)
        fleet = Fleet([leader, follower])
        self.assertTrue(fleet.connect().ok)
        session = fleet.follow(leader=leader, followers=[follower], policy=FollowPolicy.chase(distance_m=10.0))

        session.start(FollowExecutionOptions(interval_s=0.01, stop_action="none"))
        session.set_policy(follower, FollowPolicy.camera(distance_m=12.0, side_m=3.0))
        time.sleep(0.03)
        status = session.status()
        session.stop()

        self.assertEqual(session.is_active, False)
        self.assertEqual(status.followers["drone-002"].mode, FollowMode.CAMERA)
        fleet.close()

    def test_follow_session_safety_policy_triggers_return_without_velocity_command(self) -> None:
        leader, _ = self.make_drone("drone-001", "Leader")
        follower, client = self.make_drone("drone-002", "Follower")
        leader.state._update(local_x_m=20.0)
        leader.state._update(local_y_m=0.0)
        follower.state._update(local_x_m=0.0)
        follower.state._update(local_y_m=0.0)
        follower.state._update(battery_remaining_fraction=0.15)
        fleet = Fleet([leader, follower])
        actions = []
        unsubscribe = follower.events.on(SafetyActionTriggeredEvent, actions.append)
        self.assertTrue(fleet.connect().ok)
        session = fleet.follow(
            leader=leader,
            followers=[follower],
            policy=FollowPolicy.chase(distance_m=10.0),
            safety_policy=SafetyPolicy(
                battery_return_fraction=0.2,
                battery_emergency_fraction=0.05,
                min_separation_m=1.0,
            ),
        )

        session.start(interval_s=0.01, stop_action="none")
        time.sleep(0.04)
        follower.events.drain(timeout_s=1.0)
        session.stop()
        unsubscribe()

        self.assertIn(("return_to_launch",), client.calls)
        self.assertFalse([call for call in client.calls if call[0] == "set_velocity"])
        self.assertTrue(actions)
        self.assertEqual(actions[0].action.action, SafetyAction.RETURN_TO_LAUNCH.value)
        fleet.close()

    def test_follow_session_supports_dynamic_add_remove_and_stop(self) -> None:
        leader, _ = self.make_drone("drone-001", "Leader")
        follower1, _ = self.make_drone("drone-002", "Follower1")
        follower2, _ = self.make_drone("drone-003", "Follower2")
        fleet = Fleet([leader, follower1, follower2])

        session = fleet.follow(leader=leader, followers=[follower1])
        session.add(follower2, policy=FollowPolicy.relative(y_m=3.0))
        session.remove(follower1)

        self.assertEqual(session.follower_ids, ("drone-003",))
        self.assertEqual(session.policy_for(follower2).y_m, 3.0)
        fleet.unfollow(session.id)
        self.assertFalse(session.is_active)
        self.assertEqual(fleet.follow_sessions, ())
        fleet.close()

    def test_follow_accepts_single_follower_and_string_conflict(self) -> None:
        leader1, _ = self.make_drone("drone-001", "Leader1")
        leader2, _ = self.make_drone("drone-002", "Leader2")
        follower, _ = self.make_drone("drone-003", "Follower")
        fleet = Fleet([leader1, leader2, follower])
        session1 = fleet.follow(leader=leader1, followers=follower)

        session2 = fleet.follow(leader=leader2, followers=follower, conflict="replace")

        self.assertEqual(session1.followers, ())
        self.assertEqual(session2.followers, (follower,))
        fleet.close()

    def test_follow_conflict_fails_by_default_and_can_replace_explicitly(self) -> None:
        leader1, _ = self.make_drone("drone-001", "Leader1")
        leader2, _ = self.make_drone("drone-002", "Leader2")
        follower, _ = self.make_drone("drone-003", "Follower")
        fleet = Fleet([leader1, leader2, follower])
        session1 = fleet.follow(leader=leader1, followers=[follower])

        with self.assertRaises(ValueError):
            fleet.follow(leader=leader2, followers=[follower])

        session2 = fleet.follow(
            leader=leader2,
            followers=[follower],
            conflict=FollowConflict.REPLACE,
        )

        self.assertEqual(session1.followers, ())
        self.assertEqual(session2.followers, (follower,))
        fleet.close()

    def test_follow_rejects_leader_follower_chains(self) -> None:
        drone1, _ = self.make_drone("drone-001", "Drone1")
        drone2, _ = self.make_drone("drone-002", "Drone2")
        drone3, _ = self.make_drone("drone-003", "Drone3")
        fleet = Fleet([drone1, drone2, drone3])
        fleet.follow(leader=drone1, followers=[drone2])

        with self.assertRaises(ValueError):
            fleet.follow(leader=drone2, followers=[drone3])

        fleet.close()

    def test_robot_dog_fleet_exposes_stand_instead_of_unlock(self) -> None:
        drone, client = self.make_drone("drone-001", "Drone1")
        dog = RobotDog(
            ProductType.UNITREE_GO2,
            params={"network_interface": "test0"},
            metadata=DeviceMetadata(device_id="dog-001", name="Dog1"),
        )
        fleet = Fleet([drone, dog])

        self.assertTrue(fleet.connect().ok)
        dog_fleet = fleet.robot_dogs()

        self.assertFalse(hasattr(dog_fleet, "unlock"))
        self.assertTrue(hasattr(dog_fleet, "stand"))
        self.assertEqual(client.calls, [("connect",)])
        fleet.close()

    def test_parallel_plan_runs_child_plans(self) -> None:
        drone1, client1 = self.make_drone("drone-001", "Drone1")
        drone2, client2 = self.make_drone("drone-002", "Drone2")
        fleet = Fleet([drone1, drone2])
        self.assertTrue(fleet.connect().ok)
        result = Plan.parallel(
            Plan(fleet.by_id("drone-001")).require_confirmation(False).get_status(),
            Plan(fleet.by_id("drone-002")).require_confirmation(False).get_status(),
        ).run(step_timeout_s=0.05, guard_interval_s=0.001)

        self.assertIsInstance(result, ParallelTaskResult)
        self.assertTrue(result.ok)
        self.assertIn(("connect",), client1.calls)
        self.assertIn(("get_status",), client2.calls)
        fleet.close()

    def test_parallel_plan_overlaps_child_plan_execution(self) -> None:
        barrier = Barrier(2)
        client1 = ConcurrentFleetDroneClient(barrier)
        client2 = ConcurrentFleetDroneClient(barrier)
        drone1 = Drone.from_managed_object(
            ProductType.PX4_GENERIC,
            client1,
            metadata=DeviceMetadata(device_id="drone-001", name="Drone1"),
        )
        drone2 = Drone.from_managed_object(
            ProductType.PX4_GENERIC,
            client2,
            metadata=DeviceMetadata(device_id="drone-002", name="Drone2"),
        )
        fleet = Fleet([drone1, drone2])
        self.assertTrue(fleet.connect().ok)

        result = Plan.parallel(
            Plan(fleet.by_id("drone-001")).require_confirmation(False).get_status(),
            Plan(fleet.by_id("drone-002")).require_confirmation(False).get_status(),
        ).run()

        self.assertTrue(result.ok)
        self.assertIn(("get_status_released",), client1.calls)
        self.assertIn(("get_status_released",), client2.calls)
        fleet.close()


if __name__ == "__main__":
    unittest.main()
