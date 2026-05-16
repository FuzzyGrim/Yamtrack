FROM ghcr.io/astral-sh/uv:python3.12-alpine

# https://stackoverflow.com/questions/58701233/docker-logs-erroneously-appears-empty-until-container-stops
ENV PYTHONUNBUFFERED=1

# Define build argument with default value
ARG VERSION=dev
# Set it as an environment variable
ENV VERSION=$VERSION
# Disable development dependencies
ENV UV_NO_DEV=1
# Put the uv-managed virtualenv on PATH so python/gunicorn/celery resolve directly
ENV PATH="/.venv/bin:$PATH"

COPY ./pyproject.toml /pyproject.toml
COPY ./uv.lock /uv.lock
COPY ./entrypoint.sh /entrypoint.sh
COPY ./supervisord.conf /etc/supervisord.conf
COPY ./nginx.conf /etc/nginx/nginx.conf
# Generate a copy of the nginx config with IPv6 support.
RUN sed 's/listen 8000;/listen 8000; listen [::]:8000;/' /etc/nginx/nginx.conf > /etc/nginx/nginx.ipv6.conf

WORKDIR /yamtrack

RUN apk add --no-cache nginx shadow \
    && uv sync --locked \
    && rm -rf /root/.cache /tmp/* \
    && find /usr/local -type d -name __pycache__ -exec rm -rf {} + \
    && chmod +x /entrypoint.sh \
    # create user abc for later PUID/PGID mapping
    && useradd -U -M -s /bin/sh abc \
    # Create required nginx directories and set permissions
    && mkdir -p /var/log/nginx \
    && mkdir -p /var/lib/nginx/body

# Django app
COPY src ./
RUN uv run manage.py collectstatic --noinput

EXPOSE 8000

CMD ["/entrypoint.sh"]

HEALTHCHECK --interval=45s --timeout=15s --start-period=30s --retries=5 \
    CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:8000/health/ || exit 1
