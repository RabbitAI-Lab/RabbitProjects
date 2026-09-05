"""``/users/me/permissions/`` 响应序列化 —— AUTH-005 §4.2 契约。

本序列化器只承担**响应**塑形（DRF ``Response.data`` 入参）；请求侧无 body。
逻辑层（inherited 计算 + 截断判定）落在视图层，本文件保持纯结构，避免把
DB 查询散落到序列化器里（sprint-0 的设计约定：序列化器只接收已组装数据）。

字段语义（与 §4.2 字段说明一一对应）：
  - ``is_system_admin`` bool：SystemAdmin(user=me, is_active=True) 存在性
  - ``workspaces`` map<id, {slug, role}>：全部 active 工作空间成员身份
  - ``projects`` map<id, {workspace_id, role, inherited}>：
      * inherited=true 表示该用户**无显式** ProjectMember 行，但因
        workspace 角色 ≥ ADMIN 而具隐式 PROJ_ADMIN（rbac §4.1 / BR-08）
      * inherited 仅作展示提示，**不参与**判定数值 —— 判定统一走
        「有效角色 = max(显式项目角色, 工作空间推导)」（§4.5 PermissionStore）
"""
from __future__ import annotations

from rest_framework import serializers


class WorkspacePermissionEntrySerializer(serializers.Serializer):
    """``workspaces[id]`` 的单条目。"""

    slug = serializers.SlugField()
    role = serializers.IntegerField(min_value=0)


class ProjectPermissionEntrySerializer(serializers.Serializer):
    """``projects[id]`` 的单条目（显式 + 隐式 WS_ADMIN 提升共用结构）。"""

    workspace_id = serializers.UUIDField()
    role = serializers.IntegerField(min_value=0)
    inherited = serializers.BooleanField()


class PermissionsPayloadSerializer(serializers.Serializer):
    """``data`` 字段整体塑形。"""

    is_system_admin = serializers.BooleanField()
    workspaces = serializers.DictField(child=WorkspacePermissionEntrySerializer())
    projects = serializers.DictField(child=ProjectPermissionEntrySerializer())


class PermissionsMetaSerializer(serializers.Serializer):
    """``meta`` 字段：实时性元数据 + 截断标记。"""

    generated_at = serializers.DateTimeField()
    truncated = serializers.BooleanField()
