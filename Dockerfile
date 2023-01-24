FROM python:3.11-alpine

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

COPY ./requirements.txt /requirements.txt
RUN apk add --update --no-cache --virtual .tmp gcc libc-dev linux-headers
RUN pip install $(grep -ivE "libsass" requirements.txt)
RUN apk del .tmp

COPY ./entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN adduser -D user
WORKDIR /app
COPY --chown=user:user ./src .
RUN chown user:user /app

USER user
RUN [ ! -d "db" ] && mkdir db
RUN [ ! -d "static" ] && mkdir static

CMD ["/entrypoint.sh"]