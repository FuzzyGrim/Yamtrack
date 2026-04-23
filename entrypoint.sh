#!/bin/sh

set -e

python manage.py migrate --noinput

exec /usr/local/bin/supervisord -c /etc/supervisord.conf