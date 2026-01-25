#!/bin/sh

set -e

python manage.py migrate --noinput

# The container now runs as user 'abc' by default. 
# Permission management is handled during the build process.

exec /usr/local/bin/supervisord -c /etc/supervisord.conf