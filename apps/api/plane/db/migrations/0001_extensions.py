"""PG 扩展（INFRA-003 §4.10）：pg_trgm + btree_gin。
必须在 0002 创建 GIN 索引之前独立迁移；放在一起时 Django migration 操作排序不保证扩展先建。
"""
from django.contrib.postgres.operations import TrigramExtension, BtreeGinExtension
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = []

    operations = [
        BtreeGinExtension(),
        TrigramExtension(),
    ]
