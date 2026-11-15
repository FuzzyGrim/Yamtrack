"""Shared media-list filtering and next-episode resolution."""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import re
from dataclasses import dataclass, fields, replace

from django.apps import apps
from django.core.cache import cache
from django.utils import timezone

from app import helpers
from app.library_query.alphabet import normalize_initial
from app.models import (
    BasicMedia,
    CollectionEntry,
    Item,
    MediaTypes,
    Season,
    Sources,
    Status,
)
from app.templatetags.app_tags import media_url
from users.models import MediaSortChoices

MEDIA_LIST_MEDIA_TYPES = tuple(
    media_type
    for media_type in MediaTypes.values
)
MEDIA_LIST_NO_STATUS = "no_status"
MEDIA_LIST_YEAR_LENGTH = 4
MEDIA_LIST_STATUS_BY_CODE = {
    "0": Status.PLANNING.value,
    "1": Status.IN_PROGRESS.value,
    "2": Status.PAUSED.value,
    "3": Status.COMPLETED.value,
    "4": Status.DROPPED.value,
}
MEDIA_LIST_STATUS_VALUES = {
    "all",
    MEDIA_LIST_NO_STATUS,
    *(status.value.lower() for status in Status),
}
MEDIA_LIST_SORTS = {
    choice.value for choice in MediaSortChoices
} | {
    "added",
    "updated",
    "itemid",
    "mediaid",
    "type",
    "source",
    "id",
    "ended",
    "started",
    "last_watched",
}
MEDIA_LIST_SORT_DEFAULTS_ASC = {
    "author",
    "popularity",
    "runtime",
    "start_date",
    "title",
    "next_episode_air_date",
    "time_left",
    "time_to_beat",
    "platform",
}
MEDIA_LIST_PROVIDER_TYPES = {
    MediaTypes.TV.value,
    MediaTypes.MOVIE.value,
    MediaTypes.ANIME.value,
}
MEDIA_LIST_AUTHOR_TYPES = {
    MediaTypes.BOOK.value,
    MediaTypes.MANGA.value,
    MediaTypes.COMIC.value,
    MediaTypes.COMIC_ISSUE.value,
}
MEDIA_LIST_LANGUAGE_TYPES = {
    MediaTypes.TV.value,
    MediaTypes.MOVIE.value,
    MediaTypes.ANIME.value,
}


def _not_in_cache_token(default):
    """Declare a field that ``MediaListFilters.cache_token`` leaves out."""
    return dataclasses.field(default=default, metadata={"cache_token": False})


class MediaListFilterError(ValueError):
    """Raised when an API media-list query parameter is invalid."""

    def __init__(self, parameter: str, message: str):
        """Store the invalid query parameter alongside the message."""
        super().__init__(message)
        self.parameter = parameter
        self.message = message


