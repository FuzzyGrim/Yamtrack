FROM python:3.12-alpine3.21

# https://stackoverflow.com/questions/58701233/docker-logs-erroneously-appears-empty-until-container-stops
ENV PYTHONUNBUFFERED=1

# Define build argument with default value
ARG VERSION=dev
# Set it as an environment variable
ENV VERSION=$VERSION

COPY ./requirements.txt /requirements.txt
COPY ./supervisord.conf /etc/supervisord.conf
COPY --chmod=555 ./entrypoint.sh /entrypoint.sh
COPY ./nginx.conf /etc/nginx/nginx.conf

WORKDIR /yamtrack

RUN apk add --no-cache nginx shadow \
    && pip install --no-cache-dir -r /requirements.txt \
    && pip install --no-cache-dir supervisor==4.3.0 \
    && rm -rf /root/.cache /tmp/* \
    && find /usr/local -type d -name __pycache__ -exec rm -rf {} + \
    && useradd -U -M -u 1000 -s /bin/sh abc \
    && mkdir -p /yamtrack/db /yamtrack/staticfiles /var/cache/nginx \
    && chown -R abc:abc /yamtrack/db /yamtrack/staticfiles /var/cache/nginx

COPY --chmod=555 --chown=abc:abc src ./

USER abc:abc
RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["/entrypoint.sh"]

HEALTHCHECK --interval=45s --timeout=15s --start-period=30s --retries=5 \
  CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:8000/health/ || exit 1