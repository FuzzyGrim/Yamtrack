# Installation

This page walks you through a basic Docker installation.

## Prerequisites

- Docker and Docker Compose installed.

## 1) Clone the repository

```bash
git clone https://github.com/FuzzyGrim/Yamtrack.git
cd Yamtrack
```

## 2) Create the `.env` file

Create a `.env` file in the repository root with at least:

```bash
SECRET=CHANGE_ME
URLS=http://localhost:8000
```

For the full list of environment variables, see [env-variables.md](env-variables.md).

## 3) Start the containers (SQLite)

```bash
docker-compose up -d
```

## 4) Open the app

```bash
http://localhost:8000
```

## 5) Optional: PostgreSQL

Use the PostgreSQL compose file instead of the default one:

```bash
docker-compose -f docker-compose.postgres.yml up -d
```

## 6) Optional: Reverse proxy

If you run behind a reverse proxy and get a `403 - Forbidden`, set `URLS` to the public URL:

```bash
URLS=https://yamtrack.mydomain.com
```

## Troubleshooting

If containers fail to start, check logs:

```bash
docker logs -f yamtrack
```
