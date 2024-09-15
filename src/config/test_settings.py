from fakeredis import FakeConnection

from .settings import *  # noqa: F403

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL, # noqa: F405
        "TIMEOUT": 18000,  # 5 hours
        "OPTIONS": {
            "connection_class": FakeConnection,
        },
    },
}

TESTING = True
