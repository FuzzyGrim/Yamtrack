from http import HTTPStatus

from django.conf import settings
from django.core.cache import cache
from django.db import DEFAULT_DB_ALIAS, connections
from django.http import HttpResponse
from redis import Redis


def health_check(_request):
    """Fast unauthenticated dependency check for container health probes."""
    try:
        with connections[DEFAULT_DB_ALIAS].cursor() as cursor:
            cursor.execute("SELECT 1")

        redis_client = Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        redis_client.ping()

        cache_key = "yamtrack:health"
        cache.set(cache_key, "ok", timeout=5)
        if cache.get(cache_key) != "ok":
            return HttpResponse(
                "cache unavailable",
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
    except Exception:  # noqa: BLE001
        return HttpResponse("unhealthy", status=HTTPStatus.SERVICE_UNAVAILABLE)
    else:
        return HttpResponse("ok")
