import json
import logging
import math
import time
from collections import Counter, defaultdict
from copy import copy
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from django.apps import apps as django_apps
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import FieldError
from django.core.paginator import Paginator
from django.db.models import Count, F, Min, Prefetch, Q
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from app import cache_utils, helpers, history_cache
from app.bulk_actions import build_bulk_action_data
from app.columns import (
    resolve_column_config,
    resolve_columns,
    resolve_default_column_config,
    sanitize_column_prefs,
)
from app.library_query import FilterValues, LibraryQuery, LibraryQueryExecutor
from app.library_query.adapters import (
    from_media_list_filters,
    last_watched_sort_key,
)
from app.library_query.alphabet import (
    first_other_initial,
    initial_condition,
    normalize_initial,
    query_other_initial,
)
from app.media_list_entry_grouping import (
    active_entry_grouping_mode,
    entry_grouping_is_separate,
)
from app.media_list_filters import (
    apply_media_list_collection_filter,
    apply_media_list_progress_filter,
    apply_media_list_rating_filter,
    apply_media_list_status_filter,
    media_list_entries_for_items,
    parse_media_list_filters,
)
from app.models import (
    TV,
    BasicMedia,
    CollectionEntry,
    Item,
    ItemTag,
    MediaTypes,
    Sources,
    Status,
    Tag,
    prefill_episode_runtime_index,
)
from app.providers import tmdb
from app.release_years import prefill_display_release_years
from app.search_views import _mark_grouped_anime_route
from app.services import metadata_resolution
from app.stats_music import _country_name_from_code
from app.table_browser import build_table_browser_props, table_json_url
from app.templatetags import app_tags
from app.tv_sort import (
    _sort_tv_media_by_time_left,
    build_time_left_records,
    is_caught_up_record,
)
from lists import smart_rules
from users.models import (
    MediaSortChoices,
    MediaStatusChoices,
    relabel_author_sort_choice,
    relabel_end_date_sort_choice,
)

logger = logging.getLogger(__name__)

MEDIA_RATING_CHOICES = (
    ("all", "All"),
    ("rated", "Rated"),
    # "not_rated" is handled in logic but not shown in dropdown (toggle behavior)
)
MEDIA_LIST_NO_STATUS = "no_status"

# ISO language/country codes are at most 3 characters (e.g. "en", "eng");
# anything longer is treated as a display name rather than a code.
MAX_ISO_CODE_LENGTH = 3

# Number of times the debug column-prefs poll re-reads the DB after a save.
DEBUG_POLL_ATTEMPTS = 3
MEDIA_LIST_NO_STATUS_LABEL = "No Status"
RECENTLY_NOT_RATED_KEY = "recently_not_rated"
RECENTLY_NOT_RATED_LABEL = "Recently Played - Not Rated"
RECENTLY_NOT_RATED_DAYS = 7


GAME_GROUP_BY_CHOICES = (
    ("item", "Item"),
    ("series", "Collection/Series"),
    ("franchise", "Franchise"),
)


def _game_group_key_for_item(item, group_by):
    """Return the provider-qualified key for one game's selected grouping."""
    if group_by == "series":
        provider_id = str(getattr(item, "provider_collection_id", "") or "").strip()
        name = str(getattr(item, "provider_collection_name", "") or "").strip()
    else:
        provider_id = str(getattr(item, "provider_franchise_id", "") or "").strip()
        name = str(getattr(item, "provider_franchise_name", "") or "").strip()
    if provider_id:
        return f"id:{provider_id}"
    if name:
        return f"name:{name.casefold()}"
    return ""


def _game_group_name_for_item(item, group_by):
    """Return the provider name for one game's selected grouping."""
    if group_by == "series":
        return str(getattr(item, "provider_collection_name", "") or "").strip()
    return str(getattr(item, "provider_franchise_name", "") or "").strip()


def _release_year(value):
    """Return the display year for one release datetime."""
    if value is None:
        return ""
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return str(value.year)


def _build_game_groups(entries, group_by, *, sort_key, direction):
    """Return the filtered Games library rolled up into catalog groups."""
    groups = {}
    for entry in entries:
        item = entry.item
        key = _game_group_key_for_item(item, group_by)
        ungrouped = not key
        if ungrouped:
            key = "__ungrouped__"
        name = _game_group_name_for_item(item, group_by) or key
        group = groups.setdefault(
            key,
            {
                "key": key,
                "name": "Ungrouped" if ungrouped else name,
                "ungrouped": ungrouped,
                "entries": [],
                "played_count": 0,
                "completed_count": 0,
                "playtime_minutes": 0,
                "last_played": None,
                "first_release": None,
                "last_release": None,
                "first_added": None,
                "first_started": None,
                "last_ended": None,
                "genres": [],
                "platforms": [],
                "runtime_minutes": 0,
                "status_counts": Counter(),
                # TODO: use a franchise/series-level poster when one exists;
                # until then a tracked member game's poster is the fallback.
                "image": "",
                "scores": [],
            },
        )
        group["entries"].append(entry)
        if not group["image"] and getattr(item, "image", ""):
            group["image"] = item.image
        release = getattr(item, "release_datetime", None)
        if release:
            if group["first_release"] is None or release < group["first_release"]:
                group["first_release"] = release
            if group["last_release"] is None or release > group["last_release"]:
                group["last_release"] = release
        for genre in getattr(item, "genres", None) or []:
            if genre not in group["genres"]:
                group["genres"].append(genre)
        for platform in getattr(item, "platforms", None) or []:
            if platform not in group["platforms"]:
                group["platforms"].append(platform)
        group["runtime_minutes"] += getattr(item, "runtime_minutes", 0) or 0
        media = entry.media
        if media is None:
            continue
        created = getattr(media, "aggregated_created_at", None) or getattr(
            media, "created_at", None
        )
        if created and (group["first_added"] is None or created < group["first_added"]):
            group["first_added"] = created
        started = getattr(media, "aggregated_start_date", None) or getattr(
            media, "start_date", None
        )
        if started and (
            group["first_started"] is None or started < group["first_started"]
        ):
            group["first_started"] = started
        ended = getattr(media, "aggregated_end_date", None) or getattr(
            media, "end_date", None
        )
        if ended and (group["last_ended"] is None or ended > group["last_ended"]):
            group["last_ended"] = ended
        progress = getattr(media, "aggregated_progress", None)
        if progress is None:
            progress = getattr(media, "progress", 0) or 0
        status = getattr(media, "aggregated_status", None) or getattr(
            media, "status", None
        )
        played = progress > 0 or status in {
            Status.IN_PROGRESS.value,
            Status.COMPLETED.value,
        }
        if played:
            group["played_count"] += 1
            activity = (
                getattr(media, "aggregated_last_watched", None)
                or getattr(media, "end_date", None)
                or helpers.progress_activity(media)
            )
            if activity and (
                group["last_played"] is None or activity > group["last_played"]
            ):
                group["last_played"] = activity
        if status:
            group["status_counts"][status] += 1
        if status == Status.COMPLETED.value:
            group["completed_count"] += 1
        group["playtime_minutes"] += progress
        score = getattr(media, "aggregated_score", None)
        if score is None:
            score = getattr(media, "score", None)
        if score is not None:
            group["scores"].append(float(score))
    for group in groups.values():
        group["game_count"] = len(group["entries"])
        group["image"] = group["image"] or settings.IMG_NONE
        group["average_score"] = (
            sum(group["scores"]) / len(group["scores"]) if group["scores"] else None
        )
        first_year = _release_year(group["first_release"])
        last_year = _release_year(group["last_release"])
        group["release_range"] = (
            ""
            if not first_year
            else first_year
            if first_year == last_year
            else f"{first_year}-{last_year}"
        )
        status_labels = []
        for status, count in group["status_counts"].items():
            try:
                label = str(Status(status).label)
            except ValueError:
                label = status
            status_labels.append(f"{label} ({count})")
        group["status_label"] = ", ".join(status_labels)
        group["average_score"] = (
            round(group["average_score"], 1)
            if group["average_score"] is not None
            else None
        )

        representative_entry = next(
            (entry for entry in group["entries"] if entry.media is not None),
            group["entries"][0],
        )
        display_item = copy(representative_entry.item)
        display_item.title = group["name"]
        display_item.original_title = ""
        display_item.localized_title = ""
        display_item.release_datetime = None
        display_item.display_release_year = group["release_range"] or None
        display_item.genres = group["genres"]
        display_item.platforms = group["platforms"]
        display_item.runtime = (
            helpers.minutes_to_hhmm(group["runtime_minutes"])
            if group["runtime_minutes"]
            else ""
        )
        display_item.synopsis = ""
        group["display_item"] = display_item
        if representative_entry.media is not None:
            display_media = copy(representative_entry.media)
        else:
            display_media = SimpleNamespace(
                card_tile_url="",
                progress=0,
                aggregated_progress=None,
                max_progress=None,
                repeats=0,
                play_count=0,
            )
        display_media.item = display_item
        display_media.score = group["average_score"]
        display_media.aggregated_score = group["average_score"]
        display_media.status = group["status_label"]
        display_media.aggregated_status = group["status_label"]
        display_media.last_played_at = group["last_played"]
        display_media.aggregated_created_at = group["first_added"]
        display_media.aggregated_start_date = group["first_started"]
        display_media.aggregated_end_date = group["last_ended"]
        display_media.progress = group["playtime_minutes"]
        display_media.aggregated_progress = group["playtime_minutes"]
        display_media.max_progress = None
        group["display_media"] = display_media

    def _value(group):
        if sort_key in {"progress", "plays", "time_watched"}:
            return group["playtime_minutes"]
        if sort_key == "start_date":
            return group["first_started"]
        if sort_key == "date_added":
            return group["first_added"]
        if sort_key == "end_date":
            return group["last_ended"] or group["last_played"]
        if sort_key == "release_date":
            return group["last_release"]
        if sort_key in {"score", "critic_rating"}:
            return group["average_score"]
        return group["name"].casefold()

    grouped = [group for group in groups.values() if not group["ungrouped"]]
    ungrouped = [group for group in groups.values() if group["ungrouped"]]
    present = [group for group in grouped if _value(group) is not None]
    missing = [group for group in grouped if _value(group) is None]
    present.sort(
        key=lambda group: (_value(group), group["name"].casefold()),
        reverse=direction == "desc",
    )
    missing.sort(key=lambda group: group["name"].casefold())
    return present + missing + ungrouped


@dataclass
class MediaListEntry:
    """Template-facing list entry that may or may not have a tracker row."""

    item: object
    media: object | None = None

    @classmethod
    def from_media(cls, media):
        """Return the from media."""
        return cls(item=getattr(media, "item", None), media=media)

    @property
    def is_untracked(self) -> bool:
        """Return whether untracked."""
        return self.media is None

    @property
    def is_statusless(self) -> bool:
        """True when nothing tracks this item, including a rating-only media row."""
        if self.media is None:
            return True
        return (
            getattr(self.media, "aggregated_status", None) is None
            and getattr(self.media, "status", None) is None
        )

    @property
    def item_id(self):
        """Return the item id."""
        if self.media is not None:
            return getattr(self.media, "item_id", None)
        return getattr(self.item, "id", None)

    def __bool__(self):
        """Return the bool  ."""
        return self.media is not None

    def __getattr__(self, attr):
        """Return the getattr  ."""
        if attr.startswith("__") and attr.endswith("__"):
            raise AttributeError(attr)
        media = self.__dict__.get("media")
        if media is None:
            return None
        return getattr(media, attr, None)


def _tracked_media_entries(entries):
    """Return the tracker-backed objects from mixed media-list entries."""
    tracked_entries = []
    for entry in entries:
        tracked_media = getattr(entry, "media", entry)
        if tracked_media is not None:
            tracked_entries.append(tracked_media)
    return tracked_entries


def _serialize_time_left_order(records):
    """Compact, cache-friendly representation of a time_left sorted list.

    Stores primary keys plus the display values computed during the sort so
    any page can be hydrated without re-sorting the library.
    """
    return [
        {
            "media_pk": record.media_pk,
            "item_pk": record.item_pk,
            "time_left_display": record.time_left_display,
            "episodes_left_display": record.episodes_left_display,
        }
        for record in records
    ]


def _hydrate_time_left_page(user, order_slice):
    """Rebuild MediaListEntry objects for one page of a cached sort order."""
    media_pks = [o["media_pk"] for o in order_slice if o.get("media_pk")]
    item_pks = [
        o["item_pk"] for o in order_slice if not o.get("media_pk") and o.get("item_pk")
    ]
    media_queryset = TV.objects.filter(user=user, pk__in=media_pks).select_related(
        "item",
    )
    # Same season/episode prefetch profile as get_media_list, so per-page
    # progress annotation doesn't devolve into per-season queries.
    media_queryset = BasicMedia.objects._apply_prefetch_related(
        media_queryset,
        MediaTypes.TV.value,
        list_mode=True,
    )
    media_by_pk = {media.pk: media for media in media_queryset}
    items_by_pk = {item.pk: item for item in Item.objects.filter(pk__in=item_pks)}

    entries = []
    for order_entry in order_slice:
        media = media_by_pk.get(order_entry.get("media_pk"))
        if media is not None:
            entry = MediaListEntry.from_media(media)
        else:
            item = items_by_pk.get(order_entry.get("item_pk"))
            if item is None:
                # Row deleted since the order was cached; skip until the
                # invalidation signal refreshes the cache.
                continue
            entry = MediaListEntry(item=item, media=None)
        entry.time_left_display = order_entry.get("time_left_display")
        entry.episodes_left_display = order_entry.get("episodes_left_display", 0)
        entries.append(entry)
    return entries


def _serialize_media_list_order(entries):
    """Compact, cache-friendly representation of a fully filtered/sorted list.

    Stores only primary keys (plus the concrete model backing each row, since
    a grouped-anime list mixes Anime and TV rows under one route media_type);
    a cache hit hydrates one page from the DB instead of unpickling the whole
    per-user library on every request (see issue #865 — this was the
    dominant cost even when few SQL queries ran).
    """
    result = []
    for entry in entries:
        media = getattr(entry, "media", None)
        result.append(
            {
                "media_pk": getattr(media, "pk", None),
                "model": type(media).__name__.lower() if media is not None else None,
                "item_pk": entry.item_id,
            }
        )
    return result


def _prefetch_untracked_item_tags(entries):
    """Load tags for item-only rows in one query instead of one per card.

    Tracked rows inherit the ``item__item_tags`` prefetch from their tracker
    query. Untracked (collected but unstatused) rows have no tracker to inherit
    it from, and every card renders the Tags column.
    """
    item_pks = [entry.item.pk for entry in entries if entry.media is None]
    if not item_pks:
        return
    tagged_items = {
        item.pk: item
        for item in Item.objects.filter(pk__in=item_pks).prefetch_related(
            Prefetch("item_tags", queryset=ItemTag.objects.select_related("tag")),
        )
    }
    for entry in entries:
        if entry.media is None:
            entry.item = tagged_items.get(entry.item.pk, entry.item)


def _hydrate_media_list_page(user, media_type, order_slice):
    """Rebuild MediaListEntry objects for a slice of a cached compact order."""
    pks_by_model: dict[str, list] = {}
    item_pks = []
    for order_entry in order_slice:
        media_pk = order_entry.get("media_pk")
        if media_pk:
            model_name = order_entry.get("model") or media_type
            pks_by_model.setdefault(model_name, []).append(media_pk)
        elif order_entry.get("item_pk"):
            item_pks.append(order_entry["item_pk"])

    media_by_key = {}
    for model_name, media_pks in pks_by_model.items():
        model = django_apps.get_model(app_label="app", model_name=model_name)
        media_queryset = model.objects.filter(
            user=user, pk__in=media_pks
        ).select_related("item")
        # Same prefetch profile as get_media_list, so per-page progress/runtime
        # annotation doesn't devolve into per-item queries.
        media_queryset = BasicMedia.objects._apply_prefetch_related(
            media_queryset,
            model_name,
            list_mode=True,
        )
        media_list = list(media_queryset)
        # Restore duplicate-tracking aggregates (aggregated_progress/status/
        # score, dates, repeats) that get_media_list() would have attached —
        # a raw pk lookup bypasses that window/aggregation pass entirely.
        BasicMedia.objects._aggregate_duplicate_data(media_list, user, model_name)
        for media in media_list:
            media_by_key[(model_name, media.pk)] = media

    items_by_pk = (
        {item.pk: item for item in Item.objects.filter(pk__in=item_pks)}
        if item_pks
        else {}
    )

    entries = []
    for order_entry in order_slice:
        media_pk = order_entry.get("media_pk")
        if media_pk:
            model_name = order_entry.get("model") or media_type
            media = media_by_key.get((model_name, media_pk))
            if media is not None:
                entries.append(MediaListEntry.from_media(media))
            # Row deleted since the order was cached; skip until the
            # invalidation signal refreshes the cache.
            continue
        item = items_by_pk.get(order_entry.get("item_pk"))
        if item is None:
            continue
        entries.append(MediaListEntry(item=item, media=None))

    _prefetch_untracked_item_tags(entries)

    # Grouped anime (TV items in the anime bucket) are surfaced in the anime
    # list via the warm-cache hydration path too. _mark_grouped_anime_route is
    # applied at cache-build time, but hydration rebuilds media objects from
    # their pks and would otherwise lose the route_media_type annotation, so
    # grouped-anime rows must be re-marked here to keep their links on the
    # anime route (e.g. /details/tmdb/anime/...) instead of the TV route.
    if media_type == MediaTypes.ANIME.value:
        _mark_grouped_anime_route(
            [
                entry.media
                for entry in entries
                if entry.media is not None
                and getattr(entry.media, "item", None) is not None
                and entry.media.item.library_media_type == MediaTypes.ANIME.value
            ],
        )

    return entries


