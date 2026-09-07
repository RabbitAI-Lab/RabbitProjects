"""L2/L3 限流 Throttle 家族（INFRA-005 §4.3.2）。

配额数值以 api-conventions.md §7.2 冻结表为唯一来源（BR-01）；本模块只消费
配置。计数经 django.core.cache（dev/test LocMem 单进程；prod CACHES 指向
Redis——api 多副本共享计数，INTG-002 交接风险 #2 的兑现）。

与规格的三处实现偏差（ADR 登记，见 docs/adr/）：
1. 规格 §4.3.2 把 DEFAULT_THROTTLE_CLASSES 放 settings/production.py 增量；
   实现改为 base.py 全量配置 + `RATE_LIMIT_ENABLED` 运行时开关门控 L2 四类
   与 AuthBurst——settings 级 prod-only 会让 L3 视图显式展开的
   ``[*BASE_THROTTLES, X]`` 在 dev 也带起 L2（jMeter flow 六套 / pytest 会被
   全局 60/min 打爆），或在 prod 静默摘除视图级 L2，二者必居其一；运行时
   开关两头都解。dev/test 缺省 False，prod True，验收演示 env 可开。
2. SearchRateThrottle 只建类不挂载：V1.0 无独立全局搜索端点（sprint-1 搜索
   为列表 ``?q=`` 过滤），feature freeze 下不新增端点；Sprint-7+ 搜索端点
   落地时挂载（known-tech-debt 登记）。
3. 收编类（Report/Bulk/ShareUnlock）不受开关门控——它们替换的是本就全环境
   生效的 sprint-4/5 自带实现，语义与生效面都不变（脚本与测试依赖）。
"""
from __future__ import annotations

import logging
import re
import time

from django.conf import settings as dj_settings
from django.core.cache import cache
from rest_framework.throttling import SimpleRateThrottle

logger = logging.getLogger("plane.api.throttling")


