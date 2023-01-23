FROM python:3.11-alpine

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1
ENV PATH="/scripts:${PATH}"

COPY ./requirements.txt /requirements.txt
RUN apk add --update --no-cache --virtual .tmp gcc libc-dev linux-headers
RUN pip install $(grep -ivE "libsass" requirements.txt)
RUN apk del .tmp

RUN mkdir /yamtarr
COPY ./yamtarr /yamtarr
WORKDIR /yamtarr

COPY ./scripts /scripts
RUN chmod +x /scripts/*

RUN mkdir -p static
RUN adduser -D user
RUN chown -R user:user /yamtarr

USER user
CMD ["entrypoint.sh"]