"""S4[FGP]* 造数清理（sprint-4 三份 e2e spec 的 afterAll 自动调用；也可手动跑）——ORM 硬删 + 清理前后计数证据。

用法（仓库根）：
  DATABASE_URL=postgresql://rp:rp@localhost:5432/rabbit_projects SECRET_KEY=dev \
    uv run --project apps/api python tests/e2e/_cleanup_s4.py [--dry]

清理范围（三份 spec 造数全域）：
  - 项目名匹配 ^S4[FGP]（gantt=S4G*/files=S4F*/preview-share=S4P* 各前缀族）
    ——Project 硬删，FK CASCADE 连带 folders/assets/versions/sessions/shares/
    accesses/issues/members 等；
  - 测试注册用户（email 匹配 ^s4[gfp]-）——含其工作区成员行/邀请（CASCADE）。

纪律（任务说明）：测试造数必须真清理（T4-10 S4F* 曾漏清 200 个软删项目）——
用完即 ORM 硬删，不留软删行。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import django

# apps/api 加入 sys.path（uv run 的 script 模式不认 cwd；从仓库根直跑）
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plane.settings.dev")
django.setup()  # noqa: E402

from django.db import connection
from plane.db.models import FileAsset, FileShareAccess, FileShareLink, FileVersion, Project, UploadSession, User, WorkspaceMember  # noqa: E402

REGEX = r"^S4[FGP]"
EMAIL_REGEX = r"^s4[gfp]-"


def count_all() -> dict[str, int]:
    return {
        "projects(S4*)"			: Project.all_objects.filter(name__regex=REGEX).count(),
        "assets(S4*)": FileAsset.all_objects.filter(project__name__regex=REGEX).count(),
        "versions(S4*)": FileVersion.all_objects.filter(asset__project__name__regex=REGEX).count(),
        "sessions(S4*)": UploadSession.all_objects.filter(project__name__regex=REGEX).count(),
        "shares(S4*)": FileShareLink.all_objects.filter(asset__project__name__regex=REGEX).count(),
        "share_accesses(S4*)": FileShareAccess.objects.filter(share__asset__project__name__regex=REGEX).count(),
        "users(s4*)": User.objects.filter(email__regex=EMAIL_REGEX).count(),
        "ws_members(s4*)": WorkspaceMember.objects.filter(member__email__regex=EMAIL_REGEX).count(),
    }


def main() -> None:
    dry = "--dry" in sys.argv
    before = count_all()
    print("== 清理前计数 ==")
    for k, v in before.items():
        print(f"  {k}: {v}")
    if dry:
        print("(dry run，不执行删除)")
        return
    deleted_projects = 0
    for p in Project.all_objects.filter(name__regex=REGEX):
        p.delete()  # 实例级 delete = 物理 DELETE（软删重载在 QuerySet 上）；FK CASCADE 连带整树
        deleted_projects += 1
    deleted_users = 0
    # User 硬删走原生 SQL——Django collector 会连带 django_admin_log（本库 PG schema
    # 无此表，坑 1 的 migrate 已知问题），collector 反而 ProgrammingError
    # 注册即建个人工作区（含默认 issue_types 等）——ORM 级联删其自有工作区
    # （DB 侧 FK 无 ON DELETE CASCADE，裸 SQL 不级联；Django collector 负责依赖序）
    from plane.db.models import Workspace
    deleted_ws = 0
    for ws in Workspace.objects.filter(owner__email__regex=EMAIL_REGEX):
        ws.delete()
        deleted_ws += 1
    # 用户侧清理：按 FK 反查逐表处置（纯归属表 DELETE / 共享行置 NULL）——
    # ORM 删用户会连带 django_admin_log（本库 PG schema 无此表，坑 1），故走裸 SQL
    PER_USER_TABLES = {
        "workspace_members", "project_members", "users_groups",
        "users_user_permissions", "notifications", "password_reset_tokens",
        "project_favorites", "workspace_member_invites", "system_admins",
        "issue_assignees",
    }
    with connection.cursor() as cur:
        cur.execute(
            "SELECT cl.relname, a.attname FROM pg_constraint c "
            "JOIN pg_class cl ON cl.oid = c.conrelid "
            "JOIN pg_class f ON f.oid = c.confrelid "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
            "WHERE c.contype='f' AND f.relname='users' AND cl.relname != 'django_admin_log'"
        )
        refs = cur.fetchall()
        for table, col in refs:
            if table in PER_USER_TABLES:
                cur.execute(f'DELETE FROM "{table}" WHERE "{col}" IN '
                            "(SELECT id FROM users WHERE email ~ %s)", [r"^s4[gfp]-"])
            else:
                cur.execute(f'UPDATE "{table}" SET "{col}" = NULL WHERE "{col}" IN '
                            "(SELECT id FROM users WHERE email ~ %s)", [r"^s4[gfp]-"])
        cur.execute("DELETE FROM users WHERE email ~ %s", [r"^s4[gfp]-"])
        deleted_users = cur.rowcount
    print(f"  (附带：s4p 用户自有工作区 {deleted_ws} 个；FK 反查表 {len(refs)} 张已处置)")

    # ── 演示工作区测试成员治理（gate 前置）──
    # 历史迭代（s2/s3/s4f/s4g）注册的测试用户占满 MAX_WORKSPACE_MEMBERS=100 软限，
    # 新注册（注册钩子自动接受邀请）全部 409 RESOURCE_LIMIT_EXCEEDED → e2e 注册流
    # 超时。规则：@rabbit.dev 且不在演示三人组（zhangsan/lisi/wangwu）的成员行移除
    # （用户与其自有工作区保留——只释放演示工作区名额）。
    DEMO_KEEP = {"zhangsan@rabbit.dev", "lisi@rabbit.dev", "wangwu@rabbit.dev"}
    stale = WorkspaceMember.objects.filter(
        workspace__slug="workspace", is_active=True, deleted_at__isnull=True,
    ).exclude(member__email__in=DEMO_KEEP)
    stale_count = stale.count()
    stale_ids = list(stale.values_list("id", flat=True))
    WorkspaceMember.objects.filter(id__in=stale_ids).delete()
    remaining = WorkspaceMember.objects.filter(
        workspace__slug="workspace", is_active=True, deleted_at__isnull=True).count()
    print(f"== 演示工作区测试成员治理：移除 {stale_count} 行（非演示账号），余 {remaining} ==")

    after = count_all()
    print(f"== 已硬删：projects={deleted_projects} users={deleted_users} ==")
    print("== 清理后计数 ==")
    for k, v in after.items():
        print(f"  {k}: {v}")
    leftovers = {k: v for k, v in after.items() if v}
    if leftovers:
        print(f"!! 仍有残留：{leftovers}")
        sys.exit(1)
    print("清理完成：零残留")


if __name__ == "__main__":
    main()
