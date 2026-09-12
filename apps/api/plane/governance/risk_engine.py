"""风控规则引擎（AUTH-012 §4.3，P4 R1）。

事件驱动 + 规则语义键计数；刻意规则表驱动，不做通用 CEP（§1.6）。
键设计铁律（§4.3 映射表）：R-02 主体是账号、R-03 计行数（INCRBY）、
R-05 有时段谓词、R-06 需双计数——单一 tenant+window 键无法表达这四类
语义，禁止规则无关的通用计数键。

数据流：AUTH-010 审计管道扇出（audit_record 成功后 risk 订阅者）→
risk_ingest 任务 → 本引擎 ingest() → 规则命中 _fire()（BR-08 聚合防扰
+ BR-05 证据快照固化）→ transaction.on_commit 投递 execute_action。

处置档（execute_action，tasks.py）：alert 告警 / throttle 限流 /
deny 硬拒（同步档——判定在 §4.5 第 5 强制点请求路径内直接 409，不经
异步队列）/ freeze 冻结（双人审批子流程，引擎不自动执行）。

注：任务暂走 celery 默认队列（dev worker 只消费默认队列）；「独立
governance 队列」为部署期路由配置，随私有化部署文档收口。
"""

from __future__ import annotations

import logging
import math

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger("plane.governance")

WINDOW_SECONDS = {"10m": 600, "1h": 3600, "1d": 86400}

#: 事件级别映射（§3.3 线框：高=处置含冻结/硬拒、中=限流/降速、低=仅告警）
RULE_SEVERITY = {
    "R-01": "medium",
    "R-02": "low",
    "R-03": "high",
    "R-04": "low",
    "R-05": "medium",
    "R-06": "medium",
}

#: 聚合升级比较用级别秩（_fire 档位升级判定——同桶 warn→deny 留痕不吞）
_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}

#: tier 默认配额（§2.3 配额表；TenantQuota 列 null 时跟随本表）
#: enterprise 成员「按合同 Seats」→ None 表示不设硬值（seats 列承载合同值）
TIER_DEFAULTS: dict[str, dict] = {
    "free": {
        "member_limit": 10,
        "storage_bytes": 5 * 2**30,
        "api_rate_per_minute": 600,
        "export_rows_per_day": 10_000,
        "webhook_limit": 5,
        "project_limit": 3,
    },
    "standard": {
        "member_limit": 100,
        "storage_bytes": 100 * 2**30,
        "api_rate_per_minute": 3_000,
        "export_rows_per_day": 100_000,
        "webhook_limit": 50,
        "project_limit": None,
    },
    "enterprise": {
        "member_limit": None,
        "storage_bytes": 2**40,
        "api_rate_per_minute": 10_000,
        "export_rows_per_day": 1_000_000,
        "webhook_limit": 200,
        "project_limit": None,
    },
}
#: 旗舰档配额完全继承企业版（§2.3 旗舰档说明——差异在合规能力位非配额）
TIER_DEFAULTS["flagship"] = TIER_DEFAULTS["enterprise"]


def resolve_tenant(workspace_id) -> str | None:
    """workspace→tenant 映射兜底（AUTH-010 recorder 增补 tenant_id 写入前
    的过渡路径；与 §4.1 回填同一映射源）。"""
    if not workspace_id:
        return None
    from plane.db.models import Workspace

    return (
        Workspace.objects.filter(pk=workspace_id, deleted_at__isnull=True).values_list("tenant_id", flat=True).first()
    )