def _collect_reading_activity_day_keys(entries):
    """Return history/statistics day keys touched by reading entries."""
    day_keys = set()
    for entry in entries or []:
        start_dt = getattr(entry, "start_date", None)
        end_dt = getattr(entry, "end_date", None)
        if start_dt and end_dt:
            range_keys = history_cache.history_day_keys_for_range(start_dt, end_dt)
            day_keys.update(range_keys or [])
        activity_dt = end_dt or start_dt or getattr(entry, "created_at", None)
        activity_key = history_cache.history_day_key(activity_dt)
        if activity_key:
            day_keys.add(activity_key)
    return sorted(day_keys)


FORMAT_LABELS = {
    "hardcover": "Hardcover",
    "paperback": "Paperback",
    "ebook": "eBook",
    "audiobook": "Audiobook",
}


def _normalize_filter_value(value):
    return str(value or "").strip().lower()


def _extract_item_languages(item):
    """Extract languages from database fields only."""
    if not item:
        return []
    languages = getattr(item, "languages", None)
    if not languages:
        return []
    if isinstance(languages, list):
        return [str(lang).strip() for lang in languages if str(lang).strip()]
    return [str(languages).strip()] if str(languages).strip() else []


def _extract_item_country(item):
    """Extract country from database fields only."""
    if not item:
        return ""
    country = getattr(item, "country", None)
    return str(country).strip() if country else ""


def _extract_item_platforms(item):
    """Extract platforms from database fields only."""
    if not item:
        return []
    platforms = getattr(item, "platforms", None)
    if not platforms:
        return []
    if isinstance(platforms, list):
        return [str(p).strip() for p in platforms if str(p).strip()]
    return [str(platforms).strip()] if str(platforms).strip() else []


PROVIDER_MEDIA_TYPES = (
    MediaTypes.TV.value,
    MediaTypes.MOVIE.value,
    MediaTypes.ANIME.value,
)


def _extract_item_providers(item, region, pinned_providers=None):
    """Extract watch-provider names for an item, or None if region is unset.

    When ``pinned_providers`` is given, also matches those provider names
    against the item's availability in *any* region, so a pinned provider
    from outside the user's home region still filters correctly.
    """
    if not item:
        return None
    names = tmdb.item_watch_provider_names(item, region)
    if pinned_providers:
        pinned_matches = tmdb.pinned_provider_matches(item, pinned_providers)
        pinned_names = {
            provider["provider_name"]
            for provider in pinned_matches
            if provider.get("provider_name")
        }
        if pinned_names:
            return sorted((set(names) if names else set()) | pinned_names)
    return names


def _extract_item_authors(item):
    """Extract authors from database fields only."""
    if not item:
        return []
    authors = getattr(item, "authors", None)
    if not authors:
        return []
    if not isinstance(authors, list):
        authors = [authors]
    normalized = []
    for raw_author in authors:
        if isinstance(raw_author, dict):
            author_name = (
                raw_author.get("name")
                or raw_author.get("person")
                or raw_author.get("author")
            )
        else:
            author_name = raw_author
        author_text = str(author_name).strip() if author_name else ""
        if author_text:
            normalized.append(author_text)
    return normalized


def _extract_item_formats(item, collection_formats_by_item_id=None):
    """Extract normalized format values from Item and collection metadata."""
    formats = set()
    if item and hasattr(item, "format") and item.format:
        normalized_item_format = _normalize_filter_value(item.format)
        if normalized_item_format:
            formats.add(normalized_item_format)
    if item and collection_formats_by_item_id:
        formats.update(collection_formats_by_item_id.get(item.id, set()))
    return formats


def _extract_item_platforms_with_collection(item, collection_platforms_by_item_id=None):
    """Extract platform values, preferring explicit collection platform entries."""
    if not item:
        return []
    if collection_platforms_by_item_id:
        explicit_platforms = collection_platforms_by_item_id.get(item.id, set())
        if explicit_platforms:
            return sorted(explicit_platforms, key=lambda value: value.lower())
    return _extract_item_platforms(item)


def _resolve_display_platform(
    item, collection_platforms_by_item_id, active_platform_filter
):
    """Resolve a single display platform: collection data > active filter > sole IGDB platform."""
    if not item:
        return ""
    if collection_platforms_by_item_id:
        collected = collection_platforms_by_item_id.get(item.id, set())
        if collected:
            return sorted(collected, key=lambda value: value.lower())[0]
    item_platforms = _extract_item_platforms(item)
    if active_platform_filter:
        normalized_filter = _normalize_filter_value(active_platform_filter)
        for platform in item_platforms:
            if _normalize_filter_value(platform) == normalized_filter:
                return platform
    if len(item_platforms) == 1:
        return item_platforms[0]
    return ""


def build_filter_data_from_items(
    media_items,
    *,
    collection_formats_by_item_id=None,
    collection_platforms_by_item_id=None,
    region=None,
    pinned_providers=None,
    include_providers=True,
):
    """Build filter menu option lists from a sequence of media/Item objects.

    Accepts either ORM media objects (with a ``.item`` FK attribute) or raw
    Item instances.  Pass ``collection_formats_by_item_id`` /
    ``collection_platforms_by_item_id`` from the media-list view to include
    collection-enriched format/platform data; omit them (or pass None) for
    contexts that don't have collection data (e.g. person detail pages).

    ``pinned_providers`` is the user's pinned/favorited watch-provider names
    (see ``User.pinned_watch_providers``). They're always included as
    selectable options, even outside the user's home region, and the full
    TMDB provider catalog is returned under ``all_providers`` /
    ``pinned_providers`` for the filter dropdown's "More" panel.

    ``include_providers`` should be false for media types that never show a
    provider filter (games, books, music, ...) so the global TMDB catalog
    fetch is skipped entirely rather than run and then discarded.
    """
    pinned_providers = list(pinned_providers or [])
    genres_set = set()
    implied_genres_set = set()
    years_set = set()
    sources_set = set()
    languages_set = set()
    countries_set = set()
    platforms_set = set()
    formats_set = set()
    authors_set = set()
    providers_set = set()
    media_statuses_set = set()
    has_region = bool(region and region != "UNSET")
    has_unknown_year = False
    for media in media_items:
        item = getattr(media, "item", media)
        if not item:
            continue
        for genre in getattr(item, "genres", None) or []:
            genre_value = str(genre).strip()
            if genre_value:
                genres_set.add(genre_value)
        for genre in getattr(item, "implied_genres", None) or []:
            genre_value = str(genre).strip()
            if genre_value:
                implied_genres_set.add(genre_value)
        release_dt = getattr(item, "release_datetime", None)
        if release_dt and getattr(release_dt, "year", None):
            years_set.add(release_dt.year)
        else:
            has_unknown_year = True
        if getattr(item, "source", None):
            sources_set.add(item.source)
        media_status_value = str(getattr(item, "status", "") or "").strip()
        if media_status_value:
            media_statuses_set.add(media_status_value)
        db_languages = _extract_item_languages(item)
        if db_languages:
            languages_set.update(db_languages)
        country_value = _extract_item_country(item)
        if country_value:
            countries_set.add(country_value)
        platforms = _extract_item_platforms_with_collection(
            item, collection_platforms_by_item_id
        )
        if platforms:
            platforms_set.update(platforms)
        authors = _extract_item_authors(item)
        if authors:
            authors_set.update(authors)
        item_formats = _extract_item_formats(item, collection_formats_by_item_id)
        if item_formats:
            formats_set.update(item_formats)
        if include_providers and has_region:
            providers_set.update(_extract_item_providers(item, region) or [])
    if include_providers:
        providers_set.update(pinned_providers)

    genres = sorted(genres_set, key=lambda value: value.lower())
    implied_genres = sorted(implied_genres_set, key=lambda value: value.lower())
    years = [
        {"value": str(year), "label": str(year)}
        for year in sorted(years_set, reverse=True)
    ]
    if has_unknown_year:
        years.append({"value": "unknown", "label": "Unknown"})

    source_labels = dict(Sources.choices)
    sources = [
        {"value": source, "label": source_labels.get(source, source)}
        for source in sorted(sources_set)
    ]
    languages = [
        {
            "value": value,
            "label": value.upper() if len(value) <= MAX_ISO_CODE_LENGTH else value,
        }
        for value in sorted(languages_set)
    ]
    countries = [
        {
            "value": value,
            "label": value.upper() if len(value) <= MAX_ISO_CODE_LENGTH else value,
        }
        for value in sorted(countries_set)
    ]
    platforms = [
        {"value": value, "label": value}
        for value in sorted(platforms_set, key=lambda val: val.lower())
    ]
    formats = [
        {
            "value": value,
            "label": FORMAT_LABELS.get(_normalize_filter_value(value), value.title()),
        }
        for value in sorted(formats_set, key=lambda val: val.lower())
    ]
    authors = [
        {"value": value, "label": value}
        for value in sorted(authors_set, key=lambda val: val.lower())
    ]
    providers = [
        {"value": value, "label": value}
        for value in sorted(providers_set, key=lambda val: val.lower())
    ]
    all_providers_by_name = {}
    if include_providers:
        for provider in tmdb.global_watch_provider_catalog():
            provider_name = provider["provider_name"]
            all_providers_by_name[provider_name] = {
                "value": provider_name,
                "label": provider_name,
                "image": provider["image"],
            }
    all_providers = list(all_providers_by_name.values())
    media_statuses = [
        {"value": value, "label": value}
        for value in sorted(media_statuses_set, key=lambda val: val.lower())
    ]
    return {
        "genres": genres,
        "implied_genres": implied_genres,
        "years": years,
        "sources": sources,
        "languages": languages,
        "countries": countries,
        "platforms": platforms,
        "origins": [],
        "formats": formats,
        "authors": authors,
        "providers": providers,
        "all_providers": all_providers,
        "pinned_providers": sorted(pinned_providers, key=lambda val: val.lower()),
        "media_statuses": media_statuses,
        "show_languages": False,
        "show_countries": False,
        "show_platforms": False,
        "show_origins": False,
        "show_formats": False,
        "show_authors": False,
        "show_providers": False,
        "relative_date_units": [
            {"value": value, "label": label}
            for value, label in smart_rules.RELATIVE_DATE_UNIT_CHOICES
        ],
    }


def _restore_region_providers(row, region):
    """Put a region-narrowed provider slice back under ``watch_providers``.

    The query asks SQL for one region out of a blob that covers every region
    TMDB knows, because that is all the filter menu reads. Downstream still
    expects the field's real shape -- a mapping keyed by region -- so give it
    that shape around the one region it will look up.
    """
    if "watch_providers_region" not in row:
        return row
    row = dict(row)
    slice_for_region = row.pop("watch_providers_region", None)
    row["watch_providers"] = {region: slice_for_region} if slice_for_region else {}
    return row


def build_filter_data_from_item_values(
    item_values,
    *,
    collection_formats_by_item_id=None,
    collection_platforms_by_item_id=None,
    region=None,
    pinned_providers=None,
    include_providers=True,
):
    """Build filter data from ``Item.values()`` rows without ORM hydration."""
    projected_items = (
        SimpleNamespace(**_restore_region_providers(row, region)) for row in item_values
    )
    return build_filter_data_from_items(
        projected_items,
        collection_formats_by_item_id=collection_formats_by_item_id,
        collection_platforms_by_item_id=collection_platforms_by_item_id,
        region=region,
        pinned_providers=pinned_providers,
        include_providers=include_providers,
    )


def _log_tag_filter_result(
    request,
    media_type,
    tag_values,
    tag_mode,
    status_filter,
    shown,
):
    """Log what a tag-filtered list returned so an empty one can be explained."""
    status_source = "url" if "status" in request.GET else "saved"
    args = (
        request.user.id,
        media_type,
        list(tag_values),
        tag_mode,
        list(status_filter) or ["all"],
        status_source,
        shown,
    )
    if shown:
        logger.info(
            "Media list tag filter: user=%s type=%s tags=%s mode=%s "
            "status=%s (from %s) shown=%s",
            *args,
        )
        return

    # Empty result: compare against what the tag actually points at.
    # Same case-insensitive tag match as the filter itself.
    tag_match = Q()
    for value in tag_values:
        tag_match |= Q(tag__name__iexact=value)
    tagged_item_ids = set(
        ItemTag.objects.filter(
            tag_match,
            tag__user=request.user,
            item__media_type=media_type,
        ).values_list("item_id", flat=True),
    )
    # One status per item: separate-entry mode can give an item several rows.
    status_by_item = {}
    try:
        model = django_apps.get_model(app_label="app", model_name=media_type)
        status_by_item = dict(
            model.objects.filter(user=request.user, item_id__in=tagged_item_ids)
            .order_by("id")
            .values_list("item_id", "status"),
        )
    except (LookupError, AttributeError, FieldError):
        pass
    tracked_statuses = dict(Counter(status_by_item.values()))
    logger.warning(
        "Media list tag filter: user=%s type=%s tags=%s mode=%s "
        "status=%s (from %s) shown=%s | "
        "tagged_items=%s tracked_by_status=%s untracked=%s",
        *args,
        len(tagged_item_ids),
        tracked_statuses,
        len(tagged_item_ids) - len(status_by_item),
    )


def _build_media_list_tag_data(user, media_items):
    """Return user tags ordered and counted for the current media-list items."""
    item_ids = {
        getattr(getattr(media, "item", media), "id", None) for media in media_items
    }
    item_ids.discard(None)

    tag_counts = {}
    if item_ids:
        tag_counts = dict(
            ItemTag.objects.filter(
                tag__user=user,
                item_id__in=item_ids,
            )
            .values("tag_id")
            .annotate(item_count=Count("item_id", distinct=True))
            .values_list("tag_id", "item_count")
        )

    tags = list(Tag.objects.filter(user=user).values("id", "name"))
    tags.sort(
        key=lambda tag: (
            -tag_counts.get(tag["id"], 0),
            tag["name"].casefold(),
        ),
    )
    return {
        "tags": [tag["name"] for tag in tags],
        "tag_counts": {tag["name"]: tag_counts.get(tag["id"], 0) for tag in tags},
    }


AUTHOR_MEDIA_TYPES = (
    MediaTypes.BOOK.value,
    MediaTypes.MANGA.value,
    MediaTypes.COMIC.value,
    MediaTypes.COMIC_ISSUE.value,
)
# A video's channel is stored as its author, so videos filter, sort and show by
# it too. Formats stay with the reading types.
AUTHOR_FILTER_MEDIA_TYPES = (*AUTHOR_MEDIA_TYPES, MediaTypes.VIDEO.value)
CRITIC_RATING_MEDIA_TYPES = {
    MediaTypes.TV.value,
    MediaTypes.SEASON.value,
    MediaTypes.MOVIE.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.GAME.value,
    MediaTypes.BOARDGAME.value,
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
}
POPULARITY_MEDIA_TYPES = {
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
}
PROGRESS_MEDIA_TYPES = {
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
}
PLAYS_MEDIA_TYPES = {
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
}
RUNTIME_MEDIA_TYPES = {
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
}
NEXT_EPISODE_AIR_DATE_MEDIA_TYPES = {
    MediaTypes.TV.value,
    MediaTypes.SEASON.value,
    MediaTypes.ANIME.value,
}


def _author_sort_value(media):
    item = getattr(media, "item", None)
    authors = _extract_item_authors(item)
    return authors[0].strip() if authors else ""


