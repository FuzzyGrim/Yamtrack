FROM python:3.11-slim-bullseye
ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

WORKDIR /app

COPY ./requirements.txt /requirements.txt

RUN apt-get update && apt-get install -y --no-install-recommends g++ gcc gosu && \
    pip install --no-cache-dir --upgrade -r /requirements.txt && \
	rm -rf /var/lib/apt/lists/* && \
    apt-get remove -y --purge g++ gcc && apt-get autoremove -y && \
	gosu nobody true

COPY ./entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh && \
	groupmod -g 1000 users && \
	useradd -u 911 -U -M  -s /bin/bash abc && \
	usermod -G users abc && \
	mkdir -p static

COPY src ./

CMD ["/entrypoint.sh"]