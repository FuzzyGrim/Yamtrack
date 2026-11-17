"""Cheap validators for token feeds (calendar links, Discover RSS).

Calendar apps and feed readers poll these links all day. A feed's validator is
built from change markers that already exist, so an unchanged poll is answered
without building the body. Contract: docs/architecture/view-caches.md, "Token feeds".
"""

from __future__ import annotations

import hashlib
import uuid

from django.core.cache import cache

from app.models.statistics import StatisticsSyncState

# How long a client may reuse a feed before asking again. Calendar apps poll
# every 5 minutes to a few hours; 15 minutes matches the reuse window the
# feeds have always had, so no client sees changes later than before.
FEED_MAX_AGE_SECONDS = 15 * 60

# A rendered feed is kept for at most this long and only below this size.
# Entries are keyed by the validator, so a change never serves an old body;
# the lifetime only bounds memory.
FEED_BODY_CACHE_SECONDS = 60 * 60
FEED_BODY_MAX_BYTES = 4 * 1024 * 1024

_SYNC_STATE_FIELDS = (
    "generation",
    "last_marked_at",
    "full_sweep_requested_at",
    "last_finished_at",
)


def user_change_marker(user_id: int) -> str:
    """Return a value that moves whenever the user's library or plays change.

    Every tracking and play write marks the Statistics sync (see
    docs/architecture/statistics-sync.md), which bumps ``generation``. An
    import bumps it once at the start and the sync that follows moves the
    other fields, so a feed read mid-import is not pinned to partial data.
    """
    row = (
        StatisticsSyncState.objects.filter(user_id=user_id)
        .values_list(*_SYNC_STATE_FIELDS)
        .first()
    )
    return repr(row)


def shared_marker(key: str) -> str:
    """Return a cache-held change token, creating a fresh one when missing.

    A missing token (first use, eviction) becomes a new random value, which
    can only make a validator differ, never match an old one.
    """
    value = cache.get(key)
    if value is None:
        cache.add(key, uuid.uuid4().hex, None)
        value = cache.get(key)
    return value or uuid.uuid4().hex


def bump_shared_marker(key: str) -> None:
    """Record that what ``key`` guards changed."""
    cache.set(key, uuid.uuid4().hex, None)


def feed_etag(*parts) -> str:
    """Return a weak ETag for the given validator inputs."""
    digest = hashlib.sha256(repr(parts).encode()).hexdigest()[:32]
    return f'W/"{digest}"'


def body_cache_key(prefix: str, user_id: int, etag: str) -> str:
    """Key for a rendered feed body; the user id keeps users apart."""
    return f"{prefix}:{user_id}:{etag.removeprefix('W/').strip(chr(34))}"


def remember_body(key: str, body: bytes) -> None:
    """Keep a rendered feed briefly, unless it is too large to be worth it."""
    if len(body) <= FEED_BODY_MAX_BYTES:
        cache.set(key, body, FEED_BODY_CACHE_SECONDS)