def client_ip(request) -> str | None:
    """X-Forwarded-For 首段优先（与 AUTH-001 ``_client_ip`` / FILE-004
    ``file_share.client_ip`` 同范式）——收编 ShareUnlockRateThrottle 时保持
    原 IP 提取语义，不用 DRF ``get_ident``（后者不读 XFF）。
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


class RedisRateThrottle(SimpleRateThrottle):
    """L2/L3 基类：固定窗口计数（BR-03/04）。

    - **子类必须实现 get_cache_key**：SimpleRateThrottle 的默认实现直接抛
      NotImplementedError——未覆写的子类一旦挂进 throttle_classes，首个请求
      即 500（不是 429），CI 以「每个子类均有 get_cache_key 覆写」为断言
    - cache 键含窗口起点（固定窗口），跨 api 副本共享（prod Redis）
    - Redis 失联 fail-open（§2.4）：限流是保护不是依赖
    - 数据链（§4.3.3 中间件消费）：每次判定后把 limit/remaining/reset/
      wait/subject/scope 写入 request._throttle_state——必须落到 DRF Request
      包装之下的原生 HttpRequest（request._request），否则中间件层读不到
    - ``gated=True`` 的子类受 ``settings.RATE_LIMIT_ENABLED`` 门控（见模块
      docstring 偏差 1）；收编子类 ``gated=False`` 全环境生效
    """

    scope: str = ""
    #: 配额声明（子类直设，如 "60/min"）；None 回退 DEFAULT_THROTTLE_RATES[scope]
    rate: str | None = None
    #: 门控位：True = 仅 RATE_LIMIT_ENABLED 时判定（L2 全局 + AuthBurst）
    gated: bool = False

    _RATE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    #: period 语法：``[数字前缀]<单位>[字母后缀]``——"min"/"sec"/"hour"/"day"
    #: 为 DRF 惯用装饰写法（首字符单位）；"10m"/"600s" 为扩展写法（数字前缀
    #: × 单位）。DRF 原生 parse_rate 只认首字符（"5/10m" → KeyError '1'），
    #: 规格配额表含 "5/10m"（ShareUnlock），故覆写。
    _PERIOD_RE = re.compile(r"^(\d*)([smhd])[a-z]*$")

    def parse_rate(self, rate):
        if rate is None:
            return (None, None)
        num, _, period = rate.partition("/")
        m = self._PERIOD_RE.match(period.strip().lower())
        if m is None:
            raise ValueError(f"非法限流配额声明 {rate!r}（period={period!r}）")
        mult = int(m.group(1)) if m.group(1) else 1
        return int(num), self._RATE_UNITS[m.group(2)] * mult

    def _ensure_rate(self) -> None:
        """解析类属性 rate → (num_requests, duration)。

        SimpleRateThrottle 只在其 allow_request 里解析；ShareUnlock 族的原语
        （hit/clear/remaining/_window）可能先于 allow_request 触达 duration，
        故独立成原语、两处共用。
        """
        if self.rate is None:
            self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)

    def allow_request(self, request, view):
        if self.gated and not getattr(dj_settings, "RATE_LIMIT_ENABLED", False):
            return True
        self._ensure_rate()
        key = self.get_cache_key(request, view)
        if key is None:                       # 白名单/豁免主体（更强身份已计）
            return True
        now = int(time.time())
        window_start = now // self.duration
        reset_at = (window_start + 1) * self.duration          # 窗口重置 Unix 秒（§7.3）
        full_key = f"rl:{self.scope}:{key}:{window_start}"
        try:
            # add + incr 组合（BR-03），不用裸 INCR：cache.incr 对不存在的键，
            # django-redis 会自动建键但**不带 TTL**（窗口键永不过期，计数只增
            # 不清），locmem 等后端则直接抛 ValueError——被下方 except 吞掉后
            # 每个窗口首请求都 fail-open（限流形同虚设）。add 原子建键并携带
            # timeout；建键失败（键已存在）说明窗口已在计数中，转 incr。
            if not cache.add(full_key, 1, timeout=self.duration):
                hits = cache.incr(full_key)
            else:
                hits = 1
        except Exception:                     # noqa: BLE001 —— Redis 失联：放行 + 告警日志
            logger.warning("event=rate_limit_degraded scope=%s", self.scope)
            return True
        self._wait = max(1, reset_at - now)    # 写入实例属性（wait() 方法见下）
        native = getattr(request, "_request", request)
        native._throttle_state = {  # type: ignore[attr-defined]  # noqa: B010
            "scope": self.scope, "subject": key,
            "limit": self.num_requests, "remaining": max(0, self.num_requests - hits),
            "reset": reset_at, "wait": self._wait,
        }
        return hits <= self.num_requests

    def wait(self):
        # 覆写 DRF SimpleRateThrottle.wait()：返回本类写入的 _wait（int 秒数）。
        # 若直接 `self.wait = int`，DRF check_throttles 会以 self.wait 属性当方法
        # 调用 → TypeError → 500
        return getattr(self, "_wait", 0)


# ── L2：全局四类（判定顺序即列表序，BR-04；均受 RATE_LIMIT_ENABLED 门控）──


class UserRateThrottle(RedisRateThrottle):
    scope = "user"
    rate = "60/min"
    gated = True

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return f"u:{request.user.id}"
        return None                            # 未认证交给匿名类


class ApiKeyRateThrottle(RedisRateThrottle):
    """API Key 60/min（§7.2）。V1.0 无 APIKey 基建（ADR-0026 A-1#9 登记）：
    ``api_key_id`` 恒缺 → 键恒 None → 本类空转；Sprint-7+ AUTH-009 落地后
    认证中间件解析 ``request.api_key_id`` 即自动生效。"""

    scope = "apikey"
    rate = "60/min"
    gated = True

    def get_cache_key(self, request, view):
        key_id = getattr(request, "api_key_id", None)   # 认证中间件已解析
        return f"k:{key_id}" if key_id else None


class OAuthAppRateThrottle(RedisRateThrottle):
    """OAuth 应用：60/min/(用户×应用) 复合键（§7.2 行）。V1.0 无 OAuth 应用
    面,P3 落地后同 ApiKeyRateThrottle 自动生效。"""

    scope = "oauth"
    rate = "60/min"
    gated = True

    def get_cache_key(self, request, view):
        app_id = getattr(request, "oauth_app_id", None)
        if not app_id or not request.user.is_authenticated:
            return None                        # 非 OAuth 请求交给后序类
        return f"oa:{request.user.id}:{app_id}"


class AnonRateThrottle(RedisRateThrottle):
    scope = "anon"
    rate = "30/min"
    gated = True

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated \
           or getattr(request, "api_key_id", None):
            return None                        # BR-04：已认证不落匿名桶
        return f"a:{self.get_ident(request)}"


class AuthBurstRateThrottle(RedisRateThrottle):
    """登录/注册/重置：10/min（按 IP；账号维度失败锁定 5/15min 为 P1 既有
    独立机制 AUTH-001 的 AUTH_TOO_MANY_ATTEMPTS，不归本类）"""

    scope = "auth"
    rate = "10/min"
    gated = True

    def get_cache_key(self, request, view):
        return f"ip:{client_ip(request) or self.get_ident(request)}"


# ── L3：端点级（叠加于 L2 之上；ViewSet 覆盖 throttle_classes）────
# 端点级 throttle 的计数键统一含主体维度：已认证按 user_id（端点本身均要求
# 认证——report.read / presign / bulk 无匿名面），匿名兜底按 IP（防未认证
# 请求绕过计数）；不覆写 get_cache_key 的子类挂载即 500（见基类 docstring）


class ReportRateThrottle(RedisRateThrottle):
    """报表聚合（RPT-001/002 + TEAM-003 活跃度）+ 甘特聚合：10/min 按 user
    计数——收编 GANTT-002 §4.2.1 契约要点 4 自带实现（原 ``gantt-agg:{user_id}``
    键并入 scope=report，10/min·user 语义不变）与 sprint-5 RPT-002 手工实现
    ``_report_throttle``（原列表滑窗改固定窗口，10/min 语义不变）。"""

    scope = "report"
    rate = "10/min"

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return f"u:{request.user.id}"
        return f"ip:{self.get_ident(request)}"


class SearchRateThrottle(RedisRateThrottle):
    """搜索端点 30/min。V1.0 无独立全局搜索端点——只建类不挂载（模块
    docstring 偏差 2），Sprint-7+ 搜索端点落地时 ``[*BASE_THROTTLES, …]``
    挂载。"""

    scope = "search"
    rate = "30/min"

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return f"u:{request.user.id}"
        return f"ip:{self.get_ident(request)}"


class PresignRateThrottle(RedisRateThrottle):
    """文件预签名申请 30/min（§7.2「防刷上传凭证」）。挂载口径 = 凭证**会话
    发起**端点（FILE-001 attachments/presign、AUTH-004 avatar/presign、
    FILE-003 upload-sessions 发起）；FILE-003 chunks/ 换发属会话内合法高频
    （大文件百片级），不挂——防刷对象是会话不是片。"""

    scope = "presign"
    rate = "30/min"

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return f"u:{request.user.id}"
        return f"ip:{self.get_ident(request)}"


class BulkRateThrottle(RedisRateThrottle):
    """批量端点族 10/min/用户（收编 sprint-2 BOARD-004 自带实现，scope
    ``issue_bulk`` 并入 ``bulk``，语义不变；单次 ≤100 条由 Serializer 层
    VALIDATION_BULK_LIMIT_EXCEEDED 双保险）。"""

    scope = "bulk"
    rate = "10/min"

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return f"u:{request.user.id}"
        return f"ip:{self.get_ident(request)}"


class ShareUnlockRateThrottle(RedisRateThrottle):
    """分享解锁防爆破：5 次失败/10 分钟/(IP,slug)，固定窗口 600s。

    收编 FILE-004 BR-07 自带实现——**仅收编配置不改语义**：仅对密码校验
    **失败**计数（成功解锁不消耗配额），故不走 allow_request 的自动计数；
    视图在失败路径显式调用 ``hit()``、成功路径 ``clear()``（FILE-004 §4.6
    原语义：成功清零失败计数）。IP 提取保持 XFF 首段优先（``client_ip``，
    非 DRF get_ident）。窗口从 TTL 锚定改为固定窗口量化（规格 §2.1 总表
    「固定窗口 600s」自口径），边界语义差 ≤1 窗口，ADR 登记。
    """

    scope = "share_unlock"
    rate = "5/10m"

    def get_cache_key(self, request, view):
        slug = view.kwargs.get("slug") or ""
        return f"su:{client_ip(request) or 'unknown'}:{slug}"

    def key_for(self, slug: str, ip: str | None) -> str:
        """(slug, ip) 两要素直取当前窗口计数键——测试隔离 / 运维排查用。"""
        self._ensure_rate()
        now = int(time.time())
        return f"rl:{self.scope}:su:{ip or 'unknown'}:{slug}:{now // self.duration}"

    # ── 计数三原语（视图侧显式调用；不经 DRF 自动判定）──

    def _window(self, request, view) -> tuple[str, int, int]:
        """(计数键, 窗口剩余秒, 重置 Unix 秒)。"""
        self._ensure_rate()
        now = int(time.time())
        window_start = now // self.duration
        reset_at = (window_start + 1) * self.duration
        key = f"rl:{self.scope}:{self.get_cache_key(request, view)}:{window_start}"
        return key, max(1, reset_at - now), reset_at

    def allow_request(self, request, view) -> bool:  # noqa: FBT001 —— DRF 签名
        """查询不计数：当前失败计数已达配额 → 抛 Throttled（handlers 第 8 步
        出 429 信封 + Retry-After + ``rate_limit_info`` 三头）；未达 → True。"""
        from rest_framework.exceptions import Throttled

        key, wait, reset_at = self._window(request, view)
        try:
            count = int(cache.get(key) or 0)
        except Exception:                     # noqa: BLE001 —— fail-open 同基类
            logger.warning("event=rate_limit_degraded scope=%s", self.scope)
            return True
        if count < self.num_requests:
            return True
        exc: Throttled = Throttled(wait=wait)
        exc.rate_limit_info = {  # type: ignore[attr-defined]
            "limit": self.num_requests, "remaining": 0, "reset": reset_at,
        }
        raise exc

    def hit(self, request, view) -> None:
        """失败路径显式计数（add+incr 组合同基类 BR-03）。"""
        key, _, _ = self._window(request, view)
        try:
            if not cache.add(key, 1, timeout=self.duration):
                cache.incr(key)
        except Exception:                     # noqa: BLE001
            logger.warning("event=rate_limit_degraded scope=%s", self.scope)

    def clear(self, request, view) -> None:
        """成功解锁清零（FILE-004 原语义）。"""
        key, _, _ = self._window(request, view)
        try:
            cache.delete(key)
        except Exception:                     # noqa: BLE001
            logger.warning("event=rate_limit_degraded scope=%s", self.scope)

    def remaining(self, request, view) -> int:
        """密码错误响应的「剩余 N 次尝试」（§4.2.3 details）。"""
        key, _, _ = self._window(request, view)
        try:
            count = int(cache.get(key) or 0)
        except Exception:                     # noqa: BLE001
            return self.num_requests
        return max(0, self.num_requests - count)


#: L2 判定顺序（BR-04）：Key → OAuth → Session 用户 → 匿名。
#: ViewSet 侧 L3 叠加示例——DRF 的 throttle_classes 是**整体替换不是追加**，
#: 覆盖时必须展开全局四类再追加端点类（否则 L2 被静默摘除）：
#:     throttle_classes = [*BASE_THROTTLES, ReportRateThrottle]
BASE_THROTTLES = [ApiKeyRateThrottle, OAuthAppRateThrottle,
                  UserRateThrottle, AnonRateThrottle]

__all__ = [
    "AnonRateThrottle", "ApiKeyRateThrottle", "AuthBurstRateThrottle",
    "BASE_THROTTLES", "BulkRateThrottle", "OAuthAppRateThrottle",
    "PresignRateThrottle", "RedisRateThrottle", "ReportRateThrottle",
    "SearchRateThrottle", "ShareUnlockRateThrottle", "UserRateThrottle",
    "client_ip",
]
