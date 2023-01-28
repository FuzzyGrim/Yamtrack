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
    image: ghcr.io/fuzzygrim/yamtarr
    environment:
      - TMDB_API=TMDB_API_KEY
      - MAL_API=MAL_API_KEY
      - SECRET=long_random_string
      # Change this to your domain or IP
      - ALLOWED_HOSTS=192.168.x.x, yamtarr.domain.com
      - PUID=1000
      - PGID=1000
    volumes:
      - ./db:/app/db
      - assets:/app/assets

  yamtarr-gateway:
    container_name: yamtarr-gateway
    image: ghcr.io/fuzzygrim/yamtarr-gateway
    volumes:
      - assets:/vol
    ports:
      - "8080:8080"
    depends_on:
      - yamtarr

volumes:
  assets:
```

## Environment variables

| Name           |  Type       | Description                | Required     | Default  |
| -------------- | ----------- | -------------------------- | ------------ | -------- |
| SECRET         | String      | Django secret key          | Yes          | None     |
| TMDB_API       | String      | The Movie Database API key | Yes          | None     |
| MAL_API        | String      | MyAnimeList API key        | Yes          | None     |
| ALLOWED_HOSTS  | List        | Base IP / Domain           | Yes          | None     |
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