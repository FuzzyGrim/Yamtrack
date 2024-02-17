FROM python:3.11-slim-bullseye

# https://stackoverflow.com/questions/58701233/docker-logs-erroneously-appears-empty-until-container-stops
ENV PYTHONUNBUFFERED=1

COPY ./requirements.txt /requirements.txt

RUN apt-get update \
	&& apt-get install -y --no-install-recommends gosu \
	&& pip install --no-cache-dir -r /requirements.txt \
	&& apt-get -y autoremove --purge \
	&& apt-get clean -y \
	&& rm -rf /var/lib/apt/lists/*

# https://stackoverflow.com/questions/58701233/docker-logs-erroneously-appears-empty-until-container-stops
ENV PYTHONUNBUFFERED=1

WORKDIR /yamtrack

COPY ./entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh && \ 
	# create user abc for later PUID/PGID mapping
	useradd -U -M -s /bin/bash abc

# Django app
COPY src ./
RUN python manage.py compilescss
RUN python manage.py collectstatic --noinput --ignore=*.scss

CMD ["/entrypoint.sh"]