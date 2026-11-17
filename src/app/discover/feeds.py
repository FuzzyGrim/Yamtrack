"""RSS feed for external-metadata Discover rows (Radarr/Sonarr RSS List compatible)."""

from __future__ import annotations

import logging
import time

from django.contrib.auth.decorators import login_not_required
from django.contrib.syndication.views import Feed
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.cache import get_conditional_response, patch_cache_control
from django.utils.feedgenerator import Rss201rev2Feed
from django.views.decorators.http import require_GET

from app import feed_validation
from app.discover import tab_cache
from app.discover.artwork import ROW_CACHE_TTL_SECONDS
from app.discover.filters import exclude_owned_items, get_owned_keys_by_media_type
from app.discover.registry import get_rows
from app.discover.schemas import RowResult
from app.discover.service import get_live_row
from app.models import MediaTypes, Sources
from app.templatetags.app_tags import media_url
from users.models import User

logger = logging.getLogger(__name__)

EXTERNAL_ROW_SOURCES = {"trakt", "provider"}

# The feed serves a row built in the background and kept here, never one built
# inside the request (building one cost seconds of CPU per poll). An entry is
# rebuilt when the user's library or Discover activity moves, or after the
# Discover row lifetime, and the old one is served until the new one lands.
# It is kept for a week so an arr poll after a quiet spell still gets a list.
FEED_ROW_CACHE_PREFIX = "discover_feed_row:v1"
FEED_ROW_KEEP_SECONDS = 7 * 24 * 60 * 60
FEED_ROW_REFRESH_LOCK_SECONDS = 5 * 60
# With nothing stored yet the feed answers 503 rather than an empty list: an
# empty list can make Radarr/Sonarr list cleaning remove library entries.
FEED_ROW_RETRY_AFTER_SECONDS = 60

# Radarr's RSS List parser only accepts <category>movie</category>; Sonarr's only
# accepts <category>show</category>. Media types with no arr consumer are omitted.
ARR_CATEGORY_BY_MEDIA_TYPE = {
    MediaTypes.MOVIE.value: "movie",
    MediaTypes.TV.value: "show",
    MediaTypes.ANIME.value: "show",
}


def get_external_row_definitions(media_type: str):
    """Return Discover row definitions sourced from external metadata providers."""
    return [row for row in get_rows(media_type) if row.source in EXTERNAL_ROW_SOURCES]


def get_feed_row(user, media_type, row_key, *, skip_planning=True, skip_collected=True):
    """Return the filtered row for feed/preview rendering, or None if unknown."""
    row = get_live_row(user, media_type, row_key, skip_planning=skip_planning)
    if row is None:
        return None
    if skip_collected:
        owned_keys = get_owned_keys_by_media_type(user, media_type)
        row.items = exclude_owned_items(row.items, owned_keys)
    return row


def feed_row_key(user_id, media_type, row_key, skip_planning, skip_collected):
    """Return the cache key of the row one RSS link serves."""
    return (
        f"{FEED_ROW_CACHE_PREFIX}:{user_id}:{media_type}:{row_key}:"
        f"{int(skip_planning)}:{int(skip_collected)}"
    )


def _feed_row_marker(user_id, media_type) -> str:
    """Return what the row depends on besides provider data."""
    return (
        f"{feed_validation.user_change_marker(user_id)}|"
        f"{tab_cache.get_activity_version(user_id, media_type)}"
    )


def store_feed_row(
    user, media_type, row_key, *, skip_planning, skip_collected, row=None
):
    """Build the feed's row (unless given) and keep it for the RSS link."""
    # Read before building: a change during the build leaves the entry stale.
    marker = _feed_row_marker(user.id, media_type)
    if row is None:
        row = get_feed_row(
            user,
            media_type,
            row_key,
            skip_planning=skip_planning,
            skip_collected=skip_collected,
        )
    if row is None:
        return None
    entry = {"row": row.to_dict(), "built_at": time.time(), "marker": marker}
    cache.set(
        feed_row_key(user.id, media_type, row_key, skip_planning, skip_collected),
        entry,
        FEED_ROW_KEEP_SECONDS,
    )
    return entry


def _queue_feed_row_refresh(user_id, media_type, row_key, flags):
    key = feed_row_key(user_id, media_type, row_key, *flags)
    if not cache.add(f"{key}:refreshing", 1, FEED_ROW_REFRESH_LOCK_SECONDS):
        return
    from app.tasks_discover import refresh_discover_feed_row

    try:
        refresh_discover_feed_row.delay(user_id, media_type, row_key, *flags)
    except Exception:
        cache.delete(f"{key}:refreshing")
        logger.warning(
            "discover_feed_refresh_enqueue_failed user_id=%s media_type=%s row_key=%s",
            user_id,
            media_type,
            row_key,
            exc_info=True,
        )


def _stored_feed_row(user_id, media_type, row_key, flags):
    """Return the kept row entry, queueing a rebuild when it is stale or missing."""
    key = feed_row_key(user_id, media_type, row_key, *flags)
    entry = cache.get(key)
    fresh = (
        entry is not None
        and entry["marker"] == _feed_row_marker(user_id, media_type)
        and time.time() - entry["built_at"] < ROW_CACHE_TTL_SECONDS
    )
    if not fresh:
        _queue_feed_row_refresh(user_id, media_type, row_key, flags)
        if entry is None:
            # An eager worker (tests, the minimal tier) has built it already.
            entry = cache.get(key)
    return entry


