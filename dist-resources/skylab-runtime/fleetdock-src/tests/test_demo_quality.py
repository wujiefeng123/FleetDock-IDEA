import ast
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples"
EXAMPLE_FILES = tuple(sorted(EXAMPLE_ROOT.rglob("*.py")))
CHINESE_TEXT = re.compile(r"[\u4e00-\u9fff]")


class DemoQualityTests(unittest.TestCase):
    def test_examples_have_chinese_module_and_public_symbol_docstrings(self) -> None:
        failures: list[str] = []
        for path in EXAMPLE_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            module_doc = ast.get_docstring(tree)
            if not module_doc or not CHINESE_TEXT.search(module_doc):
                failures.append(f"{path.relative_to(REPO_ROOT)} 缺少中文模块说明")
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if node.name.startswith("_"):
                    continue
                docstring = ast.get_docstring(node)
                if not docstring or not CHINESE_TEXT.search(docstring):
                    failures.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} {node.name} 缺少中文说明")
        self.assertEqual([], failures)

    def test_quickstarts_stay_small_and_avoid_source_path_hacks(self) -> None:
        failures: list[str] = []
        for path in sorted((EXAMPLE_ROOT / "quickstart").glob("*.py")):
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > 80:
                failures.append(f"{path.name} 超过 80 行")
            text = "\n".join(lines)
            if "sys.path" in text:
                failures.append(f"{path.name} 不应修改 sys.path")
        for path in sorted((EXAMPLE_ROOT / "demo").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "sys.path.insert(0, str(Path(__file__).resolve().parents[2]))" in text:
                failures.append(f"{path.name} 绕过 SDK 正常安装")
            if "sys.path.insert(0, str(REPO_ROOT))" in text:
                failures.append(f"{path.name} 绕过 SDK 正常安装")
        self.assertEqual([], failures)

    def test_quickstarts_separate_zero_dependency_and_real_px4_paths(self) -> None:
        """确保零依赖入门使用内置 Mock，其余真实设备课程继续走 PX4。"""
        failures: list[str] = []
        for path in sorted((EXAMPLE_ROOT / "quickstart").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            if path.name == "00_mock_drone.py":
                if ".mock_drone()" not in source:
                    failures.append(f"{path.name} 未使用内置模拟无人机")
                continue
            if path.name == "00_mock_robot_dog.py":
                if ".mock_robot_dog()" not in source:
                    failures.append(f"{path.name} 未使用内置模拟机器狗")
                continue
            if path.name == "00_custom_sensor.py":
                if ".mock_drone()" not in source:
                    failures.append(f"{path.name} 未使用内置模拟设备")
                continue
            if ".px4_generic()" not in source:
                failures.append(f"{path.name} 未使用 px4_generic 构造器")
        self.assertEqual([], failures)

    def test_examples_cover_main_sdk_learning_topics(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8") for path in EXAMPLE_FILES)
        required_topics = {
            "设备工厂": "DeviceFactory",
            "能力与诊断": "diagnostics()",
            "标准错误": "response.code",
            "设备事件": "EventType",
            "任务编排": "Plan(",
            "异步任务": ".start()",
            "多设备": "Fleet(",
            "航线": "Route.",
            "动态跟随": "FollowPolicy",
            "安全与避障": "AvoidancePolicy",
        }
        missing = [name for name, marker in required_topics.items() if marker not in source]
        self.assertEqual([], missing)

if __name__ == "__main__":
    unittest.main()
