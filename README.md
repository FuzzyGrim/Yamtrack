# Yamtrack

![App Tests](https://github.com/FuzzyGrim/Yamtrack/actions/workflows/app-tests.yml/badge.svg)
![Docker Image](https://github.com/FuzzyGrim/Yamtrack/actions/workflows/docker-image.yml/badge.svg)
![Image](https://ghcr-badge.egpl.dev/fuzzygrim/yamtrack/size)
![CodeFactor](https://www.codefactor.io/repository/github/fuzzygrim/yamtrack/badge)
![Codecov](https://codecov.io/github/FuzzyGrim/Yamtrack/branch/dev/graph/badge.svg?token=PWUG660120)
![GitHub](https://img.shields.io/badge/license-GPL--3.0-blue)

Yamtrack is a self hosted simple media tracker. You can track movies, tv shows, anime and manga.

It uses [The Movie Database](https://www.themoviedb.org/) and [MyAnimeList](https://myanimelist.net/) APIs to fetch media information.

## Features

- Track movies, tv shows, anime and manga
- Track each season of a tv show individually and episodes watched
- Save score, status, progress, start and end dates, or write a note.
- Docker support
- Multi-users support
- Import from [MyAnimeList](https://myanimelist.net/), [The Movie Database](https://www.themoviedb.org/) and [AniList](https://anilist.co/)

## Installing with Docker

Copy the default `docker-compose.yml` file from the repository and set the environment variables. This would use a SQlite database, which is enough for most use cases.

To start the containers run:

```bash
docker-compose up -d
```

Alternatively, if you need a PostgreSQL database, you can use the `docker-compose.postgres.yml` file.

### Environment variables

| Name            | Type   | Description                   | Required    | Default  | Notes                                                                                               |
| --------------- | ------ | ----------------------------- | ----------- | -------- | --------------------------------------------------------------------------------------------------- |
| TMDB_API        | String | The Movie Database API key    | Yes         | None     | Required for movies and tv shows                                                                    |
| MAL_API         | String | MyAnimeList API key           | Yes         | None     | Required for anime and manga                                                                        |
| REDIS_URL       | String | Redis URL                     | Recommended | None     | Redis is recommended for better performance                                                         |
| SECRET          | String | Django secret key             | Recommended | "secret" | [SECRET_KEY](https://docs.djangoproject.com/en/stable/ref/settings/#secret-key)                     |
| ALLOWED_HOSTS   | List   | Base IP / Domain              | No          | "\*"     | [ALLOWED_HOSTS](https://docs.djangoproject.com/en/stable/ref/settings/#allowed-hosts)               |
| REGISTRATION    | Bool   | User registration             | No          | True     |                                                                                                     |
| DEBUG           | Bool   | Django debug mode             | No          | False    |                                                                                                     |
| PUID            | Int    | User ID                       | No          | 1000     |                                                                                                     |
| PGID            | Int    | Group ID                      | No          | 1000     |                                                                                                     |
| TZ              | String | Timezone                      | No          | UTC      |                                                                                                     |
| WEB_CONCURRENCY | Int    | Number of webserver processes | No          | 1        | [(2 x num cores) + 1](https://docs.gunicorn.org/en/latest/design.html#how-many-workers) recommended |

### Environment variables for PostgreSQL

| Name        | Type   | Description       | Required | Default    | Notes                        |
| ----------- | ------ | ----------------- | -------- | ---------- | ---------------------------- |
| DB_HOST     | String | Database host     | No       | None       | When not set, sqlite is used |
| DB_PORT     | Int    | Database port     | No       | 5432       |                              |
| DB_NAME     | String | Database name     | No       | "yamtrack" |                              |
| DB_USER     | String | Database user     | No       | "yamtrack" |                              |
| DB_PASSWORD | String | Database password | No       | "yamtrack" |                              |

## Local development

Clone the repository and change directory to it.

```bash
git clone https://github.com/FuzzyGrim/Yamtrack.git
cd Yamtrack
```

Create a `.env` file in the root directory and add the following variables.

```bash
TMDB_API=API_KEY
MAL_API=API_KEY
SECRET=SECRET
DEBUG=True
```

Then run the following commands.

```bash
python -m pip install -U -r requirements_dev.txt
python src/manage.py migrate
python src/manage.py runserver
```

Go to: http://localhost:8000
