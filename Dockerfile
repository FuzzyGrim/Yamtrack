# pip dependencies in a separate image
FROM python:3.9-slim-bullseye as builder

# https://stackoverflow.com/questions/58701233/docker-logs-erroneously-appears-empty-until-container-stops
ENV PYTHONUNBUFFERED=1

COPY ./requirements.txt /requirements.txt

RUN apt-get update \
    # libpq-dev is required for psycopg
	# libpcre3-dev is required for uwsgi regex support
	# libwebpmux3 libwebpdemux2 liblcms2-2 libopenjp2-7 are required for Pillow
	# libstdc++6 is required for libass
	&& apt-get install -y --no-install-recommends g++ libpq-dev libpcre3-dev libwebpmux3 libwebpdemux2 liblcms2-2 libopenjp2-7 libstdc++6 gosu \
	&& pip install --extra-index-url https://www.piwheels.org/simple --no-cache-dir -r /requirements.txt \
	&& apt-get purge -y --auto-remove g++ \
	&& apt-get -y autoremove --purge \
	&& apt-get clean -y \
	&& rm -rf /var/lib/apt/lists/*

# https://stackoverflow.com/questions/58701233/docker-logs-erroneously-appears-empty-until-container-stops
ENV PYTHONUNBUFFERED=1

WORKDIR /yamtrack

COPY ./mime.types /etc/mime.types
COPY ./entrypoint.sh /entrypoint.sh

# create user abc for later PUID/PGID mapping https://github.com/linuxserver/docker-baseimage-alpine/blob/master/Dockerfile
RUN chmod +x /entrypoint.sh && \ 
	groupmod -g 1000 users && \
	useradd -u 911 -U -M -s /bin/bash abc && \
	usermod -G users abc

# Django app
COPY src ./
RUN python manage.py compilescss
RUN python manage.py collectstatic --noinput --ignore=*.scss

CMD ["/entrypoint.sh"]