@dataclass(frozen=True)
class MediaListFilters:
    """Normalized query parameters shared by the API media-list endpoints."""

    statuses: tuple[str, ...] = ()
    include_no_status: bool = False
    search: str = ""
    # View state (the A to Z rail): parsed from the query, never saved as a smart rule.
    initial: str = ""
    separate_entries: bool = False
    group_by: str = "item"
    group: str = ""
    rating: str = "all"
    rating_min: str = ""
    rating_max: str = ""
    collection: str = "all"
    progress: str = "all"
    genre: str = ""
    implied_genre: str = ""
    year: str = ""
    # Each date range is resolved to from/to; ``*_within`` keeps a relative
    # window ("last 7 days") as asked, so a page can show the choice again.
    # The window is display only: the dates it resolved to are what filter,
    # so it stays out of ``cache_token``.
    completed_date_from: str = ""
    completed_date_to: str = ""
    completed_date_within: str = _not_in_cache_token("")
    completed_date_within_unit: str = _not_in_cache_token("days")
    release: str = "all"
    release_date_from: str = ""
    release_date_to: str = ""
    release_date_within: str = _not_in_cache_token("")
    release_date_within_unit: str = _not_in_cache_token("days")
    date_added_from: str = ""
    date_added_to: str = ""
    date_added_within: str = _not_in_cache_token("")
    date_added_within_unit: str = _not_in_cache_token("days")
    source: str = ""
    media_status: str = ""
    language: str = ""
    country: str = ""
    platforms: tuple[str, ...] = ()
    platform_mode: str = "or"
    origin: str = ""
    format: str = ""
    author: str = ""
    provider: str = ""
    provider_region: str = ""
    pinned_providers: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    tag_mode: str = "or"
    # API-only: ISO 8601 moment (as isoformat); keep entries changed at or after it.
    updated_since: str = ""
    sort: str = ""
    direction: str = ""
    exclude: tuple[str, ...] = ()
    media_type: str | None = None

    def cache_token(self, *, exclude: frozenset[str] = frozenset()) -> str:
        """Return a short, stable digest of everything that filters the list.

        Two filter objects that select the same entries get the same token.
        Multi-value fields are compared as sets of values, so their order
        does not matter. A new field is included automatically; declare it
        with ``_not_in_cache_token`` only when it cannot change the result.
        ``exclude`` names fields a particular cache deliberately ignores.

        The digest is computed from JSON, not ``hash()``, so it is the same in
        every process and across restarts.
        """
        names = {item.name for item in fields(self)}
        unknown = set(exclude) - names
        if unknown:
            message = f"Unknown MediaListFilters fields: {sorted(unknown)}"
            raise ValueError(message)
        values = {}
        for item in fields(self):
            if item.name in exclude or not item.metadata.get("cache_token", True):
                continue
            value = getattr(self, item.name)
            values[item.name] = sorted(value) if isinstance(value, tuple) else value
        payload = json.dumps(values, sort_keys=True, separators=(",", ":"))
        return hashlib.blake2b(payload.encode(), digest_size=12).hexdigest()

    def menu_state(self) -> dict:
        """Return the filter menu's state in the smart-rule vocabulary.

        ``static/js/libraryFilterState.js`` reads this. A relative date window
        is handed back as chosen rather than as the dates it resolved to.
        """
        state = {
            "status": [*self.statuses, *(("no_status",) if self.include_no_status else ())],
            "rating": self.rating,
            "rating_min": self.rating_min,
            "rating_max": self.rating_max,
            "collection": self.collection,
            "progress": self.progress,
            "genre": self.genre,
            "implied_genre": self.implied_genre,
            "year": self.year,
            "release": self.release,
            "source": self.source,
            "media_status": self.media_status,
            "language": self.language,
            "country": self.country,
            "platform": self.platforms[0] if self.platforms else "",
            "platforms": list(self.platforms),
            "platform_mode": self.platform_mode,
            "origin": self.origin,
            "format": self.format,
            "author": self.author,
            "provider": self.provider,
            "tag": list(self.tags),
            "tag_mode": self.tag_mode,
            "search": self.search,
        }
        for field in ("completed_date", "release_date", "date_added"):
            within = getattr(self, f"{field}_within")
            state[f"{field}_within"] = within
            state[f"{field}_within_unit"] = getattr(self, f"{field}_within_unit")
            for bound in ("from", "to"):
                key = f"{field}_{bound}"
                state[key] = "" if within else getattr(self, key)
        return state


@dataclass
class MediaListEntry:
    """An Item plus its user tracking row, if one exists."""

    item: Item
    media: object | None = None

    @property
    def item_id(self):
        """Return the tracked item's ID, or the underlying item ID."""
        return getattr(self.media, "item_id", None) or self.item.id


def _normalize(value) -> str:
    return str(value or "").strip().lower()


def normalize_completed_date_filter(value) -> str:
    """Return a YYYY-MM-DD string or empty string.

    Shared by the web list view, the API filters, and the SQL manager filter
    so `completed_date_from`/`completed_date_to` validate identically
    everywhere they're accepted.
    """
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    try:
        datetime.date.fromisoformat(normalized)
    except ValueError:
        return ""
    return normalized


def _split_values(values) -> list[str]:
    result = []
    for raw_value in values:
        result.extend(
            value.strip()
            for value in str(raw_value or "").split(",")
            if value.strip()
        )
    return result


class _Params:
    """Read one request's query string under the API or the web contract.

    ``strict`` is the API contract: comma-separated values are split and an
    invalid value raises ``MediaListFilterError``. The web media list reads
    repeated parameters only, and an invalid value (a stale bookmark) falls
    back to "not filtering".
    """

    def __init__(self, request, *, strict: bool):
        self.query = (
            request.query_params if hasattr(request, "query_params") else request.GET
        )
        self.strict = strict

    def text(self, name: str) -> str:
        return str(self.query.get(name, "") or "").strip()

    def values(self, name: str) -> tuple[str, ...]:
        if self.strict:
            return tuple(_split_values(self.query.getlist(name)))
        return tuple(
            dict.fromkeys(
                value.strip() for value in self.query.getlist(name) if value.strip()
            ),
        )

    def invalid(self, name: str, message: str, fallback):
        if self.strict:
            raise MediaListFilterError(name, message)
        return fallback


