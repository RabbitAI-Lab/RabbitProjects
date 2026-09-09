# AUTH-010 §4.1：audit_log 月分区表（Django 原生不支持分区 DDL，经 RunSQL 管理）。
# PRIMARY KEY 必须含分区键 (id, created_at)；UNIQUE (event_key, created_at) 仅承担
# 同分区微秒兜底 + 前缀查询索引——跨分区全局幂等由应用层三层去重保证（BR-06）。
# DEFAULT 分区承接建分区失败窗口的写入（§2.4）。
from django.db import migrations

DDL = """
CREATE TABLE audit_log (
    id              uuid        NOT NULL DEFAULT gen_random_uuid(),
    event_key       varchar(80) NOT NULL,
    workspace_id    uuid        NULL,
    category        varchar(24) NOT NULL,
    action          varchar(48) NOT NULL,
    actor_id        varchar(64) NULL,
    actor_snapshot  jsonb       NOT NULL DEFAULT '{}'::jsonb,
    object_type     varchar(32) NULL,
    object_id       varchar(64) NULL,
    object_snapshot jsonb       NOT NULL DEFAULT '{}'::jsonb,
    detail          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    ip              inet        NULL,
    user_agent      varchar(255) NOT NULL DEFAULT '',
    prev_hash       varchar(64) NOT NULL,
    hash            varchar(64) NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at),
    UNIQUE (event_key, created_at)
) PARTITION BY RANGE (created_at);
CREATE TABLE audit_log_default PARTITION OF audit_log DEFAULT;
CREATE INDEX idx_audit_scan ON audit_log (workspace_id, created_at DESC, id DESC);
CREATE INDEX idx_audit_event ON audit_log (workspace_id, category, action, created_at DESC);
CREATE INDEX idx_audit_actor ON audit_log (workspace_id, actor_id, created_at DESC);
CREATE INDEX idx_audit_object_type ON audit_log (workspace_id, object_type, created_at DESC);
CREATE INDEX idx_audit_ip ON audit_log (workspace_id, ip, created_at DESC);
CREATE INDEX idx_audit_event_key ON audit_log (event_key);
CREATE INDEX idx_audit_object_snapshot_name_trgm ON audit_log
    USING gin ((object_snapshot->>'name') gin_trgm_ops);
"""

DROP_DDL = "DROP TABLE IF EXISTS audit_log CASCADE;"


class Migration(migrations.Migration):

    dependencies = [
        ('db', '0028_s8_sso'),
    ]

    operations = [
        migrations.RunSQL(DDL, DROP_DDL),
    ]
