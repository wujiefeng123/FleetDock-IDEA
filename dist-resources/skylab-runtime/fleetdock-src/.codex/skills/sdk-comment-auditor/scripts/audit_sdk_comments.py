#!/usr/bin/env python3
"""扫描 Python SDK 中可能缺失或不清晰的公共注释。"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
ENUM_BASES = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}
SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "tests",
    "venv",
}


@dataclass(frozen=True)
class Finding:
    """表示一个需要人工复核的注释问题。"""

    path: Path
    line: int
    symbol: str
    level: str
    message: str


def is_public(name: str) -> bool:
    """判断名称是否属于普通公共 Python 标识符。"""

    return not name.startswith("_")


def display_base_name(base: ast.expr) -> str:
    """提取类基类表达式末端名称，用于识别枚举。"""

    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return ""


def iter_python_files(roots: Iterable[Path]) -> Iterable[Path]:
    """遍历输入路径中的 Python 文件，并跳过常见缓存和测试目录。"""

    seen: set[Path] = set()
    for root in roots:
        if root.is_file():
            candidates = [root] if root.suffix == ".py" else []
        else:
            candidates = root.rglob("*.py")
        for path in candidates:
            resolved = path.resolve()
            if resolved in seen or any(part in SKIP_DIRS for part in path.parts):
                continue
            seen.add(resolved)
            yield path


def function_arguments(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """返回调用者可传入的显式参数名称。"""

    args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    names = [arg.arg for arg in args if arg.arg not in {"self", "cls"}]
    if node.args.vararg:
        names.append(node.args.vararg.arg)
    if node.args.kwarg:
        names.append(node.args.kwarg.arg)
    return names


def inspect_docstring(
    path: Path,
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    symbol: str,
) -> list[Finding]:
    """检查公共符号 docstring 的存在性和基本可读性。"""

    findings: list[Finding] = []
    doc = ast.get_docstring(node, clean=True)
    if not doc:
        return [
            Finding(path, node.lineno, symbol, "ERROR", "缺少中文 docstring")
        ]
    if not CHINESE_RE.search(doc):
        findings.append(
            Finding(path, node.lineno, symbol, "WARN", "docstring 未包含中文说明")
        )
    compact = re.sub(r"\s+", "", doc)
    if len(compact) < 12:
        findings.append(
            Finding(path, node.lineno, symbol, "WARN", "docstring 过短，可能未说明业务语义")
        )
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        missing = [name for name in function_arguments(node) if name not in doc]
        if missing:
            findings.append(
                Finding(
                    path,
                    node.lineno,
                    symbol,
                    "WARN",
                    "可能缺少参数语义说明: " + ", ".join(missing),
                )
            )
        if node.returns is not None and not any(
            marker in doc for marker in ("返回", "Returns", "Yield", "生成")
        ):
            findings.append(
                Finding(path, node.lineno, symbol, "WARN", "可能缺少返回值说明")
            )
    return findings


def has_nearby_comment(lines: list[str], line_number: int) -> bool:
    """判断枚举成员所在行或上一有效行是否有中文注释。"""

    index = line_number - 1
    current = lines[index] if 0 <= index < len(lines) else ""
    if "#" in current and CHINESE_RE.search(current.split("#", 1)[1]):
        return True
    for previous in range(index - 1, max(-1, index - 3), -1):
        text = lines[previous].strip()
        if not text:
            continue
        return text.startswith("#") and bool(CHINESE_RE.search(text))
    return False


def inspect_file(path: Path) -> list[Finding]:
    """解析单个 Python 文件并返回公共注释候选问题。"""

    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        return [Finding(path, 1, "<module>", "ERROR", f"无法解析文件: {exc}")]

    lines = source.splitlines()
    findings: list[Finding] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and is_public(
            node.name
        ):
            findings.extend(inspect_docstring(path, node, node.name))
            continue
        if not isinstance(node, ast.ClassDef) or not is_public(node.name):
            continue

        findings.extend(inspect_docstring(path, node, node.name))
        is_enum = any(display_base_name(base) in ENUM_BASES for base in node.bases)
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and is_public(
                child.name
            ):
                findings.extend(
                    inspect_docstring(path, child, f"{node.name}.{child.name}")
                )
            elif is_enum and isinstance(child, (ast.Assign, ast.AnnAssign)):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for target in targets:
                    if (
                        isinstance(target, ast.Name)
                        and is_public(target.id)
                        and not has_nearby_comment(lines, child.lineno)
                    ):
                        findings.append(
                            Finding(
                                path,
                                child.lineno,
                                f"{node.name}.{target.id}",
                                "WARN",
                                "枚举成员缺少紧邻的中文业务说明",
                            )
                        )
    return findings


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(
        description="扫描 Python SDK 公共符号的中文 docstring 和枚举成员注释。"
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["fleetdock"],
        help="要扫描的 Python 文件或目录，默认扫描 fleetdock。",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="发现任何候选问题时返回非零退出码。",
    )
    return parser.parse_args()


def main() -> int:
    """运行扫描并输出适合定位源码的问题列表。"""

    args = parse_args()
    roots = [Path(value) for value in args.paths]
    missing = [str(path) for path in roots if not path.exists()]
    if missing:
        print("不存在的扫描路径: " + ", ".join(missing), file=sys.stderr)
        return 2

    findings = sorted(
        (
            finding
            for path in iter_python_files(roots)
            for finding in inspect_file(path)
        ),
        key=lambda item: (str(item.path), item.line, item.symbol, item.message),
    )
    for finding in findings:
        print(
            f"{finding.path}:{finding.line}: {finding.level} "
            f"{finding.symbol}: {finding.message}"
        )
    print(f"扫描完成，共发现 {len(findings)} 个候选问题。")
    return 1 if args.strict and findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