_OFFSET_SPACE = re.compile(r"(?<=\d) (?=\d{2}:?\d{2}$)")


def parse_since(params: _Params, name: str) -> str:
    """Read query parameter ``name`` as an aware datetime, returned as isoformat.

    An unescaped ``+`` in a query string arrives as a space, so a UTC offset
    written ``+01:00`` is restored before parsing.
    """
    raw = params.text(name)
    if not raw:
        return ""
    try:
        return helpers.parse_completion_datetime(
            _OFFSET_SPACE.sub("+", raw),
        ).isoformat()
    except (ValueError, TypeError):
        return params.invalid(
            name,
            f"{name} must be an ISO 8601 date or datetime",
            "",
        )


def _parse_status_values(params: _Params) -> tuple[tuple[str, ...], bool]:
    statuses = []
    include_no_status = False
    for raw_value in params.values("status"):
        normalized = _normalize(raw_value).replace("_", " ")
        if normalized == "all":
            continue
        if normalized == MEDIA_LIST_NO_STATUS.replace("_", " "):
            include_no_status = True
            continue
        status_value = MEDIA_LIST_STATUS_BY_CODE.get(normalized)
        if status_value is None:
            status_value = next(
                (
                    status.value
                    for status in Status
                    if _normalize(status.value) == normalized
                ),
                None,
            )
        if status_value is None:
            params.invalid(
                "status",
                "status must be a numeric code, status label, all, or no_status",
                None,
            )
            continue
        if status_value not in statuses:
            statuses.append(status_value)
    return tuple(statuses), include_no_status


def _parse_choice(params: _Params, name: str, allowed: set[str], default: str) -> str:
    """Parse a lower-case choice query parameter."""
    value = _normalize(params.query.get(name, default)) or default
    if value not in allowed:
        return params.invalid(
            name,
            f"{name} must be one of: {', '.join(sorted(allowed))}",
            default,
        )
    return value


def _parse_date(params: _Params, name: str) -> str:
    raw = params.text(name)
    if raw and not normalize_completed_date_filter(raw):
        return params.invalid(name, f"{name} must be a YYYY-MM-DD date", "")
    return raw


def _parse_rating_bound(params: _Params, name: str) -> str:
    from lists.smart_rules import _normalize_decimal_value

    raw = params.text(name)
    value = _normalize_decimal_value(raw)
    if raw and not value:
        return params.invalid(name, f"{name} must be a number from 0 to 10", "")
    return value


def _parse_date_ranges(params: _Params) -> dict[str, str]:
    """Parse the three date ranges, each absolute or "in the last N units".

    A relative window is resolved to dates for this request, with the helper
    smart lists use, so both read "the last 7 days" the same way.
    """
    from lists.smart_rules import (
        RELATIVE_DATE_FIELDS,
        RELATIVE_DATE_UNITS,
        _normalize_relative_amount,
        normalize_relative_unit,
        resolve_relative_date_windows,
    )

    ranges = {}
    for field in RELATIVE_DATE_FIELDS:
        ranges[f"{field}_from"] = _parse_date(params, f"{field}_from")
        ranges[f"{field}_to"] = _parse_date(params, f"{field}_to")
        amount = params.text(f"{field}_within")
        ranges[f"{field}_within"] = _normalize_relative_amount(amount)
        if amount and not ranges[f"{field}_within"]:
            params.invalid(
                f"{field}_within",
                f"{field}_within must be a whole number from 1 to 999",
                "",
            )
        unit = params.text(f"{field}_within_unit")
        ranges[f"{field}_within_unit"] = normalize_relative_unit(unit)
        if unit and unit.lower() not in RELATIVE_DATE_UNITS:
            params.invalid(
                f"{field}_within_unit",
                f"{field}_within_unit must be one of: days, months, weeks, years",
                "",
            )
    return resolve_relative_date_windows(ranges)


