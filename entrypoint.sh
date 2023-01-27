#!/bin/sh

set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput --ignore=*.scss

PUID=${PUID:-911}
PGID=${PGID:-911}

groupmod -o -g "$PGID" abc
usermod -o -u "$PUID" abc
chown -R abc:abc db
chown -R abc:abc assets

exec gosu abc:abc uwsgi --socket :8000 --master --enable-threads --die-on-term --module base.wsgi