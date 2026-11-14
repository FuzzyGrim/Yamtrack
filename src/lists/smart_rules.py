"""Smart list rule normalization, option building, and item matching."""

from __future__ import annotations

import dataclasses
import datetime
import logging
from collections.abc import Iterable
from itertools import batched

from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.db import connection
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from app.models import CollectionEntry, Item, MediaTypes, Sources, Status
from app.providers import tmdb

logger = logging.getLogger(__name__)

SMART_FILTER_KEYS = (
    "status",
    "rating",
    "rating_min",
    "rating_max",
    "collection",
    "genre",
    "implied_genre",
    "year",
    "completed_date_from",
    "completed_date_to",
    "completed_date_within",
    "completed_date_within_unit",
    "release",
    "release_date_from",
    "release_date_to",
    "release_date_within",
    "release_date_within_unit",
    "date_added_from",
    "date_added_to",
    "date_added_within",
    "date_added_within_unit",
    "source",
    "search",
    "sort",
    "sort_direction",
    "language",
    "country",
    "platform",
    "origin",
    "format",
    "author",
    "provider",
    "tag",
    "tag_mode",
    "list",
    # Which evaluation semantics the rules were saved under; missing means
    # the pre-engine semantics (see app.library_query.adapters).
    "semantics_version",
)

TAG_MODE_CHOICES = {"and", "or", "not"}

SMART_FILTER_DEFAULTS = {
    "status": [],
    "rating": "all",
    "rating_min": "",
    "rating_max": "",
    "collection": "all",
    "genre": "",
    "implied_genre": "",
    "year": "",
    "completed_date_from": "",
    "completed_date_to": "",
    "completed_date_within": "",
    "completed_date_within_unit": "days",
    "release": "all",
    "release_date_from": "",
    "release_date_to": "",
    "release_date_within": "",
    "release_date_within_unit": "days",
    "date_added_from": "",
    "date_added_to": "",
    "date_added_within": "",
    "date_added_within_unit": "days",
    "source": "",
    "search": "",
    "sort": "",
    "sort_direction": "",
    "language": "",
    "country": "",
    "platform": "",
    "origin": "",
    "format": "",
    "author": "",
    "provider": "",
    "tag": [],
    "tag_mode": "or",
    "list": [],
    "semantics_version": "",
}

MAX_RATING = 10.0

# Language/country/origin codes at or below this length (e.g. ISO 639-1
# language codes, ISO 3166 country codes) are displayed uppercased.
SHORT_CODE_MAX_LENGTH = 3

# "In the last N <unit>", stored relative so a saved smart list keeps meaning
# the same window as time passes, and resolved to dates at evaluation time.
RELATIVE_DATE_UNITS = {"days", "weeks", "months", "years"}
MAX_RELATIVE_DATE_AMOUNT = 999
RELATIVE_DATE_FIELDS = ("completed_date", "release_date", "date_added")
# Ordered for the UI; the template renders these rather than hardcoding labels,
# so the vocabulary has one definition.
RELATIVE_DATE_UNIT_CHOICES = (
    ("days", "Days"),
    ("weeks", "Weeks"),
    ("months", "Months"),
    ("years", "Years"),
)

RATING_CHOICES = {"all", "rated", "not_rated"}
COLLECTION_CHOICES = {"all", "collected", "not_collected"}
RELEASE_CHOICES = {"all", "released", "not_released"}
SHOW_COLLECTION_MEDIA_TYPES = {
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
    MediaTypes.SEASON.value,
}
# Offered in the media-type picker, but never pulled in by an implicit "all
# types" rule: a list that names no media types would otherwise materialise
# every episode in the library on its next sync. Opt in by ticking them.
IMPLICIT_ALL_EXCLUDED_MEDIA_TYPES = {
    MediaTypes.SEASON.value,
    MediaTypes.EPISODE.value,
}
# Season/episode rules ride on the show libraries rather than on their own
# sidebar preference, so a user who hides Seasons can still build a smart list
# at that granularity.
SHOW_GRANULARITY_MEDIA_TYPES = {
    MediaTypes.SEASON.value,
    MediaTypes.EPISODE.value,
}
SHOW_GRANULARITY_PARENTS = {MediaTypes.TV.value, MediaTypes.ANIME.value}
LANGUAGE_MEDIA_TYPES = {
    MediaTypes.TV.value,
    MediaTypes.MOVIE.value,
    MediaTypes.ANIME.value,
    MediaTypes.PODCAST.value,
}
COUNTRY_MEDIA_TYPES = LANGUAGE_MEDIA_TYPES
PLATFORM_MEDIA_TYPES = {MediaTypes.GAME.value}
PROVIDER_MEDIA_TYPES = {
    MediaTypes.TV.value,
    MediaTypes.MOVIE.value,
    MediaTypes.ANIME.value,
}
ORIGIN_MEDIA_TYPES = {MediaTypes.MUSIC.value}
FORMAT_MEDIA_TYPES = {
    MediaTypes.BOOK.value,
    MediaTypes.MANGA.value,
    MediaTypes.COMIC.value,
}
# A video's channel is stored as its author, so videos filter by it too.
AUTHOR_MEDIA_TYPES = FORMAT_MEDIA_TYPES | {MediaTypes.VIDEO.value}


