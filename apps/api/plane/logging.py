"""structlog：JSON 行输出到 stdout，Docker json-file driver 收集（INFRA-002 §3.4）。

进程入口（wsgi/worker/beat）共同调用 configure_logging() 完成初始化。
"""
from __future__ import annotations

import logging
import sys

import structlog

MAX_LOG_BYTES = 8 * 1024                 # BR-10：单条 ≤ 8KB
REDACT_KEYS = {"password", "token", "secret", "api_key", "x-api-key", "authorization"}
TRUNCATE_FIELDS = ("body", "payload", "query_params", "result")   # 超长截断字段


def _truncate(value, max_len=512):
    text = value if isinstance(value, str) else repr(value)
    return text[:max_len] + "…" if len(text) > max_len else text


def _redact(logger, method_name, event_dict):
    """脱敏 processor：REDACT_KEYS 命中键值 → '***'（BR-11，§13.5）。"""
    for key in list(event_dict):
        if key.lower() in REDACT_KEYS:
            event_dict[key] = "***"
        elif key in TRUNCATE_FIELDS:
            event_dict[key] = _truncate(event_dict[key])
    return event_dict


def configure_logging(debug: bool = False) -> None:
    shared = [
        structlog.contextvars.merge_contextvars,        # request_id / user_id 自动合入
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
        structlog.processors.format_exc_info,           # exception → 结构化 exc_info 字段
        structlog.processors.JSONRenderer(ensure_ascii=False),
    ]
    structlog.configure(
        processors=shared,
        # stdlib 工厂：shared 里用了 structlog.stdlib.* 处理器（add_logger_name 读
        # logger.name），默认的 PrintLoggerFactory 产出的 PrintLogger 没有 .name，
        # 二者混用会 AttributeError。同时让 structlog 与 handlers.py 的
        # logging.getLogger("plane.api.errors") 走同一条 stdlib 通道。
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if debug else logging.INFO),
        cache_logger_on_first_use=True,                 # 性能：处理器链只构造一次
    )
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
