"""Django settings for Floppy project."""

import contextlib
import hashlib
import json
import os
import secrets
import stat
import subprocess
import sys
import tempfile
import warnings
import zoneinfo
from pathlib import Path
from urllib.parse import urljoin, urlparse

from celery.schedules import crontab
from decouple import (
    Csv,
    Undefined,
    UndefinedValueError,
    config,
    undefined,
)
from django.core.cache import CacheKeyWarning
from django.core.exceptions import ImproperlyConfigured
from django.db.backends.signals import connection_created
from redis.backoff import NoBackoff
from redis.retry import Retry

from config.runtime_profile import (
    PROFILE as RESOURCE_PROFILE,
)
from config.runtime_profile import (
    by_tier,
    gunicorn_max_worker_memory_bytes,
    gunicorn_threads,
    web_concurrency,
)

# How much machine we're on. Every tier-scaled default below reads from this
# single detection rather than repeating its own heuristic; see
# config/runtime_profile.py for what is probed and why (issue #521).
RESOURCE_TIER = RESOURCE_PROFILE.tier
RUNTIME_GUNICORN_THREADS = gunicorn_threads()
RUNTIME_WEB_CONCURRENCY = web_concurrency()

BASE_URL = config("BASE_URL", default=None)
if BASE_URL:
    FORCE_SCRIPT_NAME = BASE_URL

REDIS_PREFIX = config("REDIS_PREFIX", default=None)

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
_data_dir_override = config("FLOPPY_DATA_DIR", default=None)
FLOPPY_DATA_DIR = Path(_data_dir_override) if _data_dir_override else BASE_DIR / "db"
_database_path_override = config("FLOPPY_DB_PATH", default=None)
FLOPPY_DB_PATH = (
    Path(_database_path_override)
    if _database_path_override
    else FLOPPY_DATA_DIR / "db.sqlite3"
)
DOCKER_SECRETS_DIR = Path("/run/secrets")


def secret(key, default=undefined, **kwargs):
    """Try to read a config value from a secret file.

    If only the filename is given, try to read from /run/secrets/<key>.
    If an absolute path is specified, try to read from this path.
    """
    if isinstance(default, Undefined):
        default = None

    file = config(key, default, **kwargs)

    if file is None:
        return undefined
    if file == default:
        return default

    path = Path(file)
    if not path.is_absolute():
        path = DOCKER_SECRETS_DIR / path

    try:
        secret_value = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, IsADirectoryError, OSError, UnicodeDecodeError) as err:
        msg = f"File from {key} not found. Please check the path and filename."
        raise UndefinedValueError(msg) from err

    return secret_value


def _read_docker_secret(path):
    secret_fd = os.open(
        path,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
    )
    try:
        if not stat.S_ISREG(os.fstat(secret_fd).st_mode):
            raise OSError(path)
        os.fchmod(secret_fd, 0o600)
        with os.fdopen(secret_fd, "r", encoding="utf-8") as secret_file:
            secret_fd = None
            value = secret_file.read().strip()
        if not value:
            raise OSError(path)
        return value
    finally:
        if secret_fd is not None:
            os.close(secret_fd)


def _load_or_create_docker_secret(path):
    temp_fd = None
    temp_path = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            return _read_docker_secret(path), False
        except FileNotFoundError:
            pass

        value = secrets.token_urlsafe(50)
        temp_fd, temp_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
        )
        with os.fdopen(temp_fd, "w", encoding="utf-8") as secret_file:
            temp_fd = None
            secret_file.write(value)
            secret_file.flush()
            os.fsync(secret_file.fileno())

        try:
            os.link(temp_path, path, follow_symlinks=False)
        except FileExistsError:
            return _read_docker_secret(path), False
        else:
            return value, True
    except (OSError, UnicodeError) as err:
        msg = (
            f"Cannot create or read the generated secret key at {path}. "
            "Make FLOPPY_DATA_DIR writable or set SECRET."
        )
        raise ImproperlyConfigured(msg) from err
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        if temp_path is not None:
            Path(temp_path).unlink(missing_ok=True)


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/stable/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config("SECRET", default=None)
if not SECRET_KEY:
    SECRET_KEY = secret("SECRET_FILE")

# secret() returns the `undefined` sentinel (truthy) when the env var is absent;
# normalize it so the check below works correctly.
if isinstance(SECRET_KEY, Undefined):
    SECRET_KEY = None

if not SECRET_KEY:
    if Path("/.dockerenv").exists():
        # Docker container: auto-generate and persist to the db volume so the
        # key survives container restarts without user intervention.
        _secret_file = FLOPPY_DATA_DIR / "secret_key"
        SECRET_KEY, secret_created = _load_or_create_docker_secret(_secret_file)
        if secret_created:
            warnings.warn(
                "SECRET env var is not set. A random key has been generated and "
                f"saved to {_secret_file}. For production, set SECRET explicitly "
                "in your docker-compose.yml environment section.",
                stacklevel=2,
            )
    else:
        msg = (
            "SECRET env var is not set. To fix, run:\n\n"
            '  python -c "'
            "import secrets; print('SECRET=' + secrets.token_urlsafe(50))"
            '" >> .env\n'
        )
        raise ImproperlyConfigured(msg)


# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config("DEBUG", default=False, cast=bool)
ENABLE_DEBUG_TOOLBAR = DEBUG and config(
    "ENABLE_DEBUG_TOOLBAR",
    default=True,
    cast=bool,
)
DEBUG_TOOLBAR_INCLUDE_TEMPLATES_PANEL = config(
    "DEBUG_TOOLBAR_INCLUDE_TEMPLATES_PANEL",
    default=False,
    cast=bool,
)

INTERNAL_IPS = ["127.0.0.1"]

# Docker users access via unpredictable LAN IPs, so keep the permissive
# default there. Local dev gets a strict default.
_allowed_hosts_default = "*" if Path("/.dockerenv").exists() else "localhost,127.0.0.1"
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default=_allowed_hosts_default, cast=Csv())

if ALLOWED_HOSTS != ["*"]:
    if "localhost" not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append("localhost")
    if "127.0.0.1" not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append("127.0.0.1")


CSRF_TRUSTED_ORIGINS = config("CSRF", default="", cast=Csv())
CSRF_FAILURE_VIEW = "app.error_views.csrf_failure"

URLS = config("URLS", default="", cast=Csv())

for url in URLS:
    CSRF_TRUSTED_ORIGINS.append(url)
    ALLOWED_HOSTS.append(urlparse(url).hostname)

if BASE_URL:
    # Cookie paths must match FORCE_SCRIPT_NAME exactly to ensure browsers
    # send cookies with all requests under the base URL prefix
    CSRF_COOKIE_PATH = BASE_URL

USE_X_FORWARDED = config("USE_X_FORWARDED", default=False, cast=bool)

# External artwork and links must not receive private page paths or query strings.
# Keep same-origin referrers for Django's HTTPS CSRF fallback when Origin is absent.
SECURE_REFERRER_POLICY = "same-origin"

# Opt in for HTTPS deployments; HTTP LAN installations must remain usable.
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", default=False, cast=bool)
CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", default=False, cast=bool)

SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https")
    if (config("USE_X_FORWARDED_PROTO", default=USE_X_FORWARDED, cast=bool))
    else None
)
USE_X_FORWARDED_HOST = config(
    "USE_X_FORWARDED_HOST", default=USE_X_FORWARDED, cast=bool
)
USE_X_FORWARDED_PORT = config(
    "USE_X_FORWARDED_PORT", default=USE_X_FORWARDED, cast=bool
)

# Application definition

_CELERY_PROCESS = os.environ.get("FLOPPY_PROCESS_ROLE") in {
    "background",
    "combined",
    "interactive",
}

if _CELERY_PROCESS:
    INSTALLED_APPS = [
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "app",
        "events",
        "integrations",
        "lists",
        "users",
        "django_celery_beat",
        "django_celery_results",
        "simple_history",
    ]
else:
    INSTALLED_APPS = [
        "django.contrib.auth",
        "django.contrib.admin",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
        "django.contrib.messages",
        "django.contrib.staticfiles",
        "app",
        "events",
        "integrations",
        "lists",
        "users",
        "django_celery_beat",
        "django_celery_results",
        "django_select2",
        "simple_history",
        "widget_tweaks",
        "health_check",
        "health_check.cache",
        "health_check.storage",
        "health_check.contrib.migrations",
        "health_check.contrib.celery_ping",
        "health_check.contrib.redis",
        "health_check.contrib.db_heartbeat",
        "allauth",
        "allauth.account",
        "allauth.socialaccount",
        "django.contrib.humanize",
        "rest_framework",
        "api",
        "drf_spectacular",
    ]

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        # Enforced globally on purpose: a per-view opt-in is a control that gets
        # forgotten. Views that must stay public set ``permission_classes = []``.
        "api.authentication.HasScope",
        "api.authentication.CanWriteBoundList",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "api.authentication.BearerAuthentication",
        "api.authentication.APIKeyAuthentication",
        # The native client signs in through allauth and calls the API with
        # that session, like the web islands do. HasScope already treats a
        # session (request.auth is None) as full access, and DRF still enforces
        # CSRF on unsafe methods. Keep this last so token clients are unchanged.
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_RENDERER_CLASSES": ("api.renderers.ImageCacheJSONRenderer",),
    # ``format`` is a media-list filter, not a renderer override.
    "URL_FORMAT_OVERRIDE": None,
    "DEFAULT_SCHEMA_CLASS": "api.scope_schema.ScopedAutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Floppy",
    "DESCRIPTION": "A self-hosted media tracker.",
    "VERSION": "1.0.0",
    "CONTACT": {
        "url": "https://github.com/dannyvfilms/Floppy/issues",
    },
    "LICENSE": {
        "name": "AGPL-3.0",
        "url": "https://www.gnu.org/licenses/agpl-3.0.html",
    },
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
}

if ENABLE_DEBUG_TOOLBAR:
    INSTALLED_APPS.append("debug_toolbar")

# Performance instrumentation: thresholded request and task summaries.
PERF_LOG_ENABLED = config("PERF_LOG_ENABLED", default=True, cast=bool)
PERF_LOG_SLOW_REQUEST_MS = config("PERF_LOG_SLOW_REQUEST_MS", default=500, cast=int)
# A request still running after this long is logged once as stalled_request
# with its stack, before it finishes. 0 disables the watchdog.
PERF_STALL_WARN_MS = config("PERF_STALL_WARN_MS", default=10000, cast=int)
PERF_STALL_MAX_REPORTS_PER_MINUTE = config(
    "PERF_STALL_MAX_REPORTS_PER_MINUTE", default=10, cast=int
)
PERF_LOG_SLOW_TASK_MS = config("PERF_LOG_SLOW_TASK_MS", default=5000, cast=int)
# A transaction open longer than this is logged with its request/task and
# duration. SQLite has one writer, so a long transaction is the usual reason
# every other writer waits out busy_timeout (#1609).
PERF_LOG_SLOW_TRANSACTION_MS = config(
    "PERF_LOG_SLOW_TRANSACTION_MS",
    default=1000,
    cast=int,
)
PERF_LOG_QUERY_COUNT_THRESHOLD = config(
    "PERF_LOG_QUERY_COUNT_THRESHOLD",
    default=75,
    cast=int,
)
TRAKT_IMPORT_CHUNK_ROWS = config("TRAKT_IMPORT_CHUNK_ROWS", default=100, cast=int)
TRAKT_IMPORT_CHUNK_TARGET_MS = config(
    "TRAKT_IMPORT_CHUNK_TARGET_MS", default=100, cast=int
)
TRAKT_IMPORT_STAGING_BYTES = config(
    "TRAKT_IMPORT_STAGING_BYTES", default=512 * 1024 * 1024, cast=int
)