def _normalize_filter_value(value) -> str:
    return str(value or "").strip().lower()


def _normalize_decimal_value(value) -> str:
    """Return a rating value string in [0.0, 10.0] or empty string."""
    if not value and value != 0:
        return ""
    try:
        normalized = round(float(str(value).strip()), 1)
    except (TypeError, ValueError):
        return ""
    if 0.0 <= normalized <= MAX_RATING:
        return str(normalized)
    return ""


def _normalize_date_filter(value) -> str:
    """Return a YYYY-MM-DD string or empty string."""
    if not value:
        return ""
    normalized = str(value).strip()
    try:
        datetime.date.fromisoformat(normalized)
    except ValueError:
        return ""
    return normalized


def _normalize_relative_amount(value) -> str:
    """Return a positive whole-number window size, or empty string."""
    if value in (None, ""):
        return ""
    try:
        amount = int(str(value).strip())
    except (TypeError, ValueError):
        return ""
    if 1 <= amount <= MAX_RELATIVE_DATE_AMOUNT:
        return str(amount)
    return ""


def normalize_relative_unit(value) -> str:
    """Return a supported relative-window unit, defaulting to days."""
    unit = str(value or "").strip().lower()
    return unit if unit in RELATIVE_DATE_UNITS else "days"


def resolve_relative_date_windows(rules: dict, today=None) -> dict:
    """Expand "in the last N units" rules into concrete from/to dates.

    Storage stays relative; this runs at evaluation time so a list saved as
    "completed in the last week" still means that a month from now. A relative
    window wins over any absolute from/to on the same field - the UI clears one
    when the other is set, but a hand-built payload could carry both.
    """
    if not any(rules.get(f"{field}_within") for field in RELATIVE_DATE_FIELDS):
        return rules

    resolved = dict(rules)
    today = today or timezone.localdate()
    for field in RELATIVE_DATE_FIELDS:
        amount = _normalize_relative_amount(resolved.get(f"{field}_within"))
        if not amount:
            continue
        unit = normalize_relative_unit(resolved.get(f"{field}_within_unit"))
        start = today - relativedelta(**{unit: int(amount)})
        resolved[f"{field}_from"] = start.isoformat()
        resolved[f"{field}_to"] = today.isoformat()
    return resolved


def _release_date_from_value(value):
    if value is None:
        return None
    if isinstance(value, datetime.date) and not hasattr(value, "hour"):
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


def _matches_release_filter_value(release_value, filter_value: str, today):
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


def _extract_languages(item: Item) -> list[str]:
    languages = getattr(item, "languages", None) or []
    if isinstance(languages, list):
        return [str(value).strip() for value in languages if str(value).strip()]
    language_value = str(languages).strip()
    return [language_value] if language_value else []


def _extract_country(item: Item) -> str:
    country = getattr(item, "country", "")
    return str(country).strip()


def _extract_platforms(item: Item) -> list[str]:
    platforms = getattr(item, "platforms", None) or []
    if isinstance(platforms, list):
        return [str(value).strip() for value in platforms if str(value).strip()]
    platform_value = str(platforms).strip()
    return [platform_value] if platform_value else []


def _extract_item_providers(item: Item, region) -> list[str] | None:
    """Return watch-provider names for an item, or None if region is unset."""
    return tmdb.item_watch_provider_names(item, region)


def _extract_authors(item: Item) -> list[str]:
    authors = getattr(item, "authors", None) or []
    if not isinstance(authors, list):
        authors = [authors]

    normalized: list[str] = []
    for raw_author in authors:
        if isinstance(raw_author, dict):
            author_name = (
                raw_author.get("name")
                or raw_author.get("person")
                or raw_author.get("author")
            )
        else:
            author_name = raw_author
        author_value = str(author_name).strip() if author_name else ""
        if author_value:
            normalized.append(author_value)
    return normalized


def _payload_get(payload, key: str, default=""):
    if hasattr(payload, "get"):
        return payload.get(key, default)
    if isinstance(payload, dict):
        return payload.get(key, default)
    return default


def _payload_getlist(payload, key: str) -> list[str]:
    if hasattr(payload, "getlist"):
        return [str(value) for value in payload.getlist(key)]

    if isinstance(payload, dict):
        value = payload.get(key)
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, Iterable):
            return [str(entry) for entry in value]
        return [str(value)]

    return []