def _parse_sort(params: _Params) -> tuple[str, str]:
    raw_sort = _normalize(params.query.get("sort"))
    direction = _normalize(params.query.get("direction"))
    if raw_sort.endswith(("_asc", "_desc")):
        suffix = raw_sort.rsplit("_", 1)[1]
        raw_sort = raw_sort[: -(len(suffix) + 1)]
        if direction and direction != suffix:
            params.invalid("direction", "direction conflicts with the sort suffix", None)
        direction = suffix
    if raw_sort and raw_sort not in MEDIA_LIST_SORTS:
        raw_sort = params.invalid(
            "sort",
            f"sort must be one of: {', '.join(sorted(MEDIA_LIST_SORTS))}",
            "",
        )
    if direction and direction not in {"asc", "desc"}:
        direction = params.invalid("direction", "direction must be asc or desc", "")
    if not direction:
        direction = "asc" if raw_sort in MEDIA_LIST_SORT_DEFAULTS_ASC else "desc"
    return raw_sort, direction


def parse_media_list_filters(request, *, strict: bool = True) -> MediaListFilters:
    """Parse the shared media-list query contract.

    The API calls this strictly; the web media list calls it with
    ``strict=False`` (see ``_Params``). Both get the same filters, so a filter
    added here reaches both.
    """
    params = _Params(request, strict=strict)
    statuses, include_no_status = _parse_status_values(params)
    tags = params.values("tag")
    tag_mode = _parse_choice(params, "tag_mode", {"and", "or", "not"}, "or")
    if not tags:
        legacy_tag_exclude = params.text("tag_exclude")
        if legacy_tag_exclude:
            tags = tuple(_split_values([legacy_tag_exclude]))
            tag_mode = "not"
    year = params.text("year")
    if (
        year
        and year != "unknown"
        and (not year.isdigit() or len(year) != MEDIA_LIST_YEAR_LENGTH)
    ):
        year = params.invalid("year", "year must be a four-digit year or unknown", "")
    sort, direction = _parse_sort(params)
    ranges = _parse_date_ranges(params)
    user = getattr(request, "user", None)
    return MediaListFilters(
        statuses=statuses,
        include_no_status=include_no_status,
        search=params.text("search"),
        initial=normalize_initial(params.text("initial")),
        separate_entries=_parse_choice(params, "separate_entries", {"0", "1"}, "0") == "1",
        group_by=_parse_choice(params, "group_by", {"item", "series", "franchise"}, "item"),
        group=params.text("group"),
        rating=_parse_choice(params, "rating", {"all", "rated", "not_rated"}, "all"),
        rating_min=_parse_rating_bound(params, "rating_min"),
        rating_max=_parse_rating_bound(params, "rating_max"),
        collection=_parse_choice(
            params,
            "collection",
            {"all", "collected", "not_collected"},
            "all",
        ),
        progress=_parse_choice(
            params,
            "progress",
            {"all", "caught_up", "not_caught_up"},
            "all",
        ),
        genre=params.text("genre"),
        implied_genre=params.text("implied_genre"),
        year=year,
        completed_date_from=ranges["completed_date_from"],
        completed_date_to=ranges["completed_date_to"],
        completed_date_within=ranges["completed_date_within"],
        completed_date_within_unit=ranges["completed_date_within_unit"],
        release=_parse_choice(
            params,
            "release",
            {"all", "released", "not_released"},
            "all",
        ),
        release_date_from=ranges["release_date_from"],
        release_date_to=ranges["release_date_to"],
        release_date_within=ranges["release_date_within"],
        release_date_within_unit=ranges["release_date_within_unit"],
        date_added_from=ranges["date_added_from"],
        date_added_to=ranges["date_added_to"],
        date_added_within=ranges["date_added_within"],
        date_added_within_unit=ranges["date_added_within_unit"],
        source=params.text("source"),
        media_status=params.text("media_status"),
        language=params.text("language"),
        country=params.text("country"),
        platforms=params.values("platform"),
        platform_mode=_parse_choice(params, "platform_mode", {"and", "or", "not"}, "or"),
        origin=params.text("origin"),
        format=params.text("format"),
        author=params.text("author"),
        provider=params.text("provider"),
        provider_region=str(getattr(user, "watch_provider_region", "") or "").strip(),
        pinned_providers=tuple(getattr(user, "pinned_watch_providers", None) or ()),
        tags=tags,
        tag_mode=tag_mode,
        sort=sort,
        direction=direction,
        exclude=params.values("exclude"),
        updated_since=parse_since(params, "updated_since"),
    )


