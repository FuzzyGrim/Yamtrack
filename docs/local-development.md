# Local Development

This page covers working on Yamtrack from source.

## Prerequisites

- Python 3.12
- Docker
- Redis

## 1. Clone the repository

```bash
git clone https://github.com/FuzzyGrim/Yamtrack.git
cd Yamtrack
```

## 2. Start Redis

If you do not already have Redis running locally, start it with Docker:

```bash
docker run -d --name redis -p 6379:6379 --restart unless-stopped redis:8-alpine
```

## 3. Create a virtual environment

```bash
python -m venv venv
venv/bin/python -m pip install -U -r requirements-dev.txt
venv/bin/pre-commit install
```

## 4. Configure environment values

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

## 5. Prepare the database

```bash
cd src
../venv/bin/python manage.py migrate
```

## 6. Run the app

Run the Django development server:

```bash
cd src
../venv/bin/python manage.py runserver
```

Run the Celery worker with the scheduler in another terminal:

```bash
cd src
../venv/bin/celery -A config worker --beat --scheduler django --loglevel DEBUG
```

Run Tailwind in another terminal:

```bash
cd src
../venv/bin/tailwindcss -i ./static/css/input.css -o ./static/css/tailwind.css --watch
```

Open the development server at:

```text
http://localhost:8000
```

## Testing

Run the Django test suite from the `src` directory:

```bash
cd src
../venv/bin/python manage.py test --parallel
```

To run tests for a specific app or test module, pass the test label after `test`:

```bash
cd src
../venv/bin/python manage.py test app.tests --parallel
```

## Documentation

Install the docs dependencies with the development requirements, then serve the current checkout:

```bash
venv/bin/mkdocs serve --livereload
```