def _valid_linked_list_ids(owner, raw_values: list[str]) -> list[int]:
    """Return ids from raw_values that are non-smart lists owner can access."""
    candidate_ids = {int(value) for value in raw_values if str(value).strip().isdigit()}
    if not candidate_ids or not owner:
        return []

    from lists.models import CustomList

    return list(
        CustomList.objects.filter(id__in=candidate_ids, is_smart=False)
        .filter(Q(owner=owner) | Q(collaborators=owner))
        .distinct()
        .values_list("id", flat=True),
    )


def get_available_media_types(owner) -> list[str]:
    """Return enabled media types that can participate in smart rules."""
    if owner and hasattr(owner, "get_enabled_media_types"):
        enabled = list(owner.get_enabled_media_types())
    else:
        enabled = list(MediaTypes.values)

    # Seasons and episodes follow the show libraries, not the sidebar
    # preference: tracking "what I just watched" at episode granularity is a
    # list concern, and hiding Seasons from the sidebar should not remove it.
    if SHOW_GRANULARITY_PARENTS & set(enabled):
        enabled += sorted(SHOW_GRANULARITY_MEDIA_TYPES)

    # Remove duplicates while preserving order.
    deduped = []
    seen = set()
    for media_type in enabled:
        if media_type not in MediaTypes.values:
            continue
        if media_type in seen:
            continue
        seen.add(media_type)
        deduped.append(media_type)
    return deduped


def normalize_rule_payload(payload, owner):
    """Normalize and validate smart-rule payload for persistence/matching."""
    available_media_types = get_available_media_types(owner)

    selected_media_types = _payload_getlist(payload, "media_types") or _payload_getlist(
        payload,
        "type",
    )
    normalized_media_types = []
    seen = set()
    for media_type in selected_media_types:
        value = str(media_type).strip().lower()
        if value not in available_media_types:
            continue
        if value in seen:
            continue
        seen.add(value)
        normalized_media_types.append(value)

    status_values = _payload_getlist(payload, "status")
    if not status_values:
        legacy_status = str(_payload_get(payload, "status", "") or "").strip()
        if legacy_status and legacy_status.lower() != "all":
            status_values = [legacy_status]
    normalized_statuses = []
    seen_statuses = set()
    for value in status_values:
        value = str(value).strip()  # noqa: PLW2901  # deliberate in-loop normalisation
        if not value or value.lower() == "all" or value not in Status.values:
            continue
        if value in seen_statuses:
            continue
        seen_statuses.add(value)
        normalized_statuses.append(value)

    rating = str(_payload_get(payload, "rating", "all") or "all").strip().lower()
    if rating not in RATING_CHOICES:
        rating = "all"
    rating_min = _normalize_decimal_value(_payload_get(payload, "rating_min", ""))
    rating_max = _normalize_decimal_value(_payload_get(payload, "rating_max", ""))

    collection = (
        str(_payload_get(payload, "collection", "all") or "all").strip().lower()
    )
    if collection not in COLLECTION_CHOICES:
        collection = "all"

    release = str(_payload_get(payload, "release", "all") or "all").strip().lower()
    if release not in RELEASE_CHOICES:
        release = "all"
    release_date_from = _normalize_date_filter(
        _payload_get(payload, "release_date_from", "")
    )
    release_date_to = _normalize_date_filter(
        _payload_get(payload, "release_date_to", "")
    )
    date_added_from = _normalize_date_filter(
        _payload_get(payload, "date_added_from", "")
    )
    date_added_to = _normalize_date_filter(_payload_get(payload, "date_added_to", ""))
    completed_date_from = _normalize_date_filter(
        _payload_get(payload, "completed_date_from", "")
    )
    completed_date_to = _normalize_date_filter(
        _payload_get(payload, "completed_date_to", "")
    )
    relative_windows = {}
    for field in RELATIVE_DATE_FIELDS:
        amount = _normalize_relative_amount(
            _payload_get(payload, f"{field}_within", "")
        )
        relative_windows[f"{field}_within"] = amount
        relative_windows[f"{field}_within_unit"] = normalize_relative_unit(
            _payload_get(payload, f"{field}_within_unit", ""),
        )
    # A relative window and an absolute range on the same field are mutually
    # exclusive; keeping both would leave the stored rule ambiguous.
    if relative_windows["completed_date_within"]:
        completed_date_from = completed_date_to = ""
    if relative_windows["release_date_within"]:
        release_date_from = release_date_to = ""
    if relative_windows["date_added_within"]:
        date_added_from = date_added_to = ""

    year = str(_payload_get(payload, "year", "") or "").strip().lower()
    if year and year != "unknown" and not year.isdigit():
        year = ""

    source = str(_payload_get(payload, "source", "") or "").strip().lower()
    if source and source not in Sources.values:
        source = ""
    sort = str(_payload_get(payload, "sort", "") or "").strip()
    sort_direction = (
        str(_payload_get(payload, "sort_direction", "") or "").strip().lower()
    )
    if sort_direction not in {"asc", "desc"}:
        sort_direction = ""

    tag_values = [
        str(value).strip()
        for value in _payload_getlist(payload, "tag")
        if str(value).strip()
    ]
    tag_mode = str(_payload_get(payload, "tag_mode", "") or "").strip().lower()
    if not tag_values:
        legacy_tag_exclude = str(_payload_get(payload, "tag_exclude", "") or "").strip()
        if legacy_tag_exclude:
            tag_values = [legacy_tag_exclude]
            tag_mode = "not"
    if tag_mode not in TAG_MODE_CHOICES:
        tag_mode = "or"
    seen_tags = set()
    deduped_tags = []
    for value in tag_values:
        key = value.lower()
        if key in seen_tags:
            continue
        seen_tags.add(key)
        deduped_tags.append(value)

    list_ids = _valid_linked_list_ids(owner, _payload_getlist(payload, "list"))
    semantics_version = str(_payload_get(payload, "semantics_version", "") or "").strip()
    if not semantics_version.isdigit():
        semantics_version = ""

    return {
        "media_types": normalized_media_types,
        "status": normalized_statuses,
        "rating": rating,
        "rating_min": rating_min,
        "rating_max": rating_max,
        "collection": collection,
        "genre": str(_payload_get(payload, "genre", "") or "").strip(),
        "implied_genre": str(_payload_get(payload, "implied_genre", "") or "").strip(),
        "year": year,
        "completed_date_from": completed_date_from,
        "completed_date_to": completed_date_to,
        "release": release,
        "release_date_from": release_date_from,
        "release_date_to": release_date_to,
        "date_added_from": date_added_from,
        "date_added_to": date_added_to,
        **relative_windows,
        "source": source,
        "search": str(_payload_get(payload, "search", "") or "").strip(),
        "sort": sort,
        "sort_direction": sort_direction,
        "language": str(_payload_get(payload, "language", "") or "").strip(),
        "country": str(_payload_get(payload, "country", "") or "").strip(),
        "platform": str(_payload_get(payload, "platform", "") or "").strip(),
        "origin": str(_payload_get(payload, "origin", "") or "").strip(),
        "format": str(_payload_get(payload, "format", "") or "").strip(),
        "author": str(_payload_get(payload, "author", "") or "").strip(),
        "provider": str(_payload_get(payload, "provider", "") or "").strip(),
        "tag": deduped_tags,
        "tag_mode": tag_mode,
        "list": list_ids,
        "semantics_version": semantics_version,
    }


