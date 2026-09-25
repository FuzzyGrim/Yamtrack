import logging
import zlib

from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

from app import helpers
from app.models import TMDB_RATING_MEDIA_TYPES, Item, Sources
from app.providers import services, tmdb

logger = logging.getLogger(__name__)

REFRESH_LOCK_KEY = "tmdb_rating_refresh"
REFRESH_LOCK_TIMEOUT = 60 * 60 * 6 + 60
BUCKET_COUNT = 7


def rating_refresh_bucket(item):
    """Return a stable 0-6 bucket for spreading TMDB rating refreshes."""
    payload = f"{item.media_type}:{item.media_id}:{item.season_number or ''}"
    return (zlib.crc32(payload.encode()) & 0xFFFFFFFF) % BUCKET_COUNT


def is_todays_refresh_bucket(item, today=None):
    """Return True when this item should be refreshed on ``today``."""
    if today is None:
        today = timezone.localdate()
    return rating_refresh_bucket(item) == today.toordinal() % BUCKET_COUNT


def tmdb_rating_items():
    """Return TMDB items that participate in TMDB rating sort."""
    return Item.objects.filter(
        source=Sources.TMDB.value,
        media_type__in=TMDB_RATING_MEDIA_TYPES,
    ).order_by(
        F("tmdb_rating_updated_at").asc(nulls_first=True),
        "id",
    )


def should_refresh_item(item, *, force=False, today=None):
    """Return True when the periodic or forced pass should fetch this item."""
    if force or item.tmdb_rating_updated_at is None:
        return True
    return is_todays_refresh_bucket(item, today)


def refresh_item_rating(item, *, force=False):
    """Fetch and store a TMDB rating for a single item."""
    if force:
        tmdb.delete_metadata_cache(item)
        score = tmdb.fetch_vote_average(item)
    else:
        score = tmdb.cached_score(item)
        if score is None:
            score = tmdb.fetch_vote_average(item)
    helpers.apply_tmdb_rating(item, score)


def refresh_tmdb_ratings(*, force=False, today=None):
    """Backfill missing TMDB ratings and refresh a hash-bucket of existing ones."""
    if not cache.add(REFRESH_LOCK_KEY, True, timeout=REFRESH_LOCK_TIMEOUT):  # noqa: FBT003
        logger.info("TMDB rating refresh already running")
        return {"status": "already_running"}

    refreshed = 0
    skipped = 0
    failed = 0
    try:
        for item in tmdb_rating_items().iterator():
            if not should_refresh_item(item, force=force, today=today):
                skipped += 1
                continue
            try:
                refresh_item_rating(item, force=force)
                refreshed += 1
            except services.ProviderAPIError as error:
                if error.status_code == 404:  # noqa: PLR2004
                    item.tmdb_rating_updated_at = timezone.now()
                    item.save(update_fields=["tmdb_rating_updated_at"])
                    logger.warning(
                        "TMDB rating 404 for %s %s; skipping future retries",
                        item.media_type,
                        item.media_id,
                    )
                    skipped += 1
                else:
                    failed += 1
                    logger.exception(
                        "Failed to refresh TMDB rating for %s %s",
                        item.media_type,
                        item.media_id,
                    )
            except Exception:
                failed += 1
                logger.exception(
                    "Failed to refresh TMDB rating for %s %s",
                    item.media_type,
                    item.media_id,
                )
    finally:
        cache.delete(REFRESH_LOCK_KEY)

    logger.info(
        "TMDB rating refresh finished force=%s refreshed=%s skipped=%s failed=%s",
        force,
        refreshed,
        skipped,
        failed,
    )
    return {
        "status": "ok",
        "refreshed": refreshed,
        "skipped": skipped,
        "failed": failed,
    }
