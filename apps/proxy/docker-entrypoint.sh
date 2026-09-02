#!/bin/sh
set -e
SUBST="$(printf '${%s} ' $(env | cut -d= -f1 | grep -E '^[A-Z_][A-Z0-9_]*$'))"
envsubst "$SUBST" < /etc/nginx/template/nginx.conf.template > /etc/nginx/nginx.conf
mkdir -p /etc/nginx/snippets
for t in /etc/nginx/template/conf.d/*.conf; do
    envsubst "$SUBST" < "$t" > "/etc/nginx/snippets/$(basename "$t")"
done
nginx -t
exec "$@"