def saved_filters(normalized_rules: dict, custom_list) -> dict:
    """Return the rules to store for a list, keeping its semantics version.

    Editing a list's rules never changes how the list is evaluated; only a
    newly created smart list starts on the current semantics.
    """
    filters = {
        key: normalized_rules.get(key, SMART_FILTER_DEFAULTS[key])
        for key in SMART_FILTER_KEYS
    }
    filters["semantics_version"] = str(
        (custom_list.smart_filters or {}).get("semantics_version") or "",
    )
    return filters


def normalize_list_rules(custom_list) -> dict:
    """Return normalized rules for a smart list, including excluded media types."""
    normalized_rules = normalize_rule_payload(
        {
            "media_types": custom_list.smart_media_types or [],
            **(custom_list.smart_filters or {}),
        },
        custom_list.owner,
    )

    excluded_media_types = {
        media_type
        for media_type in (custom_list.smart_excluded_media_types or [])
        if media_type in MediaTypes.values
    }
    if excluded_media_types:
        if normalized_rules["media_types"]:
            normalized_rules["media_types"] = [
                media_type
                for media_type in normalized_rules["media_types"]
                if media_type not in excluded_media_types
            ]
        else:
            normalized_rules["media_types"] = [
                media_type
                for media_type in get_available_media_types(custom_list.owner)
                if media_type not in excluded_media_types
                and media_type not in IMPLICIT_ALL_EXCLUDED_MEDIA_TYPES
            ]

    return normalized_rules