def _item_authors(item) -> list[str]:
    authors = getattr(item, "authors", None) or []
    if not isinstance(authors, list):
        authors = [authors]
    result = []
    for author_value in authors:
        selected_author = author_value
        if isinstance(selected_author, dict):
            selected_author = (
                selected_author.get("name")
                or selected_author.get("person")
                or selected_author.get("author")
            )
        if selected_author:
            result.append(str(selected_author).strip())
    return [author for author in result if author]


def _show_has_episode_collection(user, item, collected_ids) -> bool:
    if item.media_type not in {MediaTypes.TV.value, MediaTypes.ANIME.value}:
        return False
    return Item.objects.filter(
        media_type=MediaTypes.EPISODE.value,
        media_id=item.media_id,
        source=item.source,
        id__in=collected_ids,
    ).exists()


def _apply_status_filter(entries, filters):
    if not filters.statuses and not filters.include_no_status:
        return entries
    filtered = []
    for entry in entries:
        status = getattr(entry.media, "aggregated_status", None) or getattr(
            entry.media, "status", None
        )
        if (filters.include_no_status and status is None) or status in filters.statuses:
            filtered.append(entry)
    return filtered


def _apply_rating_filter(entries, rating):
    if rating == "all":
        return entries
    result = []
    for entry in entries:
        score = getattr(entry.media, "aggregated_score", None)
        if score is None:
            score = getattr(entry.media, "score", None)
        if (score is not None) == (rating == "rated"):
            result.append(entry)
    return result


def _apply_collection_filter(user, entries, collection):
    if collection == "all":
        return entries
    collected_ids = set(
        CollectionEntry.objects.filter(user=user).values_list("item_id", flat=True)
    )
    result = []
    for entry in entries:
        collected = entry.item.id in collected_ids or _show_has_episode_collection(
            user, entry.item, collected_ids
        )
        if (collection == "collected") == collected:
            result.append(entry)
    return result


def _apply_progress_filter(entries, progress, media_type):
    if progress == "all" or media_type not in {
        MediaTypes.TV.value,
        MediaTypes.ANIME.value,
    }:
        return entries
    tracked = [entry.media for entry in entries if entry.media is not None]
    if tracked:
        BasicMedia.objects.annotate_max_progress(tracked, media_type)
    return [
        entry
        for entry in entries
        if entry.media is not None
        and (
            helpers.is_caught_up_media(entry.media) == (progress == "caught_up")
        )
    ]


def apply_media_list_status_filter(entries, status_values):
    """Apply the shared latest-status and statusless-item semantics."""
    status_values = tuple(status_values or ())
    return _apply_status_filter(
        entries,
        MediaListFilters(
            statuses=tuple(
                value for value in status_values if value != MEDIA_LIST_NO_STATUS
            ),
            include_no_status=MEDIA_LIST_NO_STATUS in status_values,
        ),
    )


def apply_media_list_rating_filter(entries, rating):
    """Apply the shared rating filter to web or API list entries."""
    return _apply_rating_filter(entries, rating)


def apply_media_list_collection_filter(user, entries, collection):
    """Apply the shared collection filter to web or API list entries."""
    return _apply_collection_filter(user, entries, collection)


def apply_media_list_progress_filter(entries, progress, media_type):
    """Apply the shared released-progress filter to web or API entries."""
    return _apply_progress_filter(entries, progress, media_type)


def _episode_air_date(season, episode_number):
    events = getattr(getattr(season, "item", None), "prefetched_events", None)
    if events is None:
        from events.models import Event

        events = Event.objects.filter(
            item=season.item,
            content_number=episode_number,
        ).order_by("datetime")
    event = next(
        (
            event
            for event in events
            if getattr(event, "content_number", None) == episode_number
        ),
        None,
    )
    if event is not None and event.datetime:
        return event.datetime
    episodes = getattr(season, "episodes", None)
    if episodes is not None:
        episode = next(
            (
                episode
                for episode in episodes.all()
                if getattr(getattr(episode, "item", None), "episode_number", None)
                == episode_number
            ),
            None,
        )
        if episode is not None:
            return getattr(episode.item, "release_datetime", None)
    return None


