import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
PREPARE_SCRIPT = REPO_ROOT / "examples" / "prepare_projectairsim_drone.py"


class ProjectAirSimProfileTests(unittest.TestCase):
    def load_prepare_module(self, root: Path):
        examples_dir = root / "example_user_scripts"
        src_dir = root / "src"
        (examples_dir / "sim_config").mkdir(parents=True)
        src_dir.mkdir()
        spec = importlib.util.spec_from_file_location("fleetdock_prepare_projectairsim_test", PREPARE_SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        environment = {
            "PROJECTAIRSIM_EXAMPLES_DIR": str(examples_dir),
            "PROJECTAIRSIM_SRC_DIR": str(src_dir),
            "PROJECTAIRSIM_SENSOR_PROFILES": "",
            "MULTI_PROJECTAIRSIM_SENSOR_PROFILES": "",
        }
        with patch.dict(os.environ, environment, clear=False):
            spec.loader.exec_module(module)
        return module, examples_dir

    def write_base_configs(self, examples_dir: Path) -> None:
        config_dir = examples_dir / "sim_config"
        scene = {
            "id": "BaseScene",
            "actors": [
                {
                    "type": "robot",
                    "name": "Drone1",
                    "origin": {"xyz": "0 0 -4", "rpy-deg": "0 0 0"},
                    "robot-config": "robot_quadrotor_px4_sitl_wsl2.jsonc",
                }
            ],
        }
        robot = {
            "controller": {"px4-settings": {}},
            "sensors": [{"id": "Chase", "type": "camera"}],
        }
        (config_dir / "scene_px4_sitl_wsl2.jsonc").write_text(
            json.dumps(scene), encoding="utf-8"
        )
        (config_dir / "robot_quadrotor_px4_sitl_wsl2.jsonc").write_text(
            json.dumps(robot), encoding="utf-8"
        )

    def test_single_explicit_profiles_generate_stereo_and_lidar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            module, examples_dir = self.load_prepare_module(Path(temp_dir))
            self.write_base_configs(examples_dir)
            module.SENSOR_PROFILES_SPEC = "stereo,lidar"
            module.DRONE_NAMES = ["Drone1"]
            module.SCENE_NAME = "single.generated.jsonc"

            with patch.dict(sys.modules, {"commentjson": SimpleNamespace(load=json.load)}):
                module.prepare_single_drone_scene()

            robot = json.loads(
                (examples_dir / "sim_config" / "robot_fleetdock_px4_1.generated.jsonc").read_text(
                    encoding="utf-8"
                )
            )
            sensor_ids = {sensor["id"] for sensor in robot["sensors"]}
            self.assertTrue({"StereoLeft", "StereoRight", "lidar1"}.issubset(sensor_ids))
            self.assertEqual(4560, robot["controller"]["px4-settings"]["tcp-port"])

    def test_stereo_and_lidar_profiles_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            module, examples_dir = self.load_prepare_module(Path(temp_dir))
            self.write_base_configs(examples_dir)

            with patch.dict(sys.modules, {"commentjson": SimpleNamespace(load=json.load)}):
                stereo = module.load_robot_profile("stereo")
                lidar = module.load_robot_profile("lidar")

            stereo_ids = {sensor["id"] for sensor in stereo["sensors"]}
            lidar_ids = {sensor["id"] for sensor in lidar["sensors"]}
            self.assertTrue({"StereoLeft", "StereoRight"}.issubset(stereo_ids))
            stereo_cameras = [
                sensor for sensor in stereo["sensors"] if sensor["id"] in {"StereoLeft", "StereoRight"}
            ]
            self.assertEqual({"0.45 -0.06 0", "0.45 0.06 0"}, {item["origin"]["xyz"] for item in stereo_cameras})
            self.assertNotIn("lidar1", stereo_ids)
            self.assertIn("lidar1", lidar_ids)
            self.assertNotIn("StereoLeft", lidar_ids)

    def test_radar_profile_is_independent_and_composable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            module, examples_dir = self.load_prepare_module(Path(temp_dir))
            self.write_base_configs(examples_dir)
            with patch.dict(sys.modules, {"commentjson": SimpleNamespace(load=json.load)}):
                radar = module.load_robot_profile("radar")
                self.assertIn("radar1", {sensor["id"] for sensor in radar["sensors"]})
                radar_sensor = next(sensor for sensor in radar["sensors"] if sensor["id"] == "radar1")
                self.assertEqual([], radar_sensor["masks"])
                combined = module.load_robot_profiles(("depth", "radar"))
                self.assertTrue({"FrontDepthCamera", "radar1"}.issubset({sensor["id"] for sensor in combined["sensors"]}))
                front_camera = next(sensor for sensor in combined["sensors"] if sensor["id"] == "FrontDepthCamera")
                self.assertEqual("0.45 0 0", front_camera["origin"]["xyz"])
                self.assertEqual(front_camera["capture-interval"], 0.05)

    def test_video_profile_uses_one_front_rgb_camera_at_thirty_fps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            module, examples_dir = self.load_prepare_module(Path(temp_dir))
            self.write_base_configs(examples_dir)
            with patch.dict(sys.modules, {"commentjson": SimpleNamespace(load=json.load)}):
                video = module.load_robot_profiles(("basic", "video"))

            sensors_by_id = {sensor["id"]: sensor for sensor in video["sensors"]}
            front_camera = sensors_by_id["FrontCamera"]
            self.assertAlmostEqual(front_camera["capture-interval"], 1.0 / 30.0, places=8)
            self.assertEqual([0], [setting["image-type"] for setting in front_camera["capture-settings"]])
            self.assertEqual((640, 480), (
                front_camera["capture-settings"][0]["width"],
                front_camera["capture-settings"][0]["height"],
            ))
            self.assertNotIn("enabled", sensors_by_id["Chase"])
            if "DownCamera" in sensors_by_id:
                self.assertFalse(sensors_by_id["DownCamera"]["enabled"])

    def test_single_sensor_profile_list_combines_depth_and_lidar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            module, examples_dir = self.load_prepare_module(Path(temp_dir))
            self.write_base_configs(examples_dir)
            module.SENSOR_PROFILES_SPEC = "depth,lidar"
            module.DRONE_NAMES = ["Drone1"]
            module.SCENE_NAME = "single.generated.jsonc"

            with patch.dict(sys.modules, {"commentjson": SimpleNamespace(load=json.load)}):
                module.prepare_single_drone_scene()

            robot = json.loads(
                (examples_dir / "sim_config" / "robot_fleetdock_px4_1.generated.jsonc").read_text(
                    encoding="utf-8"
                )
            )
            sensor_ids = {sensor["id"] for sensor in robot["sensors"]}
            self.assertTrue({"FrontDepthCamera", "lidar1"}.issubset(sensor_ids))
            self.assertNotIn("StereoLeft", sensor_ids)

    def test_combined_profiles_reject_duplicate_sensor_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            module, examples_dir = self.load_prepare_module(Path(temp_dir))
            self.write_base_configs(examples_dir)

            with patch.dict(sys.modules, {"commentjson": SimpleNamespace(load=json.load)}):
                robot = module.load_robot_profile("lidar")
                with self.assertRaisesRegex(ValueError, "重复传感器 ID"):
                    module.append_profile_sensors(
                        robot,
                        [{"id": "lidar1", "type": "lidar"}],
                    )

    def test_multi_profiles_default_to_drone1_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            module, examples_dir = self.load_prepare_module(Path(temp_dir))
            self.write_base_configs(examples_dir)
            module.SENSOR_PROFILES_SPEC = "stereo,lidar"
            module.DRONE_NAMES = ["Drone1", "Drone2", "Drone3"]
            module.MULTI_SENSOR_DRONES = "Drone1"
            module.SCENE_NAME = "multi.generated.jsonc"

            with patch.dict(sys.modules, {"commentjson": SimpleNamespace(load=json.load)}):
                module.prepare_multi_drone_scene()

            generated = []
            for index in range(1, 4):
                path = examples_dir / "sim_config" / f"robot_fleetdock_px4_{index}.generated.jsonc"
                generated.append(json.loads(path.read_text(encoding="utf-8")))
            first_ids = {sensor["id"] for sensor in generated[0]["sensors"]}
            second_ids = {sensor["id"] for sensor in generated[1]["sensors"]}
            self.assertIn("lidar1", first_ids)
            self.assertNotIn("lidar1", second_ids)
            self.assertEqual(4562, generated[2]["controller"]["px4-settings"]["tcp-port"])

            module.MULTI_SENSOR_DRONES = "all"
            with patch.dict(sys.modules, {"commentjson": SimpleNamespace(load=json.load)}):
                module.prepare_multi_drone_scene()
            second = json.loads(
                (examples_dir / "sim_config" / "robot_fleetdock_px4_2.generated.jsonc").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("lidar1", {sensor["id"] for sensor in second["sensors"]})

    def test_multi_sensor_profiles_assign_different_combinations_per_drone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            module, examples_dir = self.load_prepare_module(Path(temp_dir))
            self.write_base_configs(examples_dir)
            module.DRONE_NAMES = ["Drone1", "Drone2", "Drone3"]
            module.MULTI_SENSOR_PROFILES_SPEC = "Drone1=stereo+lidar;Drone2=depth"
            module.SCENE_NAME = "multi.generated.jsonc"

            with patch.dict(sys.modules, {"commentjson": SimpleNamespace(load=json.load)}):
                module.prepare_multi_drone_scene()

            generated = [
                json.loads(
                    (
                        examples_dir
                        / "sim_config"
                        / f"robot_fleetdock_px4_{index}.generated.jsonc"
                    ).read_text(encoding="utf-8")
                )
                for index in range(1, 4)
            ]
            first_ids = {sensor["id"] for sensor in generated[0]["sensors"]}
            second_ids = {sensor["id"] for sensor in generated[1]["sensors"]}
            third_ids = {sensor["id"] for sensor in generated[2]["sensors"]}
            self.assertTrue({"StereoLeft", "StereoRight", "lidar1"}.issubset(first_ids))
            self.assertIn("FrontDepthCamera", second_ids)
            self.assertNotIn("lidar1", second_ids)
            self.assertEqual({"Chase"}, third_ids)

    def test_multi_sensor_profiles_reject_unknown_drone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            module, _ = self.load_prepare_module(Path(temp_dir))
            module.DRONE_NAMES = ["Drone1", "Drone2"]
            module.MULTI_SENSOR_PROFILES_SPEC = "Drone3=lidar"

            with self.assertRaisesRegex(ValueError, "未知无人机"):
                module.selected_profiles_for_drone("Drone1")


if __name__ == "__main__":
    unittest.main()