def _base_media_queryset(
    owner,
    media_type: str,
    status_filter: list[str] | str = "all",
    search_query: str = "",
    date_added_from: str = "",
    date_added_to: str = "",
    completed_date_from: str = "",
    completed_date_to: str = "",
):
    if isinstance(status_filter, str):
        status_filters = [] if status_filter in ("", "all") else [status_filter]
    else:
        status_filters = [
            value for value in (status_filter or []) if value and value != "all"
        ]

    model = apps.get_model("app", media_type)
    if media_type == MediaTypes.EPISODE.value:
        queryset = model.objects.filter(related_season__user=owner)
        if status_filters:
            queryset = queryset.filter(related_season__status__in=status_filters)
    else:
        queryset = model.objects.filter(user=owner)
        if status_filters:
            queryset = queryset.filter(status__in=status_filters)

    if search_query:
        queryset = queryset.filter(item__title__icontains=search_query)
    if date_added_from:
        queryset = queryset.filter(created_at__date__gte=date_added_from)
    if date_added_to:
        queryset = queryset.filter(created_at__date__lte=date_added_to)

    if completed_date_from or completed_date_to:
        if media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value):
            episode_model = apps.get_model("app", "episode")
            episode_lookup = (
                "related_season"
                if media_type == MediaTypes.SEASON.value
                else "related_season__related_tv"
            )
            episode_filter = Q(**{episode_lookup: OuterRef("pk")})
            if completed_date_from:
                episode_filter &= Q(end_date__date__gte=completed_date_from)
            if completed_date_to:
                episode_filter &= Q(end_date__date__lte=completed_date_to)
            queryset = queryset.filter(
                Exists(episode_model.objects.filter(episode_filter)),
            )
        else:
            if completed_date_from:
                queryset = queryset.filter(end_date__date__gte=completed_date_from)
            if completed_date_to:
                queryset = queryset.filter(end_date__date__lte=completed_date_to)

    return queryset.select_related("item")


def _target_media_types(owner, rules_media_types: list[str]) -> list[str]:
    available = get_available_media_types(owner)
    if rules_media_types:
        return [
            media_type for media_type in rules_media_types if media_type in available
        ]
    return [
        media_type
        for media_type in available
        if media_type not in IMPLICIT_ALL_EXCLUDED_MEDIA_TYPES
    ]


def _id_batch_size(value_count: int) -> int:
    """Return a safe `__in=` batch size for the active database backend."""
    max_query_params = connection.features.max_query_params
    if max_query_params:
        # Keep room for other parameters (e.g. media_type) in the same query.
        return max(1, max_query_params - 8)
    return max(1, value_count)


def _collection_filter_context(owner) -> tuple[set[int], set[tuple[str, str]]]:
    """Return collection lookup sets for smart list collection filtering."""
    collected_item_ids = set(
        CollectionEntry.objects.filter(user=owner).values_list("item_id", flat=True),
    )
    collected_episode_pairs = set()
    batch_size = _id_batch_size(len(collected_item_ids))
    for id_batch in batched(collected_item_ids, batch_size):
        collected_episode_pairs.update(
            Item.objects.filter(
                id__in=id_batch,
                media_type=MediaTypes.EPISODE.value,
            ).values_list("media_id", "source"),
        )
    return collected_item_ids, collected_episode_pairs


def _resolve_collection_context(
    owner,
    collection_context: tuple[set[int], set[tuple[str, str]]] | None,
    collection_context_cache: dict | None,
) -> tuple[set[int], set[tuple[str, str]]]:
    """Return a collection context, reusing a caller-supplied value or cache.

    `collection_context_cache` lets callers that resolve many rows/media types
    for the same owner in one request (e.g. building the Home page) share a
    single `CollectionEntry` scan instead of repeating it per row/media type.
    The cache is expected to be a plain dict scoped to a single request/call.
    """
    if collection_context is not None:
        return collection_context
    if collection_context_cache is None:
        return _collection_filter_context(owner)
    cache_key = getattr(owner, "id", None)
    if cache_key not in collection_context_cache:
        collection_context_cache[cache_key] = _collection_filter_context(owner)
    return collection_context_cache[cache_key]


def _collection_only_item_ids(
    owner,
    media_type: str,
    tracked_item_ids: set[int] | None = None,
    *,
    search_query: str = "",
    collection_context_cache: dict | None = None,
) -> set[int]:
    """Return collected item ids that do not already have a tracker row."""
    tracked_item_ids = tracked_item_ids or set()
    collected_item_ids, _collected_episode_pairs = _resolve_collection_context(
        owner, None, collection_context_cache,
    )
    if not collected_item_ids:
        return set()

    direct_type_match = Q(media_type=media_type) | Q(library_media_type=media_type)
    batch_size = _id_batch_size(len(collected_item_ids))
    candidate_item_ids = set()
    for id_batch in batched(collected_item_ids, batch_size):
        candidate_item_ids.update(
            Item.objects.filter(id__in=id_batch)
            .filter(direct_type_match)
            .exclude(media_type=MediaTypes.EPISODE.value)
            .values_list("id", flat=True),
        )

    if media_type in {MediaTypes.TV.value, MediaTypes.ANIME.value}:
        episode_pairs = set()
        for id_batch in batched(collected_item_ids, batch_size):
            episode_pairs.update(
                (str(media_id), str(source))
                for media_id, source in Item.objects.filter(
                    id__in=id_batch,
                    media_type=MediaTypes.EPISODE.value,
                ).values_list("media_id", "source")
            )
        if episode_pairs:
            show_media_ids = {media_id for media_id, _source in episode_pairs}
            show_sources = {source for _media_id, source in episode_pairs}
            show_queryset = Item.objects.filter(
                media_type__in=(MediaTypes.TV.value, MediaTypes.ANIME.value),
                media_id__in=show_media_ids,
                source__in=show_sources,
            ).filter(direct_type_match)
            for item in show_queryset.only("id", "media_id", "source"):
                if (str(item.media_id), str(item.source)) in episode_pairs:
                    candidate_item_ids.add(item.id)

    candidate_item_ids -= tracked_item_ids
    if not candidate_item_ids:
        return set()

    result_ids = set()
    for id_batch in batched(candidate_item_ids, _id_batch_size(len(candidate_item_ids))):
        candidate_queryset = Item.objects.filter(id__in=id_batch)
        if search_query:
            candidate_queryset = candidate_queryset.filter(
                Q(title__icontains=search_query) | Q(media_id__icontains=search_query),
            )
        result_ids.update(candidate_queryset.values_list("id", flat=True))
    return result_ids


