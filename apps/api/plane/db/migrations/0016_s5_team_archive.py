# Sprint-5 TEAM-003：workspaces +3 列（archived_at/by、default_states）+
# workspace_labels 新表 + labels +2 列（origin/overrides_global_id，存量回填
# origin='local'）+ issue_labels +1 列（name_snapshot）+ 登录命中表。
# PG 落库走 sqlmigrate + 手工 psql + migrate --fake（坑 1）。

import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


def _backfill_label_origin(apps, schema_editor):
    Label = apps.get_model("db", "Label")
    Label.objects.filter(origin="").update(origin="local")
    Label.objects.filter(origin__isnull=True).update(origin="local")


def _noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0015_s5_lifecycle'),
    ]

    operations = [
        migrations.RunPython(_backfill_label_origin, _noop),
        migrations.AddField(
            model_name='issuelabel',
            name='name_snapshot',
            field=models.CharField(blank=True, default='', max_length=50, verbose_name='名字快照'),
        ),
        migrations.AddField(
            model_name='label',
            name='origin',
            field=models.CharField(default='local', max_length=8, verbose_name='来源'),
        ),
        migrations.AddField(
            model_name='label',
            name='overrides_global_id',
            field=models.UUIDField(blank=True, db_index=True, null=True, verbose_name='覆盖的全局标签'),
        ),
        migrations.AddField(
            model_name='workspace',
            name='archived_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='归档时间'),
        ),
        migrations.AddField(
            model_name='workspace',
            name='archived_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='archived_workspaces', to=settings.AUTH_USER_MODEL, verbose_name='归档操作人'),
        ),
        migrations.AddField(
            model_name='workspace',
            name='default_states',
            field=models.JSONField(blank=True, default=list, help_text='{"version": n, "groups": [...]}（§2.3；空 = 未覆盖内置默认）', verbose_name='基础状态模板快照'),
        ),
        migrations.CreateModel(
            name='WorkspaceLabel',
            fields=[
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('deleted_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='删除时间')),
                ('name', models.CharField(max_length=50, verbose_name='标签名')),
                ('color', models.CharField(max_length=7, verbose_name='颜色')),
                ('description', models.CharField(blank=True, default='', max_length=255, verbose_name='描述')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='创建人')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='最后修改人')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='global_labels', to='db.workspace', verbose_name='工作空间')),
            ],
            options={
                'verbose_name': '全局标签',
                'db_table': 'workspace_labels',
                'ordering': ('-created_at',),
                'abstract': False,
                'constraints': [models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('workspace', 'name'), name='uniq_wslabel_ws_name')],
            },
        ),
        migrations.CreateModel(
            name='WorkspaceLoginDailyAggregate',
            fields=[
                ('id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('deleted_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='删除时间')),
                ('hit_date', models.DateField(db_index=True, verbose_name='命中日期')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_created_by', to=settings.AUTH_USER_MODEL, verbose_name='创建人')),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workspace_login_hits', to=settings.AUTH_USER_MODEL, verbose_name='成员')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_updated_by', to=settings.AUTH_USER_MODEL, verbose_name='最后修改人')),
                ('workspace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='login_hits', to='db.workspace', verbose_name='工作空间')),
            ],
            options={
                'verbose_name': '工作空间登录命中',
                'db_table': 'workspace_login_daily',
                'ordering': ('-created_at',),
                'abstract': False,
                'constraints': [models.UniqueConstraint(fields=('workspace', 'member', 'hit_date'), name='uniq_wslogin_day')],
            },
        ),
    ]
