# Yamtarr

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
    build:
      context: .
    environment:
      - TMDB_API=API_KEY
      - MAL_API=API_KEY
      - SECRET=SECRET
    volumes:
      - db:/app/db
      - static:/app/static
      - media:/app/media

  yamtarr-gateway:
    container_name: yamtarr-gateway
    image: nginxinc/nginx-unprivileged:1-alpine
    volumes:
      - ./default.conf:/etc/nginx/conf.d/default.conf
      - static:/vol/static
      - media:/vol/media
    ports:
      - "8080:8080"
    depends_on:
      - yamtarr

volumes:
  db:
  static:
  media:
```

## Environment variables

| Name           |  Type       | Description                | Required     | Default  |
| -------------- | ----------- | -------------------------- | ------------ | -------- |
| SECRET         | String      | Django secret key          | Yes          | None     |
| TMDB_API       | String      | The Movie Database API key | Yes          | None     |
| MAL_API        | String      | MyAnimeList API key        | Yes          | None     |
| ALLOWED_HOSTS  | List        | Base domain URL            | No           | None     |
| PUID           | Int         | User ID                    | No           | 911      |
| PGID           | Int         | Group ID                   | No           | 911      |
| REGISTRATION   | Bool        | Enable user registration   | No           | True     |
| ADMIN_ENABLED  | Bool        | Django admin page          | No           | False    |
| DEBUG          | Bool        | Django debug mode          | No           | False    |


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
python -m pip install -U -r requirements.txt
cd yamtarr
python manage.py migrate
python manage.py runserver
```

Go to: [http://localhost:8000](http://localhost:8000)