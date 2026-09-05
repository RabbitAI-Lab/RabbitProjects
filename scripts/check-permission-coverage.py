#!/usr/bin/env python3
"""AUTH-005 §4.6 C3 一致性守护 —— 后端侧「权限点 → 守护引用」成对扫描。

校验逻辑：
  * 解析 ``apps/api/plane/constants/permissions.py`` 的 ``PERMISSION_MATRIX``，
    取出全键（含 ``scope`` 前缀以便定位失败的消费侧）。
  * 扫描后端 Python 源码（``apps/api/plane/app/`` 与 ``apps/api/plane/db/services/``），
    收集两类「消费」证据：
      1. Permission 类声明 ``read_role = X`` / ``write_role = X`` —— 但 Permission 类
         本身的判定是按角色等级而不是按权限点 key，无法直接 grep key；C3 在后端侧的
         可执行性因此落在 (2)。
      2. ``require_permission("<key>" ...)`` 调用点 —— 这是矩阵 key 真正进入业务
         视图代码的唯一路径；用 AST 扫描定位每个 key 的调用次数。
      3. **额外的工程守护**：直接 grep 任意源码里出现权限点 key 字面量（如视图中
         写 ``threshold_of("workspace.member.invite")`` 或注释引用）。这两类都
         视作「key 被消费」。
  * 每个 key 必须至少有 1 处消费；0 处 = 「无守护权限点」 → CI 失败并列出。

运行：
  python3 scripts/check-permission-coverage.py
  或  bash tests/run-ci-checks.sh（已挂载，36 条静态断言之一）

退出码：0 全部 key 有消费；1 列出「无守护权限点」清单；2 解析错误。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MATRIX_PATH = REPO_ROOT / "apps/api/plane/constants/permissions.py"
SCAN_ROOTS = (
    REPO_ROOT / "apps/api/plane/app",
    REPO_ROOT / "apps/api/plane/db/services",
)

# ── 解析 PERMISSION_MATRIX ───────────────────────────────────────
def parse_matrix_keys() -> set[str]:
    """从 constants/permissions.py 解析出全权限点键（不分 scope，key 自身唯一）。"""
    src = MATRIX_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "PERMISSION_MATRIX":
                # PERMISSION_MATRIX = {"workspace": {...}, "project": {...}}
                if not isinstance(node.value, ast.Dict):
                    continue
                for scope_value in node.value.values:
                    if not isinstance(scope_value, ast.Dict):
                        continue
                    for key_node in scope_value.keys:
                        if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                            keys.add(key_node.value)
    if not keys:
        raise RuntimeError(f"未在 {MATRIX_PATH} 解析到任何权限点；检查 PERMISSION_MATRIX 定义")
    return keys


# ── AST 扫描 ─────────────────────────────────────────────────────
_DECORATOR_RE = re.compile(r"@require_permission\(\s*[\"']([^\"']+)[\"']")


def _scan_with_ast(py_file: Path, consumer_keys: set[str], literal_refs: set[str]) -> None:
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return  # 跳过解析失败的源码（CI 由 ruff 兜底）
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # require_permission("xxx", scope=...) 或 @require_permission("xxx")
        if isinstance(func, ast.Name) and func.id == "require_permission":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                consumer_keys.add(node.args[0].value)
            continue
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "require_permission"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            consumer_keys.add(node.args[0].value)


def _scan_with_grep(py_file: Path, all_keys: list[str], literal_refs: set[str]) -> None:
    """兜底：源代码里直接出现权限点字面量（threshold_of / 注释 / 测试）也算消费。"""
    text = py_file.read_text(encoding="utf-8")
    for k in all_keys:
        if f'"{k}"' in text or f"'{k}'" in text:
            literal_refs.add(k)


def collect_consumers(all_keys: list[str]) -> dict[str, list[Path]]:
    """每个 key 收集「被消费」的文件路径。"""
    via_decorator: dict[str, set[Path]] = {k: set() for k in all_keys}
    via_literal: dict[str, set[Path]] = {k: set() for k in all_keys}
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            decorator_keys: set[str] = set()
            literal_keys: set[str] = set()
            _scan_with_ast(py, decorator_keys, literal_keys)
            _scan_with_grep(py, all_keys, literal_keys)
            for k in decorator_keys:
                via_decorator.setdefault(k, set()).add(py)
            for k in literal_keys:
                via_literal.setdefault(k, set()).add(py)
    out: dict[str, list[Path]] = {}
    for k in all_keys:
        files = sorted(set(via_decorator.get(k, set()) | via_literal.get(k, set())))
        out[k] = files
    return out


# ── 主入口 ───────────────────────────────────────────────────────
def main() -> int:
    keys = parse_matrix_keys()
    coverage = collect_consumers(sorted(keys))

    missing = sorted(k for k, files in coverage.items() if not files)
    print(f"扫描 {len(keys)} 个权限点（{len(SCAN_ROOTS)} 个源码根）")
    for k in sorted(keys):
        files = coverage[k]
        marker = "✓" if files else "✗ 无守护"
        suffix = f"  <- {files[0].relative_to(REPO_ROOT)}" + (
            f" 等 {len(files)} 处" if len(files) > 1 else ""
        ) if files else ""
        print(f"  {marker}  {k}{suffix}")

    if missing:
        print(f"\n✗ FAIL: {len(missing)} 个权限点无消费（需 Permission 类 / @require_permission / 字面量引用）")
        for k in missing:
            print(f"    - {k}")
        return 1
    print("\n✓ ALL KEYS HAVE CONSUMERS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    # CI 守护脚本的顶层兜底：任何解析异常都要收敛成退出码 2（而非抛 traceback），
    # 否则 CI 只看到堆栈、看不出是「脚本坏了」还是「检查不通过」。
    except Exception as exc:  # noqa: BLE001
        print(f"check-permission-coverage: 解析失败 {exc!r}", file=sys.stderr)
        sys.exit(2)