def _fill_cached_episode_titles(pairs):
    """Name untitled next episodes from cached TMDB seasons, without a request.

    ``pairs`` is ``(item, next_episode)``; one ``get_many`` covers the page.
    """
    from app.providers.tmdb import _season_cache_key

    wanted = {}
    for item, next_episode in pairs:
        if (
            next_episode is None
            or next_episode.get("title") is not None
            or item.source != Sources.TMDB.value
            or next_episode.get("season_number") is None
            or next_episode.get("episode_number") is None
        ):
            continue
        key = _season_cache_key(item.media_id, next_episode["season_number"])
        wanted.setdefault(key, []).append(next_episode)
    if not wanted:
        return
    found = cache.get_many(list(wanted)) or {}
    for key, next_episodes in wanted.items():
        season_data = found.get(key)
        if not isinstance(season_data, dict):
            continue
        titles = {
            episode.get("episode_number"): Item.title_fields_from_episode_metadata(
                episode,
            )["title"]
            for episode in season_data.get("episodes") or []
        }
        for next_episode in next_episodes:
            next_episode["title"] = titles.get(next_episode["episode_number"]) or None


def _show_title(item, media):
    """Return the show's title for a TV, season, or anime row."""
    if item.media_type == MediaTypes.SEASON.value:
        tv_item = getattr(getattr(media, "related_tv", None), "item", None)
        if tv_item is not None and tv_item.title:
            return tv_item.title
    return item.title


def _enrich_next_episode(base, item, media):
    """Attach title/code/image/ids/url to a next_episode dict.

    Several write paths store an unwatched episode's Item under the show's
    title as a placeholder, so a title equal to the show's is not an episode
    name. ``title`` stays ``None`` here when no Item carries a real name;
    ``_fill_cached_episode_titles`` then tries the cached season payload.
    """
    if base is None:
        return None
    season_number = base.get("season_number")
    episode_number = base.get("episode_number")
    episode_items = []
    if episode_number is not None:
        episode_items = list(
            Item.objects.filter(
                source=item.source,
                media_id=item.media_id,
                media_type=MediaTypes.EPISODE.value,
                season_number=season_number,
                episode_number=episode_number,
            ).order_by("id")
        )
    placeholder_titles = {item.title}
    if any(
        episode_item.title and episode_item.title != item.title
        for episode_item in episode_items
    ):
        # Only a season row's own title can differ from the show's.
        placeholder_titles.add(_show_title(item, media))
    named_item = next(
        (
            episode_item
            for episode_item in episode_items
            if episode_item.title and episode_item.title not in placeholder_titles
        ),
        None,
    )
    episode_item = named_item or next(iter(episode_items), None)
    episode_code = None
    if season_number is not None and episode_number is not None:
        episode_code = f"S{season_number:02d}E{episode_number:02d}"
    return {
        **base,
        "title": named_item.title if named_item else None,
        "episode_code": episode_code,
        "image": episode_item.image if episode_item else None,
        "ids": helpers.build_provider_ids(episode_item) if episode_item else {},
        "url": (media_url(episode_item) or None) if episode_item else None,
    }


def next_episode_for_media(media):
    """Return the first released, unwatched episode for a TV-like row."""
    next_episode = _next_episode_for_media(media)
    if next_episode is not None:
        _fill_cached_episode_titles([(media.item, next_episode)])
    return next_episode