def _sort_media_items_by_author(media_items, sort_direction):
    with_author = []
    without_author = []

    for media in media_items:
        if _author_sort_value(media):
            with_author.append(media)
        else:
            without_author.append(media)

    with_author.sort(
        key=lambda media: (
            _author_sort_value(media).lower(),
            getattr(getattr(media, "item", None), "title", "").lower(),
        ),
        reverse=sort_direction == "desc",
    )
    without_author.sort(
        key=lambda media: getattr(getattr(media, "item", None), "title", "").lower(),
    )
    return with_author + without_author


def _game_time_to_beat_sort_value(media):
    item = getattr(media, "item", None)
    if not item:
        return None
    return item.game_time_to_beat_minutes


def _sort_media_items_by_platform(
    media_items,
    sort_direction,
    collection_platforms_by_item_id,
    platform_filter,
):
    with_platform = []
    without_platform = []

    for media in media_items:
        platform = _resolve_display_platform(
            getattr(media, "item", None),
            collection_platforms_by_item_id,
            platform_filter,
        )
        if platform:
            with_platform.append((media, platform))
        else:
            without_platform.append(media)

    with_platform.sort(
        key=lambda entry: (
            entry[1].lower(),
            getattr(getattr(entry[0], "item", None), "title", "").lower(),
        ),
        reverse=sort_direction == "desc",
    )
    without_platform.sort(
        key=lambda media: getattr(getattr(media, "item", None), "title", "").lower(),
    )
    return [media for media, _platform in with_platform] + without_platform


def _runtime_sort_value(media):
    return getattr(media, "total_runtime_minutes", None)


def _plays_sort_value(media):
    aggregated_progress = getattr(media, "aggregated_progress", None)
    if aggregated_progress is not None:
        return aggregated_progress
    return getattr(media, "progress", 0) or 0


def _time_watched_sort_value(media):
    return getattr(media, "time_watched_minutes", None)


def _sort_media_items_by_numeric_value(media_items, sort_direction, value_key):
    """Sort measured values, keeping missing/zero values last and title ties ascending."""
    with_values = []
    without_values = []
    for media in media_items:
        value = value_key(media)
        if value:
            with_values.append((media, value))
        else:
            without_values.append(media)
    with_values.sort(
        key=lambda entry: (
            -entry[1] if sort_direction == "desc" else entry[1],
            getattr(getattr(entry[0], "item", None), "title", "").lower(),
        ),
    )
    without_values.sort(
        key=lambda media: getattr(getattr(media, "item", None), "title", "").lower(),
    )
    return [media for media, _value in with_values] + without_values


def _resolve_media_list_preferences(request, route_media_type, comic_subview):
    """Persist valid layout/sort preferences and prepare the matching sort choices."""
    previous_sort = getattr(request.user, f"{route_media_type}_sort")
    sorted_media_sort_choices = sorted(
        MediaSortChoices.choices,
        key=lambda choice: str(choice[1]).lower(),
    )
    layout = request.user.update_preference(
        f"{route_media_type}_layout",
        request.GET.get("layout"),
    )
    sort_filter = request.user.update_preference(
        f"{route_media_type}_sort",
        request.GET.get("sort"),
    )
    direction_param = request.GET.get("direction")
    direction_field = f"{route_media_type}_direction"

    # Enforce media-type-specific sort options.
    effective_media_type = (
        MediaTypes.COMIC_ISSUE.value
        if route_media_type == MediaTypes.COMIC.value and comic_subview == "issues"
        else route_media_type
    )

    if (
        (sort_filter == "time_left" and effective_media_type != MediaTypes.TV.value)
        or (
            sort_filter == "runtime" and effective_media_type not in RUNTIME_MEDIA_TYPES
        )
        or (
            sort_filter == "time_to_beat"
            and effective_media_type != MediaTypes.GAME.value
        )
        or (sort_filter == "platform" and effective_media_type != MediaTypes.GAME.value)
        or (sort_filter == "plays" and effective_media_type not in PLAYS_MEDIA_TYPES)
        or (
            sort_filter == "time_watched"
            and effective_media_type not in RUNTIME_MEDIA_TYPES
        )
    ):
        sort_filter = "title"  # Default fallback
        # Update the user's preference to the fallback
        request.user.update_preference(f"{route_media_type}_sort", "title")
        # Reset direction to the default for the fallback sort
        direction_param = None
    elif (
        sort_filter == "next_episode_air_date"
        and effective_media_type not in NEXT_EPISODE_AIR_DATE_MEDIA_TYPES
    ):
        sort_filter = "title"  # Default fallback
        request.user.update_preference(f"{route_media_type}_sort", "title")
        direction_param = None
    elif (
        sort_filter == "author"
        and effective_media_type not in AUTHOR_FILTER_MEDIA_TYPES
    ):
        sort_filter = "title"  # Default fallback
        # Update the user's preference to the fallback
        request.user.update_preference(f"{route_media_type}_sort", "title")
        # Reset direction to the default for the fallback sort
        direction_param = None
    elif (
        sort_filter == "critic_rating"
        and effective_media_type not in CRITIC_RATING_MEDIA_TYPES
    ) or (
        sort_filter == "popularity"
        and effective_media_type not in POPULARITY_MEDIA_TYPES
    ):
        sort_filter = "title"
        request.user.update_preference(f"{route_media_type}_sort", "title")
        direction_param = None

    # Resolve and persist sort direction with the same preference flow as sort
    direction_pref = getattr(request.user, direction_field, None)
    if direction_param is not None:
        direction = BasicMedia.objects.resolve_direction(sort_filter, direction_param)
        request.user.update_preference(direction_field, direction)
    else:
        if sort_filter != previous_sort or direction_pref is None:
            direction = BasicMedia.objects.resolve_direction(sort_filter, None)
        else:
            direction = BasicMedia.objects.resolve_direction(
                sort_filter, direction_pref
            )
        request.user.update_preference(direction_field, direction)
    media_type = effective_media_type

    # Pre-filter sort choices to only include those valid for the current media type.
    # critic_rating and author remain template-gated via supports_critic_rating_sort /
    # filter_data.show_authors; all other per-type exclusions are handled here so the
    # sort dropdown template only needs a simple {% for %} loop.
    _sort_type_guards = {
        "progress": lambda mt: mt != MediaTypes.MOVIE.value,
        "time_left": lambda mt: mt == MediaTypes.TV.value,
        "runtime": lambda mt: mt in RUNTIME_MEDIA_TYPES,
        "popularity": lambda mt: mt in POPULARITY_MEDIA_TYPES,
        "time_to_beat": lambda mt: mt == MediaTypes.GAME.value,
        "platform": lambda mt: mt == MediaTypes.GAME.value,
        "plays": lambda mt: mt in PLAYS_MEDIA_TYPES,
        "time_watched": lambda mt: mt in RUNTIME_MEDIA_TYPES,
        "next_episode_air_date": lambda mt: mt in NEXT_EPISODE_AIR_DATE_MEDIA_TYPES,
    }
    sorted_media_sort_choices = [
        (value, label)
        for value, label in sorted_media_sort_choices
        if value not in _sort_type_guards or _sort_type_guards[value](media_type)
    ]
    sorted_media_sort_choices = relabel_end_date_sort_choice(
        media_type,
        sorted_media_sort_choices,
    )
    sorted_media_sort_choices = relabel_author_sort_choice(
        media_type,
        sorted_media_sort_choices,
    )

    return layout, sort_filter, direction, media_type, sorted_media_sort_choices


def _release_date_from_value(value):
    if value is None:
        return None
    if isinstance(value, date) and not hasattr(value, "hour"):
        return value
    if hasattr(value, "date"):
        try:
            if hasattr(value, "utcoffset") and timezone.is_aware(value):
                return timezone.localtime(value).date()
        except Exception:  # noqa: S110  # deliberate best-effort; failure is non-fatal here
            pass
        try:
            return value.date()
        except Exception:
            return None
    return None


def _matches_release_filter_value(release_value, filter_value, today):
    if filter_value == "all":
        return True
    release_date = _release_date_from_value(release_value)
    if not release_date:
        return filter_value == "not_released"
    if filter_value == "released":
        return release_date <= today
    if filter_value == "not_released":
        return release_date > today
    return True


def _build_artist_list_context(
    user,
    *,
    tracked_status_filter,
    search_query,
    initial="",
    rating_filter,
    release_filter,
    genre_filter,
    origin_filter,
    sort_filter,
    direction,
    layout,
    page,
    is_pagination_req,
):
    """Build the artist page and facets without mutating the route's context.

    Status, search, rating and release constrain the facet universe; genre and
    origin then narrow the page. Image refresh/backfill is limited to that page.
    The route owns preferences, shared context, and full-page/HTMX rendering.
    """
    from app.models import Artist, ArtistTracker

    artist_trackers = (
        ArtistTracker.objects.filter(user=user)
        .exclude(artist__name__isnull=True)
        .exclude(artist__name__exact="")
        .select_related("artist")
    )

    # Apply status filter to artists
    if tracked_status_filter:
        artist_trackers = artist_trackers.filter(status__in=tracked_status_filter)

    # Apply search filter to artists
    if search_query:
        artist_trackers = artist_trackers.filter(artist__name__icontains=search_query)
    artist_trackers = artist_trackers.filter(
        initial_condition(initial, name_field="artist__name")
    )

    # Apply rating filter to artists
    if rating_filter == "rated":
        artist_trackers = artist_trackers.filter(score__isnull=False)
    elif rating_filter == "not_rated":
        artist_trackers = artist_trackers.filter(score__isnull=True)

    should_annotate_first_release_date = (
        release_filter != "all" or sort_filter == "release_date" or layout == "table"
    )
    if should_annotate_first_release_date:
        artist_trackers = artist_trackers.annotate(
            first_release_date=Min("artist__albums__release_date")
        )

    # Apply sorting (limited to what makes sense for artists)
    if sort_filter == "title":
        order = "artist__name" if direction == "asc" else "-artist__name"
        artist_trackers = artist_trackers.order_by(order)
    elif sort_filter == "score":
        order = "score" if direction == "asc" else "-score"
        artist_trackers = artist_trackers.order_by(order, "artist__name")
    elif sort_filter == "release_date":
        order = (
            F("first_release_date").asc(nulls_last=True)
            if direction == "asc"
            else F("first_release_date").desc(nulls_last=True)
        )
        artist_trackers = artist_trackers.order_by(order, "artist__name")
    elif sort_filter == "date_added":
        order = "created_at" if direction == "asc" else "-created_at"
        artist_trackers = artist_trackers.order_by(order, "artist__name")
    elif sort_filter == "start_date":
        order = "start_date" if direction == "asc" else "-start_date"
        artist_trackers = artist_trackers.order_by(order)
    else:
        # Default: most recently updated
        artist_trackers = artist_trackers.order_by("-updated_at")

    artist_trackers_list = list(artist_trackers)

    if release_filter != "all":
        today = timezone.localdate()
        artist_trackers_list = [
            tracker
            for tracker in artist_trackers_list
            if _matches_release_filter_value(
                getattr(tracker, "first_release_date", None),
                release_filter,
                today,
            )
        ]

    def _build_music_filter_data(trackers):
        genres_set = set()
        origins_set = set()
        for tracker in trackers:
            artist = tracker.artist
            for genre in artist.genres or []:
                genre_value = str(genre).strip()
                if genre_value:
                    genres_set.add(genre_value)
            origin_value = (artist.country or "").strip()
            if origin_value:
                origins_set.add(origin_value)

        genres = sorted(genres_set, key=lambda value: value.lower())
        origins = []
        for value in sorted(origins_set):
            label = value.upper() if len(value) <= MAX_ISO_CODE_LENGTH else value
            try:
                if len(value) <= MAX_ISO_CODE_LENGTH:
                    country_name = _country_name_from_code(value.upper())
                    if country_name:
                        label = country_name
            except Exception:  # pragma: no cover - defensive  # noqa: S110  # deliberate best-effort; failure is non-fatal here
                pass
            origins.append({"value": value, "label": label})
        return {
            "genres": genres,
            "years": [],
            "sources": [],
            "languages": [],
            "countries": [],
            "platforms": [],
            "origins": origins,
            "show_languages": False,
            "show_countries": False,
            "show_platforms": False,
            "show_origins": True,
        }

    filter_data = _build_music_filter_data(artist_trackers_list)

    if genre_filter:
        target_genre = _normalize_filter_value(genre_filter)
        artist_trackers_list = [
            tracker
            for tracker in artist_trackers_list
            if any(
                _normalize_filter_value(genre) == target_genre
                for genre in (tracker.artist.genres or [])
            )
        ]

    if origin_filter:
        target_country = _normalize_filter_value(origin_filter)
        artist_trackers_list = [
            tracker
            for tracker in artist_trackers_list
            if _normalize_filter_value(tracker.artist.country) == target_country
        ]

    # Paginate artist trackers first
    artist_paginator = Paginator(artist_trackers_list, 32)
    artist_page = artist_paginator.get_page(page)

    # Backfill missing artist images from album covers (no API calls - uses existing data)
    # Similar to _fix_missing_season_images for TV seasons
    # First, bulk fetch latest image data from DB for all artists on this page
    # (images might have been set by background tasks, detail page visits, etc.)
    # This is more efficient and reliable than individual refresh_from_db calls
    artist_ids = [tracker.artist.id for tracker in artist_page.object_list]
    artist_images_map = dict(
        Artist.objects.filter(id__in=artist_ids).values_list("id", "image"),
    )

    refreshed_with_images = 0
    images_in_db_count = 0
    for tracker in artist_page.object_list:
        artist_id = tracker.artist.id
        old_image = tracker.artist.image
        # Always update the in-memory object with the latest image from DB
        # This ensures we have the most up-to-date data, even if it's None
        if artist_id in artist_images_map:
            # Get the actual value (could be None if DB has None)
            actual_image = artist_images_map[artist_id]
            tracker.artist.image = actual_image
            # Count images that exist in DB (for logging)
            if actual_image and actual_image not in (settings.IMG_NONE, ""):
                images_in_db_count += 1
            # Count if refresh found an image that wasn't there before
            if (
                actual_image
                and actual_image not in (settings.IMG_NONE, "")
                and (not old_image or old_image in (settings.IMG_NONE, ""))
            ):
                refreshed_with_images += 1

    # Only check artists on the current page to avoid full queryset evaluation
    # Use object_list to avoid consuming the page iterator (important for HTMX pagination)
    seen_artist_ids = set()
    artists_checked = 0
    artists_with_images = 0
    artists_missing_images = 0

    # Partition artists into those with / without images in a single pass.
    artists_missing_image_objects = []
    for tracker in artist_page.object_list:
        artist = tracker.artist
        if artist.id not in seen_artist_ids:
            seen_artist_ids.add(artist.id)
            artists_checked += 1
            has_image = artist.image and artist.image not in (
                settings.IMG_NONE,
                "",
            )
            if has_image:
                artists_with_images += 1
            else:
                artists_missing_images += 1
                artists_missing_image_objects.append(artist)

    # Queue a background backfill for artists missing images instead of writing
    # synchronously during this GET request (avoids racing concurrent importers'
    # writes and 503s from SQLite lock contention). The Celery task performs the
    # same "hero image from earliest album cover" lookup and single-row save.
    queued_for_backfill = 0
    if artists_missing_image_objects:
        from app.tasks import prefetch_album_covers_batch

        for artist in artists_missing_image_objects:
            cache_key = f"music:cover-prefetch:{artist.id}"
            if cache.add(cache_key, True, 60 * 10):
                prefetch_album_covers_batch.delay([artist.id], limit_per_artist=5)
                queued_for_backfill += 1

    logger.debug(
        "Artist image backfill check (page %d, pagination=%s): checked %d artists, %d had images in DB, %d had images after refresh, %d missing, %d queued for backfill",
        page,
        is_pagination_req,
        artists_checked,
        images_in_db_count,
        artists_with_images,
        artists_missing_images,
        queued_for_backfill,
    )

    if refreshed_with_images > 0:
        logger.info(
            "Refreshed %d artists from DB that now have images (page %d, pagination=%s)",
            refreshed_with_images,
            page,
            is_pagination_req,
        )

    return {
        "media_list": artist_page,
        "is_artist_list": True,
        "filter_data": filter_data,
        "alphabet_other_initial": first_other_initial(
            ArtistTracker.objects.filter(user=user), name_field="artist__name"
        ),
    }


