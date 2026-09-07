"""GitHub 入站事件 Worker（INTG-001 §2.2/§4.3——Sprint-5 T5-04）。

规范化事件路由：
  issues.opened → 建任务（BR-05）+ 回写标题前缀 [RBT-n]
  issues.edited / closed / reopened → 字段 diff 更新（后写胜出 BR-07）
  issue_comment.created → 评论入站（BR-06 第 4 类字段，系统账号代发）
  pull_request.closed ∧ merged → 合并自动流转（走 TASK-005 守卫，BR-10 无旁路）
  push → Commit 挂载（RBT-数字 正则提取 + sha×任务 去重，BR-11）
出站（本系统 → GitHub）：任务评论 → Issue 评论（sync_comment_outbound，
事务后投递；系统账号代发的入站评论不回投——防环）

系统账号：``rp-integration``（懒创建，BR-15——Activity ⚙ 来源区分）；
写路径全部走既有服务/守卫，绝不旁路直改 Issue。
"""
from __future__ import annotations

import logging
import re

from celery import shared_task
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger("plane.bgtasks.github_sync")

SYSTEM_ACCOUNT_EMAIL = "rp-integration@system.local"
_KEY_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{1,11}-\d+)")


def system_account():
    """系统账号（懒创建）——集成写路径的统一 actor（BR-15）。"""
    from plane.db.models import User

    account = User.objects.filter(email=SYSTEM_ACCOUNT_EMAIL).first()
    if account is None:
        account = User.objects.create_user(
            email=SYSTEM_ACCOUNT_EMAIL, password="!", display_name="GitHub 集成")
        account.is_active = True
        account.save(update_fields=["is_active"])
    return account


@shared_task(bind=True, max_retries=3, retry_backoff=True)
def dispatch_github_event(self, event: dict) -> str:
    """event = 规范化入站事件（视图层已验签/查重）。

    返回处置摘要（'created' / 'updated' / 'dropped' / 'transitioned' …）。"""
    try:
        return _route(event)
    except Exception as exc:  # noqa: BLE001
        logger.warning("github_sync.dispatch_failed event=%s exc=%s",
                       event.get("kind"), exc)
        raise self.retry(exc=exc) from exc


def _route(event: dict) -> str:
    kind = event.get("kind", "")
    handler = {
        "issue.opened": _on_issue_opened,
        "issue.edited": _on_issue_edited,
        "issue.closed": _on_issue_closed,
        "issue.reopened": _on_issue_reopened,
        "comment.created": _on_comment_created,
        "pull_request.merged": _on_pr_merged,
        "push": _on_push,
    }.get(kind)
    if handler is None:
        logger.info("github_sync.unhandled kind=%s", kind)
        return "unhandled"
    return handler(event)


def _binding(event: dict):
    from plane.db.models import IntegrationInstallation

    return IntegrationInstallation.objects.filter(
        installation_id=event["installation_id"],
        repository_full_name=event["repository_full_name"],
        deleted_at__isnull=True,
        sync_status="syncing",
    ).first()


def _locate_issue(event: dict):
    """(github, node_id) 幂等锚定位（BR-04）。"""
    from plane.db.models import Issue

    node_id = event.get("issue", {}).get("node_id")
    if not node_id:
        return None
    return Issue.objects.filter(
        external_source="github", external_id=node_id,
        deleted_at__isnull=True).first()


def _on_issue_opened(event: dict) -> str:
    binding = _binding(event)
    if binding is None:
        return "dropped"
    gh = event["issue"]
    if _locate_issue(event) is not None:
        return _on_issue_edited(event)
    from plane.db.models import Issue, Project, State
    from plane.db.seeds.issue_types import seed_issue_types  # noqa: F401 —— 类型缺省路径

    default_state = State.objects.filter(
        project=binding.project, is_default=True).first() or State.objects.filter(
        project=binding.project).order_by("sort_order").first()
    if default_state is None:
        logger.warning("github_sync.no_state project=%s", binding.project_id)
        return "dropped"
    seq = Project.objects.filter(pk=binding.project_id).first()
    next_seq = (seq.issues.count() if False else
                _next_sequence(binding.project_id)) + 1
    title = gh.get("title") or "(untitled)"
    with transaction.atomic():
        issue = Issue.objects.create(
            project=binding.project,
            name=title[:255],
            description_html=_html(gh.get("body") or ""),
            description_stripped=(gh.get("body") or "")[:2000],
            state=default_state,
            issue_type=binding.default_issue_type,
            priority="none",
            sequence_id=next_seq,
            sort_order=next_seq * 100,
            created_by=system_account(),
            updated_by=system_account(),
            external_source="github",
            external_id=gh.get("node_id"),
            github_context={"number": gh.get("number"),
                            "url": gh.get("html_url"), "prs": [], "commits": []},
        )
    # 回写标题前缀 [RBT-n]（出站，失败仅日志——不阻断入站落库）
    _writeback_title(binding, issue, gh)
    _touch_binding(binding)
    return f"created:{issue.id}"


