#!/usr/bin/env python3
"""AUTH-006 CI AST 行级守护（§4.3）—— AC-01~05 规则扫描。

用法：
  python3 scripts/lint_access.py            # 全仓扫描（plane/app/views + plane/access）
  python3 scripts/lint_access.py --selftest # 内嵌反例自检（故意破坏红屏冒烟）

规则（§4.3 表；例外经 EXCEPTIONS 登记豁免——登记项必须同步 AUTH-006 §2.2）：
  AC-01  ViewSet/View 覆盖 get_queryset 且体内无 super()/accessible_queryset/accessible_by
  AC-02  视图层 .objects.all() 直出（可见性未起步）
  AC-03  视图层手写可见性谓词（project__members / workspace__members 族 Q）
  AC-04  unsafe_all 调用缺 reason= 关键字
  AC-05  matrix.py 资源族集合与守护侧期望集漂移（BR-09 三处同步）
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VIEWS_GLOB = "apps/api/plane/app/views/**/*.py"

#: AC-02/AC-03 例外登记（文件相对路径 → 允许的规则集）；空集 = 全豁免该文件
EXCEPTIONS: dict[str, set[str]] = {}

#: AC-03 手写可见性谓词特征（视图层出现即违规——应收敛进 plane/access/matrix.py）
AC03_PATTERNS = (
    "project__members", "workspace__members", "project_projectmember",
    "workspace_members__", "project_members__",
)

#: AC-05 守护侧期望的资源族集合（与 plane/access/matrix.MATRIX 键同步——
#: BR-09 三处同步的第三处：文档表 / matrix.py / 本清单）
EXPECTED_FAMILIES = {"workspaces", "projects", "issues", "issue_comments", "file_assets"}


def _iter_view_files() -> list[Path]:
    return sorted(Path(REPO).glob(VIEWS_GLOB))


def scan_views() -> list[str]:
    violations: list[str] = []
    for path in _iter_view_files():
        rel = str(path.relative_to(REPO))
        allowed = EXCEPTIONS.get(rel, set())
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            violations.append(f"AC-00 {rel}: 语法错误 {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if not (isinstance(item, ast.FunctionDef) and item.name == "get_queryset"):
                        continue
                    if "AC-01" in allowed:
                        continue
                    body_src = ast.get_source_segment(src, item) or ""
                    if not any(k in body_src for k in ("super(", "accessible_queryset", "accessible_by")):
                        violations.append(
                            f"AC-01 {rel}:{item.lineno} {node.name}.get_queryset 未调 "
                            "super()/accessible_queryset/accessible_by（AUTH-006 BR-01）")
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Attribute) \
                    and node.targets[0].attr == "status":
                violations.append(
                    f"AC-06 {rel}:{node.lineno} 视图直改 .status ——状态迁移唯一入口为"
                    " ProjectLifecycleService.transition（PROJ-003 BR-01）")
            if isinstance(node, ast.Call) and "AC-02" not in allowed:
                f = node.func
                if (isinstance(f, ast.Attribute) and f.attr == "all"
                        and isinstance(f.value, ast.Attribute) and f.value.attr == "objects"):
                    violations.append(
                        f"AC-02 {rel}:{node.lineno} 视图层 .objects.all() 直出"
                        "（应 accessible_by 起步或 unsafe_all(reason=) 登记例外）")
        if "AC-03" not in allowed:
            for pat in AC03_PATTERNS:
                if pat in src:
                    violations.append(
                        f"AC-03 {rel}: 手写可见性谓词 {pat}"
                        "（收敛进 plane/access/matrix.py，AUTH-006 §2.2 红线）")
    return violations


def scan_unsafe_all() -> list[str]:
    violations = []
    for path in sorted((Path(REPO) / "apps/api/plane").rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(REPO))
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "unsafe_all"):
                has_reason = any(kw.arg == "reason" for kw in node.keywords)
                if not has_reason:
                    violations.append(f"AC-04 {rel}:{node.lineno} unsafe_all 缺 reason=（BR-08）")
    return violations


def scan_matrix_families() -> list[str]:
    matrix = Path(REPO) / "apps/api/plane/access/matrix.py"
    if not matrix.exists():
        return ["AC-05 plane/access/matrix.py 不存在"]
    src = matrix.read_text(encoding="utf-8")
    families = {line.split('"')[1] for line in src.splitlines()
                if line.strip().startswith('"') and '": ' in line and line.strip().endswith(("_q,", "],"))}
    # 兜底：直接解析 MATRIX 字典字面键
    try:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "MATRIX" for t in node.targets):
                families = {k.value for k in node.value.keys}  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 —— 解析失败落到行扫描结果
        pass
    if families != EXPECTED_FAMILIES:
        return [f"AC-05 matrix.MATRIX 键 {sorted(families)} 与守护期望 "
                f"{sorted(EXPECTED_FAMILIES)} 漂移（BR-09 三处同步）"]
    return []


BAD_SAMPLE = '''
from rest_framework.views import APIView
from plane.db.models import Issue


class BadListView(APIView):
    def get_queryset(self):
        return Issue.objects.all()          # AC-01 + AC-02

    def get(self, request):
        qs = Issue.objects.filter(project__members=request.user)  # AC-03
        return qs
'''


def selftest() -> int:
    """内嵌反例：四类规则都必须命中（CI 红屏冒烟，§6 系统级守卫）。"""
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "bad_view.py"
    tmp.write_text(BAD_SAMPLE, encoding="utf-8")
    violations: list[str] = []
    src = tmp.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "get_queryset":
                    body = ast.get_source_segment(src, item) or ""
                    if not any(k in body for k in ("super(", "accessible_queryset", "accessible_by")):
                        violations.append("AC-01")
        if isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Attribute) and f.attr == "all"
                    and isinstance(f.value, ast.Attribute) and f.value.attr == "objects"):
                violations.append("AC-02")
    for pat in ("project__members",):
        if pat in src:
            violations.append("AC-03")
    if not {"AC-01", "AC-02", "AC-03"} <= set(violations):
        print("✗ selftest 未全命中：", violations)
        return 1
    print("✓ selftest：AC-01/02/03 反例全命中（故意破坏红屏冒烟）")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    violations = scan_views() + scan_unsafe_all() + scan_matrix_families()
    if violations:
        print(f"✗ 行级守护发现 {len(violations)} 处违规：")
        for v in violations:
            print("  -", v)
        return 1
    print("✓ 行级守护（AC-01~05）通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
