FROM python:3.9-slim-bullseye
ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

WORKDIR /app

COPY ./requirements.txt /requirements.txt

RUN apt-get update && apt-get install -y --no-install-recommends gosu g++ gcc && \
    pip install --no-cache-dir --upgrade --extra-index-url https://www.piwheels.org/simple -r /requirements.txt && \
	rm -rf /var/lib/apt/lists/* && \
    apt-get remove -y --purge g++ gcc && apt-get autoremove -y && \
	gosu nobody true

COPY ./entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && \
	groupmod -g 1000 users && \
	useradd -u 911 -U -M  -s /bin/bash abc && \
	usermod -G users abc

COPY src ./

CMD ["/entrypoint.sh"]