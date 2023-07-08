# Yamtrack

[![App Tests](https://github.com/FuzzyGrim/Yamtrack/actions/workflows/app-tests.yml/badge.svg)](https://github.com/FuzzyGrim/Yamtrack/actions/workflows/app-tests.yml)
[![Docker Image](https://github.com/FuzzyGrim/Yamtrack/actions/workflows/docker-image.yml/badge.svg)](https://github.com/FuzzyGrim/Yamtrack/actions/workflows/docker-image.yml)
[![CodeFactor](https://www.codefactor.io/repository/github/fuzzygrim/yamtrack/badge)](https://www.codefactor.io/repository/github/fuzzygrim/yamtrack)
[![Codecov](https://codecov.io/github/FuzzyGrim/Yamtrack/branch/main/graph/badge.svg?token=PWUG660120)](https://codecov.io/github/FuzzyGrim/Yamtrack)
[![GitHub](https://img.shields.io/badge/license-GPL--3.0-blue)](https://github.com/FuzzyGrim/Yamtrack/blob/main/LICENSE)

Yamtrack is a self hosted simple media tracker. You can track movies, tv shows, anime and manga.

It uses [The Movie Database](https://www.themoviedb.org/) and [MyAnimeList](https://myanimelist.net/) APIs to fetch media information.


## Features

- Track movies, tv shows, anime and manga
- Track each tv show season
- Save score, status, progress, start and end date of each media
- Docker support
- Multi-users support
- Import from [MyAnimeList](https://myanimelist.net/), [The Movie Database](https://www.themoviedb.org/) and [AniList](https://anilist.co/)


## Docker-compose

https://github.com/FuzzyGrim/Yamtrack/blob/501ad2154a5fa9b86bc1b5b002a6b6c298dc600c/docker-compose.yml#L1-L25

### Environment variables

| Name           |  Type       | Description                | Required     | Default    | Notes                                 |
| -------------- | ----------- | -------------------------- | ------------ | ---------- | ------------------------------------- |
| SECRET         | String      | Django secret key          | Yes          | 'secret'   |                                       |
| TMDB_API       | String      | The Movie Database API key | Yes          | None       | Required for movies and tv shows      |
| MAL_API        | String      | MyAnimeList API key        | Yes          | None       | Required for anime and manga          |
| ALLOWED_HOSTS  | List        | Base IP / Domain           | Yes          | 127.0.0.1  | Your list would extend the default    |
| PUID           | Int         | User ID                    | No           | 911        |                                       |
| PGID           | Int         | Group ID                   | No           | 911        |                                       |
| TZ             | String      | Timezone                   | No           | UTC        |                                       |
| HTTPS_COOKIES  | Bool        | Cookies over HTTPS         | No           | False      | [SESSION_COOKIE_SECURE](https://docs.djangoproject.com/en/4.1/ref/settings/#std-setting-SESSION_COOKIE_SECURE) and [CSRF_COOKIE_SECURE](https://docs.djangoproject.com/en/4.1/ref/settings/#std-setting-CSRF_COOKIE_SECURE)|
| REGISTRATION   | Bool        | User registration          | No           | True       |                                       |
| ADMIN_ENABLED  | Bool        | Django admin page          | No           | False      |                                       |
| DEBUG          | Bool        | Django debug mode          | No           | False      |                                       |


### Environment variables for PostgreSQL

| Name           |  Type       | Description                | Required     | Default    | Notes                                 |
| -------------- | ----------- | -------------------------- | ------------ | ---------- | ------------------------------------- |
| DB_HOST        | String      | Database host              | No           | None       | When not set, sqlite is used          |
| DB_PORT        | Int         | Database port              | No           | 5432       |                                       |
| DB_NAME        | String      | Database name              | No           |'yamtrack'  |                                       |
| DB_USER        | String      | Database user              | No           |'yamtrack'  |                                       |
| DB_PASSWORD    | String      | Database password          | No           |'yamtrack'  |                                       |


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
cd src
python manage.py migrate
python manage.py runserver
```

Go to: [http://localhost:8000](http://localhost:8000)
