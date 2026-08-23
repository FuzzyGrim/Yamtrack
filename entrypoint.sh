#!/bin/sh

set -e

python manage.py migrate --noinput

PUID=${PUID:-1000}
PGID=${PGID:-1000}

groupmod -o -g "$PGID" abc
usermod -o -u "$PUID" abc

YAMTRACK_INTERNAL_PORT=${YAMTRACK_INTERNAL_PORT:-8000}
sed -i "s/8000/$YAMTRACK_INTERNAL_PORT/g" /etc/nginx/nginx.conf /etc/nginx/nginx.ipv6.conf

chown abc:abc /yamtrack
chown -R abc:abc db
chown -R abc:abc staticfiles
chown -R abc:abc /var/log/nginx
chown -R abc:abc /var/lib/nginx

exec supervisord -c /etc/supervisord.conf