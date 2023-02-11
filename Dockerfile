FROM python:3.11-slim-bullseye
ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

WORKDIR /app

COPY ./requirements.txt /requirements.txt
COPY install-nginx-debian.sh /install-nginx-debian.sh

RUN apt-get update && apt-get install -y --no-install-recommends g++ gcc gosu && \
    bash /install-nginx-debian.sh && \
    pip install --no-cache-dir --upgrade -r /requirements.txt && \
	rm -rf /var/lib/apt/lists/* && \
    apt-get remove -y --purge g++ gcc && apt-get autoremove -y && \
	gosu nobody true && \
	ln -sf /dev/stdout /var/log/nginx/access.log && ln -sf /dev/stderr /var/log/nginx/error.log

COPY ./default.conf /etc/nginx/conf.d/default.conf

COPY ./entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && \
	groupmod -g 1000 users && \
	useradd -u 911 -U -M  -s /bin/bash abc && \
	usermod -G users abc

COPY src ./

STOPSIGNAL SIGQUIT

CMD ["/entrypoint.sh"]