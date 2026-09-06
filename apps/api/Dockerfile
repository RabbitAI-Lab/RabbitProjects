# ───── stage 1: builder ─────
FROM python:3.12-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev     && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.8 /uv /usr/local/bin/uv
WORKDIR /app
COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project

# ───── stage 2: runtime ─────
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=plane.settings.prod
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r rp && useradd -r -g rp -u 10001 rp
# ── FILE-003 §1.6 转码工具链分层（独立层，保 base 缓存命中）──
# LibreOffice + ffmpeg 是 FILE-003 交付物（worker 侧预览派生），非 INFRA-002 基线。
# 仅 worker/beat 服务置 INSTALL_TRANSCODE_TOOLS=1（deploy/compose build.args）；
# api web 进程不装，镜像体积增量 (~700MB) 只落在 worker 镜像。
# 组件包而非 libreoffice-core 单包：core 无 docx/xlsx/pptx 导入导出滤镜，
# writer/calc/impress 组件自带所需滤镜与最小依赖闭包。
ARG INSTALL_TRANSCODE_TOOLS=0
RUN if [ "$INSTALL_TRANSCODE_TOOLS" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer libreoffice-calc libreoffice-impress ffmpeg \
      && rm -rf /var/lib/apt/lists/*; \
    fi
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY apps/api/ /app/
RUN chmod +x /app/bin/*.sh && chown -R rp:rp /app
USER rp
EXPOSE 8000
CMD ["/app/bin/docker-entrypoint-api.sh"]