def _next_sequence(project_id) -> int:
    from django.db.models import Max

    from plane.db.models import Issue

    return Issue.objects.filter(
        project_id=project_id).aggregate(m=Max("sequence_id"))["m"] or 0


def _writeback_title(binding, issue, gh_issue: dict) -> None:
    from plane.db.models import Project

    project = Project.objects.filter(pk=binding.project_id).only("identifier").first()
    prefix = f"[{project.identifier}-{issue.sequence_id}]"
    if (gh_issue.get("title") or "").startswith(prefix):
        return
    try:
        from plane.integrations.github import GitHubClient

        client = GitHubClient(binding.installation_id)
        client.edit_issue_title(
            binding.repository_full_name, gh_issue["number"],
            f"{prefix} {gh_issue.get('title')}",
            token_cache=binding.token_cache)
        binding.token_cache = client.last_token_cache
        binding.save(update_fields=["token_cache", "updated_at"])
    except Exception as exc:  # noqa: BLE001 —— 出站尽力而为（BR-14 队列暂停兜底）
        logger.warning("github_sync.writeback_failed issue=%s err=%s", issue.id, exc)


def _on_issue_edited(event: dict) -> str:
    issue = _locate_issue(event)
    if issue is None:
        logger.warning("github_sync.miss_anchor kind=edited delivery=%s",
                       event.get("delivery_id"))
        return "dropped"
    binding = _binding(event)
    gh = event["issue"]
    # 后写胜出（BR-07）：入站 updated_at 较新才覆盖
    gh_updated = _parse_dt(gh.get("updated_at"))
    if gh_updated and issue.updated_at and issue.updated_at > gh_updated:
        _log_conflict(binding, issue, "title", "system",
                      {"title": issue.name}, {"title": gh.get("title")},
                      event.get("delivery_id", ""))
        return "conflict-held"
    changed = []
    if gh.get("title") and gh["title"] != issue.name:
        issue.name = gh["title"][:255]
        changed.append("name")
    body_html = _html(gh.get("body") or "")
    if body_html != issue.description_html:
        issue.description_html = body_html
        issue.description_stripped = (gh.get("body") or "")[:2000]
        changed += ["description_html", "description_stripped"]
    if changed:
        issue.updated_by = system_account()
        issue.save(update_fields=[*changed, "updated_by", "updated_at"])
    if binding:
        _touch_binding(binding)
    return f"updated:{','.join(changed) or 'noop'}"


def _on_issue_closed(event: dict) -> str:
    return _transition_by_github_state(event, closed=True)


def _on_issue_reopened(event: dict) -> str:
    return _transition_by_github_state(event, closed=False)


def _on_comment_created(event: dict) -> str:
    """GitHub Issue 评论 → 站内评论（BR-06 白名单第 4 类；actor=系统账号代发）。

    幂等由视图层 Delivery SETNX 承担；未映射任务（issue 无 node_id 锚）静默丢弃。
    """
    issue = _locate_issue(event)
    if issue is None:
        return "dropped"
    gh_comment = event.get("comment") or {}
    body = (gh_comment.get("body") or "").strip()
    if not body:
        return "dropped"
    from plane.db.models import IssueComment

    sender = event.get("sender") or "github"
    IssueComment.objects.create(
        issue=issue,
        actor=system_account(),
        comment_html=_html(f"@{sender}（GitHub）：\n{body}"),
        comment_json={},
    )
    return "commented"


@shared_task(bind=True, max_retries=3, retry_backoff=True)
def sync_comment_outbound(self, comment_id: str) -> str:
    """任务评论 → GitHub Issue 评论（BR-06；挂点 CommentService 事务后）。

    防环：系统账号代发的评论（即入站镜像）不回投。
    """
    from plane.db.models import IssueComment

    comment = (IssueComment.objects
               .filter(pk=comment_id, deleted_at__isnull=True)
               .select_related("issue", "actor").first())
    if comment is None:
        return "dropped"
    issue = comment.issue
    if issue.external_source != "github" or not comment.actor:
        return "skipped"
    if comment.actor.email == SYSTEM_ACCOUNT_EMAIL:
        return "echo-skipped"
    ctx = issue.github_context or {}
    number = ctx.get("number")
    if not number:
        return "skipped"
    binding = _binding_for_issue(issue)
    if binding is None:
        return "no-binding"
    from plane.db.models import Project
    from plane.integrations.github import GitHubApiError, GitHubClient

    identifier = (Project.objects.filter(pk=issue.project_id)
                  .values_list("identifier", flat=True).first() or "?")
    client = GitHubClient(binding.installation_id)
    try:
        client.create_issue_comment(
            binding.repository_full_name, int(number),
            f"{comment.actor.display_name}（RabbitProjects {identifier}-{issue.sequence_id}）：\n"
            f"{comment.comment_stripped}",
            token_cache=binding.token_cache)
    except GitHubApiError:
        binding.token_cache = client.last_token_cache or binding.token_cache
        binding.save(update_fields=["token_cache", "updated_at"])
        raise  # 让 celery 退避重试
    binding.token_cache = client.last_token_cache or binding.token_cache
    binding.save(update_fields=["token_cache", "updated_at"])
    return "pushed"


