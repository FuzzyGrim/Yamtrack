[Align and update files](https://chatgpt.com/c/68ceb525-dd48-8320-b230-ff70f7b2271a)

# Yamtrack Django Media Tracker Development Guide

## Project Overview

Yamtrack is a self‑hosted media tracker for movies, TV shows, anime, manga,  video games, books and comics. Users can track seasons and individual  episodes, save ratings and progress, record repeats, and maintain a history of  all interactions with a title[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/README.md#:~:text=Yamtrack%20is%20a%20self%20hosted,manga%2C%20video%20games%20and%20books).

The application allows  custom media entries for niche titles, supports personal lists with  collaboration, and provides a calendar that can be subscribed to via an  iCalendar (.ics) URL[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/README.md#:~:text=,ics%29%20URL). Notifications about upcoming  releases are delivered through the Apprise framework, which supports  Discord, Telegram, Slack, email and many more services[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/README.md#:~:text=,Slack%2C%20email%2C%20and%20many%20more).

Yamtrack integrates with Jellyfin, Plex and Emby to automatically track  watched media and imports data from Trakt, Simkl, MyAnimeList, AniList and  Kitsu[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/README.md#:~:text=,file%20and%20import%20it%20back). Multiple users can maintain separate  accounts, and authentication is flexible thanks to django‑allauth’s support  for OIDC and over one hundred social providers[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/README.md#:~:text=,allauth).

## Tech Stack

Yamtrack relies on a modern Django stack with additional services and libraries to deliver a rich, responsive experience:

* **Programming language and framework:** Python 3.12 and Django 5.2[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/requirements.txt#:~:text=Django%3D%3D5.2.2%20django). The  codebase adheres to PEP 8 and PEP 257 guidelines and uses Ruff and DjLint  for linting and formatting.
* **Database:** SQLite by default for lightweight deployments. PostgreSQL is  supported and recommended for production; the docker‑compose configuration  includes a postgres variant[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/README.md#:~:text=). The application  automatically creates a local `db` folder for SQLite if no `DB_HOST`  environment variable is provided[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/src/config/settings.py#:~:text=).
* **Task queue:** Celery 5.5 with a Redis broker for asynchronous tasks and  scheduled jobs[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/requirements.txt#:~:text=celery%3D%3D5.5.3%20croniter%3D%3D6.0.0%20Django%3D%3D5.2.2%20django,results%3D%3D2.6.0). `django_celery_beat` and  `django_celery_results` persist schedules and task results[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/src/config/settings.py#:~:text=INSTALLED_APPS%20%3D%20%5B%20,).
* **Caching:** Redis is used as the caching backend with a 24‑hour timeout by default[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/src/config/settings.py#:~:text=,%7D%2C%20%7D%2C). Cache versioning is configured to support rolling deployments.
* **Authentication and user management:** `django‑allauth` provides local  accounts, OIDC, and numerous social logins[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/src/config/settings.py#:~:text=). Yamtrack defines a custom `users.User` model that extends `AbstractUser` with per‑media‑type preferences, notification settings and integration tokens
* **Front‑end:** HTML templates rendered by Django, styled with Tailwind CSS  (via a node build pipeline) and enhanced using `django‑select2` for  autocomplete fields and `widget_tweaks` for form customisation[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/requirements.txt#:~:text=django). Static  assets are served from the `static/` directory and collected into  `staticfiles/` for production[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/src/config/settings.py#:~:text=,files).

*   **Other key libraries:**
    * `django‑simple‑history` for model change auditing[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/requirements.txt#:~:text=django).
    * `django‑model‑utils` for utilities such as `StatusField` and `Choices`[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/requirements.txt#:~:text=django).
    * `django‑redis` for cache integration[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/requirements.txt#:~:text=django).
    * `gunicorn` as the WSGI server for production[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/requirements.txt#:~:text=gunicorn%3D%3D23).
    * `python‑decouple` to manage environment variables[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/requirements.txt#:~:text=python).
    * `requests` and `aiohttp` for HTTP calls to external APIs[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/requirements.txt#:~:text=aiohttp%3D%3D3).
    * `Apprise` for sending notifications to multiple channels[raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/requirements.txt#:~:text=apprise%3D%3D1).

## Project Structure

The repository is organised around the Django project in `src/` and is
designed to be modular and scalable:
```bash
> Yamtrack/
├── docker-compose.yml           # SQLite‑based Docker deployment
├── docker-compose.postgres.yml  # PostgreSQL‑based deployment
├── Dockerfile                   # Container build instructions
├── entrypoint.sh                # Entrypoint script for Docker
├── requirements.txt             # Production dependencies
├── requirements-dev.txt         # Development dependencies
├── pyproject.toml               # Linting and tooling configuration
└── src/
    ├── manage.py                # Django management script
    ├── config/                  # Django settings, URLs, WSGI and celery config
    │   ├── settings.py          # Settings with environment variable support
    │   ├── celery.py            # Celery application
    │   ├── urls.py              # URL configuration
    │   ├── wsgi.py              # WSGI entry point
    │   └── test_settings.py     # Settings for tests
    ├── app/                     # Core application: models, views, forms, context
    ├── events/                  # Scheduled tasks and event tracking
    ├── integrations/            # Third‑party API integrations
    ├── lists/                   # User‑created lists and collaboration
    ├── users/                   # Custom user model and account management
    ├── static/                  # CSS, JavaScript and images
    └── templates/               # Django templates
```
## Key Folders
* app/ – Contains core models representing media items, seasons, episodes, progress entries and media types.  Views in this module provide list, detail and create/update functionality for each media type.  Forms are built using ModelForm and enhanced with select2 widgets.
* events/ – Defines Celery tasks and schedules for recurring jobs, including calendar reloading, sending release notifications and daily digests.  It also contains signals to trigger tasks based on model events.
* integrations/ – Houses client wrappers for external APIs such as TMDB, MyAnimeList, IGDB, Steam, Hardcover, ComicVine, Trakt and Simkl. Each wrapper normalises data into Yamtrack’s models and handles rate limiting and caching.  Integration views and API endpoints reside here.
* lists/ – Implements personal lists and collaborative lists.  Models include List and ListItem.  Views allow creation, editing, reordering and sharing of lists.
* users/ – Defines the custom user model and related forms.  The model extends AbstractUser with per‑media‑type layout, sort and status preferences, notification settings, external service tokens and a many‑to‑many field for excluding items from notifications ￼. It also includes helpers for updating preferences and ensures values are validated using database constraints ￼.

## Development Guidelines

### General Principles
	1.	Use Django’s features first.  Rely on built‑in generic views
(ListView, DetailView, CreateView, UpdateView) and forms
(ModelForm) before writing custom logic.  Keep views thin; move
business rules into models and services.
	2.	Follow PEP 8 and PEP 257.  Maintain consistent naming and spacing.
Use Ruff and DjLint to check Python and template code.  Include
docstrings for modules, classes and functions.
	3.	Use class‑based views (CBVs) for complex logic.  For simple actions
such as toggling a status or marking an episode watched, a concise
function‑based view with @require_http_methods is acceptable.
	4.	Encapsulate integration logic.  API wrappers live in
integrations/.  Don’t scatter HTTP calls throughout views; this makes
error handling and caching easier.
	5.	Prioritise security.  Set DEBUG=False in production; keep
SECRET and API keys out of the repository; configure
ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS and URLS correctly.
	6.	Write tests.  Yamtrack uses pytest and pytest‑django.  Write
tests for models, views, forms and integrations.  Aim for high coverage
and use factories to create test data.

## Example View

The following view lists media items of a given type.  It uses ListView
with pagination and selects related objects to reduce queries.  The
MediaFilter encapsulates filtering logic such as status or sorting.

```python
from django.views.generic import ListView
from django.contrib.auth.mixins import LoginRequiredMixin
from app.models import Item
from app.filters import MediaFilter


class MediaListView(LoginRequiredMixin, ListView):
    model = Item
    template_name = "media/list.html"
    context_object_name = "items"
    paginate_by = 24

    def get_queryset(self):
        media_type = self.kwargs["media_type"]
        qs = (
            Item.objects.filter(user=self.request.user, media_type=media_type)
            .select_related("title")
            .prefetch_related("progress_entries")
        )
        return MediaFilter(self.request.GET, queryset=qs).qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"media_type": self.kwargs["media_type"]})
        return context
```

## Example Form and Validation

Use ModelForm for data input and encapsulate validation logic in the form or model.  The following example demonstrates a custom media entry form that generates a slug automatically and validates that at least one field is provided.

```python
from django import forms
from app.models import Item
from django.utils.text import slugify


class CustomMediaForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = ["title", "media_type", "description", "release_date"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
        }

    def clean(self):
        cleaned = super().clean()
        title = cleaned.get("title")
        description = cleaned.get("description")
        if not title and not description:
            raise forms.ValidationError("Title or description must be provided.")
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.slug = slugify(obj.title)  # auto‑generate slug
        if commit:
            obj.save()
        return obj
```

## Environment Setup

### Prerequisites
* Python and Node: Python 3.12 and Node.js (≥ 16) are required.  Node is used to build Tailwind CSS.
* Redis: A running Redis instance is necessary for caching and Celery.
You can start a container with docker run -d --name redis -p 6379:6379 --restart unless-stopped redis:7-alpine.
* Database: SQLite is the default.  For PostgreSQL, you need a running
server and must set the DB_* environment variables.

### Quick Start with Docker
	1.	Clone the repository and change into the directory:

```bash
  git clone https://github.com/FuzzyGrim/Yamtrack.git
cd Yamtrack
```
	2.	Copy the example docker-compose.yml to your environment.  Create a .env file and define at least SECRET, API keys and other variables (see below).  Run the stack:
```bash
docker-compose up -d
```
This configuration uses SQLite and is sufficient for most personal deployments
	3.	If you need PostgreSQL, use the docker-compose.postgres.yml file and set
DB_HOST, DB_NAME, DB_USER, DB_PASSWORD and DB_PORT in your .env
file.
	4.	When using a reverse proxy (e.g. Nginx, Traefik), set the URLS
environment variable to the external URL(s) of the app to avoid 403 errors.  Provide the full origin (including protocol) and separate multiple values with commas.

### Local Development without Docker
	1.	Install Redis or start it via Docker as shown above.
	2.	Create and activate a virtual environment, then install development
dependencies:

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -U -r requirements-dev.txt
```
	3.	In the project root, create a .env file with at least the following variables:
```bash
TMDB_API=<your_tmdb_api_key>
MAL_API=<your_mal_api_key>
IGDB_ID=<your_igdb_id>
IGDB_SECRET=<your_igdb_secret>
STEAM_API_KEY=<your_steam_api_key>
SECRET=<a_secure_secret_key>
DEBUG=True
```

Add additional keys for Trakt, Simkl, Hardcover and ComicVine if you plan to use those integrations.  See the Environment Variables wiki page for a complete list.

	4.	Apply migrations and start the development server and workers:
```bash
cd src
python manage.py migrate
# Start Django, Celery worker, Celery beat and Tailwind watcher concurrently
python manage.py runserver & \
    celery -A config worker --beat --scheduler django --loglevel DEBUG & \
    tailwindcss -i ./static/css/input.css -o ./static/css/tailwind.css --watch
``` [oai_citation:29‡raw.githubusercontent.com](https://raw.githubusercontent.com/FuzzyGrim/Yamtrack/dev/README.md#:~:text=Then%20run%20the%20following%20commands)
```

	5.	Visit http://localhost:8000 to access the application ￼.

## Environment Variables

Yamtrack uses python‑decouple to read configuration from environment
variables.  At minimum you should define:
	•	SECRET – Django’s secret key used for signing cookies and CSRF
tokens.
	•	DEBUG – Set to False in production.
	•	ALLOWED_HOSTS – Comma‑separated list of hostnames.  When not set,
defaults to * and automatically includes localhost ￼.
	•	URLS – External origin(s) for the app when behind a reverse proxy.
	•	CSRF – Additional CSRF trusted origins.
	•	REDIS_URL – Redis connection string for caching and Celery ￼.
	•	DB_* – Database configuration variables when using PostgreSQL
(DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT, optional
DB_SSL_MODE and DB_SSL_CERT_MODE) ￼.
	•	Media provider APIs – TMDB_API, MAL_API, IGDB_ID, IGDB_SECRET,
STEAM_API_KEY, HARDCOVER_API, COMICVINE_API, TRAKT_API, TRAKT_API_SECRET,
SIMKL_ID and SIMKL_SECRET ￼.  Each integration uses its
own environment variables, and you can override NSFW flags (e.g.
TMDB_NSFW) and preferred language (TMDB_LANG).

## Quality Assurance and Testing

### Linting and Formatting

* Ruff enforces code quality and style rules.  Run ruff check . to validate Python files.  The configuration in pyproject.toml sets the target Python version to 3.12 ￼ and customises ignored rules.
* DjLint checks and auto‑formats Django templates.  Run djlint --lint src/templates to lint templates and djlint --reformat src/templates to auto‑format them.

## Tests
	•	pytest with pytest‑django is used for running tests.  Execute pytest
from the repository root.  Use the --cov option to generate coverage
reports.  Example:

```bash
pytest --cov=src
```

* Fakeredis is included in the development dependencies to simulate
Redis in tests ￼.

## Continuous Integration

GitHub Actions run tests, build Docker images and enforce code quality.  The badges in the README show the status of the test and Docker workflows.  Keep the build passing by fixing lint errors and maintaining test coverage.

Deployment Guide

Running in Production
	1.	Use PostgreSQL.  Configure the DB_* variables and use the
docker-compose.postgres.yml file or a managed PostgreSQL instance.
	2.	Disable Debug Mode.  Set DEBUG=False and provide a strong
SECRET.  Define ALLOWED_HOSTS explicitly.  Configure URLS and
CSRF_TRUSTED_ORIGINS to include your domains.
	3.	Serve via WSGI.  Use Gunicorn as the WSGI server:

```bash
gunicorn config.wsgi:application --workers 3 --bind 0.0.0.0:8000 --log-level=info
```

Run Gunicorn behind a reverse proxy (e.g. Nginx or Traefik) to handle TLS
termination and static file serving.

	4.	Run Celery Workers and Beat.  Start separate processes for Celery worker and beat.  Example systemd units:

```bash
# /etc/systemd/system/yamtrack-worker.service
[Unit]
Description=Yamtrack Celery Worker
After=redis.service

[Service]
User=yamtrack
WorkingDirectory=/var/www/yamtrack/src
EnvironmentFile=/var/www/yamtrack/.env
ExecStart=/usr/local/bin/celery -A config worker --loglevel=INFO
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
# /etc/systemd/system/yamtrack-beat.service
[Unit]
Description=Yamtrack Celery Beat
After=yamtrack-worker.service

[Service]
User=yamtrack
WorkingDirectory=/var/www/yamtrack/src
EnvironmentFile=/var/www/yamtrack/.env
ExecStart=/usr/local/bin/celery -A config beat --scheduler django --loglevel=INFO
Restart=always

[Install]
WantedBy=multi-user.target
```

	5.	Collect static files and compress assets.  Run python manage.py collectstatic to collect static assets into STATIC_ROOT.  Use a CDN or Nginx to serve them.
	6.	Enable health checks.  Include the health_check URLs in your production urls.py and restrict access via your reverse proxy.  Health checks cover the database, cache, Celery, storage and migrations ￼.
	7.	Backups and Monitoring.  Schedule database backups and monitor logs (Yamtrack logs to stdout using a verbose formatter ￼).  Consider using django-admin log entries and Celery results for troubleshooting.

## Asynchronous Tasks and Scheduling

Yamtrack uses Celery with django_celery_beat to run background jobs.  The default schedule is defined in config/settings.py ￼ and includes:

* Reload calendar – Refreshes upcoming release dates every 24 hours.
* Send release notifications – Runs every 10 minutes to notify users via Apprise ￼.
* Send daily digest – Sends a daily summary of upcoming releases at a configurable hour (default 08:00) ￼.

Custom tasks should be defined in events/tasks.py using the @shared_task decorator.  Avoid long‑running synchronous calls inside tasks; use async HTTP clients (aiohttp) where appropriate and respect API rate limits by using requests‑ratelimiter ￼.

## Example Task

```python
from celery import shared_task
from apprise import Apprise, AppriseConfig
from users.models import User
from app.services import get_upcoming_releases


@shared_task
def send_release_notifications():
    """Send notifications about newly released media to all users."""
    releases = get_upcoming_releases()
    for user in User.objects.filter(release_notifications_enabled=True):
        if not user.notification_urls:
            continue
        apobj = Apprise()
        apobj.add(user.notification_urls)
        message = format_release_digest(releases)
        apobj.notify(
            body=message,
            title="New releases available!",
        )
```

## Third‑Party Integrations

Yamtrack integrates with several external services.  Each integration uses a
dedicated module in integrations/ and expects environment variables for

### API keys:
* TMDB (The Movie Database) – Provides metadata for movies and TVshows.  Requires TMDB_API and supports optional TMDB_NSFW and TMDB_LANG settings.
* MyAnimeList (MAL) – Used for anime and manga metadata.  Requires MAL_API and supports MAL_NSFW.
* IGDB – Supplies data for video games.  Requires IGDB_ID and IGDB_SECRET ￼.
* Steam – Pulls game ownership data and playtime.  Requires STEAM_API_KEY ￼.
* Hardcover, ComicVine, Trakt, Simkl – Provide book and comic metadata and import/export capabilities.  Each requires its own API keys.
* Jellyfin, Plex, Emby – Webhook integrations allow Yamtrack to mark media as watched automatically.  Users specify their usernames or tokens in their profile (e.g. plex_usernames) ￼.

## Notifications

Notifications are delivered via Apprise.  Users can specify one or morenotification URLs in their profile.  Yamtrack supports Discord, Telegram,ntfy, Slack, email and many other channels ￼.  SeeApprise’s documentation for the list of supported services.  Thesend_release_notifications and send_daily_digest Celery tasks iterate overusers with notifications enabled and send formatted messages.

## Caching and Rate Limiting

Yamtrack uses django‑redis for the default cache with a 24‑hour timeout.  Cache versioning ensures invalidation when deploying new releases.  When fetching data from external APIs, caches should be used to avoid hitting rate limits.  Additionally, the requests‑ratelimiter package enforces client‑side rate limits for HTTP calls .

## Health Checks, Logging and Monitoring

The health_check app is enabled by default and provides endpoints to test database connectivity, Redis, Celery and storage ￼. Expose these endpoints behind an authenticated route or restrict access to internal networks.  Logging is configured to output a verbose format with timestamps, process IDs and log levels ￼.  Separate loggers adjust verbosity for dependencies such as requests and psycopg. Consider forwarding logs to a central monitoring system and set up alerts on task failures or HTTP 5xx responses.

## Common Issues

The following are common pitfalls and their solutions:

* 403 Forbidden via reverse proxy: Ensure the URLS environment variable contains the external origin(s) with the correct protocol, and does not include a trailing path ￼.
* Celery tasks not executing: Check that both the worker and beat processes are running.  Verify REDIS_URL is accessible and that the result backend is configured correctly.  Inspect logs for errors.
* Static files not loading in production: Run collectstatic and configure your reverse proxy or CDN to serve files from the STATIC_ROOT directory ￼.
* Missing API data: Confirm the relevant API keys are present in .env.  Some providers (e.g Steam, Hardcover) require tokens in a specific format; refer to the provider documentation.
* Authentication issues: Ensure ACCOUNT_DEFAULT_HTTP_PROTOCOL matches
your deployment (HTTP vs HTTPS).  When mixing protocols in CSRF_TRUSTED_ORIGINS,
Django defaults to HTTPS ￼.

## Contributing

Contributions are welcome.  To report a bug, open a GitHub issue with reproduction steps ￼.  For feature suggestions, submit an issue describing the use case ￼.  Pull requests should target the dev branch and include tests and updated documentation where appropriate.  Please run ruff and pytest locally before submitting to keep the CI pipeline green.  For style and tone, adhere to the guidance in this document and the Google Developer Documentation Style Guide.

## Further Reading
* Django Documentation: https://docs.djangoproject.com/
* Celery Documentation: https://docs.celeryq.dev/
* Django Allauth: https://django-allauth.readthedocs.io/
* Apprise: https://github.com/caronc/apprise
* Redis: https://redis.io/
* Tailwind CSS: https://tailwindcss.com/
*  Yamtrack Environment Variables: See the project’s wiki for a full list of configuration variables ￼