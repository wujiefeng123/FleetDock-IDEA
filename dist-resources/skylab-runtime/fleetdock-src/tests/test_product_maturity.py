import unittest

from fleetdock import IntegrationMaturity, ProductType, __version__, default_registry


class ProductMaturityTests(unittest.TestCase):
    def test_builtin_products_expose_truthful_maturity(self) -> None:
        self.assertEqual(default_registry.get(ProductType.MOCK_DRONE).maturity, IntegrationMaturity.SIMULATED)
        self.assertEqual(default_registry.get(ProductType.MOCK_ROBOT_DOG).maturity, IntegrationMaturity.SIMULATED)
        self.assertEqual(default_registry.get(ProductType.PX4_GENERIC).maturity, IntegrationMaturity.BETA)
        self.assertEqual(default_registry.get(ProductType.UNITREE_GO2).maturity, IntegrationMaturity.SIMULATED)
        self.assertEqual(default_registry.get(ProductType.DJI_MATRICE_350_RTK).maturity, IntegrationMaturity.PLANNED)

    def test_package_version_matches_release(self) -> None:
        self.assertEqual(__version__, "0.4.0")


if __name__ == "__main__":
    unittest.main()
