FROM python:3.11-slim-bullseye

# https://stackoverflow.com/questions/58701233/docker-logs-erroneously-appears-empty-until-container-stops
ENV PYTHONUNBUFFERED=1

COPY ./requirements.txt /requirements.txt

RUN apt-get update \
	&& apt-get install -y --no-install-recommends curl \
	&& pip install --no-cache-dir -r /requirements.txt \
	&& pip install --upgrade --no-cache-dir supervisor==4.2.5 \
	&& apt-get clean -y \
	&& rm -rf /var/lib/apt/lists/*

# https://stackoverflow.com/questions/58701233/docker-logs-erroneously-appears-empty-until-container-stops
ENV PYTHONUNBUFFERED=1

WORKDIR /yamtrack

COPY ./entrypoint.sh /entrypoint.sh
COPY ./supervisord.conf /etc/supervisord.conf

RUN chmod +x /entrypoint.sh && \ 
	# create user abc for later PUID/PGID mapping
	useradd -U -M -s /bin/bash abc

# Django app
COPY src ./
RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["/entrypoint.sh"]

HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD [ "curl", "-fs", "-S", "--max-time", "2", "http://localhost:8000" ]