def media_list(request, media_type):
    """Return the media list page."""
    route_media_type = media_type
    comic_subview = None
    if route_media_type == MediaTypes.COMIC.value:
        comic_subview = request.GET.get("subview", "comics")
        if comic_subview not in {"comics", "issues"}:
            comic_subview = "comics"

    music_subview = None
    if route_media_type == MediaTypes.MUSIC.value:
        music_subview = request.GET.get("subview", "artists")
        if music_subview not in {"artists", "albums", "tracks"}:
            music_subview = "artists"

    layout, sort_filter, direction, media_type, sorted_media_sort_choices = (
        _resolve_media_list_preferences(request, route_media_type, comic_subview)
    )

    supports_untracked_status_filter = media_type not in {
        MediaTypes.MUSIC.value,
        MediaTypes.PODCAST.value,
    }
    raw_status_filter = request.GET.getlist("status")
    valid_statuses = {choice[0] for choice in MediaStatusChoices.choices}
    allowed_status_values = (valid_statuses - {MediaStatusChoices.ALL}) | (
        {MEDIA_LIST_NO_STATUS} if supports_untracked_status_filter else set()
    )
    persisted_status_pref = getattr(
        request.user,
        f"{route_media_type}_status",
        MediaStatusChoices.ALL,
    )
    persisted_status_filter = tuple(
        value
        for value in str(persisted_status_pref or "").split(",")
        if value and value in allowed_status_values
    )

    if "status" in request.GET:
        status_filter = tuple(
            dict.fromkeys(
                value for value in raw_status_filter if value in allowed_status_values
            ),
        )
        request.user.update_preference(
            f"{route_media_type}_status",
            ",".join(status_filter),
        )
    else:
        status_filter = persisted_status_filter

    status_choices = list(MediaStatusChoices.choices)
    if supports_untracked_status_filter:
        status_choices.insert(1, (MEDIA_LIST_NO_STATUS, MEDIA_LIST_NO_STATUS_LABEL))

    # One parser for the web list and the API, so a filter reaches both.
    initial = normalize_initial(request.GET.get("initial"))
    parsed_filters = replace(
        parse_media_list_filters(request, strict=False),
        initial=initial,
        # The web grouping path below owns drilldown and ordering. Keep the
        # shared engine's alphabet scope identical to the ungrouped library.
        group_by="item",
        group="",
    )
    rating_filter = parsed_filters.rating
    collection_filter = parsed_filters.collection
    progress_filter = (
        parsed_filters.progress if media_type in PROGRESS_MEDIA_TYPES else "all"
    )
    genre_filter = parsed_filters.genre
    implied_genre_filter = (
        parsed_filters.implied_genre if media_type == MediaTypes.MUSIC.value else ""
    )
    year_filter = parsed_filters.year
    completed_date_from = parsed_filters.completed_date_from
    completed_date_to = parsed_filters.completed_date_to
    # The raw window goes back to the template so the choice round-trips.
    completed_date_within = parsed_filters.completed_date_within
    completed_date_within_unit = parsed_filters.completed_date_within_unit
    release_filter = parsed_filters.release
    source_filter = parsed_filters.source
    media_status_filter = parsed_filters.media_status
    language_filter = parsed_filters.language
    country_filter = parsed_filters.country
    platform_values = parsed_filters.platforms
    platform_mode = parsed_filters.platform_mode
    platform_filter = platform_values[0] if platform_values else ""
    origin_filter = parsed_filters.origin
    format_filter = parsed_filters.format
    author_filter = parsed_filters.author
    provider_filter = parsed_filters.provider
    watch_provider_region = parsed_filters.provider_region or None
    tag_values = parsed_filters.tags
    tag_mode = parsed_filters.tag_mode
    # Ranges the library-query engine applies; the Python-built paths below
    # apply them through the engine too (``apply_range_filters``).
    range_filters = {
        name: getattr(parsed_filters, name)
        for name in (
            "rating_min",
            "rating_max",
            "release_date_from",
            "release_date_to",
            "date_added_from",
            "date_added_to",
        )
    }
    range_cache_key = ",".join(f"{k}={v}" for k, v in range_filters.items() if v)

    search_query = request.GET.get("search", "")
    try:
        page = int(request.GET.get("page", 1))
    except (ValueError, TypeError):
        page = 1

    group_by = request.GET.get("group_by", "item")
    if group_by not in dict(GAME_GROUP_BY_CHOICES) or media_type != MediaTypes.GAME.value:
        group_by = "item"
    game_group = request.GET.get("group", "").strip() if group_by != "item" else ""
    grouped_game_overview = group_by != "item" and not game_group
    current_game_group_name = ""

    def apply_range_filters(media_items):
        """Keep entries whose item is inside the rating and date ranges.

        The Python-built paths (separate entries, time left, cached orders)
        ask the library-query engine, so a range means the same thing as on
        the SQL path.
        """
        if not range_cache_key:
            return media_items
        separate = entry_grouping_is_separate()
        # One card per row: rating and date added are judged per row below,
        # so the item-level engine only answers the release date.
        item_ranges = {
            name: "" if separate and not name.startswith("release") else value
            for name, value in range_filters.items()
        }
        if any(item_ranges.values()):
            allowed = LibraryQueryExecutor(
                request.user,
                LibraryQuery(
                    media_types=(media_type,),
                    filters=FilterValues(**item_ranges),
                    within={entry.item_id for entry in media_items},
                ),
            ).ids()
            media_items = [entry for entry in media_items if entry.item_id in allowed]
        if separate:
            media_items = [
                entry for entry in media_items if _entry_in_row_ranges(entry.media)
            ]
        return media_items

    def _entry_in_row_ranges(media):
        """Check one tracker row against the ranges that belong to a row.

        Rating and date added are per row, so with one card per entry the
        item-level answer above is not enough: a 5-rated play must not show
        under ``rating_min=8`` because another play of the title scored 9.
        """
        score = getattr(media, "score", None)
        rating_min = range_filters["rating_min"]
        rating_max = range_filters["rating_max"]
        if rating_min or rating_max:
            if score is None:
                return False
            if rating_min and score < Decimal(rating_min):
                return False
            if rating_max and score > Decimal(rating_max):
                return False
        added_from = range_filters["date_added_from"]
        added_to = range_filters["date_added_to"]
        if added_from or added_to:
            added = timezone.localtime(media.created_at).date().isoformat()
            if (added_from and added < added_from) or (added_to and added > added_to):
                return False
        return True

    def apply_rating_filter(media_items, filter_value):
        return apply_media_list_rating_filter(media_items, filter_value)

    def apply_latest_status_filter(media_items, filter_values):
        """Filter against each item's latest aggregated status."""
        return apply_media_list_status_filter(media_items, filter_values)

    def apply_collection_filter(media_items, filter_value, user, media_type):
        """Filter media items based on collection status.

        For TV shows, checks both show-level and episode-level collection entries.
        Uses one CollectionEntry query and bulk episode lookup instead of per-item queries.
        """
        return apply_media_list_collection_filter(user, media_items, filter_value)

    def apply_progress_filter(media_items, filter_value, media_type):
        return apply_media_list_progress_filter(media_items, filter_value, media_type)

    collection_formats_by_item_id = defaultdict(set)
    collection_platforms_by_item_id = defaultdict(set)

    def apply_format_filter(media_items, filter_value):
        if not filter_value:
            return media_items
        target = _normalize_filter_value(filter_value)
        filtered_items = []
        for media in media_items:
            item = getattr(media, "item", None)
            if not item:
                continue
            item_formats = _extract_item_formats(item, collection_formats_by_item_id)
            if target in item_formats:
                filtered_items.append(media)
        return filtered_items

    def apply_provider_filter(media_items, filter_value, region, pinned_providers=None):
        if not filter_value:
            return media_items
        target = _normalize_filter_value(filter_value)
        filtered_items = []
        for media in media_items:
            item = getattr(media, "item", None)
            if not item:
                continue
            providers = _extract_item_providers(item, region, pinned_providers)
            if providers and any(
                _normalize_filter_value(provider) == target for provider in providers
            ):
                filtered_items.append(media)
        return filtered_items

    def apply_author_filter(media_items, filter_value):
        if not filter_value:
            return media_items
        target = _normalize_filter_value(filter_value)
        filtered_items = []
        for media in media_items:
            item = getattr(media, "item", None)
            if not item:
                continue
            authors = _extract_item_authors(item)
            if any(_normalize_filter_value(author) == target for author in authors):
                filtered_items.append(media)
        return filtered_items

    def annotate_media_authors(media_items):
        for media in media_items:
            media.display_authors = _extract_item_authors(getattr(media, "item", None))

    # Pre-fetch tag item IDs for the mode-aware (AND/OR/NOT) tag filter.
    tag_id_sets = [
        set(
            ItemTag.objects.filter(
                tag__user=request.user,
                tag__name__iexact=value,
            ).values_list("item_id", flat=True),
        )
        for value in tag_values
    ]
    if not tag_id_sets:
        tag_included_ids = tag_excluded_ids = None
    elif tag_mode == "and":
        tag_included_ids, tag_excluded_ids = set.intersection(*tag_id_sets), None
    elif tag_mode == "not":
        tag_included_ids, tag_excluded_ids = None, set().union(*tag_id_sets)
    else:
        tag_included_ids, tag_excluded_ids = set().union(*tag_id_sets), None

    # Get media list with filters applied
    query_sort_filter = (
        "title"
        if sort_filter
        in {"author", "runtime", "time_to_beat", "platform", "time_watched"}
        else last_watched_sort_key(sort_filter)
    )

    list_sql_filters = {
        "initial": initial,
        "genre": genre_filter,
        "implied_genre": implied_genre_filter,
        "year": year_filter,
        "completed_date_from": completed_date_from,
        "completed_date_to": completed_date_to,
        "release": release_filter,
        "source": source_filter,
        "media_status": media_status_filter,
        "language": language_filter,
        "country": country_filter,
        "platform_values": platform_values,
        "platform_mode": platform_mode,
        "tag_included_ids": tag_included_ids,
        "tag_excluded_ids": tag_excluded_ids,
    }
    provider_media_types = PROVIDER_MEDIA_TYPES
    sql_media_filters = replace(
        parsed_filters,
        statuses=tuple(
            value for value in status_filter if value != MEDIA_LIST_NO_STATUS
        ),
        include_no_status=MEDIA_LIST_NO_STATUS in status_filter,
        search=search_query,
        progress=progress_filter,
        implied_genre=implied_genre_filter,
        pinned_providers=tuple(request.user.pinned_watch_providers or ()),
        sort=last_watched_sort_key(sort_filter),
        direction=direction,
        exclude=(),
        media_type=media_type,
    )
    # Every list is read through the shared library-query engine except the
    # surfaces whose unit is not one item: separate-entry mode (one card per
    # tracker row), TV's time-left grouping (its own cached order), and the
    # podcast / music artist and album views (their own trackers, below).
    _skip_generic_media_list = media_type == MediaTypes.PODCAST.value or (
        media_type == MediaTypes.MUSIC.value and music_subview != "tracks"
    )
    use_sql_media_pagination = group_by == "item" and not (
        entry_grouping_is_separate()
        or (sort_filter == "time_left" and media_type == MediaTypes.TV.value)
        or _skip_generic_media_list
    )
    if use_sql_media_pagination and request.GET.get("bulk_ids") == "1":
        # The bulk toolbar asks for the ids behind the current filters so
        # "Select all matching" does not have to page through the library.
        # Resolve before any list/order cache work, including its build lock.
        engine = LibraryQueryExecutor(
            request.user,
            from_media_list_filters(sql_media_filters, (media_type,)),
        )
        return JsonResponse({"item_ids": sorted(engine.ids())})

    anime_library_mode = getattr(
        request.user,
        "anime_library_mode",
        MediaTypes.ANIME.value,
    )
    (
        include_grouped_anime_in_anime,
        include_grouped_anime_in_tv,
    ) = metadata_resolution.anime_library_visibility(request.user)
    cache_variant = (
        anime_library_mode
        if media_type in {MediaTypes.ANIME.value, MediaTypes.TV.value}
        else ""
    )

    tracked_status_filter = tuple(
        value for value in status_filter if value != MEDIA_LIST_NO_STATUS
    )

    def _item_matches_requested_media_type(item):
        if not item:
            return False
        if media_type == MediaTypes.ANIME.value:
            if item.media_type == MediaTypes.ANIME.value:
                return True
            return (
                include_grouped_anime_in_anime
                and item.media_type == MediaTypes.TV.value
                and getattr(item, "library_media_type", None) == MediaTypes.ANIME.value
            )
        if media_type == MediaTypes.TV.value:
            if item.media_type != MediaTypes.TV.value:
                return False
            return (
                include_grouped_anime_in_tv
                or getattr(item, "library_media_type", None) != MediaTypes.ANIME.value
            )
        return item.media_type == media_type

    def _statusless_media_by_item_id():
        """Media rows the user holds without a tracking status (e.g. imported ratings)."""
        model_names = {media_type}
        if media_type == MediaTypes.ANIME.value and include_grouped_anime_in_anime:
            model_names.add(MediaTypes.TV.value)

        statusless = {}
        for model_name in model_names:
            model = django_apps.get_model(app_label="app", model_name=model_name)
            for media in model.objects.filter(
                user=request.user,
                status__isnull=True,
            ).select_related("item"):
                statusless[media.item_id] = media
        return statusless

    def _build_untracked_media_entries(
        tracked_item_ids, *, ignore_platform_filter=False
    ):
        if not supports_untracked_status_filter:
            return []

        collected_item_ids = set(
            CollectionEntry.objects.filter(user=request.user).values_list(
                "item_id", flat=True
            ),
        )
        statusless_media_by_item_id = _statusless_media_by_item_id()
        if (
            not collected_item_ids
            and not statusless_media_by_item_id
            and not tag_included_ids
        ):
            return []

        candidate_item_ids = set(collected_item_ids) | set(statusless_media_by_item_id)
        # A tag can sit on an item with no tracker and no collection entry.
        if tag_included_ids:
            candidate_item_ids |= tag_included_ids
        if media_type in {MediaTypes.TV.value, MediaTypes.ANIME.value}:
            episode_pairs = {
                (str(media_id), str(source))
                for media_id, source in Item.objects.filter(
                    id__in=collected_item_ids,
                    media_type=MediaTypes.EPISODE.value,
                ).values_list("media_id", "source")
            }
            if episode_pairs:
                show_media_ids = {media_id for media_id, _source in episode_pairs}
                show_sources = {source for _media_id, source in episode_pairs}
                for show_item in Item.objects.filter(
                    media_type__in=(MediaTypes.TV.value, MediaTypes.ANIME.value),
                    media_id__in=show_media_ids,
                    source__in=show_sources,
                ).only("id", "media_id", "source"):
                    if (
                        str(show_item.media_id),
                        str(show_item.source),
                    ) in episode_pairs:
                        candidate_item_ids.add(show_item.id)

        candidate_item_ids -= tracked_item_ids
        if not candidate_item_ids:
            return []

        if media_type == MediaTypes.GAME.value:
            for item_id, collection_platform in CollectionEntry.objects.filter(
                user=request.user,
                item_id__in=candidate_item_ids,
            ).values_list("item_id", "resolution"):
                platform_value = str(collection_platform or "").strip()
                if platform_value:
                    collection_platforms_by_item_id[item_id].add(platform_value)

        if media_type in AUTHOR_MEDIA_TYPES:
            for item_id, collection_format in (
                CollectionEntry.objects.filter(
                    user=request.user,
                    item_id__in=candidate_item_ids,
                )
                .exclude(media_type="")
                .values_list("item_id", "media_type")
            ):
                normalized_collection_format = _normalize_filter_value(
                    collection_format
                )
                if normalized_collection_format:
                    collection_formats_by_item_id[item_id].add(
                        normalized_collection_format
                    )

        effective_platform_values = () if ignore_platform_filter else platform_values

        def _item_matches_platform_values(item):
            if not effective_platform_values:
                return True
            item_platforms = {
                _normalize_filter_value(platform)
                for platform in _extract_item_platforms_with_collection(
                    item,
                    collection_platforms_by_item_id,
                )
            }
            normalized_values = [
                _normalize_filter_value(value) for value in effective_platform_values
            ]
            if platform_mode == "and":
                return all(value in item_platforms for value in normalized_values)
            if platform_mode == "not":
                return not any(value in item_platforms for value in normalized_values)
            return any(value in item_platforms for value in normalized_values)

        today = timezone.localdate()
        filtered_items = []
        candidate_items = list(
            Item.objects.filter(id__in=candidate_item_ids)
            .filter(initial_condition(initial, request.user))
            .order_by("title", "id"),
        )
        for item in candidate_items:
            if not _item_matches_requested_media_type(item):
                continue
            if search_query:
                normalized_search = _normalize_filter_value(search_query)
                if normalized_search not in _normalize_filter_value(
                    item.title
                ) and normalized_search not in _normalize_filter_value(item.media_id):
                    continue
            if genre_filter:
                normalized_genre = _normalize_filter_value(genre_filter)
                if not any(
                    _normalize_filter_value(genre) == normalized_genre
                    for genre in (getattr(item, "genres", None) or [])
                ):
                    continue
            if implied_genre_filter:
                normalized_implied_genre = _normalize_filter_value(implied_genre_filter)
                if not any(
                    _normalize_filter_value(genre) == normalized_implied_genre
                    for genre in (getattr(item, "implied_genres", None) or [])
                ):
                    continue
            normalized_year = _normalize_filter_value(year_filter)
            if normalized_year == "unknown" and getattr(item, "release_datetime", None):
                continue
            if normalized_year.isdigit():
                release_value = getattr(item, "release_datetime", None)
                release_year = (
                    getattr(release_value, "year", None) if release_value else None
                )
                if release_year != int(normalized_year):
                    continue
            if completed_date_from or completed_date_to:
                # Untracked/statusless entries have no Media row, so no
                # completion date — they never match a completed-date range.
                continue
            if source_filter and getattr(item, "source", None) != source_filter:
                continue
            if (
                media_status_filter
                and getattr(item, "status", None) != media_status_filter
            ):
                continue
            if not _matches_release_filter_value(
                getattr(item, "release_datetime", None),
                release_filter,
                today,
            ):
                continue
            if language_filter and not any(
                _normalize_filter_value(language)
                == _normalize_filter_value(language_filter)
                for language in _extract_item_languages(item)
            ):
                continue
            if country_filter and _normalize_filter_value(
                _extract_item_country(item)
            ) != _normalize_filter_value(country_filter):
                continue
            if (
                media_type == MediaTypes.GAME.value
                and not _item_matches_platform_values(
                    item,
                )
            ):
                continue
            if tag_included_ids is not None and item.id not in tag_included_ids:
                continue
            if tag_excluded_ids is not None and item.id in tag_excluded_ids:
                continue
            filtered_items.append(
                MediaListEntry(
                    item=item,
                    media=statusless_media_by_item_id.get(item.id),
                ),
            )

        return filtered_items

    # Cache tracker-backed list results and filter summaries separately.
    _time_left_active = sort_filter == "time_left" and media_type == MediaTypes.TV.value
    _time_left_record_by_entry = {}

    def _time_left_records(entries):
        """Ranking records for time-left entries, read once per request."""
        missing = [
            entry for entry in entries if id(entry) not in _time_left_record_by_entry
        ]
        for entry, record in zip(
            missing,
            build_time_left_records(request.user, missing),
            strict=True,
        ):
            _time_left_record_by_entry[id(entry)] = record
        return [_time_left_record_by_entry[id(entry)] for entry in entries]

    # Platform ordering is resolved from collection entries after the base query,
    # so cache only the database-backed sort orders.
    _use_media_list_cache = (
        group_by == "item" and not _time_left_active and sort_filter != "platform"
    )
    _include_untracked_entries = MEDIA_LIST_NO_STATUS in status_filter

    def _resolve_tag_count_source_items(filter_data_source_items, tracked_item_ids):
        """Item list for tag counts: same filters as the list, minus the tag filter.

        Without this, tag counts are computed from the already tag-filtered
        list, so every tag not part of the active filter collapses to 0
        (issue #1072).
        """
        if tag_included_ids is None and tag_excluded_ids is None:
            return filter_data_source_items
        tag_free_filters = {
            **list_sql_filters,
            "tag_included_ids": None,
            "tag_excluded_ids": None,
        }
        items = [
            MediaListEntry.from_media(media)
            for media in BasicMedia.objects.get_media_list(
                user=request.user,
                media_type=media_type,
                status_filter=tracked_status_filter,
                sort_filter=query_sort_filter,
                search=search_query,
                direction=direction,
                list_sql_filters=tag_free_filters,
                prefetch_graph=not _time_left_active,
            )
        ]
        if _include_untracked_entries:
            items.extend(_build_untracked_media_entries(tracked_item_ids))
        return apply_latest_status_filter(items, status_filter)

    # The keys come from the effective filters, so every filter reaches them.
    _title_display = request.user.title_display_preference
    _media_list_cache_key = (
        cache_utils.build_media_list_cache_key(
            request.user.id,
            sql_media_filters,
            sort=sort_filter,
            library_mode=cache_variant,
            title_display=_title_display,
            entry_grouping=active_entry_grouping_mode(),
        )
        if _use_media_list_cache
        else None
    )
    _media_list_filter_cache_key = (
        cache_utils.build_media_list_filter_cache_key(
            request.user.id,
            sql_media_filters,
            library_mode=cache_variant,
            title_display=_title_display,
        )
        if (_use_media_list_cache or _time_left_active)
        else None
    )
    _media_list_cached = (
        cache.get(_media_list_cache_key) if _media_list_cache_key else None
    )
    filter_data = (
        cache.get(_media_list_filter_cache_key)
        if _media_list_filter_cache_key
        else None
    )
    # Concurrent cold requests for one key would all run the same build; let one
    # build and the rest read its result. The lock is short-lived and waiters
    # fall back to building, so a dead builder cannot wedge anyone.
    _media_list_build_lock_key = None
    _order_cache_built = bool(_media_list_cache_key) and not use_sql_media_pagination
    if _media_list_filter_cache_key and (
        filter_data is None or (_order_cache_built and _media_list_cached is None)
    ):
        if cache_utils.acquire_media_list_build_lock(_media_list_filter_cache_key):
            _media_list_build_lock_key = _media_list_filter_cache_key
        elif cache_utils.wait_for_media_list_build(
            _media_list_filter_cache_key,
            lambda: cache.get(_media_list_filter_cache_key) is not None
            and (
                not _order_cache_built or cache.get(_media_list_cache_key) is not None
            ),
        ):
            _media_list_cached = (
                cache.get(_media_list_cache_key) if _media_list_cache_key else None
            )
            filter_data = cache.get(_media_list_filter_cache_key)

    # time_left keeps its own sorted-order cache (the sort is the expensive
    # part). Check it BEFORE building the full media list: on a hit with warm
    # filter data the whole library scan + Python filtering can be skipped and
    # the page hydrated from the cached order alone.
    _time_left_cache_key = None
    _time_left_cached_order = None
    if _time_left_active:
        _time_left_cache_key = cache_utils.build_time_left_cache_key(
            request.user.id,
            sql_media_filters,
            title_display=_title_display,
        )
        _time_left_cached_order = cache.get(_time_left_cache_key)
        if _time_left_cached_order is not None and filter_data is not None:
            # Sentinel: skips the list build below; the page is hydrated
            # from _time_left_cached_order in the time_left branch.
            _media_list_cached = []

    # `_media_list_cached`, when present, holds a compact ordered list of
    # {media_pk, item_pk} rows (see `_serialize_media_list_order`), not full
    # objects — unpickling thousands of hydrated model instances per request
    # was the dominant cost behind issue #865, even on a cache hit. Only take
    # the fast (page-only-hydrate) path when filter_data is ALSO cached: the
    # two caches are written together on every rebuild, so a filter_data miss
    # here means the order cache is stale relative to it and both should be
    # rebuilt together rather than drifting apart.
    _sql_media_page = None
    _media_list_full = None
    _cached_media_list_order = None
    if use_sql_media_pagination:
        items_per_page = 32
        engine_query = from_media_list_filters(sql_media_filters, (media_type,))
        engine = LibraryQueryExecutor(request.user, engine_query)
        engine_total = engine.count()
        media_page = Paginator(range(engine_total), items_per_page).get_page(
            max(page, 1)
        )
        engine_page = engine.page(
            (media_page.number - 1) * items_per_page,
            items_per_page,
            total=engine_total,
        )
        page_entries = media_list_entries_for_items(request.user, engine_page.items)
        _prefetch_untracked_item_tags(page_entries)
        media_page.object_list = [
            MediaListEntry(item=entry.item, media=entry.media) for entry in page_entries
        ]
        if media_type == MediaTypes.ANIME.value:
            _mark_grouped_anime_route(
                [
                    entry.media
                    for entry in page_entries
                    if entry.media is not None
                    and entry.item.media_type == MediaTypes.TV.value
                ],
            )
        if sort_filter == "next_episode_air_date":
            # The cards show the date the list is ordered by.
            BasicMedia.objects.attach_show_season_events(
                [entry.media for entry in page_entries if entry.media is not None],
            )
            for entry in page_entries:
                if entry.media is not None:
                    entry.media.next_episode_air_date = (
                        BasicMedia.objects._next_episode_air_date_value(entry.media)
                    )
        _sql_media_page = media_page
        media_list = []
        _media_list_full = []
        if filter_data is None:
            # The menu lists what the status and list filters leave, before
            # the per-item filters narrow it - the same candidates the list
            # always offered - read as narrow Item rows, not hydrated media.
            menu_filters = replace(
                sql_media_filters,
                rating="all",
                rating_min="",
                rating_max="",
                release_date_from="",
                release_date_to="",
                date_added_from="",
                date_added_to="",
                collection="all",
                progress="all",
                author="",
                format="",
                provider="",
                platforms=()
                if media_type == MediaTypes.GAME.value
                else platform_values,
            )
            provider_region_for_values = (
                watch_provider_region if media_type in provider_media_types else None
            )

            def _menu_rows(filters):
                menu_engine = LibraryQueryExecutor(
                    request.user,
                    from_media_list_filters(filters, (media_type,)),
                )
                return list(
                    BasicMedia.objects.item_values_for_menu(
                        Item.objects.filter(
                            pk__in=menu_engine.filtered.values("pk")
                        ).order_by(),
                        provider_region_for_values,
                    ),
                )

            filter_data_rows = _menu_rows(menu_filters)
            item_ids = {row["id"] for row in filter_data_rows}
            if media_type == MediaTypes.GAME.value and item_ids:
                for item_id, collection_platform in CollectionEntry.objects.filter(
                    user=request.user,
                    item_id__in=item_ids,
                ).values_list("item_id", "resolution"):
                    platform_value = str(collection_platform or "").strip()
                    if platform_value:
                        collection_platforms_by_item_id[item_id].add(platform_value)
            if media_type in AUTHOR_MEDIA_TYPES and item_ids:
                for item_id, collection_format in (
                    CollectionEntry.objects.filter(
                        user=request.user,
                        item_id__in=item_ids,
                    )
                    .exclude(media_type="")
                    .values_list("item_id", "media_type")
                ):
                    normalized_collection_format = _normalize_filter_value(
                        collection_format
                    )
                    if normalized_collection_format:
                        collection_formats_by_item_id[item_id].add(
                            normalized_collection_format
                        )
            filter_data = build_filter_data_from_item_values(
                filter_data_rows,
                collection_formats_by_item_id=collection_formats_by_item_id,
                collection_platforms_by_item_id=collection_platforms_by_item_id,
                region=watch_provider_region,
                pinned_providers=request.user.pinned_watch_providers,
                include_providers=media_type in provider_media_types,
            )
            filter_data["show_languages"] = media_type in (
                MediaTypes.TV.value,
                MediaTypes.MOVIE.value,
                MediaTypes.ANIME.value,
                MediaTypes.PODCAST.value,
            )
            filter_data["show_countries"] = media_type in (
                MediaTypes.TV.value,
                MediaTypes.MOVIE.value,
                MediaTypes.ANIME.value,
                MediaTypes.PODCAST.value,
            )
            filter_data["show_platforms"] = media_type == MediaTypes.GAME.value
            filter_data["show_origins"] = media_type == MediaTypes.MUSIC.value
            filter_data["show_formats"] = media_type in AUTHOR_MEDIA_TYPES
            filter_data["show_authors"] = media_type in AUTHOR_FILTER_MEDIA_TYPES
            filter_data["author_is_channel"] = media_type == MediaTypes.VIDEO.value
            filter_data["show_providers"] = media_type in provider_media_types and bool(
                (watch_provider_region and watch_provider_region != "UNSET")
                or request.user.pinned_watch_providers
            )
            filter_data["show_progress"] = media_type in PROGRESS_MEDIA_TYPES
            # Tag counts ignore the tag filter itself, or every other tag
            # would count 0 (#1072).
            # Counting reads only the item ids, so the tag-free pass skips the
            # menu columns.
            tag_item_ids = (
                LibraryQueryExecutor(
                    request.user,
                    from_media_list_filters(
                        replace(menu_filters, tags=()), (media_type,)
                    ),
                )
                .filtered.order_by()
                .values_list("pk", flat=True)
                if tag_values
                else item_ids
            )
            filter_data.update(
                _build_media_list_tag_data(
                    request.user,
                    [SimpleNamespace(id=pk) for pk in tag_item_ids],
                ),
            )
            if _media_list_filter_cache_key:
                cache.set(
                    _media_list_filter_cache_key,
                    filter_data,
                    cache_utils.MEDIA_LIST_FILTER_CACHE_TTL,
                )
                cache_utils.register_media_list_cache_key(
                    request.user.id,
                    _media_list_filter_cache_key,
                )
                if _media_list_build_lock_key:
                    cache_utils.release_media_list_build_lock(_media_list_build_lock_key)
    elif _media_list_cached is None:
        # Podcasts always render from PodcastShowTracker further below, and
        # music's artists/albums subviews render from ArtistTracker/
        # AlbumTracker — in both cases this generic media_list/filter_data
        # pipeline is discarded, so skip materializing every episode/track
        # here to avoid an O(row count) Python pass on large libraries (see
        # issue #1198). Music's "tracks" subview is the one case that
        # actually needs the per-track list built below.
        media_queryset = (
            BasicMedia.objects.none()
            if _skip_generic_media_list
            else BasicMedia.objects.get_media_list(
                user=request.user,
                media_type=media_type,
                status_filter=tracked_status_filter,
                sort_filter=query_sort_filter,
                search=search_query,
                direction=direction,
                list_sql_filters=list_sql_filters,
                prefetch_graph=not _time_left_active,
            )
        )

        # Convert to list for filtering (rating and collection filters work on lists)
        media_list = list(media_queryset)
        if (
            media_type in {MediaTypes.TV.value, MediaTypes.SEASON.value}
            and not include_grouped_anime_in_tv
        ):
            media_list = [
                media
                for media in media_list
                if getattr(getattr(media, "item", None), "library_media_type", None)
                != MediaTypes.ANIME.value
            ]
        elif media_type == MediaTypes.ANIME.value and include_grouped_anime_in_anime:
            grouped_anime_media = list(
                BasicMedia.objects.get_media_list(
                    user=request.user,
                    media_type=MediaTypes.TV.value,
                    status_filter=tracked_status_filter,
                    sort_filter=query_sort_filter,
                    search=search_query,
                    direction=direction,
                    list_sql_filters=list_sql_filters,
                ),
            )
            grouped_anime_media = [
                media
                for media in grouped_anime_media
                if getattr(getattr(media, "item", None), "library_media_type", None)
                == MediaTypes.ANIME.value
            ]
            _mark_grouped_anime_route(grouped_anime_media)
            media_list.extend(grouped_anime_media)

        tracked_item_ids = {
            media.item_id for media in media_list if getattr(media, "item_id", None)
        }
        untracked_media_entries = []
        if _include_untracked_entries:
            untracked_media_entries = _build_untracked_media_entries(tracked_item_ids)

        media_list = [MediaListEntry.from_media(media) for media in media_list]
        media_list.extend(untracked_media_entries)

        media_list = apply_latest_status_filter(media_list, status_filter)

        if (
            status_filter == (MEDIA_LIST_NO_STATUS,)
            and media_type != MediaTypes.ANIME.value
            and sort_filter
            not in {
                "author",
                "runtime",
                "plays",
                "time_watched",
                "time_to_beat",
                "platform",
                "time_left",
            }
        ):
            _reverse = direction == "desc"
            _none_sentinel = -math.inf if _reverse else math.inf

            def _untracked_sort_key(entry):
                item = getattr(entry, "item", None)
                title = (getattr(item, "title", "") or "").lower()
                if sort_filter == "release_date":
                    val = getattr(item, "release_datetime", None)
                    return (val.timestamp() if val else _none_sentinel, title)
                if sort_filter == "popularity":
                    val = getattr(item, "trakt_popularity_rank", None)
                    return (val if val is not None else _none_sentinel, title)
                if sort_filter == "critic_rating":
                    val = getattr(item, "provider_rating", None)
                    return (val if val is not None else _none_sentinel, title)
                return title

            media_list = sorted(media_list, key=_untracked_sort_key, reverse=_reverse)

        if game_group:
            media_list = [
                entry
                for entry in media_list
                if _game_group_key_for_item(entry.item, group_by) == game_group
            ]
            current_game_group_name = next(
                (
                    _game_group_name_for_item(entry.item, group_by)
                    for entry in media_list
                    if _game_group_name_for_item(entry.item, group_by)
                ),
                "",
            )

        filter_data_source_items = media_list
        if (
            media_type == MediaTypes.GAME.value
            and platform_values
            and filter_data is None
        ):
            filter_sql_filters = {
                **list_sql_filters,
                "platform_values": (),
                "platform_mode": "or",
            }
            filter_data_source_items = [
                MediaListEntry.from_media(media)
                for media in list(
                    BasicMedia.objects.get_media_list(
                        user=request.user,
                        media_type=media_type,
                        status_filter=tracked_status_filter,
                        sort_filter=query_sort_filter,
                        search=search_query,
                        direction=direction,
                        list_sql_filters=filter_sql_filters,
                    ),
                )
            ]
            if _include_untracked_entries:
                filter_data_source_items.extend(
                    _build_untracked_media_entries(
                        tracked_item_ids,
                        ignore_platform_filter=True,
                    ),
                )
            filter_data_source_items = apply_latest_status_filter(
                filter_data_source_items,
                status_filter,
            )
        if filter_data is None and media_type == MediaTypes.GAME.value:
            item_ids = {
                media.item_id
                for media in filter_data_source_items
                if getattr(media, "item_id", None)
            }
            if item_ids:
                collection_platforms = CollectionEntry.objects.filter(
                    user=request.user,
                    item_id__in=item_ids,
                ).values_list("item_id", "resolution")
                for item_id, collection_platform in collection_platforms:
                    platform_value = str(collection_platform or "").strip()
                    if platform_value:
                        collection_platforms_by_item_id[item_id].add(platform_value)
        if filter_data is None and media_type in AUTHOR_MEDIA_TYPES:
            item_ids = {
                media.item_id for media in media_list if getattr(media, "item_id", None)
            }
            if item_ids:
                collection_formats = (
                    CollectionEntry.objects.filter(
                        user=request.user,
                        item_id__in=item_ids,
                    )
                    .exclude(media_type="")
                    .values_list("item_id", "media_type")
                )
                for item_id, collection_format in collection_formats:
                    normalized_collection_format = _normalize_filter_value(
                        collection_format
                    )
                    if normalized_collection_format:
                        collection_formats_by_item_id[item_id].add(
                            normalized_collection_format
                        )
        if filter_data is None:
            filter_data = build_filter_data_from_items(
                filter_data_source_items,
                collection_formats_by_item_id=collection_formats_by_item_id,
                collection_platforms_by_item_id=collection_platforms_by_item_id,
                region=watch_provider_region,
                pinned_providers=request.user.pinned_watch_providers,
                include_providers=media_type in provider_media_types,
            )
            filter_data["show_languages"] = media_type in (
                MediaTypes.TV.value,
                MediaTypes.MOVIE.value,
                MediaTypes.ANIME.value,
                MediaTypes.PODCAST.value,
            )
            filter_data["show_countries"] = media_type in (
                MediaTypes.TV.value,
                MediaTypes.MOVIE.value,
                MediaTypes.ANIME.value,
                MediaTypes.PODCAST.value,
            )
            filter_data["show_platforms"] = media_type == MediaTypes.GAME.value
            filter_data["show_origins"] = media_type == MediaTypes.MUSIC.value
            filter_data["show_formats"] = media_type in AUTHOR_MEDIA_TYPES
            filter_data["show_authors"] = media_type in AUTHOR_FILTER_MEDIA_TYPES
            filter_data["author_is_channel"] = media_type == MediaTypes.VIDEO.value
            filter_data["show_providers"] = media_type in provider_media_types and bool(
                (watch_provider_region and watch_provider_region != "UNSET")
                or request.user.pinned_watch_providers
            )
            filter_data["show_progress"] = media_type in PROGRESS_MEDIA_TYPES
            filter_data.update(
                _build_media_list_tag_data(
                    request.user,
                    _resolve_tag_count_source_items(
                        filter_data_source_items, tracked_item_ids
                    ),
                ),
            )
            if _media_list_filter_cache_key:
                cache.set(
                    _media_list_filter_cache_key,
                    filter_data,
                    cache_utils.MEDIA_LIST_FILTER_CACHE_TTL,
                )
                cache_utils.register_media_list_cache_key(
                    request.user.id,
                    _media_list_filter_cache_key,
                )
                if _media_list_build_lock_key:
                    cache_utils.release_media_list_build_lock(_media_list_build_lock_key)
        media_list = apply_rating_filter(media_list, rating_filter)
        media_list = apply_range_filters(media_list)
        media_list = apply_collection_filter(
            media_list, collection_filter, request.user, media_type
        )
        if not _time_left_active:
            media_list = apply_progress_filter(media_list, progress_filter, media_type)
        elif progress_filter != "all":
            media_list = [
                entry
                for entry, record in zip(
                    media_list, _time_left_records(media_list), strict=True
                )
                if entry.media is not None
                and is_caught_up_record(record, entry.item)
                == (progress_filter == "caught_up")
            ]
        if media_type in AUTHOR_FILTER_MEDIA_TYPES:
            media_list = apply_author_filter(media_list, author_filter)
        if media_type in AUTHOR_MEDIA_TYPES:
            media_list = apply_format_filter(media_list, format_filter)
        if media_type in provider_media_types:
            media_list = apply_provider_filter(
                media_list,
                provider_filter,
                watch_provider_region,
                request.user.pinned_watch_providers,
            )
        if sort_filter == "author" and media_type in AUTHOR_FILTER_MEDIA_TYPES:
            media_list = _sort_media_items_by_author(media_list, direction)
        if sort_filter == "runtime" and media_type in RUNTIME_MEDIA_TYPES:
            BasicMedia.objects.annotate_max_progress(
                _tracked_media_entries(media_list),
                media_type,
            )
            prefill_episode_runtime_index(media_list)
            media_list = _sort_media_items_by_numeric_value(
                media_list, direction, _runtime_sort_value
            )
        if sort_filter == "plays" and media_type in PLAYS_MEDIA_TYPES:
            media_list = _sort_media_items_by_numeric_value(
                media_list, direction, _plays_sort_value
            )
        if sort_filter == "time_watched" and media_type in RUNTIME_MEDIA_TYPES:
            BasicMedia.objects.annotate_max_progress(
                _tracked_media_entries(media_list),
                media_type,
            )
            prefill_episode_runtime_index(media_list)
            media_list = _sort_media_items_by_numeric_value(
                media_list, direction, _time_watched_sort_value
            )
        if sort_filter == "time_to_beat" and media_type == MediaTypes.GAME.value:
            media_list = _sort_media_items_by_numeric_value(
                media_list, direction, _game_time_to_beat_sort_value
            )
        if sort_filter == "platform" and media_type == MediaTypes.GAME.value:
            item_ids = {
                media.item_id for media in media_list if getattr(media, "item_id", None)
            }
            if item_ids:
                for item_id, collection_platform in CollectionEntry.objects.filter(
                    user=request.user,
                    item_id__in=item_ids,
                ).values_list("item_id", "resolution"):
                    platform_value = str(collection_platform or "").strip()
                    if platform_value:
                        collection_platforms_by_item_id[item_id].add(platform_value)
            media_list = _sort_media_items_by_platform(
                media_list,
                direction,
                collection_platforms_by_item_id,
                platform_filter,
            )
        if (
            media_type == MediaTypes.ANIME.value
            and any(
                getattr(getattr(media, "item", None), "media_type", None)
                == MediaTypes.TV.value
                for media in media_list
            )
            and sort_filter not in {"plays", "time_watched"}
        ):

            def _sortable_dt(value):
                if value is not None:
                    return value
                return (
                    datetime.min.replace(tzinfo=UTC)
                    if direction == "desc"
                    else datetime.max.replace(tzinfo=UTC)
                )

            def _mixed_sort_key(media):
                item = getattr(media, "item", None)
                title = getattr(item, "title", "") or ""
                if sort_filter == "score":
                    score = getattr(media, "aggregated_score", None)
                    if score is None:
                        score = getattr(media, "score", None)
                    return (score is None, score or 0, title.lower())
                if sort_filter == "progress":
                    progress = getattr(media, "aggregated_progress", None)
                    if progress is None:
                        progress = getattr(media, "progress", 0)
                    return (progress, title.lower())
                if sort_filter == "release_date":
                    release_dt = getattr(item, "release_datetime", None)
                    return (_sortable_dt(release_dt), title.lower())
                if sort_filter == "popularity":
                    rank = getattr(item, "trakt_popularity_rank", None)
                    if rank is None:
                        rank = math.inf if direction == "asc" else -math.inf
                    return (rank, title.lower())
                if sort_filter == "critic_rating":
                    rating = getattr(item, "provider_rating", None)
                    if rating is None:
                        rating = math.inf if direction == "asc" else -math.inf
                    return (rating, title.lower())
                if sort_filter == "date_added":
                    return (
                        _sortable_dt(getattr(media, "created_at", None)),
                        title.lower(),
                    )
                if sort_filter == "start_date":
                    start_dt = getattr(media, "aggregated_start_date", None) or getattr(
                        media, "start_date", None
                    )
                    return (_sortable_dt(start_dt), title.lower())
                if sort_filter == "end_date":
                    end_dt = (
                        getattr(media, "aggregated_last_watched", None)
                        or getattr(media, "aggregated_end_date", None)
                        or getattr(media, "end_date", None)
                        or helpers.progress_activity(media)
                    )
                    return (_sortable_dt(end_dt), title.lower())
                if sort_filter == "next_episode_air_date":
                    next_episode_air_date = getattr(
                        media, "next_episode_air_date", None
                    )
                    return (_sortable_dt(next_episode_air_date), title.lower())
                return title.lower()

            _sort_reverse = direction == "desc"
            media_list = sorted(media_list, key=_mixed_sort_key, reverse=_sort_reverse)

        _media_list_full = media_list
        if _media_list_cache_key:
            cache.set(
                _media_list_cache_key,
                _serialize_media_list_order(media_list),
                cache_utils.MEDIA_LIST_CACHE_TTL,
            )
            cache_utils.register_media_list_cache_key(
                request.user.id, _media_list_cache_key
            )
            if _media_list_build_lock_key and filter_data is not None:
                cache_utils.release_media_list_build_lock(_media_list_build_lock_key)
    elif filter_data is None:
        # Order cache hit but filter_data cache missed: hydrate every cached
        # row (one bulk query) instead of the full filter/sort pipeline, then
        # fall through to the normal filter_data build below.
        media_list = _hydrate_media_list_page(
            request.user, media_type, _media_list_cached
        )
        _media_list_full = media_list
    else:
        # Fast path: both caches are warm. Defer hydration to just the
        # current page instead of rebuilding or unpickling the full library.
        _cached_media_list_order = _media_list_cached

    if filter_data is None:
        tracked_item_ids = {
            media.item_id for media in media_list if getattr(media, "item_id", None)
        }
        filter_data_source_items = media_list
        if media_type == MediaTypes.GAME.value and platform_values:
            filter_sql_filters = {
                **list_sql_filters,
                "platform_values": (),
                "platform_mode": "or",
            }
            filter_data_source_items = [
                MediaListEntry.from_media(media)
                for media in list(
                    BasicMedia.objects.get_media_list(
                        user=request.user,
                        media_type=media_type,
                        status_filter=tracked_status_filter,
                        sort_filter=query_sort_filter,
                        search=search_query,
                        direction=direction,
                        list_sql_filters=filter_sql_filters,
                    ),
                )
            ]
            if _include_untracked_entries:
                filter_data_source_items.extend(
                    _build_untracked_media_entries(
                        tracked_item_ids,
                        ignore_platform_filter=True,
                    ),
                )
            filter_data_source_items = apply_latest_status_filter(
                filter_data_source_items,
                status_filter,
            )
        if media_type == MediaTypes.GAME.value:
            item_ids = {
                media.item_id
                for media in filter_data_source_items
                if getattr(media, "item_id", None)
            }
            if item_ids:
                collection_platforms = CollectionEntry.objects.filter(
                    user=request.user,
                    item_id__in=item_ids,
                ).values_list("item_id", "resolution")
                for item_id, collection_platform in collection_platforms:
                    platform_value = str(collection_platform or "").strip()
                    if platform_value:
                        collection_platforms_by_item_id[item_id].add(platform_value)
        if media_type in AUTHOR_MEDIA_TYPES:
            item_ids = {
                media.item_id for media in media_list if getattr(media, "item_id", None)
            }
            if item_ids:
                collection_formats = (
                    CollectionEntry.objects.filter(
                        user=request.user,
                        item_id__in=item_ids,
                    )
                    .exclude(media_type="")
                    .values_list("item_id", "media_type")
                )
                for item_id, collection_format in collection_formats:
                    normalized_collection_format = _normalize_filter_value(
                        collection_format
                    )
                    if normalized_collection_format:
                        collection_formats_by_item_id[item_id].add(
                            normalized_collection_format
                        )
        filter_data = build_filter_data_from_items(
            filter_data_source_items,
            collection_formats_by_item_id=collection_formats_by_item_id,
            collection_platforms_by_item_id=collection_platforms_by_item_id,
            region=watch_provider_region,
            pinned_providers=request.user.pinned_watch_providers,
            include_providers=media_type in provider_media_types,
        )
        filter_data["show_languages"] = media_type in (
            MediaTypes.TV.value,
            MediaTypes.MOVIE.value,
            MediaTypes.ANIME.value,
            MediaTypes.PODCAST.value,
        )
        filter_data["show_countries"] = media_type in (
            MediaTypes.TV.value,
            MediaTypes.MOVIE.value,
            MediaTypes.ANIME.value,
            MediaTypes.PODCAST.value,
        )
        filter_data["show_platforms"] = media_type == MediaTypes.GAME.value
        filter_data["show_origins"] = media_type == MediaTypes.MUSIC.value
        filter_data["show_formats"] = media_type in AUTHOR_MEDIA_TYPES
        filter_data["show_authors"] = media_type in AUTHOR_FILTER_MEDIA_TYPES
        filter_data["author_is_channel"] = media_type == MediaTypes.VIDEO.value
        filter_data["show_providers"] = media_type in provider_media_types and bool(
            (watch_provider_region and watch_provider_region != "UNSET")
            or request.user.pinned_watch_providers
        )
        filter_data["show_progress"] = media_type in PROGRESS_MEDIA_TYPES
        filter_data.update(
            _build_media_list_tag_data(
                request.user,
                _resolve_tag_count_source_items(
                    filter_data_source_items, tracked_item_ids
                ),
            ),
        )
        if _media_list_filter_cache_key:
            cache.set(
                _media_list_filter_cache_key,
                filter_data,
                cache_utils.MEDIA_LIST_FILTER_CACHE_TTL,
            )
            cache_utils.register_media_list_cache_key(
                request.user.id,
                _media_list_filter_cache_key,
            )
            if _media_list_build_lock_key:
                cache_utils.release_media_list_build_lock(_media_list_build_lock_key)

    # Handle time_left sorting for TV shows
    if _time_left_active:
        items_per_page = 32
        if _time_left_cached_order is not None:
            # Cached sort order: paginate the compact order and hydrate only
            # this page's rows — no library-wide sort or annotation.
            logger.debug("DEBUG: Using cached time_left sort (page %s)", page)
            paginator = Paginator(_time_left_cached_order, items_per_page)
            media_page = paginator.get_page(page)
            media_page.object_list = _hydrate_time_left_page(
                request.user,
                media_page.object_list,
            )
        else:
            # No cached order. media_list holds the filtered library as
            # trackers with only their item; rank it from grouped facts, then
            # hydrate this page exactly as a cache hit does.
            _time_left_order = _serialize_time_left_order(
                _sort_tv_media_by_time_left(
                    _time_left_records(media_list),
                    direction,
                ),
            )

            # Cache the compact sort order for 5 minutes (300 seconds)
            cache.set(_time_left_cache_key, _time_left_order, 300)
            cache_utils.register_time_left_cache_key(
                request.user.id,
                _time_left_cache_key,
            )

            paginator = Paginator(_time_left_order, items_per_page)
            media_page = paginator.get_page(page)
            media_page.object_list = _hydrate_time_left_page(
                request.user,
                media_page.object_list,
            )

        BasicMedia.objects.annotate_max_progress(
            _tracked_media_entries(media_page.object_list),
            media_type,
        )
        prefill_episode_runtime_index(media_page.object_list)
    else:
        # Paginate results normally. A grouped Games view needs every filtered
        # entry so each group's rollup covers the whole filtered library.
        items_per_page = (
            max(len(_media_list_full or []), 1) if grouped_game_overview else 32
        )
        if _sql_media_page is not None:
            media_page = _sql_media_page
        elif _cached_media_list_order is not None:
            # Fast path: paginate the compact cached order and hydrate only
            # this page's rows from the DB — no library-wide materialization.
            paginator = Paginator(_cached_media_list_order, items_per_page)
            media_page = paginator.get_page(page)
            media_page.object_list = _hydrate_media_list_page(
                request.user, media_type, media_page.object_list
            )
        else:
            paginator = Paginator(_media_list_full, items_per_page)
            media_page = paginator.get_page(page)

        BasicMedia.objects.annotate_max_progress(
            _tracked_media_entries(media_page.object_list),
            media_type,
        )
        # Table runtime/time-watched cells read total_runtime_minutes per row;
        # prefill so the page renders with one episode-runtime query.
        prefill_episode_runtime_index(media_page.object_list)
    prefill_display_release_years(media_page.object_list)

    if media_type == MediaTypes.MUSIC.value:
        # Track cards fall back to the album's cover when the track's own
        # Item.image wasn't backfilled after the album art was fetched.
        # Similar to _fix_missing_season_images for TV seasons.
        BasicMedia.objects._fix_missing_music_images(
            [entry.media for entry in media_page.object_list if entry.media is not None]
        )

    if media_type == MediaTypes.VIDEO.value:
        # Reuse the music card subtitle slot for "Channel · length".
        for entry in media_page.object_list:
            video = entry.media
            if video is None:
                continue
            parts = [video.channel] if video.channel else []
            if video.length_seconds:
                minutes, seconds = divmod(video.length_seconds, 60)
                hours, minutes = divmod(minutes, 60)
                parts.append(
                    f"{hours}:{minutes:02d}:{seconds:02d}"
                    if hours
                    else f"{minutes}:{seconds:02d}",
                )
            video.home_music_card = True
            video.card_subtitle_text = " · ".join(parts)

    if media_type == MediaTypes.GAME.value:
        _page_item_ids = {
            media.item_id
            for media in media_page.object_list
            if getattr(media, "item_id", None)
        }
        if _page_item_ids:
            for item_id, collection_platform in CollectionEntry.objects.filter(
                user=request.user,
                item_id__in=_page_item_ids,
            ).values_list("item_id", "resolution"):
                platform_value = str(collection_platform or "").strip()
                if platform_value:
                    collection_platforms_by_item_id[item_id].add(platform_value)
        for media in media_page.object_list:
            media.card_collection_platforms = sorted(
                collection_platforms_by_item_id.get(media.item_id, ()),
                key=str.lower,
            )
            media.display_platform = _resolve_display_platform(
                getattr(media, "item", None),
                collection_platforms_by_item_id,
                platform_filter,
            )

    if media_type in AUTHOR_FILTER_MEDIA_TYPES:
        annotate_media_authors(media_page.object_list)

    if filter_data is not None:
        filter_data.setdefault("departments", [])
        filter_data["show_group_by"] = group_by != "item" or media_type == MediaTypes.GAME.value

    media_groups = (
        _build_game_groups(
            media_page.object_list,
            group_by,
            sort_key=sort_filter,
            direction=direction,
        )
        if grouped_game_overview
        else []
    )
    group_params = request.GET.copy()
    group_params.pop("page", None)
    group_back_href = ""
    if game_group:
        group_params.pop("group", None)
        group_back_href = (
            f"{reverse('medialist', args=[route_media_type])}"
            f"?{group_params.urlencode()}"
        )
        if not current_game_group_name:
            current_game_group_name = game_group.split(":", 1)[-1]
    else:
        for group in media_groups:
            group_params["group"] = group["key"]
            group["href"] = (
                f"{reverse('medialist', args=[route_media_type])}"
                f"?{group_params.urlencode()}"
            )
            group["display_media"].card_tile_url = group["href"]
        group_params.pop("group", None)

    ungrouped_entries = [
        entry
        for group in media_groups
        if group["ungrouped"]
        for entry in group["entries"]
    ]
    try:
        ungrouped_page_number = int(request.GET.get("ug_page", 1))
    except (TypeError, ValueError):
        ungrouped_page_number = 1
    ungrouped_page = Paginator(ungrouped_entries, 32).get_page(
        max(ungrouped_page_number, 1)
    )
    ungrouped_next_url = ""
    if ungrouped_page.has_next():
        ungrouped_params = request.GET.copy()
        ungrouped_params.pop("page", None)
        ungrouped_params["ug_page"] = ungrouped_page.next_page_number()
        ungrouped_next_url = (
            f"{reverse('medialist', args=[route_media_type])}"
            f"?{ungrouped_params.urlencode()}"
        )

    if grouped_game_overview:
        result_count = sum(
            1 for group in media_groups if not group["ungrouped"]
        ) + len(ungrouped_entries)
    elif getattr(media_page, "paginator", None) is not None:
        result_count = media_page.paginator.count
    else:
        result_count = len(media_page)

    if tag_values and not _skip_generic_media_list:
        _log_tag_filter_result(
            request,
            media_type,
            tag_values,
            tag_mode,
            status_filter,
            media_page.paginator.count,
        )

    _layout_class = ".media-grid" if layout == "grid" else ".media-table"
    context = {
        "user": request.user,
        "media_type": route_media_type,
        "media_type_plural": app_tags.media_type_readable_plural(media_type).lower(),
        "media_list": media_page,
        "result_count": result_count,
        "library_page_model": {
            "title": app_tags.media_type_readable_plural(route_media_type),
            "count": result_count,
        },
        "current_layout": layout,
        "layout_class": _layout_class,
        "media_list_url": reverse("medialist", args=[route_media_type]),
        "filter_hx_target": (
            "#media-table-view" if layout == "table" else _layout_class
        )
        if media_page
        else "#empty_list",
        "current_group_by": group_by,
        "current_game_group": game_group,
        "current_game_group_name": current_game_group_name,
        "group_back_href": group_back_href,
        "group_by_choices": GAME_GROUP_BY_CHOICES,
        "group_by_label": dict(GAME_GROUP_BY_CHOICES).get(group_by, ""),
        "media_groups": media_groups,
        "ungrouped_page": ungrouped_page,
        "ungrouped_next_url": ungrouped_next_url,
        "current_sort": sort_filter,
        "current_direction": direction,
        "current_status": list(status_filter),
        "current_rating": rating_filter,
        "current_collection": collection_filter,
        "current_progress": progress_filter,
        "current_genre": genre_filter,
        "current_implied_genre": implied_genre_filter,
        "current_year": year_filter,
        "current_completed_date_from": ""
        if completed_date_within
        else completed_date_from,
        "current_completed_date_to": "" if completed_date_within else completed_date_to,
        "current_completed_date_within": completed_date_within,
        "current_completed_date_within_unit": completed_date_within_unit,
        "current_release": release_filter,
        "current_source": source_filter,
        "current_media_status": media_status_filter,
        "current_language": language_filter,
        "current_country": country_filter,
        "current_platform": platform_filter,
        "current_platform_values": list(platform_values),
        "current_platform_mode": platform_mode,
        "current_origin": origin_filter,
        "current_format": format_filter,
        "current_author": author_filter,
        "current_provider": provider_filter,
        "current_tag": list(tag_values),
        "current_tag_mode": tag_mode,
        # The filter menu's state, in the smart-rule vocabulary the shared
        # libraryFilterState() reads. A relative window is shown as chosen.
        # The filter menu's state for libraryFilterState(), with this page's
        # adjustments (persisted status, type-gated progress and implied genre).
        "media_list_filter_state": replace(
            parsed_filters,
            statuses=tuple(v for v in status_filter if v != MEDIA_LIST_NO_STATUS),
            include_no_status=MEDIA_LIST_NO_STATUS in status_filter,
            progress=progress_filter,
            implied_genre=implied_genre_filter,
        ).menu_state(),
        "enable_bulk_select": True,
        "sort_choices": sorted_media_sort_choices,
        "status_choices": status_choices,
        "rating_choices": MEDIA_RATING_CHOICES,
        "filter_data": filter_data,
        "is_artist_list": False,
        "is_album_list": False,
        "supports_critic_rating_sort": media_type in CRITIC_RATING_MEDIA_TYPES,
    }
    if comic_subview:
        context["current_subview"] = comic_subview

    # For music, show tracked artists instead of individual tracks
    # For podcasts, show tracked shows instead of individual episodes
    # This parallels TV which shows TV shows, not seasons/episodes
    if media_type == MediaTypes.PODCAST.value:
        from app.models import PodcastEpisode, PodcastShowTracker

        show_trackers = (
            PodcastShowTracker.objects.filter(user=request.user)
            .exclude(show__title__isnull=True)
            .exclude(show__title__exact="")
            .select_related("show")
        )

        # Apply status filter to shows
        if tracked_status_filter:
            show_trackers = show_trackers.filter(status__in=tracked_status_filter)

        # Apply search filter to shows
        if search_query:
            show_trackers = show_trackers.filter(show__title__icontains=search_query)
        show_trackers = show_trackers.filter(
            initial_condition(initial, name_field="show__title")
        )

        # A tag sits on the show's item (the show's uuid) or on one of its
        # episodes (the episode's uuid); either tags the show.
        if tag_included_ids is not None or tag_excluded_ids is not None:
            tagged_uuids = Item.objects.filter(
                pk__in=tag_included_ids
                if tag_included_ids is not None
                else tag_excluded_ids,
                media_type=MediaTypes.PODCAST.value,
            ).values("media_id")
            tagged_shows = Q(show__podcast_uuid__in=tagged_uuids) | Q(
                show_id__in=PodcastEpisode.objects.filter(
                    episode_uuid__in=tagged_uuids,
                ).values("show_id"),
            )
            if tag_included_ids is not None:
                show_trackers = show_trackers.filter(tagged_shows)
            else:
                show_trackers = show_trackers.exclude(tagged_shows)

        # Apply rating filter to shows
        if rating_filter == "rated":
            show_trackers = show_trackers.filter(score__isnull=False)
        elif rating_filter == "not_rated":
            show_trackers = show_trackers.filter(score__isnull=True)

        should_annotate_first_published = (
            release_filter != "all"
            or sort_filter == "release_date"
            or layout == "table"
        )
        if should_annotate_first_published:
            show_trackers = show_trackers.annotate(
                first_published=Min("show__episodes__published")
            )

        # Apply sorting
        if sort_filter == "title":
            order = "show__title" if direction == "asc" else "-show__title"
            show_trackers = show_trackers.order_by(order)
        elif sort_filter == "score":
            order = "score" if direction == "asc" else "-score"
            show_trackers = show_trackers.order_by(order, "show__title")
        elif sort_filter == "release_date":
            order = (
                F("first_published").asc(nulls_last=True)
                if direction == "asc"
                else F("first_published").desc(nulls_last=True)
            )
            show_trackers = show_trackers.order_by(order, "show__title")
        elif sort_filter == "date_added":
            order = "created_at" if direction == "asc" else "-created_at"
            show_trackers = show_trackers.order_by(order, "show__title")
        elif sort_filter == "start_date":
            order = "start_date" if direction == "asc" else "-start_date"
            show_trackers = show_trackers.order_by(order)
        else:
            # Default: most recently updated
            show_trackers = show_trackers.order_by("-updated_at")

        show_trackers_list = list(show_trackers)

        if release_filter != "all":
            today = timezone.localdate()
            show_trackers_list = [
                tracker
                for tracker in show_trackers_list
                if _matches_release_filter_value(
                    getattr(tracker, "first_published", None),
                    release_filter,
                    today,
                )
            ]

        def _build_podcast_filter_data(trackers):
            genres_set = set()
            languages_set = set()
            for tracker in trackers:
                show = tracker.show
                for genre in show.genres or []:
                    genre_value = str(genre).strip()
                    if genre_value:
                        genres_set.add(genre_value)
                language_value = (show.language or "").strip()
                if language_value:
                    languages_set.add(language_value)

            genres = sorted(genres_set, key=lambda value: value.lower())
            languages = [
                {
                    "value": value,
                    "label": value.upper()
                    if len(value) <= MAX_ISO_CODE_LENGTH
                    else value,
                }
                for value in sorted(languages_set)
            ]
            return {
                "genres": genres,
                "years": [],
                "sources": [],
                "languages": languages,
                "countries": [],
                "platforms": [],
                "origins": [],
                "show_languages": True,
                "show_countries": True,
                "show_platforms": False,
                "show_origins": False,
            }

        filter_data = _build_podcast_filter_data(show_trackers_list)

        if genre_filter:
            target_genre = _normalize_filter_value(genre_filter)
            show_trackers_list = [
                tracker
                for tracker in show_trackers_list
                if any(
                    _normalize_filter_value(genre) == target_genre
                    for genre in (tracker.show.genres or [])
                )
            ]

        if language_filter:
            target_language = _normalize_filter_value(language_filter)
            show_trackers_list = [
                tracker
                for tracker in show_trackers_list
                if _normalize_filter_value(tracker.show.language) == target_language
            ]

        # Convert show trackers to Media-like objects for standard templates
        # Create a simple adapter class to make trackers compatible with media components
        class PodcastShowAdapter:
            """Adapter to make PodcastShowTracker compatible with media components."""

            def __init__(self, tracker):
                self.tracker = tracker
                self.id = tracker.id
                self.status = tracker.status
                self.score = tracker.score
                self.start_date = tracker.start_date
                self.end_date = tracker.end_date
                self.notes = tracker.notes
                self.score_url = reverse(
                    "update_podcast_show_score", args=[tracker.id]
                )
                self.created_at = tracker.created_at
                self.updated_at = tracker.updated_at
                self.release_datetime = getattr(tracker, "first_published", None)

                # Reuse the home music card subtitle slot to show the author
                self.home_music_card = True
                self.card_subtitle_text = tracker.show.author or ""

                # Create a mock Item for compatibility with media components
                # Use the show's podcast_uuid as media_id for routing
                self.item, _ = Item.objects.get_or_create(
                    media_id=tracker.show.podcast_uuid,
                    source=tracker.show.source,
                    media_type=MediaTypes.PODCAST.value,
                    defaults={
                        "title": tracker.show.title,
                        "image": tracker.show.image or settings.IMG_NONE,
                    },
                )
                # Update item if show data changed
                # Always sync image to ensure it matches the show (especially after artwork fetch)
                show_image = tracker.show.image or settings.IMG_NONE
                if (
                    self.item.title != tracker.show.title
                    or self.item.image != show_image
                ):
                    self.item.title = tracker.show.title
                    self.item.image = show_image
                    self.item.save(update_fields=["title", "image"])

        # Convert trackers to adapters
        adapted_media = [PodcastShowAdapter(tracker) for tracker in show_trackers_list]

        # Paginate adapted media
        media_paginator = Paginator(adapted_media, 32)
        media_page = media_paginator.get_page(page)

        context = {
            "user": request.user,
            "media_list": media_page,
            "media_type": media_type,
            "media_type_plural": app_tags.media_type_readable_plural(
                media_type
            ).lower(),
            "current_layout": layout,
            "layout_class": ".media-grid" if layout == "grid" else ".media-table",
            "media_list_url": reverse("medialist", args=[media_type]),
            "filter_hx_target": (
                ".media-grid" if layout == "grid" else "#media-table-view"
            )
            if media_page
            else "#empty_list",
            "current_sort": sort_filter,
            "current_direction": direction,
            "current_status": list(status_filter),
            "current_rating": rating_filter,
            "current_collection": collection_filter,
            "current_genre": genre_filter,
            "current_implied_genre": implied_genre_filter,
            "current_year": year_filter,
            "current_completed_date_from": completed_date_from,
            "current_completed_date_to": completed_date_to,
            "current_release": release_filter,
            "current_source": source_filter,
            "current_media_status": media_status_filter,
            "current_language": language_filter,
            "current_country": country_filter,
            "current_platform": platform_filter,
            "current_platform_values": list(platform_values),
            "current_platform_mode": platform_mode,
            "current_origin": origin_filter,
            "enable_bulk_select": True,
            "sort_choices": sorted_media_sort_choices,
            "status_choices": status_choices,
            "rating_choices": MEDIA_RATING_CHOICES,
            "search_query": search_query,
            "filter_data": filter_data,
            "is_artist_list": False,
            "is_album_list": False,
            "supports_critic_rating_sort": False,
        }
        context["enable_bulk_select"] = False
        context["alphabet_other_initial"] = first_other_initial(
            PodcastShowTracker.objects.filter(user=request.user), name_field="show__title"
        )

    if media_type == MediaTypes.MUSIC.value:
        from app.models import AlbumTracker

        context["current_subview"] = music_subview

        if music_subview == "albums":
            from users.card_metadata import extra_query_enabled

            album_related = ["album"]
            album_prefetches = []
            if extra_query_enabled(request.user, MediaTypes.MUSIC.value, "artist"):
                album_related.append("album__artist")
                album_prefetches.append("album__artist_credits__artist")
            album_trackers = AlbumTracker.objects.filter(
                user=request.user
            ).select_related(*album_related)
            if album_prefetches:
                album_trackers = album_trackers.prefetch_related(*album_prefetches)

            if tracked_status_filter:
                album_trackers = album_trackers.filter(status__in=tracked_status_filter)

            if search_query:
                album_trackers = album_trackers.filter(
                    album__title__icontains=search_query
                )

            if rating_filter == "rated":
                album_trackers = album_trackers.filter(score__isnull=False)
            elif rating_filter == "not_rated":
                album_trackers = album_trackers.filter(score__isnull=True)

            album_trackers = album_trackers.filter(
                initial_condition(initial, name_field="album__title")
            )

            should_annotate_release_date = (
                release_filter != "all"
                or sort_filter == "release_date"
                or layout == "table"
            )
            if should_annotate_release_date:
                album_trackers = album_trackers.annotate(
                    first_release_date=F("album__release_date")
                )

            if sort_filter == "title":
                order = "album__title" if direction == "asc" else "-album__title"
                album_trackers = album_trackers.order_by(order)
            elif sort_filter == "score":
                order = "score" if direction == "asc" else "-score"
                album_trackers = album_trackers.order_by(order, "album__title")
            elif sort_filter == "release_date":
                order = (
                    F("album__release_date").asc(nulls_last=True)
                    if direction == "asc"
                    else F("album__release_date").desc(nulls_last=True)
                )
                album_trackers = album_trackers.order_by(order, "album__title")
            elif sort_filter == "date_added":
                order = "created_at" if direction == "asc" else "-created_at"
                album_trackers = album_trackers.order_by(order, "album__title")
            elif sort_filter == "start_date":
                order = "start_date" if direction == "asc" else "-start_date"
                album_trackers = album_trackers.order_by(order)
            else:
                album_trackers = album_trackers.order_by("-updated_at")

            album_trackers_list = list(album_trackers)

            if release_filter != "all":
                today = timezone.localdate()
                album_trackers_list = [
                    tracker
                    for tracker in album_trackers_list
                    if _matches_release_filter_value(
                        getattr(tracker, "first_release_date", None),
                        release_filter,
                        today,
                    )
                ]

            def _build_album_filter_data(trackers):
                genres_set = set()
                for tracker in trackers:
                    for genre in tracker.album.genres or []:
                        genre_value = str(genre).strip()
                        if genre_value:
                            genres_set.add(genre_value)
                genres = sorted(genres_set, key=lambda value: value.lower())
                return {
                    "genres": genres,
                    "years": [],
                    "sources": [],
                    "languages": [],
                    "countries": [],
                    "platforms": [],
                    "origins": [],
                    "show_languages": False,
                    "show_countries": False,
                    "show_platforms": False,
                    "show_origins": False,
                    "subview": music_subview,
                }

            filter_data = _build_album_filter_data(album_trackers_list)

            if genre_filter:
                target_genre = _normalize_filter_value(genre_filter)
                album_trackers_list = [
                    tracker
                    for tracker in album_trackers_list
                    if any(
                        _normalize_filter_value(genre) == target_genre
                        for genre in (tracker.album.genres or [])
                    )
                ]

            album_paginator = Paginator(album_trackers_list, 32)
            album_page = album_paginator.get_page(page)

            context["media_list"] = album_page
            context["is_artist_list"] = False
            context["is_album_list"] = True
            context["filter_data"] = filter_data
            context["media_type_plural"] = "albums"
            context["alphabet_other_initial"] = first_other_initial(
                AlbumTracker.objects.filter(user=request.user), name_field="album__title"
            )

        elif music_subview == "tracks":
            context["is_artist_list"] = False
            context["is_album_list"] = False
            context["media_type_plural"] = "tracks"

        if music_subview == "artists":
            context.update(
                _build_artist_list_context(
                    request.user,
                    tracked_status_filter=tracked_status_filter,
                    search_query=search_query,
                    initial=initial,
                    rating_filter=rating_filter,
                    release_filter=release_filter,
                    genre_filter=genre_filter,
                    origin_filter=origin_filter,
                    sort_filter=sort_filter,
                    direction=direction,
                    layout=layout,
                    page=page,
                    is_pagination_req=page > 1,
                )
            )

        context["enable_bulk_select"] = music_subview == "tracks"

    if not _skip_generic_media_list and not helpers.is_htmx_fragment(request):
        if use_sql_media_pagination and initial in {"", "other"}:
            # Reuse the already evaluated engine, including provider deduplication
            # and Python predicates; only the representative title is read again.
            matches = engine.matches() if engine.uses_sql else engine.ranked_ids()
            context["alphabet_other_initial"] = first_other_initial(
                Item.objects.filter(pk__in=matches), request.user
            )
        else:
            context["alphabet_other_initial"] = query_other_initial(
                request.user, from_media_list_filters(sql_media_filters, (media_type,))
            )

    if _skip_generic_media_list:
        # Container trackers replace the item page above; expose their own count
        # and empty target rather than the skipped generic library's values.
        container_page = context["media_list"]
        context["result_count"] = container_page.paginator.count
        context["filter_hx_target"] = (
            ".media-grid" if layout == "grid" else "#media-table-view"
        ) if container_page else "#empty_list"

    if context.get("enable_bulk_select"):
        context["bulk_action_data"] = build_bulk_action_data(
            request.user,
            request=request,
            status_url=reverse("bulk_status_update"),
            list_url=reverse("bulk_list_add"),
            collection_url=reverse("bulk_collection_quick_add"),
            tag_url=reverse("tag_bulk_toggle"),
            select_all_url=(
                reverse("medialist", args=[route_media_type]) + "?bulk_ids=1"
                if use_sql_media_pagination
                else ""
            ),
        )

    if context.get("is_artist_list", False):
        table_type = "artist"
    elif context.get("is_album_list", False):
        table_type = "album"
    else:
        table_type = "media"
    context["table_type"] = table_type
    if layout == "table":
        context["resolved_columns"] = resolve_columns(
            media_type,
            sort_filter,
            request.user,
            table_type,
        )
        context["column_config"] = resolve_column_config(
            media_type,
            sort_filter,
            request.user,
            table_type,
        )
        context["default_column_config"] = resolve_default_column_config(
            media_type,
            sort_filter,
            table_type,
        )
        if settings.DEBUG:
            prefs = (request.user.table_column_prefs or {}).get(media_type, {})
            pref_order = prefs.get("order", []) if isinstance(prefs, dict) else []
            pref_hidden = prefs.get("hidden", []) if isinstance(prefs, dict) else []
            resolved_keys = [column.key for column in context["resolved_columns"]]
            logger.info(
                (
                    "[COLUMN_DEBUG] media_list_resolved user=%s media_type=%s "
                    "table_type=%s sort=%s page=%s hx=%s pref_order=%s "
                    "pref_hidden=%s resolved_keys=%s"
                ),
                request.user.id,
                media_type,
                table_type,
                sort_filter,
                page,
                bool(request.headers.get("HX-Request")),
                pref_order,
                pref_hidden,
                resolved_keys,
            )

        media_page = context.get("media_list")
        next_url = (
            table_json_url(request, media_page.next_page_number())
            if media_page is not None and media_page.has_next()
            else ""
        )
        context["table_browser_props"] = build_table_browser_props(
            rows=list(media_page.object_list) if media_page is not None else [],
            resolved_columns=context["resolved_columns"],
            column_config=context["column_config"],
            default_column_config=context["default_column_config"],
            user=request.user,
            table_type=table_type,
            media_type=media_type,
            current_sort=sort_filter,
            current_direction=direction,
            update_url=reverse("medialist_columns", args=[route_media_type]),
            refresh_url=context["media_list_url"],
            refresh_target=".media-table",
            refresh_include="#filter-form",
            csrf_token=get_token(request),
            next_url=next_url,
        )
        if request.GET.get("table_json") == "1":
            return JsonResponse(
                {
                    "rows": context["table_browser_props"]["table"]["rows"],
                    "nextUrl": next_url,
                    "resultCount": context["result_count"],
                },
            )

    # Handle HTMX requests for partial updates (boosted navigation still
    # sends HX-Request but needs the full page)
    if (
        grouped_game_overview
        and request.GET.get("ug_page")
        and helpers.is_htmx_fragment(request)
    ):
        from django.template.loader import render_to_string

        html = render_to_string(
            "app/components/game_grouped_ungrouped_page.html",
            context,
            request=request,
        )
        return HttpResponse(html)

    if helpers.is_htmx_fragment(request):
        is_artist_list = context.get("is_artist_list", False)
        is_album_list = context.get("is_album_list", False)
        # Changing from empty list to a status with items
        if request.headers.get("HX-Target") == "empty_list":
            media_page = context.get("media_list")
            if media_page is not None and not media_page.object_list:
                return HttpResponse(status=204)
            response = HttpResponse()
            redirect_url = reverse("medialist", args=[media_type])
            if initial:
                redirect_url += "?" + request.GET.urlencode()
            response["HX-Redirect"] = redirect_url
            return response

        # Check if this is a pagination request (has page parameter and is not the first page)
        is_pagination = page > 1
        context["is_pagination"] = bool(is_pagination)

        if layout == "grid":
            if is_artist_list:
                template_name = "app/components/artist_grid_items.html"
            elif is_album_list:
                template_name = "app/components/album_list_grid_items.html"
            else:
                template_name = "app/components/media_grid_items.html"
        else:
            template_name = "app/components/table_items.html"

        from django.template.loader import render_to_string

        if initial and not context.get("media_list") and not is_pagination:
            template_name = "app/components/alphabet_results_empty.html"
            context["alphabet_table_fragment"] = layout == "table"
        html = render_to_string(template_name, context, request=request)

        media_page = context.get("media_list")
        if (
            media_page is not None
            and getattr(media_page, "paginator", None) is not None
        ):
            total_count = media_page.paginator.count
        else:
            try:
                total_count = len(media_page) if media_page is not None else 0
            except TypeError:
                total_count = 0

        response = HttpResponse(html)
        response["HX-Trigger"] = json.dumps(
            {"resultCountUpdated": {"count": total_count}}
        )
        return response

    context["is_pagination"] = False
    template_name = "app/media_list.html"

    return render(request, template_name, context)


