# Sprint-5 AUTH-006 行级隔离体系化：
# ① users 加 disabled_at / disabled_by（账号启停审计面，is_active 为开关本体）；
# ② projects 加 visibility（private 默认 / public——公开通道待架构回改解锁，
#    落地前 public 行为=private，AUTH-006 §4.1 注 B）。
# PG 落库走 sqlmigrate + 手工 psql + migrate --fake（CLAUDE.md 坑 1）。

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0013_s5_project_activity'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='disabled_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='禁用时间'),
        ),
        migrations.AddField(
            model_name='user',
            name='disabled_by',
            field=models.ForeignKey(null=True, blank=True, on_delete=models.SET_NULL, related_name='disabled_users', to='db.user', verbose_name='禁用操作人'),
        ),
        migrations.AddField(
            model_name='project',
            name='visibility',
            field=models.CharField(blank=False, choices=[('private', '私有'), ('public', '公开')], db_index=True, default='private', max_length=8, verbose_name='可见性'),
        ),
    ]
