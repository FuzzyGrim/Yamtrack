"""Django settings for Yamtrack project."""

import warnings
import zoneinfo
from pathlib import Path

from decouple import Csv, config
from django.core.cache import CacheKeyWarning

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/stable/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config("SECRET", default="secret")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config("DEBUG", default=False, cast=bool)

INTERNAL_IPS = ["127.0.0.1"]

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="*", cast=Csv())
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Application definition

INSTALLED_APPS = [
    "whitenoise.runserver_nostatic",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "app",
    "integrations",
    "lists",
    "users",
    "crispy_forms",
    "crispy_bootstrap5",
    "debug_toolbar",
    "django_celery_results",
    "django_select2",
    "simple_history",
]

STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

MIDDLEWARE = [
    "debug_toolbar.middleware.DebugToolbarMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "config.middleware.LoginRequiredMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
]

ROOT_URLCONF = "config.urls"

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
                "app.context_processors.export_vars",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database
# https://docs.djangoproject.com/en/stable/ref/settings/#databases

# create db folder if it doesn't exist
Path(BASE_DIR / "db").mkdir(parents=True, exist_ok=True)

if config("DB_HOST", default=None):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": config("DB_HOST"),
            "NAME": config("DB_NAME"),
            "USER": config("DB_USER"),
            "PASSWORD": config("DB_PASSWORD"),
            "PORT": config("DB_PORT"),
        },
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db" / "db.sqlite3",
        },
    }


# Cache
# https://docs.djangoproject.com/en/stable/topics/cache/
REDIS_URL = config("REDIS_URL", default="redis://localhost:6379")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "TIMEOUT": 18000,  # 5 hours,
        "VERSION": 2,
    },
}

# not using Memcached, ignore CacheKeyWarning
# https://docs.djangoproject.com/en/stable/topics/cache/#cache-key-warnings
warnings.simplefilter("ignore", CacheKeyWarning)


# Password validation
# https://docs.djangoproject.com/en/stable/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
]

# Logging
# https://docs.djangoproject.com/en/stable/topics/logging/

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "loggers": {
        "psycopg": {
            "level": "INFO",
        },
        "urllib3": {
            "level": "INFO",
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
        },
    },
    "root": {"handlers": ["console"], "level": "DEBUG" if DEBUG else "INFO"},
}

# Internationalization
# https://docs.djangoproject.com/en/stable/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = config("TZ", default="UTC")

USE_I18N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/stable/howto/static-files/

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_DIRS = [BASE_DIR / "static"]

# Default primary key field type
# https://docs.djangoproject.com/en/stable/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Auth settings

LOGIN_URL = "login"

LOGIN_REDIRECT_URL = "home"

LOGOUT_REDIRECT_URL = "login"

AUTH_USER_MODEL = "users.User"

# Yamtrack settings

TZ = zoneinfo.ZoneInfo(TIME_ZONE)

IMG_NONE = "https://www.themoviedb.org/assets/2/v4/glyphicons/basic/glyphicons-basic-38-picture-grey-c2ebdbb057f2a7614185931650f8cee23fa137b93812ccb132b9df511df1cfac.svg"

REQUEST_TIMEOUT = 30  # seconds

TMDB_API = config("TMDB_API", default="61572be02f0a068658828f6396aacf60")
TMDB_NSFW = config("TMDB_NSFW", default=False, cast=bool)
TMDB_LANG = config("TMDB_LANG", default="en")

MAL_API = config("MAL_API", default="25b5581dafd15b3e7d583bb79e9a1691")
MAL_NSFW = config("MAL_NSFW", default=False, cast=bool)

IGDB_ID = config("IGDB_ID", default="8wqmm7x1n2xxtnz94lb8mthadhtgrt")
IGDB_SECRET = config("IGDB_SECRET", default="ovbq0hwscv58hu46yxn50hovt4j8kj")
IGDB_NSFW = config("IGDB_NSFW", default=False, cast=bool)

REGISTRATION = config("REGISTRATION", default=True, cast=bool)

# Third party settings

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

DEBUG_TOOLBAR_CONFIG = {
    "SKIP_TEMPLATE_PREFIXES": (
        "django/forms/widgets/",
        "admin/widgets/",
        "app/components/",
        "bootstrap5/",
    ),
}

SELECT2_CACHE_BACKEND = "default"
SELECT2_JS = ["js/jquery.min.js", "js/select2.min.js"]
SELECT2_I18N_PATH = "js/i18n"
SELECT2_CSS = ["css/select2.min.css", "css/select2-bootstrap-5.min.css"]
SELECT2_THEME = "bootstrap-5"

# Celery settings

CELERY_BROKER_URL = REDIS_URL
CELERY_TIMEZONE = TIME_ZONE

CELERY_WORKER_HIJACK_ROOT_LOGGER = False
CELERY_WORKER_CONCURRENCY = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 600  # 10 minutes

CELERY_RESULT_EXTENDED = True
CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "default"

# https://docs.celeryq.dev/en/stable/userguide/configuration.html#task-serializer
CELERY_TASK_SERIALIZER = "pickle"
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std-setting-accept_content
CELERY_ACCEPT_CONTENT = ["application/json", "application/x-python-serialize"]
