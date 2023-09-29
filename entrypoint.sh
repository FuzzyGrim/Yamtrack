#!/bin/sh

set -e

python manage.py migrate --noinput

PUID=${PUID:-1000}
PGID=${PGID:-1000}

groupmod -o -g "$PGID" abc
usermod -o -u "$PUID" abc
chown -R abc:abc db

exec gosu abc:abc gunicorn --bind :8000 config.wsgi:application
