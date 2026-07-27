from __future__ import annotations

from pathlib import Path
import tomllib
import unittest


class DependencyExtrasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        with pyproject_path.open("rb") as file:
            cls.extras = tomllib.load(file)["project"]["optional-dependencies"]

    def test_full_prod_contains_all_runtime_dependency_groups(self) -> None:
        runtime_groups = ("px4", "dji-cloud", "projectairsim-rtsp", "vision")
        expected = {dependency for group in runtime_groups for dependency in self.extras[group]}

        self.assertEqual(set(self.extras["full-prod"]), expected)

    def test_unimplemented_unitree_integration_remains_opt_in(self) -> None:
        unitree_dependencies = set(self.extras["unitree"])

        self.assertTrue(unitree_dependencies)
        self.assertTrue(unitree_dependencies.isdisjoint(self.extras["integrations"]))
        self.assertTrue(unitree_dependencies.isdisjoint(self.extras["full-prod"]))
        self.assertTrue(unitree_dependencies.isdisjoint(self.extras["full-dev"]))

    def test_full_dev_contains_full_prod_and_dev_dependencies(self) -> None:
        expected = set(self.extras["full-prod"]) | set(self.extras["dev"])

        self.assertEqual(set(self.extras["full-dev"]), expected)


if __name__ == "__main__":
    unittest.main()
