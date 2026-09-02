#!/usr/bin/env sh
set -e
rm -f /tmp/celerybeat.pid
exec celery -A plane beat \
    --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
    --scheduler django_celery_beat.schedulers:DatabaseScheduler \
    --pidfile=/tmp/celerybeat.pid