def tier_quota(tenant) -> dict:
    """租户生效配额：TenantQuota 显式值优先，null 列跟随 tier 默认表。

    无配额行（回填迁移只建 Tenant 不建 Quota）按 tier 默认整表取值——
    反向描述符 tenant.quota 对缺行会抛 DoesNotExist，必须安全查询。"""
    defaults = TIER_DEFAULTS.get(tenant.tier, TIER_DEFAULTS["free"])
    from plane.db.models import TenantQuota

    quota = TenantQuota.objects.filter(tenant=tenant).first()
    if quota is None:
        return dict(defaults)
    return {
        "member_limit": quota.member_limit if quota.member_limit is not None else defaults["member_limit"],
        "storage_bytes": quota.storage_bytes if quota.storage_bytes is not None else defaults["storage_bytes"],
        "api_rate_per_minute": quota.api_rate_per_minute
        if quota.api_rate_per_minute is not None
        else defaults["api_rate_per_minute"],
        "export_rows_per_day": quota.export_rows_per_day
        if quota.export_rows_per_day is not None
        else defaults["export_rows_per_day"],
        "webhook_limit": quota.webhook_limit if quota.webhook_limit is not None else defaults["webhook_limit"],
        "project_limit": quota.project_limit if quota.project_limit is not None else defaults["project_limit"],
    }


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _window_start(window: str) -> int:
    """固定窗口对齐桶（window_start = epoch // 窗口秒，§4.3）。"""
    return int(timezone.now().timestamp()) // WINDOW_SECONDS[window]


def _day_stamp() -> str:
    """R-03/R-05 自然日键段（UTC YYYYMMDD，次日滚动自动恢复）。"""
    return timezone.now().strftime("%Y%m%d")


