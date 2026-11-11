from fakeredis import FakeRedisConnection

from .settings import *  # noqa: F403

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,  # noqa: F405
        "TIMEOUT": 18000,  # 5 hours
        "OPTIONS": {
            "CONNECTION_POOL_KWARGS": {"connection_class": FakeRedisConnection},
        },
    },
}

CELERY_TASK_ALWAYS_EAGER = True

TESTING = True

# Steam API key for testing
STEAM_API_KEY = "test_steam_api_key"

# Keep the suite off the network even when a developer key is exported.
OMDB_API_KEY = ""
OMDB_DAILY_LIMIT = 900
