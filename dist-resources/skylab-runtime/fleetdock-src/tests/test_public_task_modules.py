import unittest

import fleetdock
from fleetdock import device_plans, fleet_plans, plans, routes, task_models


class PublicTaskModulesTest(unittest.TestCase):
    def test_plan_modules_reexport_existing_public_types(self) -> None:
        self.assertIs(routes.Route, fleetdock.Route)
        self.assertIs(routes.Waypoint, fleetdock.Waypoint)
        self.assertIs(task_models.TaskResult, fleetdock.TaskResult)
        self.assertIs(task_models.RouteMotion, fleetdock.RouteMotion)
        self.assertIs(device_plans.DevicePlan, fleetdock.DevicePlan)
        self.assertIs(fleet_plans.FleetPlan, fleetdock.FleetPlan)
        self.assertIs(plans.Plan, fleetdock.Plan)

    def test_standard_event_type_aliases_are_public(self) -> None:
        self.assertIn("StandardEventType", fleetdock.__all__)
        self.assertIn("DeviceEventType", fleetdock.__all__)


if __name__ == "__main__":
    unittest.main()