def _library_query(owner, normalized_rules: dict, target_media_types: list[str]):
    from app.library_query.adapters import from_smart_rules

    return from_smart_rules(owner, normalized_rules, tuple(target_media_types))


def collect_matching_item_ids(
    owner,
    normalized_rules: dict,
    *,
    include_collection_only_untracked: bool = False,
    collection_context_cache: dict | None = None,
) -> set[int]:
    """Return matching Item IDs for a normalized smart-rule definition.

    Evaluated by the shared library-query engine under the semantics the rules
    were saved with (see ``app.library_query.adapters``).
    ``collection_context_cache`` is accepted for existing callers; collection
    state is now read in SQL, so there is nothing to share.
    """
    from app.library_query import LibraryQueryExecutor

    target_media_types = _target_media_types(
        owner, normalized_rules.get("media_types", [])
    )
    if not target_media_types:
        return set()
    query = _library_query(owner, normalized_rules, target_media_types)
    if include_collection_only_untracked:
        query = dataclasses.replace(query, include_collection_only=True)
    return LibraryQueryExecutor(owner, query).ids()


def matching_items(owner, normalized_rules: dict):
    """Return the rules' matches as a scope for ``LibraryQuery.within``."""
    from app.library_query import LibraryQueryExecutor

    target_media_types = _target_media_types(
        owner, normalized_rules.get("media_types", [])
    )
    if not target_media_types:
        return Item.objects.none().values("pk")
    query = _library_query(owner, normalized_rules, target_media_types)
    return LibraryQueryExecutor(owner, query).matches()


def item_matches_rules(
    owner,
    item: Item,
    normalized_rules: dict,
    *,
    collection_context: tuple[set[int], set[tuple[str, str]]] | None = None,
) -> bool:
    """Return whether a single item currently matches a normalized rule set for an owner."""
    from app.library_query import LibraryQueryExecutor

    if not owner or not item:
        return False
    target_media_types = _target_media_types(
        owner, normalized_rules.get("media_types", [])
    )
    query = _library_query(owner, normalized_rules, target_media_types)
    return LibraryQueryExecutor(owner, query).contains(item.id)


def sync_smart_lists_for_item(owner, item: Item) -> dict[str, int]:
    """Incrementally sync smart-list membership for one owner/item combination."""
    if not owner or not item or not getattr(item, "id", None):
        return {"checked": 0, "added": 0, "removed": 0}

    from lists.models import CustomList, CustomListItem

    smart_lists = list(
        CustomList.objects.filter(owner=owner, is_smart=True).only(
            "id",
            "owner_id",
            "smart_media_types",
            "smart_excluded_media_types",
            "smart_filters",
        ),
    )
    if not smart_lists:
        return {"checked": 0, "added": 0, "removed": 0}

    smart_list_ids = [custom_list.id for custom_list in smart_lists]
    existing_memberships = set(
        CustomListItem.objects.filter(
            custom_list_id__in=smart_list_ids,
            item_id=item.id,
        ).values_list("custom_list_id", flat=True),
    )

    pending_adds = []
    pending_removals = []

    for custom_list in smart_lists:
        normalized_rules = normalize_list_rules(custom_list)
        should_include = item_matches_rules(
            owner=owner,
            item=item,
            normalized_rules=normalized_rules,
        )
        currently_in_list = custom_list.id in existing_memberships

        if should_include and not currently_in_list:
            pending_adds.append(
                CustomListItem(
                    custom_list=custom_list,
                    item=item,
                    added_by=owner,
                ),
            )
        elif not should_include and currently_in_list:
            pending_removals.append(custom_list.id)

    if pending_adds:
        CustomListItem.objects.bulk_create(pending_adds, ignore_conflicts=True)
    if pending_removals:
        CustomListItem.objects.filter(
            custom_list_id__in=pending_removals,
            item_id=item.id,
        ).delete()

    return {
        "checked": len(smart_lists),
        "added": len(pending_adds),
        "removed": len(pending_removals),
    }


# Ids per membership question. Well under every backend's parameter limit,
# leaving room for the parameters the list's own rules add.
SET_SYNC_ID_BATCH = 900


