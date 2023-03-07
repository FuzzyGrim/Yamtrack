# Yamtarr

[![App Tests](https://github.com/FuzzyGrim/Yamtarr/actions/workflows/app-tests.yml/badge.svg)](https://github.com/FuzzyGrim/Yamtarr/actions/workflows/app-tests.yml)
[![Docker Image](https://github.com/FuzzyGrim/Yamtarr/actions/workflows/docker-image.yml/badge.svg)](https://github.com/FuzzyGrim/Yamtarr/actions/workflows/docker-image.yml)
[![CodeFactor](https://www.codefactor.io/repository/github/fuzzygrim/yamtarr/badge)](https://www.codefactor.io/repository/github/fuzzygrim/yamtarr)
[![Codecov](https://codecov.io/github/FuzzyGrim/Yamtarr/branch/main/graph/badge.svg?token=PWUG660120)](https://codecov.io/github/FuzzyGrim/Yamtarr)
[![GitHub](https://img.shields.io/badge/license-GPL--3.0-blue)](https://github.com/FuzzyGrim/Yamtarr/blob/main/LICENSE)


Yamtarr is a self hosted simple media tracker. You can track movies, tv shows, anime and manga.

It uses [The Movie Database](https://www.themoviedb.org/) and [MyAnimeList](https://myanimelist.net/) APIs to fetch media information.

## Features

- Track movies, tv shows, anime and manga
- Save score, status, progress of each media
- Individual tracking for each tv show season
- Docker support
- Multiple users support
- Import from [MyAnimeList](https://myanimelist.net/), [The Movie Database](https://www.themoviedb.org/) and [AniList](https://anilist.co/)

# Installation

Clone the repository and change directory to it.

```bash
git clone https://github.com/FuzzyGrim/Yamtarr.git
cd Yamtarr
```

## Docker-compose

```yml
version: "3"
services:
  yamtarr:
    container_name: yamtarr
    image: ghcr.io/fuzzygrim/yamtarr
    restart: unless-stopped
    environment:
      - TMDB_API=TMDB_API_KEY
      - MAL_API=MAL_API_KEY
      - SECRET=long_random_string
      # Change this to your domain or IP
      - ALLOWED_HOSTS=192.168.x.x, yamtarr.domain.com
      - PUID=1000
      - PGID=1000
      # - HTTPS_COOKIES=False
      # - REGISTRATION=False
    volumes:
      - ./db:/app/db
      - media:/app/media
    ports:
      - "8000:8000"

volumes:
  media:
```

## Environment variables

| Name           |  Type       | Description                | Required     | Default   | Notes                                 |
| -------------- | ----------- | -------------------------- | ------------ | --------- | ------------------------------------- |
| SECRET         | String      | Django secret key          | Yes          | 'secret'  |                                       |
| TMDB_API       | String      | The Movie Database API key | Yes          | None      | Required for movies and tv shows      |
| MAL_API        | String      | MyAnimeList API key        | Yes          | None      | Required for anime and manga          |
| ALLOWED_HOSTS  | List        | Base IP / Domain           | Yes          | 127.0.0.1 | Your list would extend the default    |
| PUID           | Int         | User ID                    | No           | 911       |                                       |
| PGID           | Int         | Group ID                   | No           | 911       |                                       |
| HTTPS_COOKIES  | Bool        | Cookies over HTTPS         | No           | False     | Avoids transmitting cookies over HTTP |
| REGISTRATION   | Bool        | Enable user registration   | No           | True      |                                       |
| ADMIN_ENABLED  | Bool        | Django admin page          | No           | False     |                                       |
| DEBUG          | Bool        | Django debug mode          | No           | False     |                                       |


## Local development

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
cd yamtarr
python manage.py migrate
python manage.py runserver
```

Go to: [http://localhost:8000](http://localhost:8000)