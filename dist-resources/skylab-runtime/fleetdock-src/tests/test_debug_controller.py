import importlib.util
import threading
import time
import unittest
from unittest.mock import patch

from fleetdock import CapabilityName, DeviceDebug, DeviceFactory, ImageEncoding, ImageFrame, SensorHub, SensorKind
from fleetdock.debug_controller import (
    _Action,
    _ControllerSnapshot,
    _DebugController,
    _MotionInput,
    _OpenCvControllerDisplay,
)


class FakeCv2:
    WINDOW_NORMAL = 0
    EVENT_LBUTTONDOWN = 1
    EVENT_LBUTTONUP = 4
    FONT_HERSHEY_SIMPLEX = 0
    LINE_AA = 0
    WND_PROP_VISIBLE = 0

    def __init__(self) -> None:
        self.keys: list[int] = []
        self.visible = 1.0
        self.opened = False
        self.destroyed = False

    def namedWindow(self, title, mode) -> None:
        self.opened = True

    def resizeWindow(self, title, width, height) -> None:
        self.size = (width, height)

    def setMouseCallback(self, title, callback) -> None:
        self.callback = callback

    def imshow(self, title, canvas) -> None:
        self.canvas_shape = canvas.shape

    def waitKey(self, delay_ms) -> int:
        return self.keys.pop(0) if self.keys else -1

    def getWindowProperty(self, title, property_name) -> float:
        return self.visible

    def destroyWindow(self, title) -> None:
        self.destroyed = True

    def rectangle(self, canvas, start, end, color, thickness) -> None:
        return None

    def getTextSize(self, text, font, scale, thickness):
        return ((max(1, len(text) * 5), 10), 0)

    def putText(self, canvas, text, point, font, scale, color, thickness, line_type) -> None:
        return None


class FakeControllerDisplay:
    def __init__(self, actions=()) -> None:
        self.actions = list(actions)
        self.opened = False
        self.closed = False
        self.snapshots = []
        self.poll_count = 0

    def open(self, title: str) -> None:
        self.opened = True
        self.title = title

    def draw(self, snapshot) -> None:
        self.snapshots.append(snapshot)

    def poll(self, delay_ms: int):
        self.poll_count += 1
        time.sleep(0.03)
        if self.actions:
            return (self.actions.pop(0),)
        return ()

    def should_close(self) -> bool:
        return self.poll_count >= 4

    def close(self) -> None:
        self.closed = True


