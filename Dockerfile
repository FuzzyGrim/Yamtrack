FROM python:3.12-alpine3.21

# https://stackoverflow.com/questions/58701233/docker-logs-erroneously-appears-empty-until-container-stops
ENV PYTHONUNBUFFERED=1

# Define build argument with default value
ARG VERSION=dev
# Set it as an environment variable
ENV VERSION=$VERSION

COPY ./requirements.txt /requirements.txt
COPY ./entrypoint.sh /entrypoint.sh
COPY ./supervisord.conf /etc/supervisord.conf
COPY ./nginx.conf /etc/nginx/nginx.conf

WORKDIR /yamtrack

RUN apk add --no-cache nginx shadow \
    && pip install --no-cache-dir -r /requirements.txt \
    && pip install --no-cache-dir supervisor==4.2.5 \
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
RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["/entrypoint.sh"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=5 \
  CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:8000/health/ || exit 1
