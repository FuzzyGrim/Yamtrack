# Yamtrack

![App Tests](https://github.com/FuzzyGrim/Yamtrack/actions/workflows/app-tests.yml/badge.svg)
![Docker Image](https://github.com/FuzzyGrim/Yamtrack/actions/workflows/docker-image.yml/badge.svg)
![Image Size](https://ghcr-badge.egpl.dev/fuzzygrim/yamtrack/size)
![CodeFactor](https://www.codefactor.io/repository/github/fuzzygrim/yamtrack/badge)
![Codecov](https://codecov.io/github/FuzzyGrim/Yamtrack/branch/dev/graph/badge.svg?token=PWUG660120)
![GitHub](https://img.shields.io/badge/license-GPL--3.0-blue)

Yamtrack is a self hosted media tracker for movies, tv shows, anime and manga.

You can try the app at [yamtrack.fuzzygrim.com](https://yamtrack.fuzzygrim.com) using the username `demo` and password `demo`.

## Features

- Track movies, tv shows, anime, manga and games
- Track each season of a tv show individually and episodes watched
- Save score, status, progress, repeats (rewatches, rereads...), start and end dates, or write a note.
- Docker support
- Multi-users support
- Import from [MyAnimeList](https://myanimelist.net/), [The Movie Database](https://www.themoviedb.org/), [AniList](https://anilist.co/) and CSV.
- Export all your tracked media to a CSV file

## Installing with Docker

Copy the default `docker-compose.yml` file from the repository and set the environment variables. This would use a SQlite database, which is enough for most use cases.

To start the containers run:

```bash
docker-compose up -d
```

Alternatively, if you need a PostgreSQL database, you can use the `docker-compose.postgres.yml` file.

### Environment variables

| Name            | Type   | Notes                                                                                                                                                                       |
| --------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| TMDB_API        | String | The Movie Database API key for movies and tv shows, a default key is provided                                                                                               |
| TMDB_NSFW       | Bool   | Default to false, set to true to include adult content in tv and movie searches                                                                                             |
| TMDB_LANG       | String | TMDB metadata language, uses a Language code in ISO 639-1 e.g "en", for more specific results a country code in ISO 3166-1 can be added e.g "en-US"                         |
| MAL_API         | String | MyAnimeList API key, for anime and manga, a default key is provided                                                                                                         |
| MAL_NSFW        | Bool   | Default to false, set to true to include adult content in anime and manga searches                                                                                          |
| IGDB_ID         | String | IGDB API key for games, a default key is provided but it's recommended to get your own as it has a low rate limit.                                                          |
| IGDB_SECRET     | String | IGDB API secret for games, a default value is provided but it's recommended to get your own as it has a low rate limit.                                                     |
| IGDB_NSFW       | Bool   | Default to false, set to true to include adult content in game searches                                                                                                     |
| REDIS_URL       | String | Redis is recommended for better performance                                                                                                                                 |
| SECRET          | String | [Secret key](https://docs.djangoproject.com/en/stable/ref/settings/#secret-key) used for cryptographic signing, should be a random string                                   |
| ALLOWED_HOSTS   | List   | Host/domain names that this Django site can serve, set this to your domain name if exposing to the public                                                                   |
| REGISTRATION    | Bool   | Default to true, set to false to disable user registration                                                                                                                  |
| DEBUG           | Bool   | Default to false, set to true for debugging                                                                                                                                 |
| PUID            | Int    | User ID for the app, default to 1000                                                                                                                                        |
| PGID            | Int    | Group ID for the app, default to 1000                                                                                                                                       |
| TZ              | String | Timezone, default to UTC                                                                                                                                                     |
| WEB_CONCURRENCY | Int    | Number of webserver processes, default to 1 but it's recommended to have a value of [(2 x num cores) + 1](https://docs.gunicorn.org/en/latest/design.html#how-many-workers) |

### Environment variables for PostgreSQL

| Name        | Type   | Notes                        |
| ----------- | ------ | ---------------------------- |
| DB_HOST     | String | When not set, sqlite is used |
| DB_PORT     | Int    |                              |
| DB_NAME     | String |                              |
| DB_USER     | String |                              |
| DB_PASSWORD | String |                              |

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
IGDB_ID=IGDB_ID
IGDB_SECRET=IGDB_SECRET
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
