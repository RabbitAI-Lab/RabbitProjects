# Sprint-5 管道扩域（PROJ-003 §4.1 迁移要点 ⑤ + ADR-0022 D-2 收口）。
# DDL 清单：① issue_activities 新增可空 project FK（project 域行承载）；
# ② XOR CHECK 约束（issue_id / project_id 二选一）；③ 条件偏索引
# idx_activity_project_time（project, created_at DESC）WHERE issue_id IS NULL，
# 服务 COLLAB-003 _STREAM_VIEW 的 project 域 UNION ALL 子查询。
# 不做存量回填（规格「按 issue.project_id 投影回填」与 XOR 约束互斥且冗余，
# 见 IssueActivity 模型注）。PG 落库走 sqlmigrate + 手工 psql + migrate --fake
# （CLAUDE.md 坑 1）。

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0012_p2_gantt_viewport'),
    ]

    operations = [
        migrations.AddField(
            model_name='issueactivity',
            name='project',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.CASCADE, related_name='project_activities', to='db.project', verbose_name='项目（project 域行）'),
        ),
        migrations.AddConstraint(
            model_name='issueactivity',
            constraint=models.CheckConstraint(condition=models.Q(('issue__isnull', True)) ^ models.Q(('project__isnull', True)), name='chk_activity_issue_project_xor'),
        ),
        migrations.AddIndex(
            model_name='issueactivity',
            index=models.Index(condition=models.Q(('issue__isnull', True)), fields=['project', '-created_at'], name='idx_activity_project_time'),
        ),
    ]
