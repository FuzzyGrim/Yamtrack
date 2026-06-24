#!/bin/sh

set -e

python manage.py migrate --noinput

PUID=${PUID:-1000}
PGID=${PGID:-1000}

groupmod -o -g "$PGID" abc
usermod -o -u "$PUID" abc

chown abc:abc /yamtrack
chown -R abc:abc db
chown -R abc:abc staticfiles
mkdir -p /yamtrack/media/profile_pictures
chown -R abc:abc /yamtrack/media
chown -R abc:abc /var/log/nginx
chown -R abc:abc /var/lib/nginx

exec supervisord -c /etc/supervisord.conf