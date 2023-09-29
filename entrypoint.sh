#!/bin/sh

set -e

python manage.py migrate --noinput

PUID=${PUID:-911}
PGID=${PGID:-911}

groupmod -o -g "$PGID" abc
usermod -o -u "$PUID" abc
chown -R abc:abc db

exec gosu abc:abc gunicorn --bind :8000 config.wsgi:application
