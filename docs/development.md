# Development

This page covers working on Yamtrack from source.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [tailwindcss CLI](https://tailwindcss.com/docs/installation/tailwind-cli) (install with `npm install -g tailwindcss @tailwindcss/cli`)
- Docker
- Redis

## General setup

### Clone the repository

```bash
git clone https://github.com/FuzzyGrim/Yamtrack.git
cd Yamtrack
```

### Start Redis

If you do not already have Redis running locally, start it with Docker:

```bash
docker run -d --name redis -p 6379:6379 --restart unless-stopped redis:8-alpine
```

### Install dependencies

uv manages the Python environment and dependencies:

```bash
uv sync
uv run pre-commit install
```

Installing the development dependencies includes pre-commit. After `uv run pre-commit install`, the hooks run automatically before each commit. You can also run the full hook set manually:

```bash
uv run pre-commit run --all-files
```

### Configure environment values

Create a `.env` file in the repository root:

```bash
TMDB_API=API_KEY
MAL_API=API_KEY
IGDB_ID=IGDB_ID
IGDB_SECRET=IGDB_SECRET
STEAM_API_KEY=STEAM_API_SECRET
BGG_API_TOKEN=BGG_API_TOKEN
SECRET=SECRET
DEBUG=True
```

See [Environment Variables](env-variables.md) for the full list of supported settings.

### Prepare the database

```bash
cd src
uv run manage.py migrate
```

### Run the app

Run the Django development server:

```bash
cd src
uv run manage.py runserver
```

Run the Celery worker with the scheduler in another terminal:

```bash
cd src
uv run celery -A config worker --beat --scheduler django --loglevel DEBUG
```

Run Tailwind in another terminal:

```bash
cd src
tailwindcss -i ./static/css/input.css -o ./static/css/tailwind.css --watch
```

Open the development server at:

```text
http://localhost:8000
```

## Documentation

Install the docs dependency group, then serve the docs from the current checkout:

```bash
uv sync --group docs
uv run zensical serve
```

## Testing

Install Playwright browsers before running integration tests:

```bash
uv run playwright install
```

Run the Django test suite from the `src` directory:

```bash
cd src
uv run manage.py test --parallel
```

To run tests for a specific app or test module, pass the test label after `test`:

```bash
cd src
uv run manage.py test app.tests --parallel
```
