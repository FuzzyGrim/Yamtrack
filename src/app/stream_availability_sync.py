import logging
import zlib

from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

from app import stream_availability
from app.models import STREAM_AVAILABILITY_MEDIA_TYPES, Item, MediaTypes, Sources
from app.providers import services, tmdb

logger = logging.getLogger(__name__)

REFRESH_LOCK_KEY = "stream_availability_refresh"
REFRESH_LOCK_TIMEOUT = 60 * 60 * 6 + 60
BUCKET_COUNT = 7


def refresh_bucket(item):
    """Return a stable 0-6 bucket for spreading stream-availability refreshes."""
    payload = f"{item.media_type}:{item.media_id}:{item.season_number or ''}"
    return (zlib.crc32(payload.encode()) & 0xFFFFFFFF) % BUCKET_COUNT


def is_todays_refresh_bucket(item, today=None):
    """Return True when this item should be refreshed on ``today``."""
    if today is None:
        today = timezone.localdate()
    return refresh_bucket(item) == today.toordinal() % BUCKET_COUNT


def stream_availability_items():
    """Return TMDB items that participate in stream-availability filtering."""
    return Item.objects.filter(
        source=Sources.TMDB.value,
        media_type__in=STREAM_AVAILABILITY_MEDIA_TYPES,
    ).order_by(
        F("stream_availability_updated_at").asc(nulls_first=True),
        "id",
    )


def should_refresh_item(item, *, force=False, today=None):
    """Return True when the periodic or forced pass should fetch this item."""
    if force or item.stream_availability_updated_at is None:
        return True
    return is_todays_refresh_bucket(item, today)


def refresh_item_stream_availability(item, regions):
    """Fetch and store watch-provider availability for a single item."""
    all_providers = tmdb.fetch_watch_providers(item)
    fallback_providers = None
    if item.media_type == MediaTypes.SEASON.value:
        fallback_providers = tmdb.fetch_tv_watch_providers(item.media_id)
    item.stream_availability = stream_availability.compute_stream_availability(
        all_providers, regions, fallback_providers
    )
    item.stream_availability_updated_at = timezone.now()
    item.save(update_fields=["stream_availability", "stream_availability_updated_at"])


def refresh_stream_availability(*, force=False, today=None):
    """Backfill missing availability and refresh a hash-bucket of existing items."""
    regions = stream_availability.configured_regions()
    if not regions:
        return {"status": "no_regions_configured"}

    if not cache.add(REFRESH_LOCK_KEY, True, timeout=REFRESH_LOCK_TIMEOUT):  # noqa: FBT003
        logger.info("Stream availability refresh already running")
        return {"status": "already_running"}

    refreshed = 0
    skipped = 0
    failed = 0
    try:
        for item in stream_availability_items().iterator():
            if not should_refresh_item(item, force=force, today=today):
                skipped += 1
                continue
            try:
                refresh_item_stream_availability(item, regions)
                refreshed += 1
            except services.ProviderAPIError as error:
                if error.status_code == 404:  # noqa: PLR2004
                    item.stream_availability_updated_at = timezone.now()
                    item.save(update_fields=["stream_availability_updated_at"])
                    logger.warning(
                        "Stream availability 404 for %s %s; skipping future retries",
                        item.media_type,
                        item.media_id,
                    )
                    skipped += 1
                else:
                    failed += 1
                    logger.exception(
                        "Failed to refresh stream availability for %s %s",
                        item.media_type,
                        item.media_id,
                    )
            except Exception:
                failed += 1
                logger.exception(
                    "Failed to refresh stream availability for %s %s",
                    item.media_type,
                    item.media_id,
                )
    finally:
        cache.delete(REFRESH_LOCK_KEY)

    logger.info(
        "Stream availability refresh finished force=%s refreshed=%s "
        "skipped=%s failed=%s",
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
