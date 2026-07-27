import ast
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_FILES = tuple(sorted((REPO_ROOT / "examples" / "demo").glob("*.py"))) + (
    REPO_ROOT / "examples" / "mavlink_random_route_rtl.py",
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
