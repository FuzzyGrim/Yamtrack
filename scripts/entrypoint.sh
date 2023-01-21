#!/bin/sh

set -e

python manage.py migrate

python manage.py collectstatic --noinput --ignore=*.scss

uwsgi --socket :8000 --master --enable-threads --module base.wsgi