# High-water memory attribution (app/memory_envelope.py). Separate from the
# slow-request log above: that one answers "what was slow", this one answers
# "what grew, and was it Python or page cache". Cheap enough -- two short
# /proc reads and three cgroup reads per boundary -- to leave on in
# production, which is the only place the excursions happen.
MEMORY_HIGH_WATER_ENABLED = config(
    "MEMORY_HIGH_WATER_ENABLED",
    default=True,
    cast=bool,
)
# Thresholds are deliberately generous. The events are for excursions, not
# for a picture of normal traffic; a log full of ordinary requests is a log
# nobody reads. These are starting points chosen to catch the known
# production high-water events (a two-minute media list, a snapshot's page
# cache) and are expected to be tuned once real events accumulate. None of
# them is a memory guarantee.
MEMORY_HIGH_WATER_DURATION_MS = config(
    "MEMORY_HIGH_WATER_DURATION_MS",
    default=10_000,
    cast=int,
)
MEMORY_HIGH_WATER_RSS_DELTA_BYTES = config(
    "MEMORY_HIGH_WATER_RSS_DELTA_BYTES",
    default=32 * 1024 * 1024,
    cast=int,
)
MEMORY_HIGH_WATER_HWM_DELTA_BYTES = config(
    "MEMORY_HIGH_WATER_HWM_DELTA_BYTES",
    default=32 * 1024 * 1024,
    cast=int,
)
MEMORY_HIGH_WATER_CGROUP_DELTA_BYTES = config(
    "MEMORY_HIGH_WATER_CGROUP_DELTA_BYTES",
    default=128 * 1024 * 1024,
    cast=int,
)
MEMORY_HIGH_WATER_CGROUP_FILE_DELTA_BYTES = config(
    "MEMORY_HIGH_WATER_CGROUP_FILE_DELTA_BYTES",
    default=128 * 1024 * 1024,
    cast=int,
)
# A process this close to its recycle ceiling is about to be retired, and the
# boundary that took it there is the one worth naming.
MEMORY_HIGH_WATER_CEILING_RATIO = config(
    "MEMORY_HIGH_WATER_CEILING_RATIO",
    default=0.85,
    cast=float,
)
# The same number config/gunicorn.py enforces, read from one definition so
# the instrumentation cannot report against a ceiling that is not the one in
# force. Zero means retirement is disabled.
GUNICORN_MAX_WORKER_MEMORY_BYTES = gunicorn_max_worker_memory_bytes()

MIDDLEWARE = [
    # Outermost by intent: the samples must bracket every other middleware's
    # allocations, not just the view's.
    "app.memory_envelope.MemoryHighWaterMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Keep rendered HTML out of the browser's heuristic cache so template fixes
    # actually reach iOS Safari and the installed PWA (#442)
    "app.middleware.NoStoreHtmlMiddleware",
    "app.middleware.RequestPerformanceLoggingMiddleware",
    "app.middleware.DatabaseRetryMiddleware",
    "app.middleware.SessionInterruptedMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "app.middleware.ProviderCredentialUserMiddleware",
    "app.middleware.UserLanguageMiddleware",
    "app.middleware.DiscoverWarmupMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "app.middleware.DemoAccountGuardMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    # Answer htmx requests with HX-Redirect instead of a followable 302 to the
    # login page, which htmx would otherwise swap into a fragment slot (#386)
    "app.middleware.HtmxAuthRedirectMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    "app.middleware.ProviderAPIErrorMiddleware",
    "app.middleware.ErrorCaptureMiddleware",
    # Convert HTML error responses for API requests into JSON responses
    "api.middleware.ApiJsonErrorMiddleware",
]
if not _CELERY_PROCESS:
    MIDDLEWARE.append("allauth.account.middleware.AccountMiddleware")

if ENABLE_DEBUG_TOOLBAR:
    MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")

# YAMTRACK_* env names stay readable as a fallback for pre-rename deployments.
FLOPPY_AUTO_LOGIN_USERNAME = config(
    "FLOPPY_AUTO_LOGIN_USERNAME",
    default=config("YAMTRACK_AUTO_LOGIN_USERNAME", default=None),
)
if FLOPPY_AUTO_LOGIN_USERNAME:
    _index = MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware")
    MIDDLEWARE.insert(_index + 1, "app.middleware.AutoLoginMiddleware")

ROOT_URLCONF = "config.celery_urls" if _CELERY_PROCESS else "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.media",
                "django.template.context_processors.i18n",
                "app.context_processors.export_vars",
                "app.context_processors.media_enums",
                "django.template.context_processors.request",
            ],
        },
    },
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

WSGI_APPLICATION = "config.wsgi.application"

# Database
# https://docs.djangoproject.com/en/stable/ref/settings/#databases

DB_HOST = config("DB_HOST", default=None)
USING_SQLITE_DATABASE = not bool(DB_HOST)

if DB_HOST:
    DB_POOL_ENABLED = config("DB_POOL_ENABLED", default=True, cast=bool)
    DB_POOL_MIN = config("DB_POOL_MIN", default=2, cast=int)
    # Derived from the same thread count gunicorn uses rather than hardcoded,
    # so the invariant holds automatically when the tier (or the user) changes
    # GUNICORN_THREADS. It must be >= the thread count so a worker's pool never
    # runs out of slots for its own concurrent request threads; +1 for headroom
    # (e.g. the /health/ probe hitting the pool at the same time). A too-small
    # pool causes psycopg_pool.PoolTimeout once concurrent requests in a worker
    # exceed the pool size (issue #341).
    DB_POOL_MAX = config(
        "DB_POOL_MAX",
        default=max(RUNTIME_GUNICORN_THREADS + 1, DB_POOL_MIN + 1),
        cast=int,
    )
    DB_POOL_TIMEOUT = config("DB_POOL_TIMEOUT", default=30, cast=int)
    db_options = {}
    if DB_POOL_ENABLED:
        db_options["pool"] = {
            "min_size": DB_POOL_MIN,
            "max_size": DB_POOL_MAX,
            "timeout": DB_POOL_TIMEOUT,
        }

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": DB_HOST,
            "NAME": config("DB_NAME", default=secret("DB_NAME_FILE")),
            "USER": config("DB_USER", default=secret("DB_USER_FILE")),
            "PASSWORD": config("DB_PASSWORD", default=secret("DB_PASSWORD_FILE")),
            "PORT": config("DB_PORT"),
            "OPTIONS": db_options,
            "CONN_HEALTH_CHECKS": True,
        },
    }

    sslmode = config("DB_SSL_MODE", default=None)
    if sslmode:
        DATABASES["default"]["OPTIONS"]["sslmode"] = sslmode

    sslcertmode = config("DB_SSL_CERT_MODE", default=None)
    if sslcertmode:
        DATABASES["default"]["OPTIONS"]["sslcertmode"] = sslcertmode

else:
    FLOPPY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    SQLITE_BUSY_TIMEOUT_SECONDS = config(
        "SQLITE_BUSY_TIMEOUT_SECONDS",
        default=30,
        cast=int,
    )
    SQLITE_JOURNAL_MODE = config("SQLITE_JOURNAL_MODE", default="WAL")
    SQLITE_SYNCHRONOUS = config("SQLITE_SYNCHRONOUS", default="NORMAL")
    # Read-side tuning. SQLite's own defaults (a 2 MiB page cache per connection,
    # no memory map, spill files for sorts) make every big library query re-read
    # the file from disk; on slow storage that is the cold-load cost. All three
    # are upper bounds, not allocations: pages are cached only as they are read.
    SQLITE_CACHE_SIZE_MIB = config(
        "SQLITE_CACHE_SIZE_MIB",
        default=by_tier(8, 24, 64),
        cast=int,
    )
    SQLITE_MMAP_SIZE_MIB = config(
        "SQLITE_MMAP_SIZE_MIB",
        default=by_tier(0, 256, 1024),
        cast=int,
    )
    SQLITE_TEMP_STORE = config("SQLITE_TEMP_STORE", default="MEMORY")

    DATABASES = {
        "default": {
            "ENGINE": "config.sqlite_backend",
            "NAME": FLOPPY_DB_PATH,
            "OPTIONS": {
                "timeout": SQLITE_BUSY_TIMEOUT_SECONDS,
                # Take the write lock when a transaction starts. A plain BEGIN
                # defers it to the first write, and a transaction that read
                # first and then writes after another process committed fails
                # at once with "database is locked", ignoring the timeout
                # above. IMMEDIATE queues behind the other writer instead.
                "transaction_mode": "IMMEDIATE",
            },
            # Reuse a thread's connection across requests instead of opening
            # one (plus the PRAGMAs below) for every request. Idle autocommit
            # connections hold no read transaction, so WAL checkpoints are not
            # held back. Replacing db.sqlite3 already requires stopping Floppy.
            "CONN_MAX_AGE": 600,
            "CONN_HEALTH_CHECKS": True,
        },
    }

    def configure_sqlite_connection(sender, connection, **_kwargs):
        """Ensure SQLite connections wait for locks and use WAL.

        Foreign keys are not set here. Django's SQLite backend already runs
        "PRAGMA foreign_keys = ON" as it opens each connection, and it turns
        them off on purpose while a migration rewrites a table. A second copy
        here would be dead on the first count and wrong on the second.
        """
        if connection.vendor != "sqlite":
            return

        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE}")
            actual_journal_mode = cursor.fetchone()[0]
            cursor.execute(f"PRAGMA synchronous={SQLITE_SYNCHRONOUS}")
            cursor.execute(
                f"PRAGMA busy_timeout={int(SQLITE_BUSY_TIMEOUT_SECONDS * 1000)}",
            )
            cursor.execute(f"PRAGMA cache_size={-1024 * int(SQLITE_CACHE_SIZE_MIB)}")
            cursor.execute(f"PRAGMA mmap_size={(1 << 20) * int(SQLITE_MMAP_SIZE_MIB)}")
            if SQLITE_TEMP_STORE.upper() in {"DEFAULT", "FILE", "MEMORY"}:
                cursor.execute(f"PRAGMA temp_store={SQLITE_TEMP_STORE.upper()}")
            if actual_journal_mode.lower() != SQLITE_JOURNAL_MODE.lower():
                # A connection stuck on a different journal mode than its
                # siblings (e.g. WAL requested but filesystem doesn't support
                # it - common on network mounts) is a known SQLite corruption
                # trigger, so this must be loud rather than a routine warning.
                import logging

                logging.getLogger(__name__).error(
                    "SQLite journal_mode mismatch: requested %s but got %s. "
                    "Mixed journal modes across connections to the same "
                    "database file can cause corruption - this often means "
                    "the database directory is on a network filesystem "
                    "(NFS/SMB/CIFS) that doesn't support WAL locking.",
                    SQLITE_JOURNAL_MODE,
                    actual_journal_mode,
                )
        except Exception:
            # Log but don't raise - allow connection to proceed even if PRAGMA fails
            # This prevents disk I/O errors during connection setup from blocking all requests
            import logging

            logger = logging.getLogger(__name__)
            logger.exception("Failed to configure SQLite connection PRAGMA settings")
        finally:
            if cursor:
                # Closing a cursor is best-effort; a failure here is not useful.
                with contextlib.suppress(Exception):
                    cursor.close()

    connection_created.connect(configure_sqlite_connection)

