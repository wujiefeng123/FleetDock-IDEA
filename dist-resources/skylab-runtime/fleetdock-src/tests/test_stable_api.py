import unittest

import fleetdock.api as api


class StableApiTests(unittest.TestCase):
    def test_stable_facade_is_intentionally_small(self) -> None:
        self.assertLessEqual(len(api.__all__), 35)
        self.assertIn("DeviceFactory", api.__all__)
        self.assertIn("Plan", api.__all__)
        self.assertIn("RelativeMove2D", api.__all__)
        self.assertNotIn("Px4MavlinkAdapter", api.__all__)


if __name__ == "__main__":
    unittest.main()
