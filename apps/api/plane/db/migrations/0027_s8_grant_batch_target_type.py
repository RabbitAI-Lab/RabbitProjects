# AUTH-008 §4.1 迁移要点：department_grant_batch 增 target_type 判别列
# （"project_membership" | "role"）——按部门挂角色复用 AUTH-007 批次表溯源。
# role 列（IntegerField）对 target_type="role" 的批次不使用，无需改容。
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0026_s8_custom_roles'),
    ]

    operations = [
        migrations.AddField(
            model_name='departmentgrantbatch',
            name='target_type',
            field=models.CharField(
                choices=[('project_membership', '项目成员'), ('role', '自定义角色')],
                default='project_membership', max_length=24,
            ),
        ),
    ]
