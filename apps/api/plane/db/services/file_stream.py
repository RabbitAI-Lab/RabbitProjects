"""file 域动态流事件（ADR-0022 D-2 管道扩域收口，Sprint-5 T5-02）。

FILE-002 BR-12（上传/重命名/移动/删除/恢复）、FILE-003 BR-13（新版本/回滚）、
FILE-004 BR-13（创建/吊销/延期——内部视角；匿名访问不入）的**动态流半边**：
经 ``enqueue_project_activity`` 落 ``issue_activities`` 的 project 域行
（issue_id IS NULL + project_id 键，XOR 双轨），由 COLLAB-003 ``_STREAM_VIEW``
的 project 域 UNION ALL 分支上流。FILE-002 BR-12 原文「不入 IssueActivity」
指不入任务域（issue_id 键）——PROJ-003 §4.3.1 已将该表泛化为双轨承载，
语义按「非任务域」读（Sprint-5 ADR 登记）。

调用约定：一律在 ``transaction.on_commit`` 回调中调用（与 WS 半边
``publish_share_event`` 等同点同规——匿名访问路径不投递，防刷屏）。
"""
from __future__ import annotations

import uuid

#: action → (verb, field)。verb 限模型三值（created/updated/deleted），
#: file 语义全部由 field 列承载——与 WS 半边事件名（EVENT_MAP 的
#: file.version.created / file.share.* 等）同词表，便于跨端对读。
_FILE_ACTIONS: dict[str, tuple[str, str]] = {
    "uploaded": ("created", "file.uploaded"),
    "renamed": ("updated", "file.renamed"),
    "moved": ("updated", "file.moved"),
    "deleted": ("deleted", "file.deleted"),
    "restored": ("updated", "file.restored"),
    "version_created": ("created", "file.version.created"),
    "version_rolled_back": ("updated", "file.version.rolled_back"),
    "share_created": ("created", "file.share.created"),
    "share_revoked": ("deleted", "file.share.revoked"),
    "share_extended": ("updated", "file.share.extended"),
}


def emit_file_activity(
    *,
    project_id: uuid.UUID | None,
    asset_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    action: str,
    comment: str,
    old_value: str | None = None,
    new_value: str | None = None,
) -> None:
    from plane.bgtasks.project_activity import enqueue_project_activity

    if project_id is None or asset_id is None:
        return  # 防御：多态域行（非文件库归属）不投项目动态
    try:
        verb, field = _FILE_ACTIONS[action]
    except KeyError:
        raise ValueError(f"未知 file 域动作：{action}") from None
    enqueue_project_activity(
        project_id=project_id, actor_id=actor_id, verb=verb, field=field,
        old_value=old_value, new_value=new_value,
        new_identifier=asset_id, comment=comment,
    )
