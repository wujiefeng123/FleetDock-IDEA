import ast
from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_FILES = tuple(sorted((REPO_ROOT / "examples" / "demo").glob("*.py"))) + (
    REPO_ROOT / "examples" / "mavlink_random_route_rtl.py",
    REPO_ROOT / "packages" / "fleetdock-server" / "examples" / "drone_client_debug.py",
    REPO_ROOT / "packages" / "fleetdock-server" / "examples" / "drone_controller_debug.py",
    REPO_ROOT / "packages" / "fleetdock-server" / "src" / "fleetdock-cli" / "projectairsim_rtsp_bridge.py",
    REPO_ROOT / "packages" / "fleetdock-server" / "examples" / "projectairsim_bridge_server.py",
)
FORBIDDEN_IMPORTS = {"subprocess"}
FORBIDDEN_CALLS = {
    "os.system",
    "os.popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "subprocess.run",
}
FORBIDDEN_COMMAND_TEXT = (
    "px4-commander",
    "px4-param",
    "px4-mavlink",
    "./bin/px4",
)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        if parent is None:
            return node.attr
        return f"{parent}.{node.attr}"
    return None


class DemoCliBoundaryTest(unittest.TestCase):
    def test_projectairsim_rtsp_bridge_launcher_only_owns_video_source(self) -> None:
        helper_path = (
            REPO_ROOT
            / "packages"
            / "fleetdock-server"
            / "src"
            / "fleetdock-cli"
            / "projectairsim_rtsp_bridge.py"
        )
        batch_path = (
            REPO_ROOT
            / "packages"
            / "fleetdock-server"
            / "examples"
            / "start_projectairsim_rtsp_bridge.bat"
        )
        content = helper_path.read_text(encoding="utf-8")
        batch = batch_path.read_text(encoding="utf-8")

        self.assertIn("ProjectAirSimRtspBridge", content)
        self.assertNotIn("fleetdock_server", content)
        self.assertNotIn("FleetDockServer", content)
        self.assertNotIn("PX4_ENDPOINT", content)
        self.assertNotIn("WHIP", content)
        self.assertIn("src\\fleetdock-cli\\projectairsim_rtsp_bridge.py", batch)
        self.assertNotIn("drone_server.py", batch)

    def test_windows_batch_files_use_crlf_without_bom(self) -> None:
        for script_name in (
            "env.example.bat",
            "run_dev.bat",
            "run_dev_multi.bat",
            "start_px4_mavlink.bat",
        ):
            content = (REPO_ROOT / "examples" / script_name).read_bytes()

            self.assertFalse(content.startswith(b"\xef\xbb\xbf"), script_name)
            self.assertEqual(content.count(b"\n"), content.count(b"\r\n"), script_name)

    def test_env_example_lists_dev_script_overrides(self) -> None:
        env_example = (REPO_ROOT / "examples" / "env.example.bat").read_text(encoding="utf-8")
        documented_names = set(re.findall(r'rem set "([A-Z][A-Z0-9_]*)=', env_example))
        internal_names = {"SCRIPT_DIR"}

        for script_name in ("run_dev.bat", "run_dev_multi.bat"):
            script = (REPO_ROOT / "examples" / script_name).read_text(encoding="utf-8")
            defaults = script[:script.index("echo load env.bat")]
            configurable_names = set(
                re.findall(r'set "([A-Z][A-Z0-9_]*)=', defaults)
            ).difference(internal_names)

            self.assertEqual(set(), configurable_names.difference(documented_names))

    def test_dev_scripts_allow_scene_config_override(self) -> None:
        for script_name in ("run_dev.bat", "run_dev_multi.bat"):
            script = (REPO_ROOT / "examples" / script_name).read_text(encoding="utf-8")
            default_position = script.index('set "PROJECTAIRSIM_SCENE_CONFIG_PATH="')
            env_position = script.index('call "%SCRIPT_DIR%env.bat"')

            self.assertLess(default_position, env_position)

    def test_dev_scripts_retry_wsl_host_detection_and_allow_fallback(self) -> None:
        for script_name in ("run_dev.bat", "run_dev_multi.bat"):
            script = (REPO_ROOT / "examples" / script_name).read_text(encoding="utf-8")

            self.assertIn("for /l %%A in (1,1,10)", script)
            self.assertIn(
                "if not defined DETECTED_PX4_WINDOWS_HOST_IP if defined PX4_WINDOWS_HOST_IP",
                script,
            )
            self.assertIn(
                'set "DETECTED_PX4_WINDOWS_HOST_IP=%PX4_WINDOWS_HOST_IP%"',
                script,
            )

    def test_dev_scripts_clean_simulator_processes_by_executable_path(self) -> None:
        helper_name = "stop_simulator_processes.ps1"
        hardcoded_process_names = (
            "AirSimNH.exe",
            "AirSimNH-Win64-Shipping.exe",
        )

        for script_name in ("run_dev.bat", "run_dev_multi.bat"):
            script = (REPO_ROOT / "examples" / script_name).read_text(encoding="utf-8")
            self.assertIn(f'-File "%SCRIPT_DIR%{helper_name}"', script)
            self.assertIn("failed to stop old simulator processes", script)
            for process_name in hardcoded_process_names:
                self.assertNotIn(f"taskkill /im {process_name}", script)

        helper = (REPO_ROOT / "examples" / helper_name).read_text(encoding="utf-8")
        self.assertIn("$env:AIRSIM_EXE", helper)
        self.assertIn("Get-CimInstance Win32_Process", helper)
        self.assertIn("ExecutablePath", helper)
        self.assertIn("Split-Path -Parent $simulatorPath", helper)
        self.assertIn("The directory containing AIRSIM_EXE cannot be a drive root", helper)

    def test_multi_start_waits_for_parameter_server_before_setting_system_id(self) -> None:
        script = (REPO_ROOT / "examples" / "run_dev_multi.bat").read_text(encoding="utf-8")

        stop_position = script.index("echo stop old px4 instances")
        clean_position = script.index("echo clean stale px4 sockets")
        start_position = script.index("echo run %MULTI_DRONE_COUNT% px4 instances")
        configure_position = script.index("echo configure unique MAVLink system IDs")
        parameter_wait_position = script.index(
            "PX4_READY_TIMEOUT_S='%PX4_READY_TIMEOUT_S%' bash '%WSL_SCRIPT_DIR%/wait_px4_ready.sh'",
            configure_position,
        )
        set_position = script.index("set MAV_SYS_ID", parameter_wait_position)

        self.assertLess(stop_position, clean_position)
        self.assertLess(clean_position, start_position)
        self.assertLess(configure_position, parameter_wait_position)
        self.assertLess(parameter_wait_position, set_position)
        self.assertIn("for attempt in {1..10}", script[parameter_wait_position:set_position])

    def test_multi_start_checks_projectairsim_preparation_result(self) -> None:
        script = (REPO_ROOT / "examples" / "run_dev_multi.bat").read_text(encoding="utf-8")

        prepare_position = script.index("echo prepare projectairsim scene and drones")
        commander_position = script.index(
            "echo wait for px4 initialization and start mavlink output",
            prepare_position,
        )
        prepare_block = script[prepare_position:commander_position]

        self.assertNotIn('start "prepare projectairsim drones"', prepare_block)
        self.assertIn('call "%CONDA_BAT%" run --no-capture-output', prepare_block)
        self.assertIn("failed to prepare projectairsim scene and drones", prepare_block)

    def test_single_start_checks_projectairsim_preparation_result(self) -> None:
        script = (REPO_ROOT / "examples" / "run_dev.bat").read_text(encoding="utf-8")

        prepare_position = script.index("echo prepare projectairsim scene and drone")
        commander_position = script.index(
            "echo wait for px4 initialization and start mavlink output",
            prepare_position,
        )
        prepare_block = script[prepare_position:commander_position]

        self.assertNotIn('start "prepare projectairsim drone"', prepare_block)
        self.assertIn("PX4_SOCKET_ONLY=1", prepare_block)
        self.assertIn('call "%CONDA_BAT%" run --no-capture-output', prepare_block)
        self.assertIn("failed to prepare projectairsim scene and drone", prepare_block)

    def test_python_demos_do_not_call_px4_cli(self) -> None:
        failures: list[str] = []

        for path in DEMO_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in FORBIDDEN_IMPORTS:
                            failures.append(f"{path.relative_to(REPO_ROOT)} imports {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module in FORBIDDEN_IMPORTS:
                        failures.append(f"{path.relative_to(REPO_ROOT)} imports from {node.module}")
                elif isinstance(node, ast.Call):
                    call_name = _call_name(node.func)
                    if call_name in FORBIDDEN_CALLS:
                        failures.append(f"{path.relative_to(REPO_ROOT)} calls {call_name}")
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for text in FORBIDDEN_COMMAND_TEXT:
                        if text in node.value:
                            failures.append(f"{path.relative_to(REPO_ROOT)} contains {text!r}")

        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
