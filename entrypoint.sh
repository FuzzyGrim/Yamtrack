#!/bin/sh

set -e

python manage.py migrate --noinput

PUID=${PUID:-1000}
PGID=${PGID:-1000}

groupmod -o -g "$PGID" abc
usermod -o -u "$PUID" abc

mkdir -p media

chown abc:abc /yamtrack
chown -R abc:abc db
chown -R abc:abc staticfiles
chown -R abc:abc media
chown -R abc:abc /var/log/nginx
chown -R abc:abc /var/lib/nginx

exec /usr/local/bin/supervisord -c /etc/supervisord.conf