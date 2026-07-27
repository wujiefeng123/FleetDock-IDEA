import unittest

from fleetdock import DeviceFactory, DeviceLifecycleState, Drone, ErrorCode, ProductType


class ManagedClient:
    def __init__(self) -> None:
        self.closed = False

    def connect(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class DeviceLifecycleTests(unittest.TestCase):
    def test_close_is_idempotent_and_rejects_future_commands(self) -> None:
        device = DeviceFactory.create().unitree_go2().params(network_interface="mock0").build()
        self.assertEqual(device.lifecycle_state, DeviceLifecycleState.CREATED)
        self.assertTrue(device.connect().ok)
        self.assertEqual(device.lifecycle_state, DeviceLifecycleState.CONNECTED)
        diagnostic = device.diagnostics()
        self.assertTrue(diagnostic.connected)
        self.assertEqual(diagnostic.lifecycle_state, "connected")
        self.assertEqual(diagnostic.maturity.value, "simulated")

        device.close()
        device.close()

        self.assertEqual(device.lifecycle_state, DeviceLifecycleState.CLOSED)
        response = device.get_status()
        self.assertFalse(response.ok)
        self.assertEqual(response.code, ErrorCode.DEVICE_STATE_ERROR)
        self.assertEqual(response.data["lifecycle_state"], "closed")

    def test_managed_object_remains_caller_owned_by_default(self) -> None:
        client = ManagedClient()
        device = Drone.from_managed_object(ProductType.PX4_GENERIC, client)

        device.close()

        self.assertFalse(client.closed)

    def test_managed_object_can_transfer_ownership_to_device(self) -> None:
        client = ManagedClient()
        with Drone.from_managed_object(
            ProductType.PX4_GENERIC,
            client,
            close_managed_object=True,
        ) as device:
            self.assertIs(device.native_client, client)

        self.assertTrue(client.closed)
        self.assertEqual(device.lifecycle_state, DeviceLifecycleState.CLOSED)

    def test_context_manager_does_not_implicitly_connect(self) -> None:
        with DeviceFactory.create().unitree_go2().params(network_interface="mock0").build() as device:
            self.assertEqual(device.lifecycle_state, DeviceLifecycleState.CREATED)

        self.assertEqual(device.lifecycle_state, DeviceLifecycleState.CLOSED)


if __name__ == "__main__":
    unittest.main()
