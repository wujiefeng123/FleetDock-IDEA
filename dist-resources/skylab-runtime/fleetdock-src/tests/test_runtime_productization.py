import threading
import time
import unittest

from fleetdock import (
    DeviceEventHandlerError,
    DeviceEventStream,
    EventType,
    EventHandlerFailedEvent,
    EventSource,
    FollowController,
    FollowExecutionOptions,
    FollowPolicy,
    HandlerErrorPolicy,
    ManagedWorker,
    NativeRouteCallbacks,
    NativeRouteRequest,
    Plan,
    Route,
    TaskResult,
    TaskStatus,
    Waypoint,
    WorkerState,
)


class ManagedWorkerTest(unittest.TestCase):
    def test_worker_can_stop_join_and_report_state(self) -> None:
        stopped = threading.Event()
        worker = ManagedWorker("test-worker", lambda: stopped.wait(), request_stop=stopped.set).start()

        self.assertTrue(worker.status().is_alive)
        self.assertTrue(worker.stop(timeout_s=1.0))
        self.assertEqual(worker.status().state, WorkerState.STOPPED)


class EventErrorPolicyTest(unittest.TestCase):
    def make_stream(self) -> DeviceEventStream:
        return DeviceEventStream(EventSource("drone-1", "mock.drone", "drone", "MockDroneAdapter"))

    def test_publish_policy_emits_typed_diagnostic_event(self) -> None:
        stream = self.make_stream()
        diagnostics = []
        delivered = threading.Event()

        def fail_once(event) -> None:
            if event.type is not EventType.HANDLER_FAILED:
                raise ValueError("broken callback")

        stream.subscribe(fail_once, error_policy=HandlerErrorPolicy.PUBLISH)
        stream.on(EventHandlerFailedEvent, lambda event: (diagnostics.append(event), delivered.set()))
        stream.publish_custom("custom.test.trigger")

        self.assertTrue(delivered.wait(1.0))
        self.assertEqual(diagnostics[0].failure.exception_type, "ValueError")
        self.assertEqual(stream.status().handler_error_count, 1)
        stream.close()

    def test_raise_on_drain_surfaces_handler_error_to_caller(self) -> None:
        stream = self.make_stream()
        stream.subscribe(
            lambda event: (_ for _ in ()).throw(RuntimeError("failed")),
            error_policy=HandlerErrorPolicy.RAISE_ON_DRAIN,
        )
        stream.publish_custom("custom.test.trigger")

        with self.assertRaises(DeviceEventHandlerError):
            stream.drain(timeout_s=1.0)
        stream.close()


class FollowControllerTest(unittest.TestCase):
    def test_controller_is_pure_and_enforces_speed_limit(self) -> None:
        controller = FollowController(FollowExecutionOptions(default_max_speed_m_s=2.0))
        command = controller.compute(
            (0.0, 0.0),
            (20.0, 0.0),
            FollowPolicy.chase(distance_m=5.0, max_speed_m_s=1.5),
        )

        self.assertEqual(command.reason, "chase")
        self.assertLessEqual(command.speed, 1.5)


class NativeRouteRequestTest(unittest.TestCase):
    def test_request_keeps_typed_route_and_callbacks(self) -> None:
        callback = lambda: None
        route = Route.polyline((Waypoint(x_m=0.0, y_m=0.0), Waypoint(x_m=5.0, y_m=0.0)))
        request = NativeRouteRequest(route=route, speed_m_s=2.0, callbacks=NativeRouteCallbacks(guard=callback))

        self.assertIs(request.route, route)
        self.assertIs(request.callbacks.guard, callback)


class ParallelPlanFailFastTest(unittest.TestCase):
    def test_fail_fast_cancels_remaining_plans_without_masking_failure(self) -> None:
        blocking = _BlockingPlan()
        failing = _FailingPlan(blocking.started)

        result = Plan.parallel(failing, blocking, failure_policy="fail_fast").run(guard_interval_s=0.001)

        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertTrue(blocking.safe_stopped.is_set())
        self.assertTrue(blocking.cancel_seen)
        self.assertEqual(result.plan_results[0].status, TaskStatus.FAILED)


class _FailingPlan:
    def __init__(self, wait_for: threading.Event) -> None:
        self._wait_for = wait_for

    def run(self, step_timeout_s=60.0, guard_interval_s=0.2, *, _cancellation_token=None) -> TaskResult:
        self._wait_for.wait(timeout=1.0)
        return TaskResult(False, TaskStatus.FAILED, "planned failure")

    def _safe_stop(self) -> None:
        return None


class _BlockingPlan:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.safe_stopped = threading.Event()
        self.cancel_seen = False

    def run(self, step_timeout_s=60.0, guard_interval_s=0.2, *, _cancellation_token=None) -> TaskResult:
        self.started.set()
        deadline_s = time.monotonic() + 1.0
        while time.monotonic() < deadline_s:
            if _cancellation_token is not None and _cancellation_token.cancelled:
                self.cancel_seen = True
                return TaskResult(False, TaskStatus.CANCELLED, "cancelled by fail_fast")
            time.sleep(0.001)
        return TaskResult(True, TaskStatus.SUCCESS, "completed")

    def _safe_stop(self) -> None:
        self.safe_stopped.set()


if __name__ == "__main__":
    unittest.main()