def _binding_for_issue(issue):
    """任务 → 绑定：github_context.url 里的仓库名精确匹配，退而取项目首个 syncing 绑定。"""
    from plane.db.models import IntegrationInstallation

    qs = IntegrationInstallation.objects.filter(
        project_id=issue.project_id, deleted_at__isnull=True, sync_status="syncing")
    url = str((issue.github_context or {}).get("url") or "").lower()
    for b in qs:
        if b.repository_full_name and b.repository_full_name in url:
            return b
    return qs.first()


def _transition_by_github_state(event: dict, *, closed: bool) -> str:
    """GitHub 开↔关映射到任务完成组↔非完成组——走流转守卫（无旁路）。"""
    issue = _locate_issue(event)
    if issue is None:
        return "dropped"
    from plane.db.models import State

    target_group = "completed" if closed else "started"
    current = issue.state.group if issue.state else None
    done_groups = ("completed", "cancelled")
    already = (current in done_groups) if closed else (current not in done_groups)
    if already:
        return "noop"
    target_state = State.objects.filter(
        project_id=issue.project_id, group=target_group).order_by("sort_order").first()
    if target_state is None:
        return "no-target-state"
    actor = system_account()
    # 回声抑制（BR-09）：本系统触发的关闭（github_context.echo）跳过
    ctx = dict(issue.github_context or {})
    if closed and ctx.get("echo_close"):
        ctx["echo_close"] = False
        issue.github_context = ctx
        issue.save(update_fields=["github_context"])
        return "echo-suppressed"
    ok, reason = _guarded_transition(issue, target_state, actor,
                                     comment=f"GitHub Issue 同步自动{'关闭' if closed else '重开'}")
    if binding := _binding(event):
        _touch_binding(binding)
    return "transitioned" if ok else f"blocked:{reason}"


def _guarded_transition(issue, target_state, actor, *, comment: str) -> tuple[bool, str]:
    """TASK-005 守卫族（依赖拦截 assert_completable）；失败降级系统评论（BR-10）。"""
    from plane.bgtasks.issue_activity import enqueue_activity
    from plane.db.services.issue_transition_guard import (
        TransitionBlockedError,
        assert_completable,
    )

    try:
        assert_completable(issue=issue, to_state=target_state, force=False, is_admin=False)
    except TransitionBlockedError as blocked:
        keys = "、".join(b["issue_key"] for b in blocked.blockers)
        _post_system_comment(
            issue, f"GitHub 同步：存在未完成前置 {keys}，请人工确认后完成")
        return False, f"blocked:{keys}"
    old_group = issue.state.group if issue.state else None
    issue.state = target_state
    issue.updated_by = actor
    if target_state.group == "completed" and issue.completed_at is None:
        issue.completed_at = timezone.now()
    issue.save(update_fields=["state", "updated_by", "completed_at", "updated_at"])
    epoch = timezone.now().timestamp() * 1000.0
    enqueue_activity(
        issue_id=issue.id, actor_id=actor.id, verb="updated", epoch=epoch,
        before={"state": old_group}, after={"state": target_state.group},
        comment=comment)
    return True, "ok"