class DebugControllerTests(unittest.TestCase):
    def ready_drone(self):
        drone = DeviceFactory.create().mock_drone().build()
        drone.connect()
        drone.unlock()
        drone.takeoff(5.0)
        deadline = time.monotonic() + 1.0
        while not drone.readiness_for(CapabilityName.MANUAL_CONTROL).ready:
            if time.monotonic() >= deadline:
                self.fail("mock drone did not become ready")
            time.sleep(0.005)
        return drone

    def test_controller_uses_public_manual_control_and_cleans_up(self) -> None:
        drone = self.ready_drone()
        display = FakeControllerDisplay(
            (
                _MotionInput(frozenset({_Action.FORWARD, _Action.YAW_LEFT})),
                _MotionInput(frozenset()),
                _Action.HOVER,
            )
        )
        drone.debug._controller_display_factory = lambda: display

        drone.debug.controller(camera=False)

        self.assertTrue(display.opened)
        self.assertTrue(display.closed)
        self.assertGreater(drone.adapter.position["x"], 0.0)
        self.assertNotEqual(drone.adapter.yaw_deg, 0.0)
        self.assertIsNone(drone._manual_control_session)
        self.assertTrue(any(snapshot.manual_active for snapshot in display.snapshots))
        self.assertTrue(any("运动：已停止" in snapshot.messages for snapshot in display.snapshots))
        drone.close()

    def test_device_close_stops_running_controller(self) -> None:
        drone = self.ready_drone()

        class WaitingDisplay(FakeControllerDisplay):
            def should_close(self) -> bool:
                return False

        display = WaitingDisplay()
        drone.debug._controller_display_factory = lambda: display
        worker = threading.Thread(target=lambda: drone.debug.controller(camera=False))
        worker.start()
        deadline = time.monotonic() + 1.0
        while not display.opened:
            if time.monotonic() >= deadline:
                self.fail("controller did not start")
            time.sleep(0.005)

        drone.close()
        worker.join(timeout=2.0)

        self.assertFalse(worker.is_alive())
        self.assertTrue(display.closed)

    def test_force_unlock_requires_confirmation_and_uses_public_sdk(self) -> None:
        drone = DeviceFactory.create().mock_drone().build()
        drone.connect()
        display = FakeControllerDisplay((_Action.FORCE_UNLOCK, _Action.FORCE_UNLOCK))
        drone.debug._controller_display_factory = lambda: display

        with patch.object(drone, "unlock", wraps=drone.unlock) as unlock:
            drone.debug.controller(camera=False)

        unlock.assert_called_once_with(force=True)
        self.assertTrue(
            any("仅限台架诊断" in message for snapshot in display.snapshots for message in snapshot.messages)
        )
        drone.close()

    def test_controller_explains_flight_controller_auto_disarm(self) -> None:
        drone = DeviceFactory.create().mock_drone().build()
        drone.connect()
        controller = _DebugController(drone, title="test", camera=False)
        drone.state._update(armed=False, in_air=False)
        controller._snapshot()
        response = drone.unlock(force=True)
        controller._append_response(_Action.FORCE_UNLOCK, response)
        drone.state._update(armed=True)
        controller._snapshot()

        drone.adapter.unlocked = False
        drone.state._update(armed=False)
        snapshot = controller._snapshot()

        self.assertTrue(any("飞控已自动上锁" in message for message in snapshot.messages))
        self.assertTrue(any("不能替代飞行预检" in message for message in snapshot.messages))
        controller.close()
        drone.close()

    def test_controller_horizontal_speed_defaults_to_three_meters_per_second(self) -> None:
        drone = DeviceFactory.create().mock_drone().build()
        controller = _DebugController(drone, title="test", camera=False)

        self.assertEqual(controller._snapshot().horizontal_speed_m_s, 3.0)
        controller.close()
        drone.close()

    def test_held_motion_input_is_not_refreshed_at_display_frame_rate(self) -> None:
        drone = self.ready_drone()
        controller = _DebugController(drone, title="test", camera=False, display_factory=FakeControllerDisplay)
        display = FakeControllerDisplay((_MotionInput(frozenset({_Action.FORWARD})),))
        controller._display_factory = lambda: display
        update_threads: list[str] = []
        original_update_motion = controller._update_motion

        def record_update_thread(actions) -> None:
            update_threads.append(threading.current_thread().name)
            original_update_motion(actions)

        with patch.object(controller, "_update_motion", side_effect=record_update_thread) as update_motion:
            controller.run()

        self.assertEqual(update_motion.call_count, 1)
        self.assertTrue(update_threads[0].startswith("fleetdock-debug-controller-"))
        drone.close()

    def test_keyboard_repeat_cannot_run_display_loop_without_frame_pacing(self) -> None:
        class ImmediateKeyboardRepeatDisplay(FakeControllerDisplay):
            def poll(self, delay_ms: int):
                self.poll_count += 1
                return ()

        drone = DeviceFactory.create().mock_drone().build()
        display = ImmediateKeyboardRepeatDisplay()
        controller = _DebugController(
            drone,
            title="test",
            camera=False,
            display_factory=lambda: display,
        )

        started_at_s = time.monotonic()
        controller.run()
        elapsed_s = time.monotonic() - started_at_s

        self.assertGreaterEqual(elapsed_s, 0.1)
        self.assertEqual(display.poll_count, 4)
        drone.close()

    def test_controller_reports_smoothed_camera_receive_fps(self) -> None:
        drone = DeviceFactory.create().mock_drone().build()
        controller = _DebugController(drone, title="test", camera=False)
        frame = ImageFrame(
            sensor_id="camera.front",
            kind=SensorKind.RGB_CAMERA,
            sequence=1,
            width=1,
            height=1,
            encoding=ImageEncoding.BGR8,
            data=memoryview(b"\x00\x00\x00"),
        )

        with patch("fleetdock.debug_controller.time.monotonic", side_effect=(10.0, 10.05)):
            controller._on_camera(frame)
            controller._on_camera(frame)

        self.assertAlmostEqual(controller._snapshot().camera_fps or 0.0, 20.0)
        controller.close()
        drone.close()

    def test_controller_accepts_standard_camera_subscription_and_cancels_it(self) -> None:
        drone = DeviceFactory.create().mock_drone().build()
        display = FakeControllerDisplay()
        frame = ImageFrame(
            sensor_id="camera.front",
            kind=SensorKind.RGB_CAMERA,
            sequence=1,
            width=1,
            height=1,
            encoding=ImageEncoding.BGR8,
            data=memoryview(b"\x00\x00\x00"),
        )
        cancelled: list[bool] = []

        def subscribe(handler):
            handler(frame)
            return lambda: cancelled.append(True)

        drone.debug._controller_display_factory = lambda: display
        drone.debug.controller(camera_subscribe=subscribe)

        self.assertTrue(any(snapshot.camera is frame for snapshot in display.snapshots))
        self.assertEqual(cancelled, [True])
        drone.close()

    def test_standalone_debug_entry_has_no_controller_authority(self) -> None:
        debug = DeviceDebug(SensorHub("standalone"), _controller_display_factory=FakeControllerDisplay)
        try:
            with self.assertRaisesRegex(RuntimeError, "标准设备"):
                debug.controller(camera=False)
        finally:
            debug.close()

    @unittest.skipUnless(
        importlib.util.find_spec("cv2") is not None
        and importlib.util.find_spec("numpy") is not None
        and importlib.util.find_spec("PIL") is not None,
        "未安装 vision 可选依赖",
    )
    def test_opencv_display_draws_and_maps_mouse_and_keyboard_actions(self) -> None:
        display = _OpenCvControllerDisplay()
        fake_cv2 = FakeCv2()
        display._cv2 = fake_cv2
        snapshot = _ControllerSnapshot(
            device_id="drone-1",
            product="px4.generic",
            connected=False,
            armed=None,
            in_air=None,
            flight_mode=None,
            failsafe=None,
            battery_percent=None,
            relative_altitude_m=None,
            roll_deg=None,
            pitch_deg=None,
            yaw_deg=None,
            telemetry_age_s=None,
            camera_fps=18.0,
            horizontal_speed_m_s=1.0,
            vertical_speed_m_s=0.6,
            yaw_rate_deg_s=30.0,
            takeoff_altitude_m=5.0,
            manual_active=False,
            camera=None,
            messages=("摄像头订阅已启动",),
            enabled=frozenset(
                {
                    _Action.CONNECT,
                    _Action.FORWARD,
                    _Action.YAW_LEFT,
                    _Action.FORCE_UNLOCK,
                    _Action.TAKEOFF,
                    _Action.LAND,
                    _Action.EMERGENCY_STOP,
                }
            ),
        )
        pressed_keys: set[int] = set()

        display.open("controller-test")
        display._keyboard_reader = lambda: frozenset(pressed_keys)
        display.draw(snapshot)
        display._on_mouse(fake_cv2.EVENT_LBUTTONDOWN, 732, 245, 0)
        display._on_mouse(fake_cv2.EVENT_LBUTTONDOWN, 930, 245, 0)
        display._on_mouse(fake_cv2.EVENT_LBUTTONDOWN, 840, 460, 0)
        display._on_mouse(fake_cv2.EVENT_LBUTTONUP, 840, 460, 0)

        self.assertEqual(
            display.poll(1),
            (
                _Action.CONNECT,
                _Action.FORCE_UNLOCK,
                _MotionInput(frozenset({_Action.FORWARD})),
                _MotionInput(frozenset()),
            ),
        )
        pressed_keys.add(ord("W"))
        self.assertEqual(display.poll(1), (_MotionInput(frozenset({_Action.FORWARD})),))
        pressed_keys.add(ord("Q"))
        self.assertEqual(
            display.poll(1),
            (_MotionInput(frozenset({_Action.FORWARD, _Action.YAW_LEFT})),),
        )
        pressed_keys.remove(ord("W"))
        self.assertEqual(display.poll(1), (_MotionInput(frozenset({_Action.YAW_LEFT})),))
        pressed_keys.clear()
        self.assertEqual(display.poll(1), (_MotionInput(frozenset()),))
        pressed_keys.add(0x20)
        self.assertEqual(display.poll(1), (_Action.TAKEOFF,))
        pressed_keys.clear()
        self.assertEqual(display.poll(1), ())
        pressed_keys.add(0x11)
        self.assertEqual(display.poll(1), (_Action.LAND,))
        pressed_keys.clear()
        self.assertEqual(display.poll(1), ())
        pressed_keys.add(ord("W"))
        self.assertEqual(display.poll(1), (_MotionInput(frozenset({_Action.FORWARD})),))
        display._keyboard_reader = lambda: None
        self.assertEqual(display.poll(1), (_MotionInput(frozenset()),))
        self.assertEqual(fake_cv2.canvas_shape, (700, 1100, 3))
        self.assertTrue(any("FPS 18.0" in text for text, *_rest in display._text_items))
        self.assertEqual(display._buttons[0].label, "连接")
        self.assertEqual(display._buttons[2].label, "台架强解锁")
        self.assertEqual(display._buttons[9].label, "紧急停止")
        self.assertFalse(display.should_close())
        display._keyboard_reader = lambda: frozenset({ord("X")})
        display._keyboard_armed = True
        display._keyboard_pressed = frozenset()
        self.assertEqual(display.poll(1), ())
        self.assertTrue(display.should_close())
        display.close()
        self.assertTrue(fake_cv2.opened)
        self.assertTrue(fake_cv2.destroyed)


if __name__ == "__main__":
    unittest.main()
