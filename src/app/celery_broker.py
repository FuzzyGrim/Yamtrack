import logging

import redis
from django.conf import settings
from kombu.transport.redis import Channel as KombuRedisChannel

logger = logging.getLogger(__name__)

KOMBU_REDIS_DEFAULT_SEPARATOR = KombuRedisChannel.sep
KOMBU_REDIS_BINDING_KEY_PATTERN = KombuRedisChannel.keyprefix_queue
SCAN_PAGE_SIZE = 1000
KOMBU_REDIS_BINDING_FIELD_COUNT = 3
LEGACY_PRIORITY_SEPARATOR = ":"


def _decode_redis_value(value: bytes | str) -> str:
    """Return a text value for Redis keys and set members."""
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def normalize_kombu_binding_member(
    raw_value: str,
    desired_separator: str,
) -> str | None:
    """Return the binding entry normalized to the desired Kombu separator."""
    separators = [desired_separator]
    for separator in (KOMBU_REDIS_DEFAULT_SEPARATOR, LEGACY_PRIORITY_SEPARATOR):
        if separator not in separators:
            separators.append(separator)

    for separator in separators:
        parts = raw_value.split(separator)
        if len(parts) == KOMBU_REDIS_BINDING_FIELD_COUNT:
            return desired_separator.join(parts)
    return None


def repair_celery_redis_bindings() -> dict[str, int]:
    """Normalize persisted Kombu Redis bindings after a separator change."""
    transport_options = getattr(settings, "CELERY_BROKER_TRANSPORT_OPTIONS", {}) or {}
    desired_separator = transport_options.get("sep")
    broker_url = getattr(settings, "CELERY_BROKER_URL", "")

    if not desired_separator or desired_separator == KOMBU_REDIS_DEFAULT_SEPARATOR:
        return {"keys": 0, "members": 0, "repaired": 0, "removed": 0}
    if not broker_url.startswith(("redis://", "rediss://")):
        return {"keys": 0, "members": 0, "repaired": 0, "removed": 0}

    global_keyprefix = transport_options.get("global_keyprefix", "") or ""
    binding_match = f"{global_keyprefix}{KOMBU_REDIS_BINDING_KEY_PATTERN % '*'}"
    client = redis.Redis.from_url(
        broker_url,
        socket_timeout=transport_options.get("socket_timeout", 30),
        socket_connect_timeout=transport_options.get("socket_connect_timeout", 10),
    )
    summary = {"keys": 0, "members": 0, "repaired": 0, "removed": 0}

    # SCAN walks the whole keyspace (every cache key too) to find the few
    # binding keys; Redis's default of 10 keys per call made that thousands of
    # round trips, about 3 s, on every run.
    for raw_key in client.scan_iter(match=binding_match, count=SCAN_PAGE_SIZE):
        summary["keys"] += 1
        key = _decode_redis_value(raw_key)
        members = client.smembers(key)
        summary["members"] += len(members)
        stale_members: list[str] = []
        repaired_members: set[str] = set()

        for raw_member in members:
            member = _decode_redis_value(raw_member)
            normalized_member = normalize_kombu_binding_member(
                member,
                desired_separator,
            )
            if normalized_member is None:
                stale_members.append(member)
                summary["removed"] += 1
                logger.warning(
                    "Removing malformed Kombu Redis binding from %s: %r",
                    key,
                    member,
                )
                continue
            if normalized_member != member:
                stale_members.append(member)
                repaired_members.add(normalized_member)
                summary["repaired"] += 1

        if stale_members or repaired_members:
            with client.pipeline() as pipe:
                if stale_members:
                    pipe.srem(key, *stale_members)
                if repaired_members:
                    pipe.sadd(key, *sorted(repaired_members))
                pipe.execute()

    return summary
