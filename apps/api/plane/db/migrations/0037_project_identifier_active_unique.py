# BR-14 收口：uniq_project_identifier_per_workspace 补 status='active' 条件。
# 规格（WF/PROJ 生命周期 §2.2 + project_lifecycle._guard_activate）明确：
# identifier 占用只看 active 项目；draft 期间与他人 active 项目同号是合法中间态，
# 激活时由应用层复检挡回 409。迁移版无条件唯一约束比规格更严——draft 直接改名
# 即 IntegrityError（2026-09-10 api-ci scratch 库回归 test_draft_activate_guards 发现；
# dev 库因手工建库缺该索引而长期未暴露）。

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0036_s9_health'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='project',
            name='uniq_project_identifier_per_workspace',
        ),
        migrations.AddConstraint(
            model_name='project',
            constraint=models.UniqueConstraint(
                fields=['workspace', 'identifier'],
                condition=models.Q(deleted_at__isnull=True, status='active'),
                name='uniq_project_identifier_per_workspace',
            ),
        ),
    ]
