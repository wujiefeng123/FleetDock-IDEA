#!/usr/bin/env python3
"""对 FleetDock 仓库执行只读、确定性的质量预扫描。"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


VENDOR_IMPORT_PREFIXES = ("mavsdk", "pymavlink", "unitree_sdk2", "dji")
FORBIDDEN_CLI_CALLS = {
    "os.system",
    "os.popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "subprocess.run",
}
PX4_CLI_MARKERS = ("px4-commander", "px4-param", "px4-mavlink", "./bin/px4")


@dataclass(frozen=True)
class Finding:
    """保存一条静态扫描发现。"""

    severity: str
    code: str
    path: str
    line: int
    message: str


def parse_args() -> argparse.Namespace:
    """解析仓库路径和输出选项。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", help="FleetDock 仓库根目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--strict", action="store_true", help="发现 P0/P1 时返回非零状态")
    return parser.parse_args()


def call_name(node: ast.AST) -> str | None:
    """返回调用表达式的点分名称。"""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def relative(path: Path, root: Path) -> str:
    """返回使用正斜杠的仓库相对路径。"""

    return path.relative_to(root).as_posix()


def iter_python_files(path: Path) -> Iterable[Path]:
    """按路径排序返回 Python 文件。"""

    return sorted(path.rglob("*.py")) if path.exists() else ()