class RiskRuleEngine:
    """六类规则的事件计数与触发（规则表驱动，§1.6 取舍）。"""

    WINDOW_SECONDS = WINDOW_SECONDS

    # ── 入口 ───────────────────────────────────────────────────────────

    def ingest(self, audit_event: dict) -> None:
        """AUTH-010 扇出事件消费入口（BR-11 双重门控 + 未治理租户跳过）。"""
        if not getattr(settings, "TENANT_GOVERNANCE_ENABLED", False):
            return
        tenant_id = audit_event.get("tenant_id") or resolve_tenant(audit_event.get("workspace_id"))
        if tenant_id is None:
            return  # 未治理租户（私有化/迁移期）
        action = str(audit_event.get("action", ""))
        handler = self._route(action)
        if handler is not None:
            handler(self, str(tenant_id), audit_event)

    @staticmethod
    def _route(action: str):
        """审计动作 → 规则处理器（模块级路由表 _route_action，底部登记）。"""
        return _route_action(action)

    # ── 计数原语 ───────────────────────────────────────────────────────

    def _bump(self, key: str, window: str, amount: int = 1) -> int:
        """固定窗口计数：首击 add 自带 TTL（键含 window_start，窗口滚动换键），
        非首击原子自增；返回当前值。add+incr 组合不裸 INCR（同 INFRA-005 BR-03，
        django-redis 对不存在键 incr 不带 TTL 的坑）。"""
        try:
            if cache.add(key, amount, timeout=self.WINDOW_SECONDS[window]):
                return amount
            return int(cache.incr(key, amount))  # R-03 场景 amount=导出行数
        except Exception:  # noqa: BLE001 —— Redis 失联 fail-open
            logger.warning("risk.counter_degraded key=%s", key.split(":")[1])
            return 0

    def _read(self, key: str) -> int:
        try:
            return int(cache.get(key) or 0)
        except Exception:  # noqa: BLE001
            return 0

    # ── 规则解析 ──────────────────────────────────────────────────────

    def _rule(self, tenant_id: str, code: str):
        """租户覆盖行优先、平台默认兜底（只紧不松在保存端校验，§4.4）。
        返回 (rule, threshold)；规则不存在（未播种）返回 (None, None)。"""
        from plane.db.models import RiskRule

        rule = (
            RiskRule.objects.filter(code=code, tenant_id=tenant_id, deleted_at__isnull=True, is_enabled=True).first()
            or RiskRule.objects.filter(code=code, tenant__isnull=True, is_enabled=True).first()
        )
        if rule is None:
            return None, None
        return rule, rule.threshold or {}

    # ── 六规则处理器（§4.3 映射表逐行落地）───────────────────────────

    def on_login_failed(self, tenant_id: str, ev: dict) -> None:
        """R-01 登录风暴：租户 10m 失败登录 >200 触发 + IP 段封禁 30min。"""
        rule, thr = self._rule(tenant_id, "R-01")
        if rule is None:
            return
        key = f"risk:R-01:tenant:{tenant_id}:10m:{_window_start('10m')}"
        n = self._bump(key, "10m")
        if n > int(thr.get("count", 200)):
            ip = ev.get("ip")
            self._ban_ip_range(ip)
            self._fire(
                rule,
                tenant_id,
                f"tenant:{tenant_id}",
                ev,
                evidence={"failed_logins": n, "ip": ip, "ip_ban": self._cidr(ip)},
            )

    def on_login(self, tenant_id: str, ev: dict) -> None:
        """R-02 异地登录：账号主体，haversine >1,000km 触发后刷新 last_geo。"""
        rule, thr = self._rule(tenant_id, "R-02")
        if rule is None:
            return
        uid = ev.get("actor_id")
        geo = (ev.get("detail") or {}).get("geo") or {}
        if not uid or "lat" not in geo or "lng" not in geo:
            return
        last_key = f"risk:R-02:user:{uid}:last_geo"
        try:
            last = cache.get(last_key)
            cache.set(last_key, f"{geo['lat']},{geo['lng']}", timeout=3600)
        except Exception:  # noqa: BLE001
            last = None
        if not last:
            return
        try:
            lat1, lng1 = (float(x) for x in str(last).split(","))
            distance = _haversine_km(lat1, lng1, float(geo["lat"]), float(geo["lng"]))
        except (ValueError, TypeError):
            return
        if distance > float(thr.get("distance_km", 1000)):
            self._fire(
                rule, tenant_id, f"user:{uid}", ev, evidence={"distance_km": round(distance, 1), "actor_id": uid}
            )

    def on_export(self, tenant_id: str, ev: dict) -> None:
        """R-03 批量导出：按行数 INCRBY（非事件计数）；80% 预警 / 100% 越线
        （硬拒判定在导出端点 §4.5 第 5 强制点同步 409，此处落事件台账）。"""
        rule, thr = self._rule(tenant_id, "R-03")
        if rule is None:
            return
        try:
            rows = int((ev.get("detail") or {}).get("rows", 1))
        except (TypeError, ValueError):
            rows = 1
        from plane.db.models import Tenant

        tenant = Tenant.objects.filter(pk=tenant_id).first()
        quota = tier_quota(tenant)["export_rows_per_day"] if tenant else None
        if not quota:
            return
        key = f"risk:R-03:tenant:{tenant_id}:1d:{_day_stamp()}:rows"
        used = self._bump(key, "1d", amount=max(rows, 1))
        evidence = {"rows_used": used, "rows_quota": quota, "ratio": round(used / quota, 4)}
        if used > quota:  # >100%：硬拒线越界
            self._fire(
                rule, tenant_id, f"tenant:{tenant_id}", ev, severity="high", evidence={**evidence, "tier": "deny"}
            )
        elif used > quota * float(thr.get("warn_ratio", 0.8)):
            self._fire(
                rule, tenant_id, f"tenant:{tenant_id}", ev, severity="medium", evidence={**evidence, "tier": "warn"}
            )

    def on_perm_grant(self, tenant_id: str, ev: dict) -> None:
        """R-04 权限批量提升：操作者主体，1h 授予管理员 >5 触发。"""
        rule, thr = self._rule(tenant_id, "R-04")
        if rule is None:
            return
        uid = ev.get("actor_id")
        if not uid:
            return
        key = f"risk:R-04:operator:{uid}:1h:{_window_start('1h')}"
        n = self._bump(key, "1h")
        if n > int(thr.get("count", 5)):
            self._fire(rule, tenant_id, f"operator:{uid}", ev, evidence={"grants_by_operator": n, "actor_id": uid})

    def on_delete(self, tenant_id: str, ev: dict) -> None:
        """R-05 深夜批量删除：时段谓词前置（00:00-06:00 UTC 窗外不计数），
        自然日 >50 触发。"""
        rule, thr = self._rule(tenant_id, "R-05")
        if rule is None:
            return
        hour = timezone.now().hour
        if not (int(thr.get("night_start_hour", 0)) <= hour < int(thr.get("night_end_hour", 6))):
            return  # 谓词前置：键不增长（UT-16）
        key = f"risk:R-05:tenant:{tenant_id}:1d:{_day_stamp()}"
        n = self._bump(key, "1d")
        if n > int(thr.get("count", 50)):
            self._fire(rule, tenant_id, f"tenant:{tenant_id}", ev, evidence={"night_deletes": n})

    def on_api_call(self, tenant_id: str, ev: dict) -> None:
        """R-06 API 爬虫模式：token 主体双键（total + getlist）；
        total > 租户 rpm×60×50% 且 getlist/total ≥95% 触发（§2.3 量纲）。"""
        rule, thr = self._rule(tenant_id, "R-06")
        if rule is None:
            return
        detail = ev.get("detail") or {}
        token_id = detail.get("token_id")
        if not token_id:
            return
        bucket = _window_start("1h")
        total = self._bump(f"risk:R-06:token:{token_id}:1h:{bucket}:total", "1h")
        if detail.get("is_get_list"):
            self._bump(f"risk:R-06:token:{token_id}:1h:{bucket}:getlist", "1h")
        from plane.db.models import Tenant

        tenant = Tenant.objects.filter(pk=tenant_id).first()
        rpm = tier_quota(tenant)["api_rate_per_minute"] if tenant else None
        if not rpm:
            return
        if total > rpm * 60 * float(thr.get("quota_ratio", 0.5)):
            getlist = self._read(f"risk:R-06:token:{token_id}:1h:{bucket}:getlist")
            if getlist / max(total, 1) >= float(thr.get("getlist_ratio", 0.95)):
                self._fire(
                    rule,
                    tenant_id,
                    f"token:{token_id}",
                    ev,
                    evidence={
                        "token_id": token_id,
                        "total_calls": total,
                        "getlist_calls": getlist,
                        "getlist_ratio": round(getlist / total, 4),
                    },
                )

    # ── 触发与证据 ────────────────────────────────────────────────────

    def _fire(
        self,
        rule,
        tenant_id: str,
        subject_key: str,
        ev: dict,
        *,
        severity: str | None = None,
        evidence: dict | None = None,
    ):
        """BR-08 聚合（同租户同规则同主体 UTC 小时桶内只告警一次，
        occurrences 递增）+ BR-05 证据快照固化 + on_commit 处置投递。"""
        from plane.db.models import RiskEvent

        hour_bucket = timezone.now().isoformat()[:13]  # UTC 小时桶
        agg_key = f"{rule.code}:{tenant_id}:{subject_key}:{hour_bucket}"
        existing = RiskEvent.objects.filter(aggregate_key=agg_key, status="open").first()
        if existing:
            existing.evidence["occurrences"] = existing.evidence.get("occurrences", 1) + 1
            # 档位升级不吞（IT-10：warn→deny 仍在同桶聚合事件上留痕——
            # 升级 severity 并合并证据，处置链按最高档执行）
            new_sev = severity or RULE_SEVERITY.get(rule.code, "medium")
            if _SEVERITY_RANK.get(new_sev, 0) > _SEVERITY_RANK.get(existing.severity, 0):
                existing.severity = new_sev
                existing.evidence.update(self._snapshot_evidence(ev, evidence or {}))
                existing.evidence["escalated_to"] = new_sev
            existing.save(update_fields=["evidence", "severity", "updated_at"])
            return existing
        event = RiskEvent.objects.create(
            tenant_id=tenant_id,
            rule_code=rule.code,
            severity=severity or RULE_SEVERITY.get(rule.code, "medium"),
            evidence=self._snapshot_evidence(ev, evidence or {}),
            aggregate_key=agg_key,
        )
        transaction.on_commit(lambda: _dispatch_action(rule.action, tenant_id, rule.code, str(event.id)))
        return event

    @staticmethod
    def _snapshot_evidence(ev: dict, extra: dict) -> dict:
        """证据快照（BR-05 触发即固化；BR-07 L1 最小知情——只留统计与 ID，
        不落业务内容字段）。"""
        return {
            "actor_id": ev.get("actor_id"),
            "ip": ev.get("ip"),
            "object_id": ev.get("object_id"),
            "action": ev.get("action"),
            **extra,
        }

    # ── 处置辅助 ──────────────────────────────────────────────────────

    @staticmethod
    def _cidr(ip: str | None) -> str | None:
        """来源 IP → /24 段（IPv4；IPv6 归整个 /64 简化为原地址段）。"""
        if not ip or "." not in ip:
            return ip
        return f"{ip.rsplit('.', 1)[0]}.0/24"

    def _ban_ip_range(self, ip: str | None) -> None:
        """R-01 触发附 IP 段临时封禁 30min（Redis 载体；中间件快路径消费，
        与 rbac §11.5 IP 白名单分域）。"""
        cidr = self._cidr(ip)
        if not cidr:
            return
        try:
            cache.add(f"risk:R-01:ipban:{cidr}", 1, timeout=1800)
        except Exception:  # noqa: BLE001
            logger.warning("risk.ipban_degraded cidr=%s", cidr)


