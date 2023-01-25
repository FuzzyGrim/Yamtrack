FROM python:3.11-slim-bullseye
ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

COPY ./requirements.txt /requirements.txt

RUN apt-get update && apt-get install -y --no-install-recommends g++ gcc && \
    pip install --no-cache-dir --upgrade -r /requirements.txt && \
    apt-get remove -y --purge g++ gcc && apt-get autoremove -y

COPY ./entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN useradd -M user
WORKDIR /app
COPY --chown=user:user ./src .
RUN chown user:user /app

USER user
RUN mkdir -p static

CMD ["/entrypoint.sh"]