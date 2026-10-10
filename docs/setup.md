# Setup

This page covers running Yamtrack with Docker.

## Docker

The Docker setup uses the published Yamtrack image, so you do not need to clone the repository. Download a Compose file, adjust the environment values, and start the containers.

## Prerequisites

- Docker and Docker Compose installed.

## 1) Download a Compose file

For the default SQLite setup:

```bash
curl -LO https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/release/docker-compose.yml
```

SQLite is enough for most personal installs. It stores the database in the local `db` directory created beside the Compose file.

If you prefer PostgreSQL, download the PostgreSQL example instead:

```bash
curl -LO https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/release/docker-compose.postgres.yml
```

## 2) Update the environment values

Open the Compose file and replace the example secret:

```yaml
SECRET=longstring
```

Use a long random value for `SECRET`. If you are running Yamtrack behind a reverse proxy, also set `URLS` to the public origin:

```yaml
URLS=https://yamtrack.mydomain.com
```

The URL must include the protocol (`https` or `http`) and should not include a trailing slash or application path. Multiple origins can be separated with commas.

For the full list of supported settings, see [Environment Variables](env-variables.md).

## 3) Start Yamtrack

For SQLite:

```bash
docker compose up -d
```

For PostgreSQL:

```bash
docker compose -f docker-compose.postgres.yml up -d
```

If your system uses the older Compose command, replace `docker compose` with `docker-compose`.

## 4) Open the app

Open Yamtrack at:

```text
http://localhost:8000
```

If you changed the port mapping in the Compose file, use the port you configured.

## Reverse Proxy Setup

When using a reverse proxy, `URLS` tells Yamtrack which public origins it should trust. This is required for CSRF protection, OAuth redirects, and webhook integrations.

Example:

```yaml
services:
  yamtrack:
    environment:
      - URLS=https://yamtrack.mydomain.com
```

If you see `403 Forbidden` behind a proxy, check that `URLS` exactly matches the public URL you use in the browser.

## Troubleshooting

Check the Yamtrack container logs:

```bash
docker logs -f yamtrack
```

Check the Redis container logs:

```bash
docker logs -f yamtrack-redis
```