# Cache
# https://docs.djangoproject.com/en/stable/topics/cache/
CACHE_TIMEOUT = 86400  # 24 hours
REDIS_URL = config("REDIS_URL", default="redis://localhost:6379")
REDIS_CACHE_URL = config("REDIS_CACHE_URL", default=None) or REDIS_URL
REDIS_ADMIN_URL = config("REDIS_ADMIN_URL", default=None) or REDIS_CACHE_URL
# Byte count or a redis.conf-style size ("256mb"). Unset means "derive one from
# the host"; 0 means "never touch the operator's Redis". See app/redis_tuning.py.
FLOPPY_REDIS_MAXMEMORY = config(
    "FLOPPY_REDIS_MAXMEMORY",
    default=config("YAMTRACK_REDIS_MAXMEMORY", default=None),
)
KEY_PREFIX = f"{REDIS_PREFIX}" if REDIS_PREFIX else ""
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_CACHE_URL,
        "TIMEOUT": CACHE_TIMEOUT,
        "VERSION": 14,
        "KEY_PREFIX": KEY_PREFIX,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "COMPRESSOR": "app.cache_safety.SizedZlibCompressor",
            "REDIS_CLIENT_CLASS": "app.cache_safety.CacheRedis",
            "CONNECTION_POOL_CLASS": "app.cache_safety.CacheConnectionPool",
            # A cache is allowed to be unavailable. Without this, a slow or full
            # Redis raised out of every one of ~460 cache.get calls and took
            # page loads, webhooks and background tasks down with it (#521).
            # Callers that use the cache for control flow rather than caching
            # must distinguish "unavailable" from "miss" - see app/cache_safety.py.
            "IGNORE_EXCEPTIONS": config(
                "REDIS_IGNORE_EXCEPTIONS",
                default=True,
                cast=bool,
            ),
            # A half-dead Redis (TCP accepts, never replies) must raise
            # promptly instead of blocking worker threads forever (#341).
            "SOCKET_CONNECT_TIMEOUT": config(
                "REDIS_SOCKET_CONNECT_TIMEOUT",
                default=1,
                cast=int,
            ),
            # Every thread that touches the cache blocks for this long when Redis
            # stops answering, so the whole web tier stalls for
            # threads x timeout. Shorter on hosts that can least afford it.
            "SOCKET_TIMEOUT": config(
                "REDIS_SOCKET_TIMEOUT",
                default=1,
                cast=int,
            ),
            "CONNECTION_POOL_KWARGS": {
                # django-redis leaves this at redis-py's 2**31 default, so a
                # stalled Redis grows sockets and file descriptors without
                # bound while threads pile up waiting on it.
                "max_connections": config(
                    "REDIS_MAX_CONNECTIONS",
                    default=by_tier(12, 20, 32),
                    cast=int,
                ),
                # Optional cache data must not repeat a full socket timeout.
                # Cached sessions fall back to their database source of truth.
                "retry_on_timeout": False,
                "retry": Retry(NoBackoff(), 0),
                "health_check_interval": 30,
            },
        },
    },
}

# IGNORE_EXCEPTIONS swallows the traceback; without this a Redis outage is
# invisible in the logs, which is the opposite of what we want while
# diagnosing one. This is a global setting, not a per-cache OPTION.
DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS = True

# not using Memcached, ignore CacheKeyWarning
# https://docs.djangoproject.com/en/stable/topics/cache/#cache-key-warnings
warnings.simplefilter("ignore", CacheKeyWarning)

# Sessions
# Read through Redis for speed, but persist to the database too. The pure cache
# backend silently treats any Redis error - a restart, an eviction, a socket
# timeout - as "no session", logging the user out with no log line (#386).
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_CACHE_ALIAS = "default"

# Deliberately NOT setting SESSION_SAVE_EVERY_REQUEST: it would slide the
# expiry on activity, but at the cost of a session UPDATE on every request.
# On SQLite that is write-lock pressure on the hot path - the same class of
# fragility this section exists to fix. Session lifetime therefore stays fixed
# from login, per the user's session_duration preference.


# Password validation
# https://docs.djangoproject.com/en/stable/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
]

# Logging
# https://docs.djangoproject.com/en/stable/topics/logging/

# Recent logs are also kept on disk (in addition to stdout) so the app can
# offer a sanitized log download from Settings > Advanced (#510). They default
# to a folder inside the data directory, which Docker users already mount, so
# they survive the container being recreated after a crash.
LOG_DIR = config("LOG_DIR", default=str(FLOPPY_DATA_DIR / "logs"))
Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
LOG_FILE = str(Path(LOG_DIR) / "floppy.log")

# With DEBUG off the console hides routine chatter (see ConsoleNoiseFilter). The
# log file always keeps the full record at INFO (DEBUG when DEBUG is on), so the
# download in Settings > Advanced is complete without asking anyone to change a
# setting before reporting a problem (#1831).

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "cache_cooldown": {"()": "app.cache_safety.CacheCooldownLogFilter"},
        "console_noise": {"()": "app.log_safety.ConsoleNoiseFilter"},
    },
    "loggers": {
        "django_redis.cache": {
            "filters": ["cache_cooldown"],
        },
        "requests_ratelimiter.requests_ratelimiter": {
            "level": "DEBUG" if DEBUG else "WARNING",
        },
        "psycopg": {
            "level": "DEBUG" if DEBUG else "WARNING",
        },
        "urllib3": {
            "level": "DEBUG" if DEBUG else "WARNING",
        },
    },
    "formatters": {
        "verbose": {
            # format consistent with gunicorn's
            "format": "[{asctime}] [{process}] [{levelname}] {message}",
            "datefmt": "%Y-%m-%d %H:%M:%S %z",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "level": "DEBUG" if DEBUG else "INFO",
            "filters": [] if DEBUG else ["console_noise"],
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_FILE,
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "verbose",
            "level": "DEBUG" if DEBUG else "INFO",
        },
    },
    "root": {"handlers": ["console", "file"], "level": "DEBUG" if DEBUG else "INFO"},
}

# Internationalization
# https://docs.djangoproject.com/en/stable/topics/i18n/

LANGUAGE_CODE = "en-us"

