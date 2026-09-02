#!/usr/bin/env sh
set -e
echo "[migrator] applying migrations ..."
python manage.py migrate --noinput --verbosity 1
echo "[migrator] collecting static files ..."
python manage.py collectstatic --noinput || true
echo "[migrator] done."
