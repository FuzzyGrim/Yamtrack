#!/bin/sh

set -e

if [ -n "$CA_CERT" ] && [ -f "$CA_CERT" ]; then
    echo "Adding custom CA certificate to Python certificate bundle..."
    cat "$CA_CERT" >> $(python -m certifi)
    echo "Custom CA certificate added to Python certificate bundle"
fi

python manage.py migrate --noinput

PUID=${PUID:-1000}
PGID=${PGID:-1000}

groupmod -o -g "$PGID" abc
usermod -o -u "$PUID" abc

chown abc:abc /yamtrack
chown -R abc:abc db
chown -R abc:abc staticfiles
chown -R abc:abc /var/log/nginx
chown -R abc:abc /var/lib/nginx

exec /usr/local/bin/supervisord -c /etc/supervisord.conf