LANGUAGES = [
    ("en", "English"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("fr", "Français"),
    ("ru", "Русский"),
    ("it", "Italiano"),
]

LOCALE_PATHS = [BASE_DIR / "locale"]

TIME_ZONE = os.getenv("TZ", "UTC")

USE_I18N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/stable/howto/static-files/

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_DIRS = [BASE_DIR / "static"]

if BASE_URL:
    STATIC_URL = f"{BASE_URL}/static/"

# Default primary key field type
# https://docs.djangoproject.com/en/stable/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Auth settings

LOGIN_URL = "account_login"

LOGIN_REDIRECT_URL = "home"

AUTH_USER_MODEL = "users.User"

# Floppy settings

# For CSV imports
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10 MB

# A Trakt data export is ~170 loose .json files if uploaded unzipped.
DATA_UPLOAD_MAX_NUMBER_FILES = 300


def _clean_metadata_value(value):
    """Normalize version metadata values from the environment."""
    if value is None:
        return None

    cleaned = str(value).strip()
    if not cleaned or cleaned.lower() in {"unknown", "none"}:
        return None
    return cleaned


def _find_git_dir(start_dir=BASE_DIR):
    """Search upward from start_dir for a git directory or gitdir file."""
    start_path = Path(start_dir).resolve()

    for candidate in (start_path, *start_path.parents):
        dot_git = candidate / ".git"
        if dot_git.is_dir():
            return dot_git

        if not dot_git.is_file():
            continue

        try:
            dot_git_contents = dot_git.read_text().strip()
        except (OSError, UnicodeDecodeError):
            continue

        if not dot_git_contents.startswith("gitdir:"):
            continue

        _, git_dir_path = dot_git_contents.split(":", 1)
        git_dir = (candidate / git_dir_path.strip()).resolve()
        if git_dir.exists():
            return git_dir

    return None


def _read_git_ref(git_dir, ref_path):
    """Read a git ref from loose refs or packed-refs."""
    ref_file = git_dir / ref_path
    try:
        ref_value = ref_file.read_text().strip()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        ref_value = None

    if ref_value:
        return ref_value

    packed_refs = git_dir / "packed-refs"
    try:
        packed_ref_lines = packed_refs.read_text().splitlines()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None

    for raw_line in packed_ref_lines:
        line = raw_line.strip()
        if not line or line.startswith(("#", "^")):
            continue

        try:
            sha, name = line.split(" ", 1)
        except ValueError:
            continue

        if name == ref_path:
            return sha

    return None


def _get_local_commit_hash(base_dir=BASE_DIR):
    """Return the current commit hash from the local git checkout if available."""
    git_rev_parse_command = ["git", "rev-parse", "HEAD"]
    try:
        git_rev = subprocess.run(  # noqa: S603  # static argv, no shell, no user input
            git_rev_parse_command,
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if git_rev:
            return git_rev
    except (OSError, subprocess.SubprocessError):
        pass

    git_dir = _find_git_dir(base_dir)
    if git_dir is None:
        return None

    head_file = git_dir / "HEAD"
    try:
        head_contents = head_file.read_text().strip()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None

    if head_contents.startswith("ref:"):
        _, ref_path = head_contents.split(":", 1)
        return _read_git_ref(git_dir, ref_path.strip())

    return head_contents or None


def _get_env_commit_hash():
    """Return the build/deployment commit hash from the environment."""
    return _clean_metadata_value(
        config("COMMIT_SHA", default=None)
        or config("GIT_COMMIT", default=None)
        or config("GITHUB_SHA", default=None),
    )


def _get_local_version(base_dir=BASE_DIR, commit_sha=None):
    """Return a version string from the local git checkout if available."""
    git_describe_command = ["git", "describe", "--tags", "--always", "--dirty"]
    try:
        git_describe = subprocess.run(  # noqa: S603  # static argv, no shell, no user input
            git_describe_command,
            cwd=base_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if git_describe:
            return git_describe
    except (OSError, subprocess.SubprocessError):
        pass

    local_commit = commit_sha or _get_local_commit_hash(base_dir)
    if local_commit:
        return local_commit[:7]
    return None


def _select_commit_hash(local_commit_sha, env_commit_sha):
    """Prefer runtime checkout metadata over build metadata."""
    return local_commit_sha or env_commit_sha


def _select_version(local_version, local_commit_sha, env_version, env_commit_sha):
    """Pick the most accurate version string available for the running code."""
    if local_version:
        return local_version

    if env_version and (not local_commit_sha or local_commit_sha == env_commit_sha):
        return env_version

    if local_commit_sha:
        return local_commit_sha[:7]

    if env_version:
        return env_version

    if env_commit_sha:
        return env_commit_sha[:7]

    return "dev"


ENV_VERSION_RAW = _clean_metadata_value(config("VERSION", default=None))
ENV_COMMIT_SHA = _get_env_commit_hash()
LOCAL_COMMIT_SHA = _get_local_commit_hash()
LOCAL_VERSION = _get_local_version(commit_sha=LOCAL_COMMIT_SHA)

COMMIT_SHA = _select_commit_hash(LOCAL_COMMIT_SHA, ENV_COMMIT_SHA)
COMMIT_SHA_SHORT = COMMIT_SHA[:7] if COMMIT_SHA else None

VERSION = _select_version(
    LOCAL_VERSION,
    LOCAL_COMMIT_SHA,
    ENV_VERSION_RAW,
    ENV_COMMIT_SHA,
)


def _parse_repo_owner(value):
    if not value:
        return None
    value = value.strip()
    if value.startswith("git@") and ":" in value:
        value = value.split(":", 1)[1]
    parsed = urlparse(value)
    repo_path = parsed.path if parsed.netloc else value
    repo_path = repo_path.strip("/")
    repo_path = repo_path.removesuffix(".git")
    if not repo_path:
        return None
    return repo_path.split("/", 1)[0]


def _parse_repo_slug(value):
    if not value:
        return None
    value = value.strip()
    if value.startswith("git@") and ":" in value:
        value = value.split(":", 1)[1]
    parsed = urlparse(value)
    repo_path = parsed.path if parsed.netloc else value
    repo_path = repo_path.strip("/")
    repo_path = repo_path.removesuffix(".git")
    if "/" not in repo_path:
        return None
    owner, repo = repo_path.split("/", 1)
    if not owner or not repo:
        return None
    return f"{owner}/{repo}"


def _read_fork_owner_file():
    file_paths = []
    configured_path = config("FORK_OWNER_FILE", default=None)
    if configured_path:
        file_paths.append(Path(configured_path))
    file_paths.append(BASE_DIR / ".fork_owner")
    file_paths.append(Path("/etc/floppy/fork_owner"))
    file_paths.append(Path("/etc/yamtrack/fork_owner"))  # pre-rename images

    for path in file_paths:
        try:
            value = path.read_text().strip()
        except (FileNotFoundError, OSError, UnicodeDecodeError):
            continue
        if value:
            return value
    return None


def _get_fork_owner():
    owner = config("FORK_OWNER_NAME", default=None) or config(
        "GITHUB_REPOSITORY_OWNER", default=None
    )
    if owner:
        return owner.strip()

    owner = _parse_repo_owner(config("GITHUB_REPOSITORY", default=None))
    if owner:
        return owner

    file_owner = _read_fork_owner_file()
    if file_owner:
        return _parse_repo_owner(file_owner) or file_owner.strip()

    try:
        git_remote = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],  # noqa: S607  # relative path is deliberate; the image resolves it via PATH
            cwd=BASE_DIR,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return _parse_repo_owner(git_remote)
    except (OSError, subprocess.SubprocessError):
        return None


def _get_fork_repository():
    for value in (
        config("FORK_REPOSITORY", default=None),
        config("GITHUB_REPOSITORY", default=None),
    ):
        repository = _parse_repo_slug(value)
        if repository:
            return repository

    file_owner = _read_fork_owner_file()
    repository = _parse_repo_slug(file_owner)
    if repository:
        return repository

    try:
        git_remote = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],  # noqa: S607  # relative path is deliberate; the image resolves it via PATH
            cwd=BASE_DIR,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return _parse_repo_slug(git_remote)
    except (OSError, subprocess.SubprocessError):
        return None


FORK_OWNER_NAME = _get_fork_owner()
FORK_OWNER_URL = config("FORK_OWNER_URL", default=None)
if not FORK_OWNER_URL:
    fork_repository = _get_fork_repository()
    if fork_repository:
        FORK_OWNER_URL = f"https://github.com/{fork_repository}"
    elif FORK_OWNER_NAME:
        FORK_OWNER_URL = f"https://github.com/{FORK_OWNER_NAME}"

ADMIN_ENABLED = config("ADMIN_ENABLED", default=False, cast=bool)

TRACK_TIME = config("TRACK_TIME", default=True, cast=bool)

BACKUP_DIR = config("BACKUP_DIR", default=str(BASE_DIR / "backups"))

# Raw SQLite snapshots for disaster recovery (#1053) -- distinct from the CSV
# exports above, which cannot replace a physically damaged db.sqlite3. Rides
# the same BACKUP_DIR volume mount installs already have.
DB_SNAPSHOT_ENABLED = config("DB_SNAPSHOT_ENABLED", default=True, cast=bool)
# Daily PRAGMA optimize so SQLite always has planner statistics (SQLite only).
# analysis_limit bounds each ANALYZE to a row sample, keeping the first run on a
# multi-GB database short.
DB_OPTIMIZE_ENABLED = config("DB_OPTIMIZE_ENABLED", default=True, cast=bool)
# Sequential read of the SQLite file at worker start and hourly, so cold page
# loads hit the OS cache (app/tasks_db_warm.py). The host shares that cache with
# everything else it runs, so a database on a slow disk falls out of it within
# an hour or two of other activity. Budget is further capped at 25% of free
# memory; the standard tier covers the whole file of a large library.
DB_WARM_ENABLED = config("DB_WARM_ENABLED", default=True, cast=bool)
DB_WARM_MAX_MIB = config("DB_WARM_MAX_MIB", default=by_tier(0, 512, 8192), cast=int)
DB_WARM_PAUSE_MS = config("DB_WARM_PAUSE_MS", default=20, cast=int)
# Below this sequential read speed the warm logs a "slow_disk" warning.
DB_SLOW_DISK_MIB_PER_SECOND = config("DB_SLOW_DISK_MIB_PER_SECOND", default=60, cast=int)
DB_OPTIMIZE_ANALYSIS_LIMIT = config(
    "DB_OPTIMIZE_ANALYSIS_LIMIT",
    default=1000,
    cast=int,
)
# How long an idempotency receipt stays replayable. A client that retries after
# this window gets a fresh operation, not the prior result, so keep it longer
# than the longest client backoff. Measure real retry intervals before lowering.
# How long an applied change stays in the watched-state log. A binding that has
# not checked in within this window must take a fresh snapshot rather than pin
# the log open forever. Compaction never crosses a live binding's checkpoint,
# whatever this says.
WATCH_STATE_CHANGE_RETENTION_DAYS = config(
    "WATCH_STATE_CHANGE_RETENTION_DAYS",
    default=30,
    cast=int,
)

INTEGRATION_RECEIPT_RETENTION_DAYS = config(
    "INTEGRATION_RECEIPT_RETENTION_DAYS",
    default=14,
    cast=int,
)

DB_SNAPSHOT_RETENTION_COUNT = config(
    "DB_SNAPSHOT_RETENTION_COUNT",
    default=7,
    cast=int,
)
DB_SNAPSHOT_HOUR = config("DB_SNAPSHOT_HOUR", default=2, cast=int)
# Deliberately not a quarter hour. The incremental metadata backfill runs on
# crontab(minute="*/15") or "*/30" depending on tier, so the old :30 default
# started a whole-database copy in the same minute as a bulk sweep -- one
# filling the page cache while the other allocated. Production showed exactly
# that pairing at 02:30. :37 collides with nothing else in the schedule below,
# and an operator who has set DB_SNAPSHOT_MINUTE keeps their own value.
DB_SNAPSHOT_MINUTE = config("DB_SNAPSHOT_MINUTE", default=37, cast=int)

# Runtime population settings
RUNTIME_POPULATION_DISABLED = config(
    "RUNTIME_POPULATION_DISABLED", default=False, cast=bool
)
RUNTIME_POPULATION_ON_STARTUP = config(
    "RUNTIME_POPULATION_ON_STARTUP", default=False, cast=bool
)
_DISCOVER_WARMUP_ON_STARTUP = config(
    "DISCOVER_WARMUP_ON_STARTUP",
    default=None,
)
if _DISCOVER_WARMUP_ON_STARTUP is None:
    DISCOVER_WARMUP_ON_STARTUP = False if USING_SQLITE_DATABASE else not DEBUG
else:
    DISCOVER_WARMUP_ON_STARTUP = config(
        "DISCOVER_WARMUP_ON_STARTUP",
        cast=bool,
    )
# A tracked write rebuilds Discover in the background only for someone who
# opened Discover (or called the recommendations API) in the last this many
# days; for everyone else the tab is marked stale and rebuilt on the next visit.
# 0 rebuilds after every write for everyone (the previous behaviour).
DISCOVER_EAGER_REBUILD_DAYS = config(
    "DISCOVER_EAGER_REBUILD_DAYS",
    default=3,
    cast=int,
)

TZ = zoneinfo.ZoneInfo(TIME_ZONE)

# Self-contained grey picture placeholder (was a hotlinked TMDB SVG).
# Avoids requiring external network access before lazyload swaps in real covers.
# Same icon as TMDB's glyphicons-basic-38-picture-grey SVG, embedded inline.
# Decoded SVG:
#   <svg id="glyphicons-basic" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
#     <path fill="#b5b5b5" id="picture"
#       d="M27.5,5H4.5A1.50008,1.50008,0,0,0,3,6.5v19A1.50008,1.50008,0,0,0,4.5,27h23A1.50008,1.50008,0,0,0,29,25.5V6.5A1.50008,1.50008,0,0,0,27.5,5ZM26,18.5l-4.79425-5.2301a.99383.99383,0,0,0-1.44428-.03137l-5.34741,5.34741L19.82812,24H17l-4.79291-4.793a1.00022,1.00022,0,0,0-1.41418,0L6,24V8H26Zm-17.9-6a2.4,2.4,0,1,1,2.4,2.4A2.40005,2.40005,0,0,1,8.1,12.5Z"/>
#   </svg>
IMG_NONE = config(
    "IMG_NONE",
    default=(
        "data:image/svg+xml;base64,PHN2ZyBpZD0iZ2x5cGhpY29ucy1iYXNpYyIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIiB2aWV3Qm94PSIwIDAgMzIgMzIiPgogIDxwYXRoIGZpbGw9IiNiNWI1YjUiIGlkPSJwaWN0dXJlIiBkPSJNMjcuNSw1SDQuNUExLjUwMDA4LDEuNTAwMDgsMCwwLDAsMyw2LjV2MTlBMS41MDAwOCwxLjUwMDA4LDAsMCwwLDQuNSwyN2gyM0ExLjUwMDA4LDEuNTAwMDgsMCwwLDAsMjksMjUuNVY2LjVBMS41MDAwOCwxLjUwMDA4LDAsMCwwLDI3LjUsNVpNMjYsMTguNWwtNC43OTQyNS01LjIzMDFhLjk5MzgzLjk5MzgzLDAsMCAwLTEuNDQ0MjgtLjAzMTM3bC01LjM0NzQxLDUuMzQ3NDFMMTkuODI4MTIsMjRIMTdsLTQuNzkyOTEtNC43OTNhMS4wMDAyMiwxLjAwMDIyLDAsMCAwLTEuNDE0MTgsMEw2LDI0VjhIMjZabS0xNy45LTZhMi40LDIuNCwwLDEsMSwyLjQsMi40QTIuNDAwMDUsMi40MDAwNSwwLDAsMSw4LjEsMTIuNVoiLz4KPC9zdmc+Cg=="
    ),
)

REQUEST_TIMEOUT = 120  # seconds
PER_PAGE = 24

MUSICBRAINZ_URL = config(
    "MUSICBRAINZ_URL",
    default="https://musicbrainz.org/ws/2",
)

# Provider credentials that intentionally ship as app-owned, shared metadata
# keys. The value is a single quota shared by every Floppy install, so Settings
# > Metadata lets an operator or user supply their own. These are public by
# design, not user or account credentials. Never add a private credential or a
# *_SECRET setting here; those must come from the environment, a Docker secret,
# or encrypted provider-credential storage.
SHARED_DEFAULT_CREDENTIALS = {
    "TMDB_API": "61572be02f0a068658828f6396aacf60",
    "MAL_API": "25b5581dafd15b3e7d583bb79e9a1691",
    "IGDB_ID": "8wqmm7x1n2xxtnz94lb8mthadhtgrt",
    "BGG_API_TOKEN": "92f43ab1-d1d5-4e18-8b82-d1f56dc12927",
    "COMICVINE_API": "cdab0706269e4bca03a096fbc39920dadf7e4992",
    "WETRAKR_CLIENT_ID": "84fcb12dd6f78fc0efc0f279d5fea165",
}

TMDB_API = config(
    "TMDB_API",
    default=secret(
        "TMDB_API_FILE",
        SHARED_DEFAULT_CREDENTIALS["TMDB_API"],
    ),
)
TMDB_NSFW = config("TMDB_NSFW", default=False, cast=bool)
TMDB_LANG = config("TMDB_LANG", default="en")

SEERR_GLOBAL_WEBHOOK_SECRET = config(
    "SEERR_GLOBAL_WEBHOOK_SECRET",
    default=secret("SEERR_GLOBAL_WEBHOOK_SECRET_FILE", ""),
)

TVDB_API_KEY = config(
    "TVDB_API_KEY",
    default=secret(
        "TVDB_API_KEY_FILE",
        "",
    ),
)
TVDB_PIN = config(
    "TVDB_PIN",
    default=secret(
        "TVDB_PIN_FILE",
        "",
    ),
)

MAL_API = config(
    "MAL_API",
    default=secret(
        "MAL_API_FILE",
        SHARED_DEFAULT_CREDENTIALS["MAL_API"],
    ),
)
MAL_NSFW = config("MAL_NSFW", default=False, cast=bool)

# Only used to sync watch status back to MyAnimeList. Unlike MAL_API (search),
# OAuth requires a redirect URI registered to a specific app, so this can't
# ship a shared default the way IGDB_SECRET does - each user or instance
# needs their own MyAnimeList API application (see integrations.mal_sync).
MAL_API_SECRET = config(
    "MAL_API_SECRET",
    default=secret(
        "MAL_API_SECRET_FILE",
        "",
    ),
)

# The application key every WeTrakr account is connected with. WeTrakr issued it
# to Floppy as an app-owned developer key (no client secret, no redirect URI:
# login is a device code), so it ships as a shared default. An instance or member
# can still supply their own (Settings > Providers > WeTrakr).
WETRAKR_CLIENT_ID = config(
    "WETRAKR_CLIENT_ID",
    default=secret(
        "WETRAKR_CLIENT_ID_FILE",
        SHARED_DEFAULT_CREDENTIALS["WETRAKR_CLIENT_ID"],
    ),
)

MU_NSFW = config("MU_NSFW", default=False, cast=bool)

MANGABAKA_NSFW = config("MANGABAKA_NSFW", default=False, cast=bool)

IGDB_ID = config(
    "IGDB_ID",
    default=secret(
        "IGDB_ID_FILE",
        SHARED_DEFAULT_CREDENTIALS["IGDB_ID"],
    ),
)
IGDB_SECRET = config(
    "IGDB_SECRET",
    default=secret(
        "IGDB_SECRET_FILE",
        "",
    ),
)
IGDB_NSFW = config("IGDB_NSFW", default=False, cast=bool)

# BoardGameGeek API Token - Register at https://boardgamegeek.com/using_the_xml_api
BGG_API_TOKEN = config(
    "BGG_API_TOKEN",
    default=secret(
        "BGG_API_TOKEN_FILE",
        SHARED_DEFAULT_CREDENTIALS["BGG_API_TOKEN"],
    ),
)

STEAM_API_KEY = config(
    "STEAM_API_KEY",
    default=secret(
        "STEAM_API_KEY_FILE",
        "",
    ),  # Generate default key https://steamcommunity.com/dev/apikey
)

# Intentionally has no default. Hardcover meters its free tier per account
# (5000 requests/day), so a token bundled with the image is a single quota
# shared by every Floppy install on the internet and is permanently exhausted
# (#1025). Operators supply their own token, or users set a personal one in
# Preferences; without either, Hardcover is simply not an available source.
HARDCOVER_API = config(
    "HARDCOVER_API",
    default=secret("HARDCOVER_API_FILE", ""),
)

GOOGLE_BOOKS_API_KEY = config(
    "GOOGLE_BOOKS_API_KEY",
    default=secret("GOOGLE_BOOKS_API_KEY_FILE", ""),
)

# RapidAPI key for OpenCritic game scores. No default: the free plan's daily
# quota belongs to one account, so every install brings its own key.
OPENCRITIC_API_KEY = config(
    "OPENCRITIC_API_KEY",
    default=secret("OPENCRITIC_API_KEY_FILE", ""),
)

# Podcast Index (podcastindex.org) is free but asks every app for its own key
# and secret, so there is no shared default.
PODCASTINDEX_API_KEY = config(
    "PODCASTINDEX_API_KEY",
    default=secret("PODCASTINDEX_API_KEY_FILE", ""),
)
PODCASTINDEX_API_SECRET = config(
    "PODCASTINDEX_API_SECRET",
    default=secret("PODCASTINDEX_API_SECRET_FILE", ""),
)

COMICVINE_API = config(
    "COMICVINE_API",
    default=secret(
        "COMICVINE_API_FILE",
        SHARED_DEFAULT_CREDENTIALS["COMICVINE_API"],
    ),
)

# Game artwork providers. SteamGridDB and TheGamesDB take a key of the member's
# own. ScreenScraper identifies Floppy itself with developer credentials that
# ScreenScraper issues to the project: they are read only from the environment
# or a Docker secret, are never committed, and leave ScreenScraper inactive
# until set. SCREENSCRAPER_SSID and SCREENSCRAPER_SSPASSWORD are an optional
# member login that raises personal limits.
STEAMGRIDDB_API_KEY = config(
    "STEAMGRIDDB_API_KEY",
    default=secret("STEAMGRIDDB_API_KEY_FILE", ""),
)
THEGAMESDB_API_KEY = config(
    "THEGAMESDB_API_KEY",
    default=secret("THEGAMESDB_API_KEY_FILE", ""),
)
SCREENSCRAPER_DEV_ID = config(
    "SCREENSCRAPER_DEV_ID",
    default=secret("SCREENSCRAPER_DEV_ID_FILE", ""),
)
SCREENSCRAPER_DEV_PASSWORD = config(
    "SCREENSCRAPER_DEV_PASSWORD",
    default=secret("SCREENSCRAPER_DEV_PASSWORD_FILE", ""),
)
SCREENSCRAPER_SOFTNAME = config("SCREENSCRAPER_SOFTNAME", default="Floppy")
SCREENSCRAPER_SSID = config(
    "SCREENSCRAPER_SSID",
    default=secret("SCREENSCRAPER_SSID_FILE", ""),
)
SCREENSCRAPER_SSPASSWORD = config(
    "SCREENSCRAPER_SSPASSWORD",
    default=secret("SCREENSCRAPER_SSPASSWORD_FILE", ""),
)

# Grand Comics Database login. No default: GCD limits anonymous API access to
# 30 requests an hour, and a login is tied to one person's account.
GCD_USERNAME = config("GCD_USERNAME", default=secret("GCD_USERNAME_FILE", ""))
GCD_PASSWORD = config("GCD_PASSWORD", default=secret("GCD_PASSWORD_FILE", ""))

TRAKT_API = config(
    "TRAKT_API",
    default=secret(
        "TRAKT_API_FILE",
        "",
    ),
)

TRAKT_API_SECRET = config(
    "TRAKT_API_SECRET",
    default=secret(
        "TRAKT_API_SECRET_FILE",
        "",
    ),
)

ANILIST_ID = config(
    "ANILIST_ID",
    default=secret(
        "ANILIST_ID_FILE",
        "",
    ),
)

ANILIST_SECRET = config(
    "ANILIST_SECRET",
    default=secret(
        "ANILIST_SECRET_FILE",
        "",
    ),
)

# No shared SIMKL default: the token exchange needs the app's secret, which
# cannot ship, and a bundled ID without it only fails after the user approves
# on SIMKL (#1318). Operators or users supply both.
SIMKL_ID = config(
    "SIMKL_ID",
    default=secret(
        "SIMKL_ID_FILE",
        "",
    ),
)
SIMKL_SECRET = config(
    "SIMKL_SECRET",
    default=secret(
        "SIMKL_SECRET_FILE",
        "",
    ),
)

DEFAULT_PLEX_CLIENT_IDENTIFIER = hashlib.sha256(SECRET_KEY.encode()).hexdigest()[:24]
PLEX_CLIENT_IDENTIFIER = config(
    "PLEX_CLIENT_IDENTIFIER",
    default=DEFAULT_PLEX_CLIENT_IDENTIFIER,
)
PLEX_PRODUCT = config("PLEX_PRODUCT", default="Floppy")
PLEX_DEVICE = config("PLEX_DEVICE", default="Floppy Importer")
PLEX_PLATFORM = config("PLEX_PLATFORM", default="Floppy")
PLEX_PLATFORM_VERSION = config("PLEX_PLATFORM_VERSION", default=VERSION)
PLEX_SSL_VERIFY = config("PLEX_SSL_VERIFY", default=False, cast=bool)
PLEX_SECTIONS_TTL_HOURS = config("PLEX_SECTIONS_TTL_HOURS", default=24, cast=int)
PLEX_SECTIONS_TIMEOUT_BUDGET = config(
    "PLEX_SECTIONS_TIMEOUT_BUDGET",
    default=15,
    cast=int,
)
PLEX_HISTORY_PAGE_SIZE = config("PLEX_HISTORY_PAGE_SIZE", default=200, cast=int)
PLEX_HISTORY_MAX_ITEMS = config("PLEX_HISTORY_MAX_ITEMS", default=0, cast=int)

LASTFM_API_KEY = config("LASTFM_API_KEY", default="")
LASTFM_POLL_INTERVAL_MINUTES = config(
    "LASTFM_POLL_INTERVAL_MINUTES", default=15, cast=int
)
LASTFM_HISTORY_PAGES_PER_TASK = config(
    "LASTFM_HISTORY_PAGES_PER_TASK", default=5, cast=int
)

AUDIOBOOKSHELF_POLL_INTERVAL_MINUTES = config(
    "AUDIOBOOKSHELF_POLL_INTERVAL_MINUTES", default=15, cast=int
)

TESTING = False

# Drop-in music listen hooks. Empty means none load. See
# docs/architecture/music-listen-hooks.md.
MUSIC_HOOKS_DIR = config("MUSIC_HOOKS_DIR", default="")

HEALTHCHECK_CELERY_PING_TIMEOUT = config(
    "HEALTHCHECK_CELERY_PING_TIMEOUT",
    default=1,
    cast=int,
)

# The container healthcheck polls /health/ constantly; the celery broadcast
# ping always waits its full timeout (~1s), pinning a gunicorn thread per
# probe. The probe uses this fast subset; /health/full/ runs every check.
#
# Redis is deliberately NOT a liveness signal. With IGNORE_EXCEPTIONS the app
# serves correctly from Postgres through a Redis outage, so reporting unhealthy
# would get a working container restarted by whatever watches the healthcheck -
# turning graceful degradation into a restart loop (#521). Cache and Redis
# checks remain on /health/full/, which is where you look to diagnose one.
HEALTH_CHECK = {
    "SUBSETS": {
        "liveness": [
            "DatabaseHeartBeatCheck",
        ],
    },
}

# Third party settings

DEBUG_TOOLBAR_CONFIG = {
    "SKIP_TEMPLATE_PREFIXES": (
        "django/forms/widgets/",
        "admin/widgets/",
    ),
    "ROOT_TAG_EXTRA_ATTRS": "hx-preserve",
}
if ENABLE_DEBUG_TOOLBAR:
    # Imported here rather than at module scope: debug_toolbar is a runtime
    # dependency, so a top-level import loads it into every long-lived process
    # -- gunicorn and all three Celery roles -- even though the toolbar only
    # ever runs with DEBUG on.
    from debug_toolbar.settings import PANELS_DEFAULTS

    DEBUG_TOOLBAR_PANELS = [
        panel
        for panel in PANELS_DEFAULTS
        if (
            DEBUG_TOOLBAR_INCLUDE_TEMPLATES_PANEL
            or panel != "debug_toolbar.panels.templates.TemplatesPanel"
        )
    ]

SELECT2_CACHE_BACKEND = "default"
SELECT2_JS = [
    "js/libraries/jquery-3.7.1.min.js",
    "js/libraries/select2-4.1.0.min.js",
]
SELECT2_I18N_PATH = "js/i18n"
SELECT2_CSS = [
    "css/libraries/select2-4.1.0.min.css",
]
SELECT2_THEME = "tailwindcss-4"

# Celery settings

CELERY_BROKER_URL = config("CELERY_BROKER_URL", default=None) or REDIS_URL
CELERY_TIMEZONE = TIME_ZONE

CELERY_BROKER_TRANSPORT_OPTIONS = {
    # Let interactive refreshes jump ahead of maintenance batches in Redis.
    "queue_order_strategy": "priority",
    "priority_steps": list(range(10)),
    "sep": ":",
    # A task not acked within this window is redelivered, so it must exceed the
    # longest a task can legitimately run or work silently runs twice. Tracks
    # CELERY_TASK_TIME_LIMIT below, with headroom.
    "visibility_timeout": by_tier(3600, 3600, 60 * 60 * 6 + 1800),
    # The broker connection is a long-lived BRPOP, so it needs a far longer
    # socket timeout than the cache's - and keepalives, or a NAT/firewall idle
    # timeout silently blackholes it and the worker stops receiving tasks.
    "socket_timeout": 30,
    "socket_connect_timeout": 10,
    "socket_keepalive": True,
    "retry_on_timeout": True,
    "health_check_interval": 30,
}
if REDIS_PREFIX:
    CELERY_BROKER_TRANSPORT_OPTIONS.update(
        {
            "global_keyprefix": f"{REDIS_PREFIX}",
            "queue_prefix": f"{REDIS_PREFIX}",
        },
    )

# Broker resilience: none of this was configured, so a Redis blip at the wrong
# moment killed a worker outright instead of being retried (#521).
CELERY_BROKER_POOL_LIMIT = config(
    "CELERY_BROKER_POOL_LIMIT",
    default=by_tier(4, 6, 10),
    cast=int,
)
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
# Retry forever rather than exit: the container restarts into the same
# situation, so giving up only turns a slow Redis into a crash loop.
CELERY_BROKER_CONNECTION_MAX_RETRIES = None

# ``integrations.tasks`` keeps its historical public re-exports lazy so web
# workers do not load every importer while resolving URLs. Celery workers still
# import every task implementation explicitly and retain the same task names.
if os.environ.get("FLOPPY_PROCESS_ROLE") == "interactive":
    CELERY_IMPORTS = (
        "app.tasks_interactive",
        "integrations.tasks._plex_sections",
        "integrations.tasks._webhook",
    )
else:
    CELERY_IMPORTS = (
        "app.tasks_game_groups",
        "integrations.tasks._change_log",
        "integrations.tasks._jellyfin_pull",
        "integrations.tasks._koito",
        "integrations.tasks._lastfm",
        "integrations.tasks._mal_sync",
        "integrations.tasks._media_imports",
        "integrations.tasks._plex_collection",
        "integrations.tasks._plex_sections",
        "integrations.tasks._receipts",
        "integrations.tasks._ratings",
        "integrations.tasks._state_sync",
        "integrations.tasks._webhook",
        "integrations.tasks._wetrakr_export",
    )
CELERY_REDIS_RETRY_ON_TIMEOUT = True

CELERY_WORKER_HIJACK_ROOT_LOGGER = False
CELERY_WORKER_CONCURRENCY = config("CELERY_WORKER_CONCURRENCY", default=1, cast=int)
CELERY_WORKER_PREFETCH_MULTIPLIER = config(
    "CELERY_WORKER_PREFETCH_MULTIPLIER",
    default=1,
    cast=int,
)
# Recycling a worker child is the only thing that returns leaked/fragmented
# memory to the OS, so smaller hosts recycle sooner (issue #521).
CELERY_WORKER_MAX_TASKS_PER_CHILD = config(
    "CELERY_WORKER_MAX_TASKS_PER_CHILD",
    default=by_tier(15, 25, 50),
    cast=int,
)
# A hard RSS ceiling per child: Celery retires the child after the task that
# crosses it finishes, so no task is lost to it.
#
# Standard hosts had no ceiling at all, on the reasoning that a large import
# needs the headroom and there is memory to spare. The second half of that has
# stopped being the goal: a child that grew during one import then stays
# resident until max_tasks_per_child recycles it, which on a warm-idle install
# can be days. The ceiling here is several times a freshly started child (~100
# MiB of imports) so an import still has room to work, while the creep an idle
# instance accumulates is returned to the OS.
#
# The interactive worker gets its own, much lower ceiling. One number cannot
# serve both: the background ceiling has to clear a large import, and a child
# that only runs webhooks and cache refreshes never comes close to it, so it
# is never retired and its creep is never returned. Production showed exactly
# that -- an interactive child at 215 MiB after three hours, still under the
# 400 MiB background ceiling and still climbing. Retiring this child is cheap:
# Celery retires it after the task that crossed the ceiling finishes, and its
# tasks are short. Sized at roughly three times a fresh interactive child
# (~50 MiB; its CELERY_IMPORTS are a third of the background worker's). A
# statistics rebuild large enough to cross it will retire the child every time
# it runs -- which is the right trade: that rebuild's memory is then returned
# rather than held against the next webhook.
_INTERACTIVE_ROLE = os.environ.get("FLOPPY_PROCESS_ROLE") == "interactive"
CELERY_WORKER_MAX_MEMORY_PER_CHILD = config(
    "CELERY_WORKER_MAX_MEMORY_PER_CHILD",
    default=(
        by_tier(120 * 1024, 140 * 1024, 160 * 1024)
        if _INTERACTIVE_ROLE
        else by_tier(180 * 1024, 250 * 1024, 400 * 1024)
    ),
    cast=int,
)
if not CELERY_WORKER_MAX_MEMORY_PER_CHILD:
    CELERY_WORKER_MAX_MEMORY_PER_CHILD = None
CELERY_BEAT_SYNC_EVERY = 10

CELERY_TASK_TRACK_STARTED = True
# A wedged task holds its worker for the whole limit, and with concurrency 1
# that is the entire queue. Six hours is survivable on a host with spare
# workers; on a constrained one it is an outage, so bound it much sooner and
# give tasks a SoftTimeLimitExceeded they can clean up after.
CELERY_TASK_TIME_LIMIT = config(
    "CELERY_TASK_TIME_LIMIT",
    default=by_tier(60 * 30, 60 * 30, 60 * 60 * 6),
    cast=int,
)
CELERY_TASK_SOFT_TIME_LIMIT = config(
    "CELERY_TASK_SOFT_TIME_LIMIT",
    default=by_tier(60 * 15, 60 * 15, 0),
    cast=int,
)
if not CELERY_TASK_SOFT_TIME_LIMIT:
    CELERY_TASK_SOFT_TIME_LIMIT = None
# Redis priorities are inverted relative to AMQP: kombu publishes priority N to
# the key "<queue>:N" (priority 0 uses the bare "<queue>"), and the worker BRPOPs
# those keys in ascending order, so it drains "celery" first and "celery:9" last.
# Lower number == higher priority. Keep these ordered accordingly; assigning 9 to
# interactive work strands it behind every background batch.
CELERY_TASK_PRIORITY_INTERACTIVE = 0
CELERY_TASK_PRIORITY_FOLLOWUP = 3
# A background Statistics sync yields to webhooks (0) but, on the minimal-tier
# combined worker, still drains ahead of FOLLOWUP imports and backfills: at 3 a
# chunk of the old refresh run could wait behind them indefinitely (#1272).
CELERY_TASK_PRIORITY_STATISTICS_SYNC = 1
CELERY_TASK_PRIORITY_DEFAULT = 5
CELERY_TASK_PRIORITY_BACKGROUND = 9
# Celery copies task_default_priority onto every task before it consults the
# route table. A concrete value here would therefore override route-specific
# priorities during apply_async() and Beat dispatch. The route table below
# supplies both the explicit classes and the default fallback instead.
CELERY_TASK_DEFAULT_PRIORITY = None

# Statistics sync (docs/architecture/statistics-sync.md). One sync task works
# for at most this many seconds, then queues its own follow-up, so the
# single-slot interactive worker is never held for a whole All Time rebuild.
STATISTICS_SYNC_TASK_BUDGET_SECONDS = config(
    "STATISTICS_SYNC_TASK_BUDGET_SECONDS", default=10, cast=int
)
# Days built per prefetch slice inside a sync.
STATISTICS_SYNC_SLICE_DAYS = config("STATISTICS_SYNC_SLICE_DAYS", default=25, cast=int)
# The heavy ranges (Last 90 Days .. All Time) are rebuilt once changes have
# been quiet this long, and never trail by more than the max delay. Hot ranges
# (Today .. Last 30 Days) rebuild on every sync.
STATISTICS_SYNC_HEAVY_SETTLE_SECONDS = config(
    "STATISTICS_SYNC_HEAVY_SETTLE_SECONDS", default=90, cast=int
)
STATISTICS_SYNC_HEAVY_MAX_DELAY_SECONDS = config(
    "STATISTICS_SYNC_HEAVY_MAX_DELAY_SECONDS", default=600, cast=int
)
# A credits backfill finishes a batch roughly every minute, and marking every
# batch made the Statistics sync run back to back for the whole backfill -
# competing with the credits writes for SQLite's single writer and the CPU
# (#1706). Credits changes are coalesced to this interval, with one trailing
# mark so the last credits written are still published. Kept at the heavy-range
# max delay: a shorter interval would make every mark settle the heavy ranges
# sooner and rebuild them more often than today.
STATISTICS_CREDITS_MARK_SECONDS = config(
    "STATISTICS_CREDITS_MARK_SECONDS", default=600, cast=int
)

CELERY_RESULT_EXTENDED = True
CELERY_RESULT_BACKEND = config("CELERY_RESULT_BACKEND", default=None) or REDIS_URL
CELERY_CACHE_BACKEND = "default"
CELERY_RESULT_EXPIRES = 60 * 60 * 24 * 7  # 7 days
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# https://docs.celeryq.dev/en/stable/userguide/configuration.html#task-serializer
# Old pickle queues must be drained before upgrading; see docs/security-hardening.md.
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_RESULT_SERIALIZER = "json"
CELERY_RESULT_ACCEPT_CONTENT = ["json"]
CELERY_TASK_ROUTES = {
    "app.tasks.populate_*": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "app.tasks.reconcile_*": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "Cleanup task results": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "Repair Celery broker bindings": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "Cleanup image cache": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "Purge expired Discover caches": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "Write database snapshot": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "Optimize database planner statistics": {
        "priority": CELERY_TASK_PRIORITY_BACKGROUND,
    },
    "Backfill item metadata": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "Ensure genre backfill reconcile": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "Ensure watch provider backfill reconcile": {
        "priority": CELERY_TASK_PRIORITY_BACKGROUND,
    },
    "Ensure external ID backfill reconcile": {
        "priority": CELERY_TASK_PRIORITY_BACKGROUND,
    },
    "Ensure podcast website backfill reconcile": {
        "priority": CELERY_TASK_PRIORITY_BACKGROUND,
    },
    "Ensure watch state repair": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "Nightly metadata quality backfill": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "Refresh IMDB game credits from datasets": {
        "priority": CELERY_TASK_PRIORITY_BACKGROUND
    },
    "Sync IMDB ratings from datasets": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "Sync MAL ratings from API": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    # A user is watching a spinner for this. It makes many provider calls, so
    # it stays off the single-slot interactive worker, but it must not queue
    # behind metadata backfills on the background worker.
    "Preview sync to MyAnimeList": {"priority": CELERY_TASK_PRIORITY_INTERACTIVE},
    # A per-item push is one quick API call that follows a watch or an edit.
    # At default priority it starved behind back-to-back statistics syncs.
    "Sync status to MyAnimeList": {"priority": CELERY_TASK_PRIORITY_INTERACTIVE},
    "Warm Discover API Cache": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "Warm Discover Startup Tabs": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "Warm History Day Cache Coverage": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "Repair History Day Cache Coverage": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "Refresh Discover Profiles": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    "Refresh Discover Profile For User": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    # Long-running scheduled tasks — low priority so beat-catch-up bursts don't
    # starve other celery-queue work.
    "Reload calendar": {"priority": CELERY_TASK_PRIORITY_BACKGROUND},
    # Discover cache rebuilds go to the dedicated discover worker so a post-restart
    # burst of O(users x tabs) tasks never starves imports or background work.
    "Refresh Discover Tab Cache": {
        "queue": "discover",
        "priority": CELERY_TASK_PRIORITY_BACKGROUND,
    },
    # User-triggered cache rebuilds go to the dedicated interactive worker so they
    # are never blocked behind long-running background tasks.
    "app.tasks.refresh_statistics_cache_task": {
        "queue": "interactive",
        "priority": CELERY_TASK_PRIORITY_INTERACTIVE,
    },
    # Retired chunk-run name; drains queued messages into a sync.
    "app.tasks.continue_statistics_refresh_task": {
        "queue": "interactive",
        "priority": CELERY_TASK_PRIORITY_STATISTICS_SYNC,
    },
    # Budget-bounded bulk derived work: ordinary syncs stay off the interactive
    # lane so webhooks and manual refreshes never wait behind a range aggregate.
    # The task itself overrides the queue to interactive for urgent requests.
    # See docs/architecture/statistics-sync.md.
    "app.tasks.statistics_sync_task": {
        "queue": "celery",
        "priority": CELERY_TASK_PRIORITY_STATISTICS_SYNC,
    },
    # The trailing half of the credits Statistics debounce: one state update
    # and a sync enqueue, so a delayed mark never waits behind background work.
    "app.tasks.mark_credits_statistics_refresh": {
        "queue": "interactive",
        "priority": CELERY_TASK_PRIORITY_STATISTICS_SYNC,
    },
    # Rebuilds a talent section the viewer already sees a stale copy of. It is a
    # full talent scan, so it belongs with the other bulk derived work.
    "Refresh statistics talent fragment": {
        "queue": "celery",
        "priority": CELERY_TASK_PRIORITY_STATISTICS_SYNC,
    },
    # Periodic recovery is not awaited by a page. Keep its sweeps off the
    # interactive worker; viewer-requested syncs select that lane separately.
    "Reconcile statistics sync": {
        "priority": CELERY_TASK_PRIORITY_STATISTICS_SYNC,
    },
    # History cache rebuilds now bound their inline work (see
    # refresh_history_cache in history_cache_reader.py), but they're kept off the
    # interactive queue as defense-in-depth so an unanticipated slow rebuild can
    # never block Plex webhook scrobbles, which share that single-concurrency
    # worker.
    "app.tasks.refresh_history_cache_task": {
        "queue": "celery",
        "priority": CELERY_TASK_PRIORITY_INTERACTIVE,
    },
    # Webhook scrobbles must land right after a play finishes, so they run on the
    # interactive worker at top priority — never behind imports or backfills.
    "Process media server webhook": {
        "queue": "interactive",
        "priority": CELERY_TASK_PRIORITY_INTERACTIVE,
    },
    "Process Stremio playback webhook": {
        "queue": "interactive",
        "priority": CELERY_TASK_PRIORITY_INTERACTIVE,
    },
    "Process Stremio tracker event": {
        "queue": "interactive",
        "priority": CELERY_TASK_PRIORITY_INTERACTIVE,
    },
    # Fill-in artwork resolution for the home-page playback card; keeps the
    # HTTP request path free of provider calls.
    "Resolve live playback image": {
        "queue": "interactive",
        "priority": CELERY_TASK_PRIORITY_INTERACTIVE,
    },
    # Keeps the HTTP request path free of live Plex connection probing when
    # loading Integrations settings.
    "Refresh Plex library sections": {
        "queue": "interactive",
        "priority": CELERY_TASK_PRIORITY_INTERACTIVE,
    },
    # Recurring imports are time-sensitive (every 2 h) — bump above generic
    # background tasks so a backlog of low-priority work doesn't delay them.
    "Import from Radarr (Recurring)": {"priority": CELERY_TASK_PRIORITY_FOLLOWUP},
    "Import from Sonarr (Recurring)": {"priority": CELERY_TASK_PRIORITY_FOLLOWUP},
    "Import from Mylar3 (Recurring)": {"priority": CELERY_TASK_PRIORITY_FOLLOWUP},
    "Import from Kapowarr (Recurring)": {"priority": CELERY_TASK_PRIORITY_FOLLOWUP},
    "Import from Audiobookshelf (Recurring)": {
        "priority": CELERY_TASK_PRIORITY_FOLLOWUP,
    },
    "Import from Kavita (Recurring)": {"priority": CELERY_TASK_PRIORITY_FOLLOWUP},
    "Import from Komga (Recurring)": {"priority": CELERY_TASK_PRIORITY_FOLLOWUP},
    "Import from Hardcover Account": {"priority": CELERY_TASK_PRIORITY_FOLLOWUP},
    "Import from Pocket Casts (Recurring)": {"priority": CELERY_TASK_PRIORITY_FOLLOWUP},
    "Import from GPodder (Recurring)": {"priority": CELERY_TASK_PRIORITY_FOLLOWUP},
    "Migrate TV shows to preferred metadata provider": {
        "priority": CELERY_TASK_PRIORITY_BACKGROUND,
    },
    # Keep this last. Celery's map router uses the first matching wildcard, and
    # this fallback preserves the former default priority for all other tasks.
    "*": {"priority": CELERY_TASK_PRIORITY_DEFAULT},
}


DAILY_DIGEST_HOUR = config(
    "DAILY_DIGEST_HOUR",
    default=8,
    cast=int,
)

# Background maintenance sizing. These used to be literals in the beat schedule
# below; a constrained host cannot absorb the same sweep as a NAS with 16 GB, and
# several of the old values were tuned to converge a one-time migration quickly
# rather than to be run forever (issue #521, #512).
#
# The reconcile cadence is 30 minutes rather than the previous 5 because a
# reconcile now records durable completion state and backs off, so frequent
# polling buys nothing - see app/reconcile_state.py.
RECONCILE_INTERVAL_SECONDS = config(
    "RECONCILE_INTERVAL_SECONDS",
    default=by_tier(60 * 120, 60 * 60, 60 * 30),
    cast=int,
)
RECONCILE_BATCH_SIZE = config(
    "RECONCILE_BATCH_SIZE",
    default=by_tier(200, 300, 500),
    cast=int,
)
WATCH_PROVIDERS_RECONCILE_BATCH_SIZE = RECONCILE_BATCH_SIZE
EXTERNAL_IDS_RECONCILE_BATCH_SIZE = RECONCILE_BATCH_SIZE
GENRE_RECONCILE_BATCH_SIZE = RECONCILE_BATCH_SIZE
# Chunks of 50 items enqueued per reconcile pass, bounding in-flight work.
RECONCILE_MAX_CHUNKS_PER_RUN = config(
    "RECONCILE_MAX_CHUNKS_PER_RUN",
    default=by_tier(4, 8, 20),
    cast=int,
)
# Cap on how many users one cache-warming sweep may touch, mirroring the
# existing STARTUP_WARMUP_TASK_LIMIT in app/tasks_discover.py.
WARMUP_USER_LIMIT = config(
    "WARMUP_USER_LIMIT",
    default=by_tier(10, 20, 50),
    cast=int,
)
# History day payloads built ahead of a visit: the days of the last
# HISTORY_COVERAGE_WINDOW_DAYS (and at least one page), in the user's own game
# logging style. Older days and the other style are built when a page asks.
# 0 warms every day; True below also warms the style the user does not use.
HISTORY_COVERAGE_WINDOW_DAYS = config(
    "HISTORY_COVERAGE_WINDOW_DAYS", default=62, cast=int
)
HISTORY_COVERAGE_BOTH_STYLES = config(
    "HISTORY_COVERAGE_BOTH_STYLES", default=False, cast=bool
)
METADATA_BACKFILL_SCALE = by_tier(0.25, 0.5, 1.0)
# The nightly calendar reload queues a metadata backfill rather than running one
# inline at up to 5000 items, which used to hold a worker while filling the cache
# with thousands of provider payloads.
CALENDAR_RELOAD_BACKFILL_BATCH_SIZE = config(
    "CALENDAR_RELOAD_BACKFILL_BATCH_SIZE",
    default=by_tier(250, 500, 1000),
    cast=int,
)
# fetch_releases walks every tracked item, one provider call at a time. As a
# single task that holds a worker for the whole walk -- 16.6 minutes on a
# 1444-item library -- so process the work in bounded slices and re-queue the
# remainder between them. Smaller hosts free the worker more often, at the cost
# of more task round-trips; 0 disables chunking and restores the single pass.
CALENDAR_RELOAD_CHUNK_SIZE = config(
    "CALENDAR_RELOAD_CHUNK_SIZE",
    default=by_tier(50, 100, 200),
    cast=int,
)
# Provider responses are not cached, so every selected item costs a live network
# call. Skip items whose calendar was checked within this window; the daily beat
# reload still refreshes everything, while the extra import-triggered and manual
# reloads in between stop re-walking unchanged items. Items that TMDB's change
# feed reports as changed bypass this gate. Not tier-derived: how stale a release
# date may be is a correctness question, not a sizing one. 0 disables it.
CALENDAR_ITEM_STALE_AFTER_HOURS = config(
    "CALENDAR_ITEM_STALE_AFTER_HOURS",
    default=12,
    cast=int,
)
# How often a show whose episodes have all aired is looked at again for a newly
# announced season. TMDB's change feed covers a few days and TVDB has none, so
# without this such a show never gains the new season's calendar events and
# cannot sort by "Next episode air date". 0 disables it.
CALENDAR_AIRED_SHOW_RECHECK_DAYS = config(
    "CALENDAR_AIRED_SHOW_RECHECK_DAYS",
    default=7,
    cast=int,
)
# Cap on TMDB /changes pagination. total_pages there can run into the hundreds,
# and the loop had no limit at all.
TMDB_CHANGES_MAX_PAGES = config(
    "TMDB_CHANGES_MAX_PAGES",
    default=by_tier(5, 10, 20),
    cast=int,
)


def _scaled(value: int) -> int:
    """Scale a batch size to the host, never below 1."""
    return max(1, int(value * METADATA_BACKFILL_SCALE))


CELERY_BEAT_SCHEDULE = {
    "cleanup_task_results": {
        "task": "Cleanup task results",
        "schedule": 60 * 15,
        "kwargs": {"batch_size": 5000},
    },
    # A control-command reply (control.revoke, the celery_ping health check)
    # can write a malformed Kombu Redis binding at any point during uptime,
    # not just at startup, so this bounds how long a bad entry survives (#588).
    # The Statistics sync's safety net: finds users whose ranges trail their
    # changes (lost/starved sync messages, midnight rollover, first build after
    # an upgrade or a cache flush) and queues a sync.
    "reconcile_statistics_sync": {
        "task": "Reconcile statistics sync",
        "schedule": 60,
        # DatabaseScheduler owns this safety-net lifetime: replace any old
        # absolute expiry with a rolling lifetime. The next fresh tick rechecks
        # durable state, so stale ticks need not replay database work.
        "expires": None,
        "options": {"expire_seconds": 60},
    },
    # Outbound deliveries (watched state, ratings) retry through their own
    # next_attempt_at and recover from a lost broker message only if something
    # sweeps them. Without this entry a transient failure was never retried.
    "sweep_watched_state_deliveries": {
        "task": "Sweep pending watched state deliveries",
        "schedule": 60,
    },
    "repair_celery_broker_bindings": {
        "task": "Repair Celery broker bindings",
        "schedule": 60 * 15,
    },
    # Folds shows that the old routing tracked in both Anime and TV Shows back
    # into one row. Self-limiting: once no duplicates remain each run is a
    # single cheap query (discussion #967).
    "repair_duplicated_anime_libraries": {
        "task": "Repair duplicated anime libraries",
        "schedule": crontab(hour=5, minute=30),
    },
    "reload_calendar": {
        "task": "Reload calendar",
        "schedule": 60 * 60 * 24,  # every 24 hours
    },
    "cleanup_image_cache": {
        "task": "Cleanup image cache",
        "schedule": crontab(hour=4, minute=0),
    },
    "purge_expired_discover_caches": {
        "task": "Purge expired Discover caches",
        "schedule": crontab(hour=4, minute=15),
    },
    # Interval, not crontab: a never-run interval entry is due as soon as beat
    # starts, so a database with no statistics is fixed on the first boot.
    "optimize_database_statistics": {
        "task": "Optimize database planner statistics",
        "schedule": 60 * 60 * 24,
    },
    "warm_database_page_cache": {
        "task": "Warm database page cache",
        "schedule": 60 * 60,
    },
    "write_database_snapshot": {
        "task": "Write database snapshot",
        "schedule": crontab(hour=DB_SNAPSHOT_HOUR, minute=DB_SNAPSHOT_MINUTE),
    },
    "send_release_notifications": {
        "task": "Send release notifications",
        "schedule": 60 * 10,  # every 10 minutes
    },
    "send_daily_digest": {
        "task": "Send daily digest",
        "schedule": crontab(hour=DAILY_DIGEST_HOUR, minute=0),
    },
    "send_premiere_digest": {
        "task": "Send premiere digest",
        "schedule": crontab(day_of_week="mon", hour=DAILY_DIGEST_HOUR, minute=0),
    },
    "backfill_item_metadata": {
        "task": "Backfill item metadata",
        "schedule": crontab(hour=3, minute=0),  # every day at 3 AM
        "kwargs": {
            "batch_size": _scaled(1000),
            "game_length_batch_size": _scaled(200),
        },  # A nightly bulk pass plus a bounded HLTB enrichment sweep.
    },
    "backfill_item_metadata_incremental": {
        "task": "Backfill item metadata",
        # Gradual convergence between the nightly passes. Stretched on smaller
        # hosts, where a sweep every 15 minutes never leaves the CPU idle.
        "schedule": by_tier(
            crontab(minute=0),
            crontab(minute="*/30"),
            crontab(minute="*/15"),
        ),
        "kwargs": {
            "batch_size": _scaled(150),
            "game_length_batch_size": _scaled(25),
        },
    },
    "nightly_metadata_quality_backfill": {
        "task": "Nightly metadata quality backfill",
        "schedule": crontab(hour=3, minute=30),  # every day at 3:30 AM
        "kwargs": {
            "genre_batch_size": _scaled(1500),
            "runtime_batch_size": _scaled(500),
            "episode_season_batch_size": _scaled(300),
            "credits_batch_size": _scaled(2500),
            "credits_scan_multiplier": 20,
            "trakt_popularity_batch_size": _scaled(300),
        },
    },
    # Both reconcilers gate on durable completion state and back off once they
    # find nothing (app/reconcile_state.py), so this is a cheap liveness poll
    # rather than the every-5-minutes whole-library scan it used to be.
    "ensure_genre_backfill_reconcile": {
        "task": "Ensure genre backfill reconcile",
        "schedule": RECONCILE_INTERVAL_SECONDS,
        "kwargs": {
            "batch_size": GENRE_RECONCILE_BATCH_SIZE,
        },
    },
    "ensure_provider_backfill_reconcile": {
        "task": "Ensure watch provider backfill reconcile",
        "schedule": RECONCILE_INTERVAL_SECONDS,
        "kwargs": {
            "batch_size": WATCH_PROVIDERS_RECONCILE_BATCH_SIZE,
        },
    },
    "ensure_external_ids_backfill_reconcile": {
        "task": "Ensure external ID backfill reconcile",
        "schedule": RECONCILE_INTERVAL_SECONDS,
        "kwargs": {
            "batch_size": EXTERNAL_IDS_RECONCILE_BATCH_SIZE,
        },
    },
    # One-shot: goes quiet for good once every show with a feed has been swept
    # (app/tasks_podcast.py), so this only has to catch the passes after the
    # first one the startup hook kicks off.
    "ensure_podcast_website_backfill_reconcile": {
        "task": "Ensure podcast website backfill reconcile",
        "schedule": RECONCILE_INTERVAL_SECONDS,
    },
    # One-shot like the podcast sweep: the startup hook starts it, and this
    # resumes a library too large to finish in one run (app/tasks_watch_state.py).
    "ensure_watch_state_repair": {
        "task": "Ensure watch state repair",
        "schedule": RECONCILE_INTERVAL_SECONDS,
    },
    "warm_discover_api_cache": {
        "task": "Warm Discover API Cache",
        "schedule": by_tier(60 * 60 * 6, 60 * 60 * 3, 60 * 60),
    },
    "warm_history_day_cache_coverage": {
        "task": "Warm History Day Cache Coverage",
        "schedule": by_tier(60 * 60 * 12, 60 * 60 * 6, 60 * 60 * 2),
    },
    "refresh_discover_profiles": {
        "task": "Refresh Discover Profiles",
        "schedule": crontab(hour=4, minute=0),  # every day at 4 AM
    },
    "sync_imdb_ratings": {
        "task": "Sync IMDB ratings from datasets",
        "schedule": crontab(hour=5, minute=0),  # every day at 5 AM
    },
    "backfill_youtube_game_tags": {
        "task": "Backfill YouTube game tags",
        "schedule": 60 * 30,  # 20 videos per run keeps requests to YouTube slow
    },
    "sync_mal_ratings": {
        "task": "Sync MAL ratings from API",
        "schedule": crontab(hour=5, minute=15),  # every day at 5:15 AM
    },
    "backfill_opencritic_scores": {
        "task": "Backfill OpenCritic scores",
        # Only spends quota in the hour before the daily reset; other runs no-op.
        "schedule": crontab(minute="*/20"),
    },
}

IS_PROD = not any(cmd in sys.argv for cmd in ("runserver", "test"))
if IS_PROD:
    ALLAUTH_TRUSTED_CLIENT_IP_HEADER = "X-Real-IP"

# Allauth settings
if CSRF_TRUSTED_ORIGINS:
    # Check if all origins start with http:// or https://
    all_http = all(
        origin.startswith("http://") for origin in CSRF_TRUSTED_ORIGINS if origin
    )
    all_https = all(
        origin.startswith("https://") for origin in CSRF_TRUSTED_ORIGINS if origin
    )

    if all_http:
        ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http"
    elif all_https:
        ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"
    else:
        # Mixed protocols or invalid formats, use config value
        ACCOUNT_DEFAULT_HTTP_PROTOCOL = config(
            "ACCOUNT_DEFAULT_HTTP_PROTOCOL",
            default="https",
        )
else:
    # Empty CSRF_TRUSTED_ORIGINS, default to http
    ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http"

ACCOUNT_LOGOUT_REDIRECT_URL = config(
    "ACCOUNT_LOGOUT_REDIRECT_URL",
    default="/accounts/login/?loggedout=1",
)
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_USER_MODEL_EMAIL_FIELD = None
ACCOUNT_FORMS = {
    "login": "users.forms.CustomLoginForm",
    "signup": "users.forms.CustomSignupForm",
}
SOCIALACCOUNT_FORMS = {
    "signup": "users.forms.CustomSocialSignupForm",
}

if BASE_URL:
    # Join base only if relative URL
    if not urlparse(ACCOUNT_LOGOUT_REDIRECT_URL).netloc:
        ACCOUNT_LOGOUT_REDIRECT_URL = urljoin(BASE_URL, ACCOUNT_LOGOUT_REDIRECT_URL)
    # Cookie paths must match FORCE_SCRIPT_NAME exactly to ensure browsers
    # send session cookies with all requests under the base URL prefix
    SESSION_COOKIE_PATH = BASE_URL

SOCIALACCOUNT_LOGIN_ON_GET = True

SOCIAL_PROVIDERS = config("SOCIAL_PROVIDERS", default="", cast=Csv())
INSTALLED_APPS += SOCIAL_PROVIDERS

SOCIALACCOUNT_PROVIDERS = config(
    "SOCIALACCOUNT_PROVIDERS",
    default=secret(
        "SOCIALACCOUNT_PROVIDERS_FILE",
        default="{}",
    ),
    cast=json.loads,
)

SOCIALACCOUNT_ONLY = config("SOCIALACCOUNT_ONLY", default=False, cast=bool)
if SOCIALACCOUNT_ONLY:
    ACCOUNT_EMAIL_VERIFICATION = "none"

# The built-in Floppy app signs social and OIDC accounts in through the OAuth
# device flow under a fixed client id. When this is on, the person is told on
# the approval page that the app gets full access, and its token may then manage
# the account except for credential disclosure/issuance (including URL secrets,
# OAuth approval, password/authenticator/recovery and admin credential changes),
# which require a session (password sign-in). Off: the app gets a scoped token like
# any other application, and tokens already issued lose full access at once.
# See integrations.oauth_scope_info.has_full_account_access.
FLOPPY_APP_FULL_ACCESS = config("FLOPPY_APP_FULL_ACCESS", default=True, cast=bool)

REGISTRATION = config("REGISTRATION", default=True, cast=bool)
if not REGISTRATION:
    ACCOUNT_ADAPTER = "users.account_adapter.NoNewUsersAccountAdapter"

REDIRECT_LOGIN_TO_SSO = config("REDIRECT_LOGIN_TO_SSO", default=False, cast=bool)

# Demo provisioning is opt-in. It creates a publicly known login
# (demo/demodemo) after migrations, which is what a shared demo install
# wants and what a private install must set DEMO_ACCOUNT_ENABLED=True to
# get. Existing installs that already provisioned the account keep it:
# this setting gates provisioning, not the account itself (see
# floppy_preflight's demo check for how to notice and retire it).
DEMO_ACCOUNT_ENABLED = config("DEMO_ACCOUNT_ENABLED", default=False, cast=bool)
