from fakeredis import FakeConnection

from .settings import *  # noqa: F403

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,  # noqa: F405
        "TIMEOUT": 18000,  # 5 hours
        "OPTIONS": {
            "CONNECTION_POOL_KWARGS": {"connection_class": FakeConnection},
        },
    },
}

CELERY_TASK_ALWAYS_EAGER = True

TESTING = True

# Steam API key for testing
STEAM_API_KEY = "test_steam_api_key"