def _next_episode_for_media(media):
    """Resolve the next episode, before the cached-title fallback."""
    if media is None:
        return None
    item = getattr(media, "item", None)
    media_type = getattr(item, "media_type", None)
    if media_type == MediaTypes.TV.value:
        seasons = getattr(media, "seasons", None)
        if seasons is None:
            seasons = Season.objects.filter(related_tv=media).select_related("item")
        seasons = sorted(
            seasons.all() if hasattr(seasons, "all") else seasons,
            key=lambda season: getattr(season.item, "season_number", 0) or 0,
        )
        excluded_season_numbers = {
            season.item.season_number
            for season in seasons
            if getattr(season, "item", None)
            and season.item.season_number not in (None, 0)
        }
        for season in seasons:
            season_number = getattr(season.item, "season_number", None)
            if season_number in (None, 0) or season.status in {
                Status.DROPPED.value,
                Status.PAUSED.value,
            }:
                continue
            episode_number = season.next_episode_number()
            if episode_number is not None:
                return _enrich_next_episode(
                    {
                        "season_number": season_number,
                        "episode_number": episode_number,
                        "air_date": _episode_air_date(season, episode_number),
                        "is_rewatch": season.is_rewatching,
                    },
                    item,
                    media,
                )
        from events.models import Event, released_q

        untracked_events = (
            Event.objects.filter(
                item__media_type=MediaTypes.SEASON.value,
                item__media_id=item.media_id,
                item__source=item.source,
                content_number__isnull=False,
            )
            .filter(released_q("datetime", timezone.now()))
            .exclude(item__season_number=0)
            .exclude(item__season_number__in=excluded_season_numbers)
            .order_by("item__season_number", "content_number", "datetime")
        )
        event = untracked_events.first()
        if event is not None:
            return _enrich_next_episode(
                {
                    "season_number": event.item.season_number,
                    "episode_number": event.content_number,
                    "air_date": event.datetime,
                    "is_rewatch": False,
                },
                item,
                media,
            )
        return None
    if media_type == MediaTypes.SEASON.value and hasattr(media, "next_episode_number"):
        episode_number = media.next_episode_number()
        if episode_number is None:
            return None
        return _enrich_next_episode(
            {
                "season_number": getattr(item, "season_number", None),
                "episode_number": episode_number,
                "air_date": _episode_air_date(media, episode_number),
                "is_rewatch": media.is_rewatching,
            },
            item,
            media,
        )
    if media_type == MediaTypes.ANIME.value:
        from events.models import Event, released_q

        progress = int(getattr(media, "progress", 0) or 0)
        event = (
            Event.objects.filter(
                item=item,
                content_number__gt=progress,
            )
            .filter(released_q("datetime", timezone.now()))
            .exclude(datetime__year__lt=1900)
            .order_by("content_number", "datetime")
            .first()
        )
        if event is not None:
            return _enrich_next_episode(
                {
                    "season_number": None,
                    "episode_number": event.content_number,
                    "air_date": event.datetime,
                    "is_rewatch": False,
                },
                item,
                media,
            )
    return None


def _sort_value(entry, sort, next_episode):
    media = entry.media
    item = entry.item
    if sort in {"title", ""}:
        return getattr(item, "title", "").lower()
    if sort == "score":
        score = getattr(media, "aggregated_score", None)
        return score if score is not None else getattr(media, "score", None)
    if sort == "critic_rating":
        return getattr(item, "provider_rating", None)
    if sort == "popularity":
        return getattr(item, "trakt_popularity_rank", None)
    if sort in {"progress", "plays"}:
        progress = getattr(media, "aggregated_progress", None)
        return progress if progress is not None else getattr(media, "progress", 0)
    if sort == "runtime":
        return getattr(media, "total_runtime_minutes", None)
    if sort == "time_watched":
        return getattr(media, "time_watched_minutes", None)
    if sort == "time_to_beat":
        return getattr(item, "game_time_to_beat_minutes", None)
    if sort == "platform":
        return _normalize(next(iter(getattr(item, "platforms", None) or []), ""))
    if sort == "author":
        return _normalize(_item_authors(item)[0] if _item_authors(item) else "")
    if sort in {"release_date", "release_datetime"}:
        return getattr(item, "release_datetime", None)
    if sort in {"date_added", "added", "created_at"}:
        return getattr(media, "aggregated_created_at", None) or getattr(media, "created_at", None)
    if sort in {"start_date", "started"}:
        return getattr(media, "aggregated_start_date", None) or getattr(
            media, "start_date", None
        )
    if sort in {"end_date", "ended"}:
        return getattr(media, "aggregated_end_date", None) or getattr(
            media, "end_date", None
        )
    if sort == "last_watched":
        return (
            getattr(media, "aggregated_last_watched", None)
            or getattr(media, "end_date", None)
            or helpers.progress_activity(media)
        )
    if sort in {"updated", "progressed_at"}:
        return getattr(media, "progressed_at", None)
    if sort == "next_episode_air_date":
        return next_episode.get("air_date") if next_episode else None
    if sort == "time_left":
        max_progress = getattr(media, "max_progress", None)
        if max_progress is None:
            return None
        return max_progress - int(getattr(media, "progress", 0) or 0)
    if sort in {"id", "itemid", "mediaid"}:
        return str(getattr(item, "media_id", ""))
    if sort == "source":
        return getattr(item, "source", "")
    if sort == "type":
        return getattr(item, "media_type", "")
    return getattr(item, "title", "").lower()


def media_list_media_types(filters: MediaListFilters, media_type) -> tuple[str, ...]:
    """Return the libraries a media-list request covers.

    The root endpoint spans every list type except seasons and episodes (they
    belong to their shows) and any the client excluded.
    """
    if media_type is not None:
        return (media_type,)
    return tuple(
        current_type
        for current_type in MEDIA_LIST_MEDIA_TYPES
        if current_type not in {MediaTypes.SEASON.value, MediaTypes.EPISODE.value}
        and current_type not in filters.exclude
    )


