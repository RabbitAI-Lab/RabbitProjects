"""审计事件注册表（AUTH-010 BR-05：category/action 必须 ∈ 注册表）。

新事件须先在此登记（CI 断言全表非空且格式合法）；未注册事件 worker
拒写 + 告警（直接入 DLQ，重试无意义）。
"""
from __future__ import annotations

#: category → {action: 中文描述}
EVENT_REGISTRY: dict[str, dict[str, str]] = {
    "auth": {
        "login_success": "登录成功",
        "login_failure": "登录失败（邮箱）",
        "login_failure_burst": "登录失败爆破聚合",
        "logout": "登出",
    },
    "member": {
        "invited": "邀请成员",
        "removed": "移除成员",
        "role_changed": "成员角色变更",
        "member_assigned": "成员部门归属变更",
    },
    "department": {
        "created": "新建部门",
        "renamed": "部门改名",
        "reordered": "部门排序",
        "moved": "部门移动",
        "deleted": "部门删除",
        "members_moved": "批量调部门",
        "granted": "按部门授权",
        "member_assigned": "挂接部门",
    },
    "role": {
        "create": "新建自定义角色",
        "update": "更新自定义角色",
        "delete": "删除自定义角色",
        "assign": "挂接自定义角色",
        "revoke": "卸除自定义角色",
        "granted": "批量挂接自定义角色",
    },
    "sso": {
        "login": "SSO 登录",
        "claim": "SSO 认领绑定",
        "unbind": "SSO 解绑",
        "email_conflict": "SSO 邮箱冲突",
        "config_updated": "SSO 配置更新",
        "connection_check": "SSO 测试连接",
        "enforce_on": "开启强制 SSO",
        "enforce_off": "关闭强制 SSO",
    },
    "audit": {
        "exported": "审计导出",
        "instance_query": "实例级审计检索（自审计）",
    },
    "workflow": {
        "published": "工作流发布",
        "state_changed": "任务流转",
    },
    "export": {
        "generic": "通用导出",
    },
    "system": {
        "maintenance": "系统维护任务",
    },
}


def validate_registered(category: str, action: str) -> None:
    actions = EVENT_REGISTRY.get(category)
    if actions is None or action not in actions:
        from plane.audit.recorder import UnregisteredEvent

        raise UnregisteredEvent(f"{category}.{action}")
