"""RunSQL：IM 订阅 COALESCE 表达式唯一索引（INTG-005 BR-01——0045 同款范式）。"""
from django.db import migrations

SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_im_sub_scope_uniq
    ON integration_im_subscriptions (channel_id,
        COALESCE(project_id, '00000000-0000-0000-0000-000000000000'::uuid))
    WHERE deleted_at IS NULL;
"""


class Migration(migrations.Migration):

    dependencies = [("db", "0046_p4_intg005_wecom_dingtalk")]

    operations = [
        migrations.RunSQL(sql=SQL,
                          reverse_sql="DROP INDEX IF EXISTS uq_im_sub_scope_uniq;"),
    ]
