"""RunSQL：Slack 订阅 COALESCE 表达式唯一索引（INTG-003 §4.1.1）。

普通 UniqueConstraint 对可空 project 行（null=全项目）不去重（PG NULL
互异）——同频道「全项目订阅」可重复插入致同事件重复投递（BR-04 合并
失效）；表达式索引 COALESCE(project_id, 零值 UUID) 统一去重，WHERE
deleted_at IS NULL 使软删行让位可重建（INTG-001/FILE-002 同款先例）。
"""

from django.db import migrations

SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_slack_sub_channel_uniq
    ON integration_slack_subscriptions (installation_id, channel_id,
        COALESCE(project_id, '00000000-0000-0000-0000-000000000000'::uuid))
    WHERE deleted_at IS NULL;
"""


class Migration(migrations.Migration):
    dependencies = [("db", "0044_p4_intg003_slack_zoom")]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql="DROP INDEX IF EXISTS uq_slack_sub_channel_uniq;"),
    ]
