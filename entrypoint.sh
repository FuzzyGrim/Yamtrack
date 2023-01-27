#!/bin/sh

set -e

PUID=${PUID:-911}
PGID=${PGID:-911}

groupmod -o -g "$PGID" abc
usermod -o -u "$PUID" abc
chown -R abc:abc db
chown -R abc:abc media
chown -R abc:abc static

gosu abc:abc sh -c "python manage.py migrate --noinput &&
python manage.py collectstatic --noinput --ignore=*.scss && 
uwsgi --socket :8000 --master --enable-threads --module base.wsgi"