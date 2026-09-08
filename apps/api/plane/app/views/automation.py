"""自动化规则端点（WF-003 §4.5）——CRUD + Dry Run + 设置 PATCH。

CRUD：列表/详情/创建/PATCH/删除 + 启停 + Dry Run + settings 读写。
Dry Run 入口 0 写（BR-11）；启停通过 PATCH is_active。
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.app.permissions import IsAuthenticated, ProjectAdminPermission
from plane.app.views._access import get_project_or_404
from plane.base.exception import AppException
from plane.base.response import created_response, success_response
from plane.db.models import AutomationRule, AutomationSetting, Issue
from plane.workflow.automation_service import (
    dry_run,
    get_settings,
    invalidate_rules_cache,
    invalidate_settings_cache,
    validate_rule_definition,
)


class AutomationRuleListCreateView(APIView):
    """GET（成员可读）/ POST（PROJ_ADMIN）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        rules = list(AutomationRule.objects.filter(
            project=project, deleted_at__isnull=True).order_by("-updated_at"))
        return success_response([{
            "id": str(r.id), "name": r.name, "trigger": r.trigger,
            "conditions": r.conditions, "actions": r.actions,
            "dedup_window_minutes": r.dedup_window_minutes, "is_active": r.is_active,
            "consecutive_failures": r.consecutive_failures,
            "updated_at": r.updated_at,
        } for r in rules])

    def post(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT",
                               message="automation.manage 权限不足（PROJ_ADMIN+）")
        # 项目 ≤ 50（§2.7）
        if AutomationRule.objects.filter(
                project=project, deleted_at__isnull=True).count() >= 50:
            raise AppException("RESOURCE_LIMIT_EXCEEDED",
                               message="自动化规则已达 50 条上限")
        issues = validate_rule_definition(request.data or {})
        if issues:
            raise AppException("VALIDATION_ERROR", details=issues, message="规则配置校验未通过")
        name = (request.data.get("name") or "").strip()
        if not name:
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "name", "code": "REQUIRED"}],
                               message="规则名称必填")
        if AutomationRule.objects.filter(
                project=project, name=name, deleted_at__isnull=True).exists():
            raise AppException("RESOURCE_ALREADY_EXISTS", message="同名规则已存在")
        rule = AutomationRule.objects.create(
            project=project, name=name,
            trigger=request.data["trigger"],
            conditions=request.data.get("conditions") or [],
            actions=request.data["actions"],
            dedup_window_minutes=request.data.get("dedup_window_minutes", 60),
            is_active=bool(request.data.get("is_active", True)),
            created_by=request.user, updated_by=request.user,
        )
        invalidate_rules_cache(project.id)
        return created_response(
            {"id": str(rule.id), "name": rule.name, "is_active": rule.is_active},
            location=f"/api/v1/workspaces/{slug}/projects/{project_id}/automation-rules/{rule.id}/")


class AutomationRuleDetailView(APIView):
    """GET / PATCH（启停 + 改条件/动作）/ DELETE（软删）。"""

    permission_classes = [IsAuthenticated]

    def _get(self, slug, project_id, rule_id, user):
        project, _, _ = get_project_or_404(slug, project_id, user)
        try:
            return project, AutomationRule.objects.get(
                id=rule_id, project=project, deleted_at__isnull=True)
        except AutomationRule.DoesNotExist:
            raise AppException("RESOURCE_NOT_FOUND", message="规则不存在") from None

    def get(self, request, slug, project_id, rule_id):
        _, rule = self._get(slug, project_id, rule_id, request.user)
        return success_response({
            "id": str(rule.id), "name": rule.name, "trigger": rule.trigger,
            "conditions": rule.conditions, "actions": rule.actions,
            "dedup_window_minutes": rule.dedup_window_minutes, "is_active": rule.is_active,
        })

    def patch(self, request, slug, project_id, rule_id):
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT")
        project, rule = self._get(slug, project_id, rule_id, request.user)
        issues = validate_rule_definition(request.data or {})
        if issues:
            raise AppException("VALIDATION_ERROR", details=issues)
        for f in ("trigger", "conditions", "actions", "dedup_window_minutes", "is_active"):
            if f in request.data:
                setattr(rule, f, request.data[f])
        rule.updated_by = request.user
        rule.save()
        invalidate_rules_cache(project.id)
        return success_response({"id": str(rule.id)})

    def delete(self, request, slug, project_id, rule_id):
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT")
        project, rule = self._get(slug, project_id, rule_id, request.user)
        rule.deleted_at = rule.updated_at
        rule.save(update_fields=["deleted_at", "updated_at"])
        invalidate_rules_cache(project.id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AutomationRuleDryRunView(APIView):
    """POST …/automation-rules/{id}/dry-run/ {issue_id}——0 写。"""

    permission_classes = [IsAuthenticated]

    def post(self, request, slug, project_id, rule_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        try:
            rule = AutomationRule.objects.get(
                id=rule_id, project=project, deleted_at__isnull=True)
        except AutomationRule.DoesNotExist:
            raise AppException("RESOURCE_NOT_FOUND", message="规则不存在") from None
        issue_id = (request.data or {}).get("issue_id")
        if not issue_id:
            raise AppException("VALIDATION_ERROR",
                               details=[{"field": "issue_id", "code": "REQUIRED"}])
        try:
            issue = Issue.objects.get(
                id=issue_id, project=project, deleted_at__isnull=True)
        except Issue.DoesNotExist:
            raise AppException("RESOURCE_NOT_FOUND", message="任务不可见") from None
        return success_response(dry_run(rule=rule, issue=issue))


class AutomationSettingView(APIView):
    """GET / PATCH …/automation-settings/ —— allow_rule_chain 单字段（BR-07）。"""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug, project_id):
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        s = get_settings(project.id)
        return success_response({
            "allow_rule_chain": bool(s and s.allow_rule_chain)})

    def patch(self, request, slug, project_id):
        if not ProjectAdminPermission().has_permission(request, self):
            raise AppException("PERM_ROLE_INSUFFICIENT")
        project, _, _ = get_project_or_404(slug, project_id, request.user)
        s, _ = AutomationSetting.objects.get_or_create(project=project)
        s.allow_rule_chain = bool(request.data.get("allow_rule_chain", False))
        s.updated_by = request.user
        s.save()
        invalidate_settings_cache(project.id)
        return success_response({"allow_rule_chain": s.allow_rule_chain})