def _on_pr_merged(event: dict) -> str:
    """合并自动流转（§2.3）：PR 正文/标题提取 RBT-123 → 守卫流转完成。"""
    binding = _binding(event)
    if binding is None:
        return "dropped"
    pr = event.get("pull_request", {})
    if not pr.get("merged"):
        return "not-merged"
    text = f"{pr.get('title') or ''}\n{pr.get('body') or ''}"
    keys = set(_KEY_RE.findall(text))
    from plane.db.models import Issue, Project, State

    project = Project.objects.filter(pk=binding.project_id).only("identifier").first()
    matched = []
    for key in keys:
        if not key.startswith(f"{project.identifier}-"):
            continue
        seq = int(key.rsplit("-", 1)[1])
        issue = Issue.objects.filter(
            project_id=binding.project_id, sequence_id=seq,
            deleted_at__isnull=True).first()
        if issue:
            matched.append(issue)
    if not matched:
        logger.info("github_sync.merge_no_match pr=%s", pr.get("number"))
        return "no-match"
    done_state = State.objects.filter(
        project_id=binding.project_id, group="completed").order_by("sort_order").first()
    if done_state is None:
        return "no-target-state"
    actor = system_account()
    results = []
    for issue in matched:
        ctx = dict(issue.github_context or {})
        ctx.setdefault("prs", []).append({
            "number": pr.get("number"), "url": pr.get("html_url"),
            "title": pr.get("title"), "merged_at": pr.get("merged_at"),
        })
        issue.github_context = ctx
        issue.save(update_fields=["github_context"])
        if issue.state and issue.state.group in ("completed", "cancelled"):
            results.append(f"{issue.id}:noop")
            continue
        ok, reason = _guarded_transition(
            issue, done_state, actor,
            comment=f"⚙ GitHub 由 PR #{pr.get('number')} 合并 自动完成")
        if not ok:
            _notify_assignees(issue, pr.get("number"))
        results.append(f"{issue.id}:{'done' if ok else reason}")
    _touch_binding(binding)
    return ";".join(results)


def _on_push(event: dict) -> str:
    """Commit 挂载（BR-11）：push commits 的 message 提取 RBT-123 → 校验属
    当前绑定项目 → github_context.commits 追加（sha×任务 去重）。"""
    binding = _binding(event)
    if binding is None:
        return "dropped"
    from plane.db.models import Issue, Project

    project = Project.objects.filter(pk=binding.project_id).only("identifier").first()
    prefix = f"{project.identifier}-"
    matched: dict[str, list[dict]] = {}
    for commit in event.get("commits", []):
        message = commit.get("message") or ""
        for key in set(_KEY_RE.findall(message)):
            if not key.startswith(prefix):
                continue
            seq = int(key.rsplit("-", 1)[1])
            issue = Issue.objects.filter(
                project_id=binding.project_id, sequence_id=seq,
                deleted_at__isnull=True).first()
            if issue:
                matched.setdefault(str(issue.id), []).append({
                    "sha": commit.get("id", "")[:12],
                    "message": message.splitlines()[0][:120],
                    "url": commit.get("url"),
                    "author": (commit.get("author") or {}).get("name"),
                })
    mounted = 0
    for iid, commits in matched.items():
        issue = Issue.objects.get(pk=iid)
        ctx = dict(issue.github_context or {})
        existing = {(c.get("sha"), c.get("message"))
                    for c in ctx.get("commits", [])}
        fresh = [c for c in commits if (c["sha"], c["message"]) not in existing]
        if fresh:
            ctx.setdefault("commits", []).extend(fresh)
            issue.github_context = ctx
            issue.updated_by = system_account()
            issue.save(update_fields=["github_context", "updated_by", "updated_at"])
            mounted += len(fresh)
    _touch_binding(binding)
    return f"mounted:{mounted}"


def _post_system_comment(issue, text: str) -> None:
    """被阻塞流转的降级系统评论（BR-10）。"""
    from plane.db.models import IssueComment

    IssueComment.objects.create(
        issue=issue, actor=system_account(),
        comment_html=f"<p>{text}</p>", comment_json={}, accessory={},
        created_by=system_account(), updated_by=system_account())


def _notify_assignees(issue, pr_number) -> None:
    from plane.bgtasks.notifications import send_workspace_notification
    from plane.db.models import IssueAssignee

    for uid in IssueAssignee.objects.filter(
            issue=issue).values_list("assignee_id", flat=True):
        send_workspace_notification.delay(
            receiver_id=str(uid), event="integration.merge_blocked",
            context={"issue_id": str(issue.id), "pr_number": pr_number},
            title=f"PR #{pr_number} 已合并，但任务存在未完成前置，请人工确认")


def _log_conflict(binding, issue, scope, winner_side, winner, loser, delivery_id) -> None:
    from plane.db.models import SyncConflictLog

    if binding is None:
        return
    SyncConflictLog.objects.create(
        binding=binding, issue=issue, scope=scope, direction="inbound",
        winner_side=winner_side, winner_payload=winner, loser_payload=loser,
        delivery_id=delivery_id or "",
        occurred_at=timezone.now())


def _touch_binding(binding) -> None:
    binding.last_synced_at = timezone.now()
    binding.save(update_fields=["last_synced_at", "updated_at"])


def _html(markdown_body: str) -> str:
    """GitHub body（Markdown）→ 站内 HTML（P2 简易换行转换；富转换归前端渲染层）。"""
    import html as html_mod

    escaped = html_mod.escape(markdown_body)
    return "<p>" + escaped.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"


def _parse_dt(value):
    from datetime import datetime

    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