@require_POST
def update_table_columns(request, media_type):
    """Persist table column order/visibility and trigger table refresh."""
    if not request.user.is_authenticated:
        return HttpResponseBadRequest("Authentication required")

    table_type = request.POST.get("table_type", "media")
    if table_type not in {"media", "artist", "album"}:
        table_type = "media"
    if media_type != MediaTypes.MUSIC.value:
        table_type = "media"

    raw_order = request.POST.get("order", "[]")
    raw_hidden = request.POST.get("hidden", "[]")

    previous_prefs = (request.user.table_column_prefs or {}).get(media_type, {})
    previous_order = (
        previous_prefs.get("order", []) if isinstance(previous_prefs, dict) else []
    )
    previous_hidden = (
        previous_prefs.get("hidden", []) if isinstance(previous_prefs, dict) else []
    )

    try:
        parsed_order = json.loads(raw_order)
    except json.JSONDecodeError:
        parsed_order = []
    try:
        parsed_hidden = json.loads(raw_hidden)
    except json.JSONDecodeError:
        parsed_hidden = []

    order = (
        [value for value in parsed_order if isinstance(value, str)]
        if isinstance(parsed_order, list)
        else []
    )
    hidden = (
        [value for value in parsed_hidden if isinstance(value, str)]
        if isinstance(parsed_hidden, list)
        else []
    )

    current_sort = request.POST.get("sort") or getattr(
        request.user, f"{media_type}_sort", MediaSortChoices.SCORE
    )
    if (
        (current_sort == "time_left" and media_type != MediaTypes.TV.value)
        or (
            current_sort == "runtime"
            and media_type
            not in {
                MediaTypes.MOVIE.value,
                MediaTypes.TV.value,
                MediaTypes.ANIME.value,
            }
        )
        or (current_sort == "time_to_beat" and media_type != MediaTypes.GAME.value)
        or (current_sort == "platform" and media_type != MediaTypes.GAME.value)
        or (
            current_sort == "plays"
            and media_type
            not in {
                MediaTypes.MOVIE.value,
                MediaTypes.TV.value,
                MediaTypes.ANIME.value,
            }
        )
        or (
            current_sort == "time_watched"
            and media_type
            not in {
                MediaTypes.MOVIE.value,
                MediaTypes.TV.value,
                MediaTypes.ANIME.value,
            }
        )
        or (
            current_sort == "next_episode_air_date"
            and media_type
            not in {
                MediaTypes.TV.value,
                MediaTypes.SEASON.value,
                MediaTypes.ANIME.value,
            }
        )
        or (
            current_sort == "critic_rating"
            and media_type
            in {
                MediaTypes.MUSIC.value,
                MediaTypes.PODCAST.value,
            }
        )
        or (
            current_sort == "popularity"
            and media_type
            not in {
                MediaTypes.MOVIE.value,
                MediaTypes.TV.value,
                MediaTypes.ANIME.value,
            }
        )
    ):
        current_sort = "title"

    if settings.DEBUG:
        logger.info(
            (
                "[COLUMN_DEBUG] update_request user=%s media_type=%s table_type=%s "
                "sort=%s previous_order=%s previous_hidden=%s requested_order=%s "
                "requested_hidden=%s raw_order=%s raw_hidden=%s"
            ),
            request.user.id,
            media_type,
            table_type,
            current_sort,
            previous_order,
            previous_hidden,
            order,
            hidden,
            raw_order,
            raw_hidden,
        )

    clean_order, clean_hidden = sanitize_column_prefs(
        media_type=media_type,
        current_sort=current_sort,
        user=request.user,
        table_type=table_type,
        order=order,
        hidden=hidden,
    )

    request.user.update_column_prefs(
        media_type=media_type,
        table_type=table_type,
        order=clean_order,
        hidden=clean_hidden,
    )

    if settings.DEBUG:
        logger.info(
            (
                "[COLUMN_DEBUG] update_sanitized user=%s media_type=%s table_type=%s "
                "sanitized_order=%s sanitized_hidden=%s"
            ),
            request.user.id,
            media_type,
            table_type,
            clean_order,
            clean_hidden,
        )

        poll_results = []
        for attempt in range(1, 4):
            request.user.refresh_from_db(fields=["table_column_prefs"])
            polled_prefs = (request.user.table_column_prefs or {}).get(media_type, {})
            polled_order = (
                polled_prefs.get("order", []) if isinstance(polled_prefs, dict) else []
            )
            polled_hidden = (
                polled_prefs.get("hidden", []) if isinstance(polled_prefs, dict) else []
            )
            resolved_keys = [
                column.key
                for column in resolve_columns(
                    media_type,
                    current_sort,
                    request.user,
                    table_type,
                )
            ]
            poll_results.append(
                {
                    "attempt": attempt,
                    "order": polled_order,
                    "hidden": polled_hidden,
                    "resolved": resolved_keys,
                },
            )
            if attempt < DEBUG_POLL_ATTEMPTS:
                time.sleep(0.05)

        logger.info(
            "[COLUMN_DEBUG] update_poll user=%s media_type=%s table_type=%s polls=%s",
            request.user.id,
            media_type,
            table_type,
            poll_results,
        )

    response = HttpResponse(status=204)
    response["HX-Trigger"] = json.dumps({"refreshTableColumns": True})
    return response


def toggle_pinned_provider(request):
    """Pin or unpin a watch-provider name, promoting/demoting it in filter menus."""
    if not request.user.is_authenticated:
        return HttpResponseBadRequest("Authentication required")

    provider_name = request.POST.get("provider_name", "").strip()
    if not provider_name:
        return HttpResponseBadRequest("provider_name is required")

    request.user.toggle_pinned_provider(provider_name)

    return HttpResponse(status=204)
