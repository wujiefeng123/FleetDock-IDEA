import unittest

from fleetdock import (
    AdapterNotFoundError,
    DeviceMetadata,
    Drone,
    ErrorCode,
    Fleet,
    FleetDockError,
    FirmwareVersion,
    ProductNotRegisteredError,
    ProductType,
    default_registry,
)


class _ManagedClient:
    def connect(self) -> None:
        return None

    def close(self) -> None:
        return None


class PublicErrorModelTest(unittest.TestCase):
    def test_registry_uses_public_error_hierarchy(self) -> None:
        with self.assertRaises(ProductNotRegisteredError) as missing_product:
            default_registry.get("missing.product")
        self.assertIsInstance(missing_product.exception, FleetDockError)
        self.assertEqual(missing_product.exception.code, ErrorCode.PRODUCT_NOT_REGISTERED)

        with self.assertRaises(AdapterNotFoundError) as missing_adapter:
            default_registry.get_adapter(ProductType.OTHER, FirmwareVersion.CUSTOM)
        self.assertEqual(missing_adapter.exception.code, ErrorCode.ADAPTER_NOT_FOUND)

    def test_fleet_aggregates_unexpected_device_exception(self) -> None:
        drone = Drone.from_managed_object(
            ProductType.PX4_GENERIC,
            _ManagedClient(),
            params={"endpoint": "test"},
            metadata=DeviceMetadata(device_id="drone-error"),
        )

        def fail_connect():
            raise RuntimeError("unexpected failure")

        drone.connect = fail_connect  # type: ignore[method-assign]
        result = Fleet([drone]).connect()

        self.assertFalse(result.ok)
        self.assertEqual(result.failed_devices, ("drone-error",))
        self.assertEqual(result.device_results["drone-error"].code, ErrorCode.UNKNOWN_ERROR)
        self.assertEqual(result.device_results["drone-error"].data["exception_type"], "RuntimeError")
        drone.close()


if __name__ == "__main__":
    unittest.main()