def function_end_line(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """返回函数结束行号。"""

    return max((getattr(item, "end_lineno", node.lineno) or node.lineno for item in ast.walk(node)), default=node.lineno)


def scan_examples(root: Path) -> tuple[list[Finding], dict[str, object]]:
    """检查示例规模、SDK 边界和复杂控制结构。"""

    findings: list[Finding] = []
    stats: dict[str, object] = {}
    examples = root / "examples"
    for group, limit, severity in (("quickstart", 80, "P1"), ("demo", 150, "P2")):
        files = list(iter_python_files(examples / group))
        sizes: list[tuple[int, str]] = []
        for path in files:
            text = path.read_text(encoding="utf-8")
            lines = text.splitlines()
            rel = relative(path, root)
            sizes.append((len(lines), rel))
            if len(lines) > limit:
                findings.append(Finding(severity, "EXAMPLE_TOO_LONG", rel, 1, f"{group} 示例 {len(lines)} 行，超过 {limit} 行门禁。"))
            try:
                tree = ast.parse(text, filename=str(path))
            except SyntaxError as exc:
                findings.append(Finding("P1", "EXAMPLE_SYNTAX", rel, exc.lineno or 1, f"示例无法解析：{exc.msg}"))
                continue
            has_fleetdock_import = False
            class_names: set[str] = set()
            uses_managed_object = False
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        has_fleetdock_import |= alias.name == "fleetdock" or alias.name.startswith("fleetdock.")
                        if alias.name.startswith(VENDOR_IMPORT_PREFIXES):
                            findings.append(Finding("P1", "DIRECT_VENDOR_IMPORT", rel, node.lineno, f"直接导入厂商 SDK：{alias.name}"))
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    has_fleetdock_import |= module == "fleetdock" or module.startswith("fleetdock.")
                    if module.startswith(VENDOR_IMPORT_PREFIXES):
                        findings.append(Finding("P1", "DIRECT_VENDOR_IMPORT", rel, node.lineno, f"直接导入厂商 SDK：{module}"))
                elif isinstance(node, ast.Call):
                    name = call_name(node.func)
                    uses_managed_object |= name is not None and (name == "managed_object" or name.endswith(".managed_object"))
                    if name in FORBIDDEN_CLI_CALLS:
                        findings.append(Finding("P0", "DEVICE_CLI_CALL", rel, node.lineno, f"Python 示例调用命令行入口：{name}"))
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for marker in PX4_CLI_MARKERS:
                        if marker in node.value:
                            findings.append(Finding("P0", "PX4_CLI_TEXT", rel, getattr(node, "lineno", 1), f"Python 示例包含 PX4 CLI：{marker}"))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    length = function_end_line(node) - node.lineno + 1
                    if group == "quickstart" and length > 40:
                        findings.append(Finding("P2", "LONG_QUICKSTART_FUNCTION", rel, node.lineno, f"函数 {node.name} 长 {length} 行，可能承载了过多教学逻辑。"))
                elif isinstance(node, ast.ClassDef):
                    class_names.add(node.name)
            if group == "quickstart" and not has_fleetdock_import:
                findings.append(Finding("P1", "QUICKSTART_BYPASSES_SDK", rel, 1, "Quick Start 没有导入 FleetDock SDK。"))
            if uses_managed_object and any(name.lower().startswith(("demo", "fake", "mock")) for name in class_names):
                findings.append(Finding("P2", "DEMO_MANAGED_FAKE", rel, 1, "示例向标准设备注入自定义演示客户端；应明确说明不会控制真实设备。"))
        stats[group] = {
            "file_count": len(files),
            "total_lines": sum(size for size, _ in sizes),
            "average_lines": round(sum(size for size, _ in sizes) / len(files), 1) if files else 0.0,
            "largest": sorted(sizes, reverse=True)[:5],
        }

    standalone = [path for path in iter_python_files(examples) if path.parent == examples]
    for path in standalone:
        text = path.read_text(encoding="utf-8")
        rel = relative(path, root)
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            elif isinstance(node, ast.Import):
                module = ",".join(alias.name for alias in node.names)
            if module.startswith(VENDOR_IMPORT_PREFIXES) or any(part.startswith(VENDOR_IMPORT_PREFIXES) for part in module.split(",")):
                findings.append(Finding("P2", "STANDALONE_VENDOR_EXAMPLE", rel, node.lineno, "独立示例直接使用厂商 SDK；应明确标记为底层对照或环境工具。"))
                break
    return findings, stats


def scan_capability_contracts(root: Path) -> list[Finding]:
    """运行时核对内置产品声明与适配器处理器。"""

    findings: list[Finding] = []
    sys.path.insert(0, str(root))
    try:
        from fleetdock.registry import default_registry
    except Exception as exc:
        return [Finding("P1", "SDK_IMPORT_FAILED", "fleetdock", 1, f"无法导入 SDK 执行能力契约扫描：{exc}")]

    for profile in default_registry.list():
        try:
            adapter_type = default_registry.get_adapter(profile.product, profile.default_firmware)
        except Exception:
            if getattr(profile.maturity, "value", str(profile.maturity)) not in {"planned"}:
                findings.append(Finding("P1", "ADAPTER_MISSING", "fleetdock/registry.py", 1, f"产品 {profile.product.value} 没有可用适配器，但成熟度不是 planned。"))
            continue
        source_path = Path(inspect.getsourcefile(adapter_type) or root / "fleetdock" / "registry.py")
        for command in profile.supported_commands:
            handler = getattr(adapter_type, f"_handle_{command.value}", None)
            if not callable(handler):
                findings.append(Finding("P0", "HANDLER_MISSING", relative(source_path, root), 1, f"{profile.product.value} 声明命令 {command.value}，但适配器缺少处理器。"))
                continue
            try:
                source_lines, start_line = inspect.getsourcelines(handler)
            except (OSError, TypeError):
                continue
            handler_source = "".join(source_lines)
            if "ErrorCode.UNSUPPORTED_COMMAND" in handler_source and "StandardResponse.success" not in handler_source:
                findings.append(Finding("P0", "DECLARED_BUT_UNSUPPORTED", relative(source_path, root), start_line, f"{profile.product.value} 声明命令 {command.value}，处理器却只返回 UNSUPPORTED_COMMAND。"))
    return findings


def scan_timeout_contract(root: Path) -> list[Finding]:
    """检查标准命令超时是否被适配器执行链消费。"""

    files = list(iter_python_files(root / "fleetdock" / "adapters"))
    consumers: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if "command.timeout_s" in text:
            consumers.append(relative(path, root))
    if consumers:
        return []
    return [Finding("P1", "COMMAND_TIMEOUT_UNUSED", "fleetdock/devices.py", 333, "公共 execute(timeout_s=...) 会写入命令，但适配器中没有读取 command.timeout_s。")]


def validate_layout(root: Path) -> list[Finding]:
    """检查审计所需的仓库结构。"""

    required = ("fleetdock", "examples", "tests", "pyproject.toml", "README.md")
    return [Finding("P1", "REPO_LAYOUT", name, 1, f"缺少必要路径：{name}") for name in required if not (root / name).exists()]


def main() -> int:
    """执行扫描并输出结果。"""

    args = parse_args()
    root = Path(args.root).resolve()
    findings = validate_layout(root)
    example_findings, stats = scan_examples(root) if not findings else ([], {})
    findings.extend(example_findings)
    if not findings or all(item.code != "REPO_LAYOUT" for item in findings):
        findings.extend(scan_capability_contracts(root))
        findings.extend(scan_timeout_contract(root))
    findings.sort(key=lambda item: ({"P0": 0, "P1": 1, "P2": 2}.get(item.severity, 3), item.path, item.line, item.code))

    if args.json:
        print(json.dumps({"root": str(root), "stats": stats, "findings": [asdict(item) for item in findings]}, ensure_ascii=False, indent=2))
    else:
        print("FleetDock 质量预扫描")
        for group, values in stats.items():
            print(f"- {group}: {values['file_count']} 个文件，平均 {values['average_lines']} 行，总计 {values['total_lines']} 行")
        if findings:
            print(f"发现 {len(findings)} 项：")
            for item in findings:
                print(f"[{item.severity}] {item.code} {item.path}:{item.line} - {item.message}")
        else:
            print("未发现确定性静态问题；仍需按 quality-gates.md 完成语义、SITL 和真机审计。")
    if args.strict and any(item.severity in {"P0", "P1"} for item in findings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