def sync_smart_lists_for_items(owner, items) -> dict[str, int]:
    """Sync the owner's smart lists for many changed items, one question per list.

    Gives every item the membership ``sync_smart_lists_for_item`` would, but
    each list's rules are normalized once and asked which of the items match
    in one set query, instead of once per item. A list that fails is logged
    and skipped so the others still sync.
    """
    zero = {"checked": 0, "added": 0, "removed": 0}
    item_ids = sorted({item.id for item in items or [] if item and getattr(item, "id", None)})
    if not owner or not item_ids:
        return zero

    from app.library_query import LibraryQueryExecutor
    from lists.models import CustomList, CustomListItem

    smart_lists = list(
        CustomList.objects.filter(owner=owner, is_smart=True).only(
            "id",
            "owner_id",
            "smart_media_types",
            "smart_excluded_media_types",
            "smart_filters",
        ),
    )
    if not smart_lists:
        return zero

    batch_size = min(SET_SYNC_ID_BATCH, _id_batch_size(len(item_ids)))
    existing: dict[int, set[int]] = {custom_list.id: set() for custom_list in smart_lists}
    for id_batch in batched(item_ids, batch_size):
        for list_id, item_id in CustomListItem.objects.filter(
            custom_list_id__in=existing.keys(),
            item_id__in=id_batch,
        ).values_list("custom_list_id", "item_id"):
            existing[list_id].add(item_id)

    added = removed = 0
    for custom_list in smart_lists:
        try:
            normalized_rules = normalize_list_rules(custom_list)
            target_media_types = _target_media_types(
                owner,
                normalized_rules.get("media_types", []),
            )
            query = _library_query(owner, normalized_rules, target_media_types)
            matched = LibraryQueryExecutor(owner, query).matching_among(
                item_ids,
                chunk_size=batch_size,
            )
            current = existing[custom_list.id]
            to_add = sorted(matched - current)
            to_remove = sorted(current - matched)
            if to_add:
                CustomListItem.objects.bulk_create(
                    [
                        CustomListItem(custom_list=custom_list, item_id=item_id, added_by=owner)
                        for item_id in to_add
                    ],
                    ignore_conflicts=True,
                )
            for id_batch in batched(to_remove, batch_size):
                CustomListItem.objects.filter(
                    custom_list_id=custom_list.id,
                    item_id__in=id_batch,
                ).delete()
        except Exception:
            logger.exception(
                "Smart list sync failed for owner_id=%s list_id=%s",
                owner.id,
                custom_list.id,
            )
            continue
        added += len(to_add)
        removed += len(to_remove)

    return {"checked": len(smart_lists), "added": added, "removed": removed}


def build_rule_filter_data(
    owner,
    media_types: list[str],
    status: list[str] | str,
    search: str,
    *,
    include_collection_only_untracked: bool = False,
    precomputed_tags: list[str] | None = None,
    include_list_options: bool = True,
):
    """Build menu options for smart-rule filters from matched candidate media."""
    target_media_types = _target_media_types(owner, media_types)
    status_is_all = not status or status == "all"

    item_ids = set()
    for media_type in target_media_types:
        queryset = _base_media_queryset(
            owner=owner,
            media_type=media_type,
            status_filter=status,
            search_query=search,
        )
        queryset_item_ids = set(queryset.values_list("item_id", flat=True))
        item_ids.update(queryset_item_ids)
        if include_collection_only_untracked and status_is_all:
            item_ids.update(
                _collection_only_item_ids(
                    owner,
                    media_type,
                    queryset_item_ids,
                    search_query=search,
                ),
            )

    return build_filter_data_for_items(
        owner,
        item_ids,
        target_media_types,
        precomputed_tags=precomputed_tags,
        include_list_options=include_list_options,
    )


