import unittest

from fleetdock import DeviceFactory, EventHub, MessageEventType


class LoggingTest(unittest.TestCase):
    def test_device_command_emits_debug_log(self) -> None:
        dog = DeviceFactory.create().unitree_go2().params(network_interface="eth0").build()

        with self.assertLogs("fleetdock", level="DEBUG") as captured:
            response = dog.connect()

        self.assertTrue(response.ok)
        self.assertTrue(any("设备命令开始" in message for message in captured.output))
        self.assertTrue(any("适配器命令完成" in message for message in captured.output))

    def test_event_publish_emits_debug_log(self) -> None:
        hub = EventHub()

        with self.assertLogs("fleetdock.message_bus", level="DEBUG") as captured:
            hub.publish(MessageEventType.CUSTOM, "logging.test")

        self.assertTrue(any("事件发布" in message for message in captured.output))


if __name__ == "__main__":
    unittest.main()
