#!/bin/sh

set -e

configure_trusted_proxies() {
    proxies="$1"

    case "$proxies" in
        *[!0-9a-fA-F:./," "]*)
            echo "Invalid TRUSTED_PROXIES: '$proxies' (expected comma-separated IPv4/IPv6 addresses or CIDR subnets)" >&2
            exit 1
            ;;
    esac

    directives=$(for proxy in $(printf '%s' "$proxies" | tr ',' ' '); do
        printf '    set_real_ip_from %s;\\n' "$proxy"
    done)

    if [ -z "$directives" ]; then
        echo "Invalid TRUSTED_PROXIES: no entries found in '$proxies'" >&2
        exit 1
    fi

    directives="${directives}    real_ip_header X-Forwarded-For;\\n    real_ip_recursive on;"

    sed -i "s|^    # __TRUSTED_PROXIES__.*|${directives}|" /etc/nginx/nginx.conf /etc/nginx/nginx.ipv6.conf

    nginx -t -c /etc/nginx/nginx.conf
    nginx -t -c /etc/nginx/nginx.ipv6.conf
}

YAMTRACK_INTERNAL_PORT=${YAMTRACK_INTERNAL_PORT:-8000}
# Must match src/config/gunicorn.py and the nginx upstream.
GUNICORN_PORT=23847

case "$YAMTRACK_INTERNAL_PORT" in
    *[!0-9]*)
        echo "Invalid YAMTRACK_INTERNAL_PORT: '$YAMTRACK_INTERNAL_PORT' (must be a number)" >&2
        exit 1
        ;;
esac
if [ "$YAMTRACK_INTERNAL_PORT" -lt 1 ] || [ "$YAMTRACK_INTERNAL_PORT" -gt 65535 ]; then
    echo "Invalid YAMTRACK_INTERNAL_PORT: '$YAMTRACK_INTERNAL_PORT' (must be between 1 and 65535)" >&2
    exit 1
fi
# nginx would become its own upstream and loop requests forever.
if [ "$YAMTRACK_INTERNAL_PORT" -eq "$GUNICORN_PORT" ]; then
    echo "Invalid YAMTRACK_INTERNAL_PORT: '$YAMTRACK_INTERNAL_PORT' is reserved for gunicorn inside the container" >&2
    exit 1
fi

python manage.py migrate --noinput

PUID=${PUID:-1000}
PGID=${PGID:-1000}

groupmod -o -g "$PGID" abc
usermod -o -u "$PUID" abc


if [ -n "$TRUSTED_PROXIES" ]; then
    configure_trusted_proxies "$TRUSTED_PROXIES"
fi

sed -i \
    -e "s/listen [0-9]\{1,5\};/listen ${YAMTRACK_INTERNAL_PORT};/" \
    -e "s/listen \[::\]:[0-9]\{1,5\};/listen [::]:${YAMTRACK_INTERNAL_PORT};/" \
    /etc/nginx/nginx.conf /etc/nginx/nginx.ipv6.conf

chown abc:abc /yamtrack
chown -R abc:abc db
chown -R abc:abc staticfiles
chown -R abc:abc /var/log/nginx
chown -R abc:abc /var/lib/nginx

exec supervisord -c /etc/supervisord.conf