def _parse_bool_param(request, name: str, *, default: bool = True) -> bool:
    raw = request.GET.get(name)
    if raw is None:
        return default
    return raw not in {"0", "false", "False"}


class DiscoverRssFeed(Rss201rev2Feed):
    """RSS feed generator emitting the arr-compatible <category> element."""

    def add_item_elements(self, handler, item):
        """Add standard RSS elements plus the Radarr/Sonarr category."""
        super().add_item_elements(handler, item)
        category = item.get("arr_category")
        if category:
            handler.addQuickElement("category", category)


class DiscoverRowFeed(Feed):
    """RSS feed for a single Discover row's external-metadata items."""

    feed_type = DiscoverRssFeed

    def get_object(self, request, obj):
        """Return the row definition and stored row the view resolved."""
        self.request = request
        return obj

    def title(self, obj):
        """Return the feed title."""
        return f"{obj['row_definition'].title} - Floppy"

    def link(self, obj):
        """Return the Discover page URL."""
        return self.request.build_absolute_uri(reverse("discover"))

    def description(self, obj):
        """Return the feed description."""
        return obj["row_definition"].why

    def items(self, obj):
        """Return the row's candidate items."""
        row = obj["row"]
        return row.items if row else []

    def item_title(self, item):
        """Return the item title."""
        return item.title

    def item_description(self, item):
        """Return the item description."""
        return item.source_reason or item.title

    def item_link(self, item):
        """Return the item's Floppy detail URL."""
        return self.request.build_absolute_uri(media_url(item))

    def item_guid(self, item):
        """Return the arr-compatible guid, preferring the TMDB scheme."""
        if item.source == Sources.TMDB.value:
            return f"tmdb://{item.media_id}"
        # Scheme stays "yamtrack": guids are opaque dedupe keys already held
        # by subscribers, and changing them re-marks every item unread.
        return f"yamtrack://{item.media_type}/{item.source}/{item.media_id}"

    def item_guid_is_permalink(self, item):
        """Guids are identifiers, not permalinks."""
        return False

    def item_extra_kwargs(self, item):
        """Expose the arr category for DiscoverRssFeed to render."""
        return {"arr_category": ARR_CATEGORY_BY_MEDIA_TYPE.get(item.media_type, "")}


@login_not_required
@require_GET
def discover_row_feed(request, token, media_type, row_key):
    """Serve a Discover row as an RSS feed (Radarr/Sonarr "RSS List" compatible).

    Reads the row kept by ``store_feed_row`` and never builds one here. The
    ETag covers the stored row, the link's options and the host the links
    are written for, so an unchanged poll is a 304 without rendering.
    """
    try:
        user = User.objects.get(token=token)
    except ObjectDoesNotExist as error:
        msg = "Feed not found"
        raise Http404(msg) from error

    row_definition = next(
        (row for row in get_external_row_definitions(media_type) if row.key == row_key),
        None,
    )
    if row_definition is None:
        msg = "Feed not found"
        raise Http404(msg)

    flags = (
        _parse_bool_param(request, "skip_planning"),
        _parse_bool_param(request, "skip_collected"),
    )
    entry = _stored_feed_row(user.id, media_type, row_key, flags)
    if entry is None:
        response = HttpResponse(
            "This feed is being prepared. Try again in a minute.",
            status=503,
            content_type="text/plain",
        )
        response["Retry-After"] = str(FEED_ROW_RETRY_AFTER_SECONDS)
        return response

    etag = feed_validation.feed_etag(
        "discover_rss",
        user.id,
        token,
        media_type,
        row_key,
        flags,
        entry["built_at"],
        request.build_absolute_uri("/"),
    )
    response = get_conditional_response(request, etag=etag)
    if response is None:
        obj = {
            "row_definition": row_definition,
            "row": RowResult.from_dict(entry["row"]),
        }
        response = DiscoverRowFeed()(request, obj=obj)
    response["ETag"] = etag
    patch_cache_control(
        response,
        private=True,
        max_age=feed_validation.FEED_MAX_AGE_SECONDS,
    )
    return response


@require_GET
def discover_row_feed_preview(request):
    """Render an HTMX preview of the filtered Discover row for the RSS settings page."""
    media_type = request.GET.get("media_type", "")
    row_key = request.GET.get("row_key", "")

    row = None
    if any(row.key == row_key for row in get_external_row_definitions(media_type)):
        skip_planning = _parse_bool_param(request, "skip_planning")
        skip_collected = _parse_bool_param(request, "skip_collected")
        row = get_feed_row(
            request.user,
            media_type,
            row_key,
            skip_planning=skip_planning,
            skip_collected=skip_collected,
        )
        # The preview is what the copied link will serve: keep it, so the
        # first poll from Radarr or Sonarr is answered at once.
        if row is not None:
            store_feed_row(
                request.user,
                media_type,
                row_key,
                skip_planning=skip_planning,
                skip_collected=skip_collected,
                row=row,
            )

    return render(
        request,
        "app/components/discover_row_preview.html",
        {
            "row": row,
            "selected_media_type": media_type,
        },
    )
