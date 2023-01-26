#!/bin/sh

set -e

id
ls -aln
ls -aln static

python manage.py migrate --noinput

python manage.py collectstatic --noinput --ignore=*.scss

uwsgi --socket :8000 --master --enable-threads --module base.wsgi