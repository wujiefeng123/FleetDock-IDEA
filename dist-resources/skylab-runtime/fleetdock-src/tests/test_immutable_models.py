import unittest

from fleetdock import CommandName, DeviceMetadata, ProductType, StandardCommand, StandardResponse


class ImmutablePublicModelsTest(unittest.TestCase):
    def test_metadata_recursively_freezes_caller_mapping(self) -> None:
        source = {"labels": {"area": "north"}, "priorities": [1, 2]}
        metadata = DeviceMetadata(extra=source)

        source["labels"]["area"] = "south"
        source["priorities"].append(3)

        self.assertEqual(metadata.extra["labels"]["area"], "north")
        self.assertEqual(metadata.extra["priorities"], (1, 2))
        with self.assertRaises(TypeError):
            metadata.extra["new"] = True  # type: ignore[index]

    def test_command_and_response_snapshots_do_not_share_mutable_data(self) -> None:
        params = {"target": {"x_m": 1.0}}
        command = StandardCommand(CommandName.MOVE, "device-1", ProductType.MOCK_DRONE, params=params)
        response = StandardResponse.success(command, data={"trace": ["accepted"]})

        params["target"]["x_m"] = 9.0

        self.assertEqual(command.params["target"]["x_m"], 1.0)
        self.assertEqual(response.data["trace"], ("accepted",))


if __name__ == "__main__":
    unittest.main()
