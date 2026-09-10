# PROJ-003：内置 3 模板种子（基础/敏捷研发/产品设计）——test_proj003.test_templates_crud
# 断言「迁移种子」而种子此前只存在于 dev 库手工灌数，全新环境必缺
# （2026-09-10 api-ci scratch 库回归发现）。用 RunSQL 而非 RunPython：
# api-db-bootstrap 走 sqlmigrate 配方建库，只有 RunSQL 的 SQL 能进 DDL 流。
# SQL 本体在 sql/0038_builtin_templates.sql（避免迁移文件内嵌超长 JSON 行）。

from pathlib import Path

from django.db import migrations

SQL_FILE = Path(__file__).resolve().parent / "sql" / "0038_builtin_templates.sql"

SEED_SQL = SQL_FILE.read_text()

REVERSE_SQL = """
DELETE FROM project_templates WHERE is_builtin AND workspace_id IS NULL;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0037_project_identifier_active_unique'),
    ]

    operations = [
        migrations.RunSQL(SEED_SQL, REVERSE_SQL),
    ]
