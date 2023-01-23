FROM python:3.11-alpine

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1
ENV PATH="/scripts:${PATH}"

COPY ./requirements.txt /requirements.txt
RUN apk add --update --no-cache --virtual .tmp gcc libc-dev linux-headers
RUN pip install $(grep -ivE "libsass" requirements.txt)
RUN apk del .tmp

COPY ./scripts /scripts
RUN chmod +x /scripts/*

RUN adduser -D user
WORKDIR /yamtarr
COPY --chown=user:user ./yamtarr .
RUN chown user:user /yamtarr

USER user
CMD ["entrypoint.sh"]