def _dispatch_action(action: str, tenant_id: str, rule_code: str, event_id: str) -> None:
    """on_commit 投递处置（governance.tasks.execute_action；broker 不可达
    不阻塞业务事务）。"""
    from plane.governance.tasks import execute_action

    try:
        execute_action.delay(action, tenant_id, rule_code, event_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("risk.dispatch_failed rule=%s err=%s", rule_code, exc)


# ── 审计动作 → 处理器路由表（RULE_HANDLERS，§4.3）────────────────────
#: registry 动作名经 _route 的包含匹配解析（精确命中优先）。
_ROUTE_TABLE: dict[str, object] = {}
_ROUTE_CONTAINS: list[tuple[str, object]] = []


def _register(actions: list[str], contains: list[str], handler) -> None:
    for a in actions:
        _ROUTE_TABLE[a] = handler
    for c in contains:
        _ROUTE_CONTAINS.append((c, handler))


_register(actions=["login_failed"], contains=["login_failed", "login_fail"], handler=RiskRuleEngine.on_login_failed)
_register(actions=["login", "login_success"], contains=["login_success"], handler=RiskRuleEngine.on_login)
_register(actions=["export"], contains=["export"], handler=RiskRuleEngine.on_export)
_register(
    actions=["perm_grant", "role_grant"],
    contains=["role_grant", "perm_grant", "grant_role"],
    handler=RiskRuleEngine.on_perm_grant,
)
_register(actions=["delete"], contains=["delete", "purge"], handler=RiskRuleEngine.on_delete)
_register(actions=["api_call"], contains=["api_call"], handler=RiskRuleEngine.on_api_call)


def _route_action(action: str):
    """精确命中优先，其次包含匹配（兼容 AUTH-010 registry 的域前缀命名）。
    引擎刻意保守：无命中即无计数（漏报优于误报——处置四档均落在运营侧复核）。"""
    if action in _ROUTE_TABLE:
        return _ROUTE_TABLE[action]
    for needle, handler in _ROUTE_CONTAINS:
        if needle in action:
            return handler
    return None