def attach_card_collection_platforms(user, items):
    """Attach the owner's collected platforms to a bounded set of game items."""
    from app.models import CollectionEntry

    games = [item for item in items if item.media_type == MediaTypes.GAME.value and item.pk]
    if not games:
        return
    platforms = {}
    for item_id, value in CollectionEntry.objects.filter(
        user=user, item_id__in=[item.pk for item in games]
    ).values_list("item_id", "resolution"):
        platform = value.strip()
        if platform:
            platforms.setdefault(item_id, set()).add(platform)
    for item in games:
        item.card_collection_platforms = sorted(platforms.get(item.pk, ()), key=str.lower)


def media_list_entries_for_items(user, items) -> list[MediaListEntry]:
    """Attach each item's tracker row for a page of items.

    The row shown is the item's newest, with duplicate rows (repeat viewings)
    aggregated onto it and the list prefetches applied - for this page only.
    Items without a row (collected but untracked) have no media.
    """
    item_ids_by_type: dict[str, list[int]] = {}
    for item in items:
        item_ids_by_type.setdefault(item.media_type, []).append(item.pk)
    media_by_item_id = {}
    for media_type, item_ids in item_ids_by_type.items():
        model = apps.get_model("app", media_type)
        if media_type == MediaTypes.EPISODE.value:
            owner = {"related_season__user": user}
            # Episode cards read max_progress and status through their season.
            related = (
                "item",
                "related_season",
                "related_season__item",
                "related_season__related_tv",
                "related_season__related_tv__item",
            )
        else:
            owner = {"user": user}
            related = ("item",)
        rows = model.objects.filter(item_id__in=item_ids, **owner).select_related(*related)
        rows = rows.defer(*(f"{relation}__watch_providers" for relation in related if relation.endswith("item")))
        rows = list(BasicMedia.objects._apply_prefetch_related(rows, media_type, list_mode=True, compact_episodes=True, light_rows=True))
        if media_type != MediaTypes.EPISODE.value:
            BasicMedia.objects._aggregate_duplicate_data(rows, user, media_type)
        for media in sorted(rows, key=lambda row: (row.created_at, row.pk)):
            media_by_item_id[media.item_id] = media
    # A tracked entry keeps its row's own item: the prefetches (events, tags)
    # hang off that instance. Generic/API hydration must not query CollectionEntry
    # for card decoration; HTML surfaces attach platforms from page context (#1004).
    return [
        MediaListEntry(
            item=media.item if media is not None else item,
            media=media,
        )
        for item in items
        for media in [media_by_item_id.get(item.pk)]
    ]


def get_media_list_entries(user, media_type, filters: MediaListFilters, *, limit=None, offset=None):
    """Return ``(entries, total)`` for one page of a media list.

    Evaluated by the shared library-query engine: SQL-capable filters and
    sorts page in the database, anything else in bounded batches; only the
    returned page is hydrated. ``limit=None`` returns every match.
    """
    from app.library_query import LibraryQueryExecutor
    from app.library_query.adapters import from_media_list_filters

    if media_type is not None and media_type not in MEDIA_LIST_MEDIA_TYPES:
        parameter = "media_type"
        message = "Unsupported media type"
        raise MediaListFilterError(parameter, message)
    filters = replace(filters, media_type=media_type)
    query = from_media_list_filters(filters, media_list_media_types(filters, media_type))
    executor = LibraryQueryExecutor(user, query)
    offset = offset or 0
    if filters.separate_entries:
        from app.media_list_entry_grouping import preference_field

        if not preference_field(media_type):
            parameter = "separate_entries"
            message = "This media type does not support separate entries"
            raise MediaListFilterError(parameter, message)
        return executor.entry_page(offset, limit)
    if limit is None:
        total = executor.count()
        page = executor.page(offset, max(total - offset, 0), total=total)
    else:
        page = executor.page(offset, limit)
    return media_list_entries_for_items(user, page.items), page.total


def get_next_episode_map(entries):
    """Build the next-episode payload map used by media serializers."""
    next_episodes = {
        entry.item.id: _next_episode_for_media(entry.media)
        for entry in entries
        if entry.media is not None
    }
    _fill_cached_episode_titles(
        (entry.media.item, next_episodes[entry.item.id])
        for entry in entries
        if entry.media is not None
    )
    return next_episodes