def build_filter_data_for_items(
    owner,
    item_ids,
    target_media_types,
    *,
    precomputed_tags: list[str] | None = None,
    include_list_options: bool = True,
):
    """Build filter-menu options (genres, years, platforms...) from ``item_ids``.

    ``owner`` supplies the tag names, provider region and linked-list options;
    ``target_media_types`` decides which type-specific panes the menu shows.
    """
    item_ids = set(item_ids)
    region = getattr(owner, "watch_provider_region", None)

    only_fields = (
        "genres",
        "implied_genres",
        "release_datetime",
        "source",
        "languages",
        "country",
        "platforms",
        "format",
        "media_type",
        "authors",
    )
    # The column holds every region's availability (~146 KiB a title); read it
    # only when the menu will show the providers pane.
    if region and region != "UNSET":
        only_fields += ("watch_providers",)
    item_batch_size = _id_batch_size(len(item_ids))
    items = [
        item
        for id_batch in batched(item_ids, item_batch_size)
        for item in Item.objects.filter(id__in=id_batch).only(*only_fields)
    ]

    format_labels = {
        "hardcover": "Hardcover",
        "paperback": "Paperback",
        "ebook": "eBook",
        "audiobook": "Audiobook",
    }

    genres_set = set()
    implied_genres_set = set()
    years_set = set()
    sources_set = set()
    languages_set = set()
    countries_set = set()
    platforms_set = set()
    origins_set = set()
    formats_set = set()
    authors_set = set()
    providers_set = set()
    has_unknown_year = False

    for item in items:
        for genre in item.genres or []:
            genre_value = str(genre).strip()
            if genre_value:
                genres_set.add(genre_value)
        for genre in getattr(item, "implied_genres", None) or []:
            genre_value = str(genre).strip()
            if genre_value:
                implied_genres_set.add(genre_value)

        release_datetime = getattr(item, "release_datetime", None)
        if release_datetime and getattr(release_datetime, "year", None):
            years_set.add(release_datetime.year)
        else:
            has_unknown_year = True

        if item.source:
            sources_set.add(item.source)

        languages_set.update(_extract_languages(item))

        country_value = _extract_country(item)
        if country_value:
            countries_set.add(country_value)
            origins_set.add(country_value)

        platforms_set.update(_extract_platforms(item))

        format_value = str(getattr(item, "format", "") or "").strip()
        if format_value:
            formats_set.add(format_value)
        authors_set.update(_extract_authors(item))

        if region and region != "UNSET":
            providers_set.update(_extract_item_providers(item, region) or [])

    source_labels = dict(Sources.choices)
    filter_data = {
        "genres": sorted(genres_set, key=lambda value: value.lower()),
        "implied_genres": sorted(implied_genres_set, key=lambda value: value.lower()),
        "years": [
            {"value": str(year), "label": str(year)}
            for year in sorted(years_set, reverse=True)
        ],
        "sources": [
            {"value": source, "label": source_labels.get(source, source)}
            for source in sorted(sources_set)
        ],
        "languages": [
            {
                "value": value,
                "label": value.upper()
                if len(value) <= SHORT_CODE_MAX_LENGTH
                else value,
            }
            for value in sorted(languages_set)
        ],
        "countries": [
            {
                "value": value,
                "label": value.upper()
                if len(value) <= SHORT_CODE_MAX_LENGTH
                else value,
            }
            for value in sorted(countries_set)
        ],
        "platforms": [
            {"value": value, "label": value}
            for value in sorted(platforms_set, key=lambda value: value.lower())
        ],
        "origins": [
            {
                "value": value,
                "label": value.upper()
                if len(value) <= SHORT_CODE_MAX_LENGTH
                else value,
            }
            for value in sorted(origins_set)
        ],
        "show_languages": any(
            media_type in LANGUAGE_MEDIA_TYPES for media_type in target_media_types
        ),
        "show_countries": any(
            media_type in COUNTRY_MEDIA_TYPES for media_type in target_media_types
        ),
        "show_platforms": any(
            media_type in PLATFORM_MEDIA_TYPES for media_type in target_media_types
        ),
        "show_origins": any(
            media_type in ORIGIN_MEDIA_TYPES for media_type in target_media_types
        ),
        "formats": [
            {"value": value, "label": format_labels.get(value, value.title())}
            for value in sorted(formats_set, key=lambda val: val.lower())
        ],
        "show_formats": any(
            media_type in FORMAT_MEDIA_TYPES for media_type in target_media_types
        ),
        "authors": [
            {"value": value, "label": value}
            for value in sorted(authors_set, key=lambda value: value.lower())
        ],
        "show_authors": any(
            media_type in AUTHOR_MEDIA_TYPES for media_type in target_media_types
        ),
        "author_is_channel": bool(target_media_types)
        and all(media_type == MediaTypes.VIDEO.value for media_type in target_media_types),
        "providers": [
            {"value": value, "label": value}
            for value in sorted(providers_set, key=lambda value: value.lower())
        ],
        "show_providers": bool(region and region != "UNSET")
        and any(media_type in PROVIDER_MEDIA_TYPES for media_type in target_media_types),
        "relative_date_units": [
            {"value": value, "label": label}
            for value, label in RELATIVE_DATE_UNIT_CHOICES
        ],
    }

    if has_unknown_year:
        filter_data["years"].append({"value": "unknown", "label": "Unknown"})

    if precomputed_tags is not None:
        filter_data["tags"] = precomputed_tags
    else:
        from app.models import Tag

        filter_data["tags"] = list(
            Tag.objects.filter(user=owner)
            .values_list("name", flat=True)
            .order_by("name")
        )

    filter_data["lists"] = []
    if include_list_options:
        from lists.models import CustomList

        filter_data["lists"] = [
            {"id": custom_list.id, "label": custom_list.name}
            for custom_list in CustomList.objects.get_user_lists(owner)
            .filter(is_smart=False)
            .order_by("name")
        ]

    return filter_data
