# pip dependencies in a separate image
FROM python:3.9-slim as builder

COPY ./requirements.txt /requirements.txt

RUN apt-get update && apt-get install -y --no-install-recommends g++ gosu && \
    pip install --extra-index-url https://www.piwheels.org/simple --target=/dependencies -r /requirements.txt


FROM python:3.9-slim-bullseye

# https://stackoverflow.com/questions/58701233/docker-logs-erroneously-appears-empty-until-container-stops
ENV PYTHONUNBUFFERED=1

COPY --from=builder /dependencies /usr/local
ENV PYTHONPATH=/usr/local
COPY --from=builder /usr/sbin/gosu /usr/sbin/gosu

WORKDIR /app

COPY ./mime.types /etc/mime.types
COPY ./entrypoint.sh /entrypoint.sh

# create user abc for later PUID/PGID mapping https://github.com/linuxserver/docker-baseimage-alpine/blob/master/Dockerfile
RUN chmod +x /entrypoint.sh && \ 
	groupmod -g 1000 users && \
	useradd -u 911 -U -M -s /bin/bash abc && \
	usermod -G users abc

# Django app
COPY src ./
RUN python manage.py collectstatic --noinput --ignore=*.scss

CMD ["/entrypoint.sh"]