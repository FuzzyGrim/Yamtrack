#!/bin/sh

set -e

python manage.py migrate --noinput

PUID=${PUID:-911}
PGID=${PGID:-911}

groupmod -o -g "$PGID" abc
usermod -o -u "$PUID" abc
chown -R abc:abc db
chown -R abc:abc media

exec gosu abc:abc uwsgi --ini /yamtrack/config/uwsgi.ini