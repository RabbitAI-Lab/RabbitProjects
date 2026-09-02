#!/usr/bin/env sh
set -e
exec celery -A plane worker \
    --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
    --concurrency="${CELERY_CONCURRENCY:-4}" \
    -Q notifications,webhooks,reports,imports
