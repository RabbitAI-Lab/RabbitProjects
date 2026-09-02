#!/usr/bin/env sh
set -e
exec gunicorn plane.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --worker-class gthread \
    --threads "${GUNICORN_THREADS:-4}" \
    --timeout 120 \
    --graceful-timeout 30 \
    --max-requests 2000 --max-requests-jitter 200 \
    --access-logfile - --error-logfile -
