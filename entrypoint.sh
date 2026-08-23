#!/bin/sh

set -e

python manage.py migrate --noinput

PUID=${PUID:-1000}
PGID=${PGID:-1000}

groupmod -o -g "$PGID" abc
usermod -o -u "$PUID" abc

YAMTRACK_INTERNAL_PORT=${YAMTRACK_INTERNAL_PORT:-8000}
case "$YAMTRACK_INTERNAL_PORT" in
    ''|*[!0-9]*)
        echo "Invalid YAMTRACK_INTERNAL_PORT: '$YAMTRACK_INTERNAL_PORT' (must be a number)" >&2
        exit 1
        ;;
esac
if [ "$YAMTRACK_INTERNAL_PORT" -lt 1 ] || [ "$YAMTRACK_INTERNAL_PORT" -gt 65535 ]; then
    echo "Invalid YAMTRACK_INTERNAL_PORT: '$YAMTRACK_INTERNAL_PORT' (must be between 1 and 65535)" >&2
    exit 1
fi

sed -i \
    -e "s/listen [0-9]\{1,5\};/listen ${YAMTRACK_INTERNAL_PORT};/" \
    -e "s/listen \[::\]:[0-9]\{1,5\};/listen [::]:${YAMTRACK_INTERNAL_PORT};/" \
    /etc/nginx/nginx.conf /etc/nginx/nginx.ipv6.conf

chown abc:abc /yamtrack
chown -R abc:abc db
chown -R abc:abc staticfiles
chown -R abc:abc /var/log/nginx
chown -R abc:abc /var/lib/nginx

exec supervisord -c /etc/supervisord.conf