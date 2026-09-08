"""字段级权限判定服务（TASK-012 §4.4）——四方共用的单一入口。

四态 access（rbac §11.2）：editable / readonly / hidden / required。
三集合白名单语义：空集合 = 全员（P2 默认零回归）；ADMIN 豁免 hidden 不豁免
readonly（§2.2 优先级）。两级缓存：定义缓存承 TASK-008 不变；access 判定
维度化（项目存在 user: 授权时旁路角色共享键，键补 user 维度防串数据）。
"""
from __future__ import annotations

from django.core.cache import cache

from plane.db.models import SystemAdmin, WorkspaceMember
from plane.db.models.roles import ProjectRole, WorkspaceRole

#: 有效项目角色整数 → 矩阵角色码（小写形；仅 proj_* 四项——两族 IntEnum
#: 数值区间重叠，混作字典键会同值互撞覆盖（TASK-012 §4.4 注））
ROLE_CODE_BY_LEVEL: dict[int, str] = {
    int(ProjectRole.ADMIN): "proj_admin",
    int(ProjectRole.CONTRIBUTOR): "proj_contributor",
    int(ProjectRole.COMMENTER): "proj_commenter",
    int(ProjectRole.VIEWER): "proj_viewer",
}

ACCESS_TTL = 60  # 秒；permission_config 变更走信号失效（BR-09）


def effective_project_role(actor, project) -> int:
    """rbac §7.4：SYSTEM_ADMIN / WS_ADMIN+ → 隐式 PROJ_ADMIN（与守卫域同口径）。"""
    from plane.db.models import ProjectMember

    if SystemAdmin.objects.filter(user=actor, is_active=True).exists():
        return ProjectRole.ADMIN
    pm = ProjectMember.objects.filter(
        project=project, member=actor, is_active=True).values_list("role", flat=True).first()
    if pm is not None:
        return pm
    role = WorkspaceMember.objects.filter(
        workspace_id=project.workspace_id, member=actor, is_active=True
    ).values_list("role", flat=True).first()
    if role is not None and role >= WorkspaceRole.ADMIN:
        return ProjectRole.ADMIN
    return ProjectRole.VIEWER  # 非成员兜底（视图层 accessible 过滤先行，此处仅防炸）


class FieldPermissionService:
    """字段级权限唯一判定入口——Schema 标注 / Serializer 读写 / FilterCompiler /
    导出四方共用（BR-10/11/12/15/16）。"""

    def resolve(self, actor, project, definitions) -> dict[str, str]:
        """三集合白名单 → 四态判定。返回 {field_key: access}；请求级缓存
        request.field_access 由视图层挂载。"""
        role = effective_project_role(actor, project)
        me_role = f"role:{ROLE_CODE_BY_LEVEL.get(int(role), 'proj_viewer')}"
        me_user = f"user:{actor.id}"
        result: dict[str, str] = {}
        for d in definitions:
            cfg = d.permission_config or {}
            read = set(cfg.get("read") or [])
            write = set(cfg.get("write") or [])
            can_read = not read or me_role in read or me_user in read
            can_write = not write or me_role in write or me_user in write
            if not can_read:
                access = "hidden"
            elif not can_write:
                access = "readonly"
            elif me_role in set(cfg.get("required_for") or []):
                access = "required"
            else:
                access = "editable"
            if int(role) >= int(ProjectRole.ADMIN) and access == "hidden":
                access = "editable"  # §2.2：ADMIN 豁免 hidden；readonly 不豁免
            result[d.field_key] = access
        return result

    def cached_resolve(self, request, actor, project, definitions) -> dict[str, str]:
        """请求级缓存（request.field_access）+ Redis 角色维度键（§4.4 两级缓存）。

        项目内任一字段含 user: 授权时旁路共享键（防同角色不同用户串数据）。
        """
        cached = getattr(request, "field_access", None)
        if cached is not None:
            return cached
        defs = list(definitions)
        has_user_grant = any(
            any(str(m).startswith("user:") for m in (d.permission_config or {}).get(k, []) or [])
            for d in defs for k in ("read", "write"))
        role_code = ROLE_CODE_BY_LEVEL.get(
            int(effective_project_role(actor, project)), "proj_viewer")
        key = (f"field_access:v1:{project.workspace_id}:{project.id}:{role_code}"
               + (f":{actor.id}" if has_user_grant else ""))
        access_map = cache.get(key)
        if access_map is None:
            access_map = self.resolve(actor, project, defs)
            cache.set(key, access_map, ACCESS_TTL)
        request.field_access = access_map
        return access_map

    @staticmethod
    def apply_to_payload(data: dict, access_map: dict[str, str]) -> dict:
        """序列化剔除：hidden 键从 custom_fields 中移除（列表/详情/导出共用）。"""
        cf = data.get("custom_fields")
        if cf:
            data["custom_fields"] = {k: v for k, v in cf.items()
                                     if access_map.get(k) != "hidden"}
        return data

    @staticmethod
    def drop_non_writable(patch_cf: dict, access_map: dict[str, str]) -> list[str]:
        """写入路径：readonly/hidden 键静默丢弃（rbac §11.2，不 403），返回被丢弃
        键供 meta.warning.dropped_fields 回显（BR-16）。"""
        dropped = [k for k in patch_cf if access_map.get(k) in ("readonly", "hidden")]
        for k in dropped:
            del patch_cf[k]
        return dropped

    @staticmethod
    def required_missing(definitions, access_map: dict[str, str], payload_cf: dict) -> list[str]:
        """按角色必填（BR-15）：required_for 命中且可写的字段缺值 → 缺失键清单。"""
        missing = []
        for d in definitions:
            if access_map.get(d.field_key) != "required":
                continue
            if d.field_key not in (payload_cf or {}):
                missing.append(d.field_key)
        return missing
