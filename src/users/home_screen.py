"""Shared helpers for Home screen row persistence and rendering."""

from __future__ import annotations

import copy
import hashlib
import json
import random
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Case, F, IntegerField, Min, Q, Subquery, Value, When
from django.db.models.functions import Coalesce, Lower, Replace, Trim
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext

from app.artwork_sizes import card_image_url
from app.helpers import progress_activity
from app.library_query import FilterValues, LibraryQuery, LibraryQueryExecutor, SortSpec
from app.library_query.adapters import (
    from_home_row_filters,
    home_engine_direction,
    last_watched_sort_key,
)
from app.library_query.filters import NEEDS_MAX_PROGRESS, NEEDS_MEDIA
from app.library_query.sorts import RANDOM_MODULUS, SortDef
from app.library_query.sorts import register as register_sort
from app.library_query.spec import STATUS_MATCH_ANY
from app.models import (
    BasicMedia,
    Episode,
    Item,
    MediaTypes,
    Sources,
    Status,
    Tag,
    prefill_episode_runtime_index,
)
from app.models.tv import TERMINAL_PRODUCTION_STATUSES
from app.release_years import prefill_display_release_years
from app.request_timing import boundary
from app.templatetags import app_tags
from lists import smart_rules
from lists.models import CustomList
from users.models import (
    HOME_ALL_MEDIA_TYPE,
    DirectionChoices,
    HomeScreenRow,
    HomeScreenRowTypeChoices,
    HomeSortChoices,
    ListDetailSortChoices,
    MediaSortChoices,
    MediaStatusChoices,
    relabel_end_date_sort_choice,
)

# A tag line needs at least this many parts before its values are usable.
MIN_TAG_PARTS = 4

RECENTLY_UNRATED_DAYS = 7
RECENTLY_UNRATED_EPISODE_DAYS = 30
RECENTLY_UNRATED_LABEL = "Recently Played - Not Rated"
# Cap on how many active-filter labels are shown in a row's settings summary.
MAX_SUMMARY_FILTER_PARTS = 4
SQUARE_HOME_MEDIA_TYPES = {
    MediaTypes.MUSIC.value,
    MediaTypes.PODCAST.value,
}
# Music has a single Home section/header, but each row under it can independently
# target tracks, albums, or artists (mirrors the media-list subview toggle).
# A row's choice is stored in filters["subview"]; rows of different types mix freely.
MUSIC_SUBVIEW_TRACKS = "tracks"
MUSIC_SUBVIEW_ALBUMS = "albums"
MUSIC_SUBVIEW_ARTISTS = "artists"
MUSIC_SUBVIEW_DEFAULT = MUSIC_SUBVIEW_TRACKS
# Ordered for the settings filter menu (media type sits at the top of the list).
MUSIC_SUBVIEW_VALUES = (
    MUSIC_SUBVIEW_ARTISTS,
    MUSIC_SUBVIEW_ALBUMS,
    MUSIC_SUBVIEW_TRACKS,
)
MUSIC_SUBVIEW_LABELS = {
    MUSIC_SUBVIEW_ARTISTS: "Artists",
    MUSIC_SUBVIEW_ALBUMS: "Albums",
    MUSIC_SUBVIEW_TRACKS: "Tracks",
}


# Podcasts work the same way: a row lists the tracked shows or the episodes.
PODCAST_SUBVIEW_SHOWS = "shows"
PODCAST_SUBVIEW_EPISODES = "episodes"
PODCAST_SUBVIEW_DEFAULT = PODCAST_SUBVIEW_SHOWS
PODCAST_SUBVIEW_VALUES = (PODCAST_SUBVIEW_SHOWS, PODCAST_SUBVIEW_EPISODES)
PODCAST_SUBVIEW_LABELS = {
    PODCAST_SUBVIEW_SHOWS: "Shows",
    PODCAST_SUBVIEW_EPISODES: "Episodes",
}

# media type -> (choices in menu order, labels, default choice)
SUBVIEWS_BY_MEDIA_TYPE = {
    MediaTypes.MUSIC.value: (
        MUSIC_SUBVIEW_VALUES,
        MUSIC_SUBVIEW_LABELS,
        MUSIC_SUBVIEW_DEFAULT,
    ),
    MediaTypes.PODCAST.value: (
        PODCAST_SUBVIEW_VALUES,
        PODCAST_SUBVIEW_LABELS,
        PODCAST_SUBVIEW_DEFAULT,
    ),
}

# All Media keeps its historical defaults, independently of dedicated shelves.
ALL_MEDIA_SUBVIEWS = {
    MediaTypes.TV.value: (
        ("shows", "seasons", "episodes"),
        {"shows": "Shows", "seasons": "Seasons", "episodes": "Episodes"},
        "shows",
    ),
    MediaTypes.MUSIC.value: SUBVIEWS_BY_MEDIA_TYPE[MediaTypes.MUSIC.value],
    MediaTypes.PODCAST.value: (PODCAST_SUBVIEW_VALUES, PODCAST_SUBVIEW_LABELS, "episodes"),
}


def _canonical_subview(value, media_type: str) -> str:
    """Normalize a row's subview value to a known choice for its media type."""
    values, _labels, default = SUBVIEWS_BY_MEDIA_TYPE[media_type]
    raw_value = str(value or "").strip().lower()
    return raw_value if raw_value in values else default


AUTHOR_MEDIA_TYPES = {
    MediaTypes.BOOK.value,
    MediaTypes.MANGA.value,
    MediaTypes.COMIC.value,
    MediaTypes.COMIC_ISSUE.value,
}
HOME_PROGRESS_MEDIA_TYPES = {
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
}
HOME_PROGRESS_FILTER_MEDIA_TYPES = {*HOME_PROGRESS_MEDIA_TYPES, HOME_ALL_MEDIA_TYPE}
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
HOME_ONLY_SORTS = {
    HomeSortChoices.UPCOMING,
    HomeSortChoices.RECENT,
    HomeSortChoices.COMPLETION,
    HomeSortChoices.EPISODES_LEFT,
}
STATUS_FILTER_VALUES = {"all", *Status.values}
STATUS_FILTER_ALIASES = {"all": "all"}
for _status_choice in Status:
    STATUS_FILTER_ALIASES[str(_status_choice.value).strip().casefold()] = (
        _status_choice.value
    )
    STATUS_FILTER_ALIASES[str(_status_choice.label).strip().casefold()] = (
        _status_choice.value
    )

# Sort key of the Up Next row's own queue order.
UP_NEXT_SORT = "episode_feed"
HOME_QUERY_DEFAULT_FILTERS = {
    "status": [Status.IN_PROGRESS.value],
    "progress": "all",
    "rating": "all",
    "collection": "all",
    "genre": "",
    "year": "",
    "release": "all",
    "source": "",
    "language": "",
    "country": "",
    "platform": "",
    "origin": "",
    "format": "",
    "author": "",
    "provider": "",
    "tag": [],
    "tag_mode": "or",
}
# Home Screen only ever supports the query filters above, plus "subview"
# (music-only). It must NOT be derived from smart_rules.SMART_FILTER_KEYS:
# that set includes smart-list-only fields (completed_date_within_unit,
# release_date_from, rating_min, sort, ...) which have no Home Screen UI and
# would otherwise round-trip back from the browser and fail validation in
# validate_library_row_filters() for every media type.
HOME_SCREEN_FILTER_KEYS = tuple(
    dict.fromkeys((*HOME_QUERY_DEFAULT_FILTERS.keys(), "subview", "media_types", "media_subviews")),
)
SUPPORTED_FILTERS_BY_MEDIA_TYPE = {
    MediaTypes.TV.value: {
        "status",
        "progress",
        "rating",
        "collection",
        "genre",
        "year",
        "release",
        "source",
        "language",
        "country",
        "provider",
        "tag",
    },
    MediaTypes.SEASON.value: {
        "status",
        "rating",
        "collection",
        "genre",
        "year",
        "release",
        "source",
        "tag",
    },
    MediaTypes.MOVIE.value: {
        "status",
        "rating",
        "collection",
        "genre",
        "year",
        "release",
        "source",
        "language",
        "country",
        "provider",
        "tag",
    },
    MediaTypes.ANIME.value: {
        "status",
        "progress",
        "rating",
        "collection",
        "genre",
        "year",
        "release",
        "source",
        "language",
        "country",
        "provider",
        "tag",
    },
    MediaTypes.MANGA.value: {
        "status",
        "rating",
        "collection",
        "genre",
        "year",
        "release",
        "source",
        "format",
        "author",
        "tag",
    },
    MediaTypes.GAME.value: {
        "status",
        "rating",
        "collection",
        "genre",
        "year",
        "release",
        "source",
        "platform",
        "tag",
    },
    MediaTypes.BOARDGAME.value: {
        "status",
        "rating",
        "collection",
        "genre",
        "year",
        "release",
        "source",
        "tag",
    },
    MediaTypes.BOOK.value: {
        "status",
        "rating",
        "collection",
        "genre",
        "year",
        "release",
        "source",
        "format",
        "author",
        "tag",
    },
    MediaTypes.COMIC.value: {
        "status",
        "rating",
        "collection",
        "genre",
        "year",
        "release",
        "source",
        "format",
        "author",
        "tag",
    },
    MediaTypes.COMIC_ISSUE.value: {
        "status",
        "rating",
        "year",
        "release",
        "source",
        "author",
        "tag",
    },
    MediaTypes.MUSIC.value: {
        "subview",
        "status",
        "rating",
        "collection",
        "genre",
        "year",
        "release",
        "source",
        "origin",
        "tag",
    },
    MediaTypes.PODCAST.value: {
        "subview",
        "status",
        "rating",
        "collection",
        "genre",
        "year",
        "release",
        "source",
        "language",
        "country",
        "tag",
    },
}
# Videos use the shared item filters, without streaming-provider filtering.
SUPPORTED_FILTERS_BY_MEDIA_TYPE[MediaTypes.VIDEO.value] = (
    SUPPORTED_FILTERS_BY_MEDIA_TYPE[MediaTypes.MOVIE.value] - {"provider"}
)
SUPPORTED_FILTERS_BY_MEDIA_TYPE[HOME_ALL_MEDIA_TYPE] = {
    "media_types",
    "media_subviews",
    "status",
    "progress",
    "rating",
    "collection",
    "release",
    "tag",
}


class HomeScreenValidationError(ValidationError):
    """Raised when submitted Home screen settings are invalid."""


@dataclass
class HomeRowEntry:
    """Template-facing Home row item wrapper."""

    item: Item
    media: object | None = None
    use_podcast_show: bool = False
    podcast_show: object | None = None
    show_progress_controls: bool = True
    subtitle_override: object | None = None
    next_episode: dict | None = None


def resolve_home_row_direction(sort_by: str, direction: str | None = None) -> str:
    """Return a valid direction for the requested home-row sort key."""
    normalized = (direction or "").strip().lower()
    if normalized in DirectionChoices.values:
        return normalized

    if sort_by == HomeSortChoices.UPCOMING:
        return DirectionChoices.ASC
    if sort_by == HomeSortChoices.RECENT:
        return DirectionChoices.DESC
    if sort_by == HomeSortChoices.COMPLETION:
        return DirectionChoices.DESC
    if sort_by in {HomeSortChoices.EPISODES_LEFT, UP_NEXT_SORT}:
        return DirectionChoices.ASC
    if sort_by == HomeSortChoices.RANDOM:
        return DirectionChoices.DESC
    if sort_by == MediaSortChoices.NEXT_EPISODE_AIR_DATE:
        return DirectionChoices.DESC
    return BasicMedia.objects.resolve_direction(sort_by, None)


def get_enabled_home_media_types(user) -> list[str]:
    """Return enabled sidebar media types in stable display order."""
    return list(user.get_enabled_media_types())


def get_all_media_row_types(user) -> list[str]:
    """Return selectable top-level families for an All media row."""
    return [
        media_type
        for media_type in get_enabled_home_media_types(user)
        if media_type not in {MediaTypes.SEASON.value, MediaTypes.EPISODE.value}
    ]


def get_home_configurable_media_types(user) -> list[str]:
    """Return media types available for Home screen configuration.

    Follows the sidebar setting exactly, so a media type that is turned off
    (TV Seasons included) is neither offered in Home Screen settings nor
    rendered on Home. Its saved rows are kept and return when it is re-enabled.
    """
    types = [HOME_ALL_MEDIA_TYPE, *user.get_enabled_media_types()]

    preferred_order = getattr(user, "home_screen_media_type_order", None) or []
    ordered = [media_type for media_type in preferred_order if media_type in types]
    if HOME_ALL_MEDIA_TYPE not in ordered:
        ordered.insert(0, HOME_ALL_MEDIA_TYPE)
    remaining = [media_type for media_type in types if media_type not in ordered]
    return ordered + remaining


def get_allowed_sort_choices(media_type: str, row_type: str) -> list[dict]:
    """Return sort options for a home row."""
    if row_type == HomeScreenRowTypeChoices.UP_NEXT:
        return [
            {"value": UP_NEXT_SORT, "label": gettext("Up Next")},
            {"value": MediaSortChoices.TITLE, "label": gettext("Title")},
            {"value": MediaSortChoices.SCORE, "label": gettext("Rating")},
            {"value": MediaSortChoices.DATE_ADDED, "label": gettext("Date Added")},
            {"value": MediaSortChoices.END_DATE, "label": gettext("Last Watched")},
            {"value": HomeSortChoices.RANDOM, "label": gettext("Random")},
        ]
    if media_type == HOME_ALL_MEDIA_TYPE:
        return [
            {"value": HomeSortChoices.RECENT, "label": gettext("Recent")},
            {"value": MediaSortChoices.END_DATE, "label": gettext("Last Watched")},
            {"value": MediaSortChoices.TITLE, "label": gettext("Title")},
            {"value": MediaSortChoices.SCORE, "label": gettext("Rating")},
            {"value": MediaSortChoices.DATE_ADDED, "label": gettext("Date Added")},
            {"value": HomeSortChoices.RANDOM, "label": gettext("Random")},
        ]
    sort_choices: list[tuple[str, str]] = [
        (MediaSortChoices.SCORE, gettext("Rating")),
        (MediaSortChoices.TITLE, gettext("Title")),
        (MediaSortChoices.PROGRESS, gettext("Progress")),
        (MediaSortChoices.RELEASE_DATE, gettext("Release Date")),
        (MediaSortChoices.NEXT_EPISODE_AIR_DATE, gettext("Episode Air Date")),
        (MediaSortChoices.DATE_ADDED, gettext("Date Added")),
        (MediaSortChoices.START_DATE, gettext("Start Date")),
        (MediaSortChoices.END_DATE, gettext("Last Watched")),
    ]

    if media_type in CRITIC_RATING_MEDIA_TYPES:
        sort_choices.append((MediaSortChoices.CRITIC_RATING, gettext("Critic Rating")))
    if media_type in AUTHOR_MEDIA_TYPES:
        sort_choices.append((MediaSortChoices.AUTHOR, gettext("Author")))
    if media_type in POPULARITY_MEDIA_TYPES:
        sort_choices.append((MediaSortChoices.POPULARITY, gettext("Popularity")))
    if media_type in RUNTIME_MEDIA_TYPES:
        sort_choices.append((MediaSortChoices.RUNTIME, gettext("Runtime")))
        sort_choices.append((MediaSortChoices.TIME_WATCHED, gettext("Time Watched")))
    if media_type in PLAYS_MEDIA_TYPES:
        sort_choices.append((MediaSortChoices.PLAYS, gettext("Plays")))
    if media_type == MediaTypes.GAME.value:
        sort_choices.append((MediaSortChoices.TIME_TO_BEAT, gettext("Time to Beat")))
    if media_type == MediaTypes.TV.value:
        sort_choices.append((MediaSortChoices.TIME_LEFT, gettext("Time Left")))
    if (
        media_type not in HOME_PROGRESS_MEDIA_TYPES
        and media_type != MediaTypes.SEASON.value
    ):
        sort_choices = [
            choice
            for choice in sort_choices
            if choice[0] != MediaSortChoices.NEXT_EPISODE_AIR_DATE
        ]

    if row_type == HomeScreenRowTypeChoices.LIBRARY_QUERY:
        sort_choices.extend(
            [
                (HomeSortChoices.UPCOMING, gettext("Upcoming")),
                (HomeSortChoices.RECENT, gettext("Recent")),
                (HomeSortChoices.COMPLETION, gettext("Completion")),
                (HomeSortChoices.EPISODES_LEFT, gettext("Episodes Left")),
            ],
        )

    sort_choices = relabel_end_date_sort_choice(media_type, sort_choices)
    sort_choices.append((HomeSortChoices.RANDOM, gettext("Random")))

    deduped: list[dict] = []
    seen = set()
    for value, label in sort_choices:
        if value in seen:
            continue
        seen.add(value)
        deduped.append({"value": value, "label": label})
    return deduped


def _media_type_group_label(media_type: str) -> str:
    if media_type == HOME_ALL_MEDIA_TYPE:
        return gettext("All media")
    return app_tags.media_type_readable_plural(media_type)


def _media_type_group_icon_name(media_type: str) -> str:
    return "home" if media_type == HOME_ALL_MEDIA_TYPE else media_type


def _default_library_sort(user, media_type: str) -> str:
    requested = getattr(user, "home_sort", HomeSortChoices.TITLE)
    allowed = {
        choice["value"]
        for choice in get_allowed_sort_choices(
            media_type, HomeScreenRowTypeChoices.LIBRARY_QUERY
        )
    }
    if requested in allowed:
        return requested
    return MediaSortChoices.TITLE


def _seeded_home_media_types(user) -> list[str]:
    """Return the enabled media types that should receive default Home rows."""
    return list(get_enabled_home_media_types(user))


def _preferred_default_library_sort(user, media_type: str) -> str:
    """Return the Home-row default sort that best matches legacy Home behavior."""
    requested = _default_library_sort(user, media_type)
    if requested != HomeSortChoices.UPCOMING:
        return requested
    if media_type == MediaTypes.SEASON.value:
        return HomeSortChoices.UPCOMING
    return HomeSortChoices.RECENT


def _home_default_library_sort(media_type: str, user) -> str:
    """Return the desired default sort for a Home library row."""
    if media_type in HOME_PROGRESS_MEDIA_TYPES:
        return MediaSortChoices.NEXT_EPISODE_AIR_DATE
    return _preferred_default_library_sort(user, media_type)


def _legacy_home_default_library_sort(user, media_type: str) -> str:
    """Return the historical sort used by older seeded Home rows."""
    if media_type in HOME_PROGRESS_MEDIA_TYPES:
        return MediaSortChoices.TITLE
    return _default_library_sort(user, media_type)


def _default_recent_row_direction() -> str:
    return DirectionChoices.DESC


def _build_default_rows_for_media_type(user, media_type: str) -> list[HomeScreenRow]:
    sort_by = _home_default_library_sort(media_type, user)
    default_filters = dict(HOME_QUERY_DEFAULT_FILTERS)
    if media_type in HOME_PROGRESS_MEDIA_TYPES:
        default_filters["progress"] = "not_caught_up"
    defaults = [
        HomeScreenRow(
            user=user,
            media_type=media_type,
            position=0,
            enabled=True,
            row_type=HomeScreenRowTypeChoices.LIBRARY_QUERY,
            sort_by=sort_by,
            direction=resolve_home_row_direction(sort_by),
            filters=default_filters,
        ),
    ]
    if getattr(user, "show_planned_on_home", "disabled") != "disabled":
        planned_filters = dict(HOME_QUERY_DEFAULT_FILTERS)
        planned_filters["status"] = [Status.PLANNING.value]
        defaults.append(
            HomeScreenRow(
                user=user,
                media_type=media_type,
                position=len(defaults),
                enabled=True,
                row_type=HomeScreenRowTypeChoices.LIBRARY_QUERY,
                sort_by=sort_by,
                direction=resolve_home_row_direction(sort_by),
                filters=planned_filters,
            ),
        )
    return defaults


def _row_signature(
    row: HomeScreenRow,
    media_type: str,
    *,
    ignore_direction: bool = False,
) -> dict:
    filters = {}
    custom_list_id = None
    if row.row_type == HomeScreenRowTypeChoices.LIBRARY_QUERY:
        filters = _normalized_filter_payload(row.filters or {}, media_type)
    elif row.row_type == HomeScreenRowTypeChoices.CUSTOM_LIST:
        custom_list_id = row.custom_list_id

    return {
        "enabled": row.enabled,
        "row_type": row.row_type,
        "sort_by": row.sort_by,
        "direction": None if ignore_direction else row.direction,
        "filters": filters,
        "custom_list_id": custom_list_id,
    }


def _legacy_default_rows_for_media_type(user, media_type: str) -> list[HomeScreenRow]:
    sort_by = _legacy_home_default_library_sort(user, media_type)
    defaults = [
        HomeScreenRow(
            user=user,
            media_type=media_type,
            position=0,
            enabled=True,
            row_type=HomeScreenRowTypeChoices.LIBRARY_QUERY,
            sort_by=sort_by,
            direction=resolve_home_row_direction(sort_by),
            filters=dict(HOME_QUERY_DEFAULT_FILTERS),
        ),
    ]
    if getattr(user, "show_planned_on_home", "disabled") != "disabled":
        planned_filters = dict(HOME_QUERY_DEFAULT_FILTERS)
        planned_filters["status"] = [Status.PLANNING.value]
        defaults.append(
            HomeScreenRow(
                user=user,
                media_type=media_type,
                position=len(defaults),
                enabled=True,
                row_type=HomeScreenRowTypeChoices.LIBRARY_QUERY,
                sort_by=sort_by,
                direction=resolve_home_row_direction(sort_by),
                filters=planned_filters,
            ),
        )
    defaults.append(
        HomeScreenRow(
            user=user,
            media_type=media_type,
            position=len(defaults),
            enabled=True,
            row_type=HomeScreenRowTypeChoices.RECENTLY_UNRATED,
            sort_by=HomeSortChoices.RECENT,
            direction=_default_recent_row_direction(),
            filters={},
        ),
    )
    return defaults


def _single_query_default_rows_for_media_type(
    user,
    media_type: str,
    sort_by: str,
) -> list[HomeScreenRow]:
    defaults = [
        HomeScreenRow(
            user=user,
            media_type=media_type,
            position=0,
            enabled=True,
            row_type=HomeScreenRowTypeChoices.LIBRARY_QUERY,
            sort_by=sort_by,
            direction=resolve_home_row_direction(sort_by),
            filters=dict(HOME_QUERY_DEFAULT_FILTERS),
        ),
    ]
    if getattr(user, "show_planned_on_home", "disabled") != "disabled":
        planned_filters = dict(HOME_QUERY_DEFAULT_FILTERS)
        planned_filters["status"] = [Status.PLANNING.value]
        defaults.append(
            HomeScreenRow(
                user=user,
                media_type=media_type,
                position=len(defaults),
                enabled=True,
                row_type=HomeScreenRowTypeChoices.LIBRARY_QUERY,
                sort_by=sort_by,
                direction=resolve_home_row_direction(sort_by),
                filters=planned_filters,
            ),
        )
    return defaults


def _legacy_default_row_variants_for_media_type(
    user, media_type: str
) -> list[list[HomeScreenRow]]:
    """Return historical seeded row layouts that should upgrade in place."""
    return [
        _legacy_default_rows_for_media_type(user, media_type),
        _single_query_default_rows_for_media_type(
            user,
            media_type,
            _legacy_home_default_library_sort(user, media_type),
        ),
        _single_query_default_rows_for_media_type(
            user,
            media_type,
            _preferred_default_library_sort(user, media_type),
        ),
    ]


def _rows_match_signature(
    existing_rows: list[HomeScreenRow],
    expected_rows: list[HomeScreenRow],
    media_type: str,
    *,
    ignore_direction: bool = False,
) -> bool:
    if len(existing_rows) != len(expected_rows):
        return False
    return all(
        _row_signature(existing, media_type, ignore_direction=ignore_direction)
        == _row_signature(expected, media_type, ignore_direction=ignore_direction)
        for existing, expected in zip(existing_rows, expected_rows, strict=False)
    )


def ensure_home_screen_rows(user) -> list[HomeScreenRow]:
    """Ensure each enabled media type has a default Home row set."""
    enabled_media_types = get_enabled_home_media_types(user)
    rows = list(
        user.home_screen_rows.select_related("custom_list").order_by(
            "media_type", "position", "id"
        ),
    )
    rows_by_media_type: dict[str, list[HomeScreenRow]] = defaultdict(list)
    for row in rows:
        rows_by_media_type[row.media_type].append(row)

    media_types_to_reset: list[str] = []
    replacement_rows: list[HomeScreenRow] = []
    for media_type in enabled_media_types:
        media_rows = rows_by_media_type.get(media_type, [])
        if not media_rows:
            continue
        ignore_legacy_direction = media_type in HOME_PROGRESS_MEDIA_TYPES
        if not any(
            _rows_match_signature(
                media_rows,
                legacy_defaults,
                media_type,
                ignore_direction=ignore_legacy_direction,
            )
            for legacy_defaults in _legacy_default_row_variants_for_media_type(
                user, media_type
            )
        ):
            continue
        desired_defaults = _build_default_rows_for_media_type(user, media_type)
        if _rows_match_signature(media_rows, desired_defaults, media_type):
            continue
        media_types_to_reset.append(media_type)
        replacement_rows.extend(desired_defaults)

    if media_types_to_reset:
        with transaction.atomic():
            HomeScreenRow.objects.filter(
                user=user,
                media_type__in=media_types_to_reset,
            ).delete()
            if replacement_rows:
                HomeScreenRow.objects.bulk_create(replacement_rows)
        rows = list(
            user.home_screen_rows.select_related("custom_list").order_by(
                "media_type", "position", "id"
            ),
        )

    existing_media_types = {row.media_type for row in rows}
    saved_media_types = set(getattr(user, "home_screen_media_type_order", None) or [])
    missing_media_types = [
        media_type
        for media_type in _seeded_home_media_types(user)
        if media_type not in existing_media_types
        and media_type not in saved_media_types
    ]
    if missing_media_types:
        HomeScreenRow.objects.bulk_create(
            [
                row
                for media_type in missing_media_types
                for row in _build_default_rows_for_media_type(user, media_type)
            ],
        )
        rows = list(
            user.home_screen_rows.select_related("custom_list").order_by(
                "media_type", "position", "id"
            ),
        )
    return rows


def build_filter_field_data(
    user,
    media_type: str,
    precomputed_tags: list[str] | None = None,
) -> list[dict]:
    """Return template-friendly filter field definitions for a media type.

    build_rule_filter_data scans the user's whole library for this media
    type to aggregate facet options (~1.5s each on large libraries), and
    the Home Screen settings page needs one per enabled media type — so
    the payload is cached and registered for invalidation on media save.
    All Media only needs fixed choices and tags, so it skips that aggregation.
    """
    from django.core.cache import cache

    from app import cache_utils

    tags_fingerprint = hashlib.md5(  # noqa: S324 - cache key, not security
        "\x1f".join(precomputed_tags or ()).encode(),
    ).hexdigest()[:12]
    filter_media_types = (
        get_all_media_row_types(user)
        if media_type == HOME_ALL_MEDIA_TYPE
        else [media_type]
    )
    media_types_fingerprint = "-".join(filter_media_types)
    cache_key = (
        f"home_filter_fields_v3_{user.id}_{media_type}_"
        f"{media_types_fingerprint}_{tags_fingerprint}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    if media_type == HOME_ALL_MEDIA_TYPE:
        # Mixed rows expose fixed choices and tags, not library-wide facets.
        # Building genres/authors/etc. here scans every enabled library.
        tags = precomputed_tags
        if tags is None:
            tags = list(Tag.objects.filter(user=user).order_by("name").values_list("name", flat=True))
        filter_data = {"tags": tags}
    else:
        filter_data = smart_rules.build_rule_filter_data(
            user,
            filter_media_types,
            "all",
            "",
            include_collection_only_untracked=True,
            precomputed_tags=precomputed_tags,
        )
    filter_data["show_authors"] = media_type in AUTHOR_MEDIA_TYPES

    values, labels, _default = SUBVIEWS_BY_MEDIA_TYPE.get(
        media_type, SUBVIEWS_BY_MEDIA_TYPE[MediaTypes.MUSIC.value]
    )
    field_definitions = [
        {
            "key": "media_types",
            "label": "Media types",
            "options": [
                {
                    "value": value,
                    "label": _media_type_group_label(value),
                    "subviews": [
                        {"value": choice, "label": gettext(ALL_MEDIA_SUBVIEWS[value][1][choice])}
                        for choice in ALL_MEDIA_SUBVIEWS[value][0]
                    ] if value in ALL_MEDIA_SUBVIEWS else [],
                    "default_subview": ALL_MEDIA_SUBVIEWS[value][2]
                    if value in ALL_MEDIA_SUBVIEWS else "",
                }
                for value in get_all_media_row_types(user)
            ],
            "visible": media_type == HOME_ALL_MEDIA_TYPE,
        },
        {
            "key": "subview",
            "label": "Media Type",
            "options": [
                {"value": value, "label": labels[value]}
                for value in values
            ],
        },
        {
            "key": "status",
            "label": "Status",
            "options": [
                {"value": "all", "label": "All"},
                {"value": Status.IN_PROGRESS.value, "label": Status.IN_PROGRESS.label},
                {"value": Status.COMPLETED.value, "label": Status.COMPLETED.label},
                {"value": Status.PLANNING.value, "label": Status.PLANNING.label},
                {"value": Status.PAUSED.value, "label": Status.PAUSED.label},
                {"value": Status.DROPPED.value, "label": Status.DROPPED.label},
            ],
        },
        {
            "key": "progress",
            "label": "Progress",
            "options": [
                {"value": "all", "label": "All"},
                {"value": "caught_up", "label": "Caught Up"},
                {"value": "not_caught_up", "label": "Not Caught Up"},
            ],
            "visible": media_type in HOME_PROGRESS_FILTER_MEDIA_TYPES,
        },
        {
            "key": "rating",
            "label": "Rating",
            "options": [
                {"value": "all", "label": "All"},
                {"value": "rated", "label": "Rated"},
                {"value": "not_rated", "label": "Not Rated"},
            ],
        },
        {
            "key": "collection",
            "label": "Collection",
            "options": [
                {"value": "all", "label": "All"},
                {"value": "collected", "label": "Collected"},
                {"value": "not_collected", "label": "Not Collected"},
            ],
        },
        {
            "key": "genre",
            "label": "Genre",
            "options": [{"value": "", "label": "Any"}]
            + [
                {"value": value, "label": value}
                for value in filter_data.get("genres", [])
            ],
        },
        {
            "key": "year",
            "label": "Year",
            "options": [{"value": "", "label": "Any"}, *filter_data.get("years", [])],
        },
        {
            "key": "release",
            "label": "Release",
            "options": [
                {"value": "all", "label": "All"},
                {"value": "released", "label": "Released"},
                {"value": "not_released", "label": "Not Released"},
            ],
        },
        {
            "key": "source",
            "label": "Source",
            "options": [{"value": "", "label": "Any"}, *filter_data.get("sources", [])],
        },
        {
            "key": "language",
            "label": "Language",
            "options": [
                {"value": "", "label": "Any"},
                *filter_data.get("languages", []),
            ],
            "visible": filter_data.get("show_languages", False),
        },
        {
            "key": "country",
            "label": "Country",
            "options": [
                {"value": "", "label": "Any"},
                *filter_data.get("countries", []),
            ],
            "visible": filter_data.get("show_countries", False),
        },
        {
            "key": "platform",
            "label": "Platform",
            "options": [
                {"value": "", "label": "Any"},
                *filter_data.get("platforms", []),
            ],
            "visible": filter_data.get("show_platforms", False),
        },
        {
            "key": "origin",
            "label": "Origin",
            "options": [{"value": "", "label": "Any"}, *filter_data.get("origins", [])],
            "visible": filter_data.get("show_origins", False),
        },
        {
            "key": "format",
            "label": "Format",
            "options": [{"value": "", "label": "Any"}, *filter_data.get("formats", [])],
            "visible": filter_data.get("show_formats", False),
        },
        {
            "key": "author",
            "label": "Author",
            "options": [{"value": "", "label": "Any"}, *filter_data.get("authors", [])],
            "visible": filter_data.get("show_authors", False),
        },
        {
            "key": "provider",
            "label": "Streaming Service",
            "options": [
                {"value": "", "label": "Any"},
                *filter_data.get("providers", []),
            ],
            "visible": filter_data.get("show_providers", False),
        },
        {
            "key": "tag",
            "label": "Tag",
            "options": [
                {"value": value, "label": value}
                for value in filter_data.get("tags", [])
            ],
        },
    ]

    supported_fields = SUPPORTED_FILTERS_BY_MEDIA_TYPE.get(media_type, set())
    visible_fields = []
    for field in field_definitions:
        if field["key"] not in supported_fields:
            continue
        if field.get("visible", True):
            visible_fields.append(field)

    cache.set(cache_key, visible_fields, getattr(settings, "CACHE_TIMEOUT", None))
    cache_utils.register_media_list_cache_key(user.id, cache_key)
    return visible_fields


_SUMMARY_STATIC_FILTER_LABELS = {
    "progress": {
        "caught_up": "Caught Up",
        "not_caught_up": "Not Caught Up",
    },
    "rating": {
        "rated": "Rated",
        "not_rated": "Not Rated",
    },
    "collection": {
        "collected": "Collected",
        "not_collected": "Not Collected",
    },
    "release": {
        "released": "Released",
        "not_released": "Not Released",
    },
    "source": dict(Sources.choices),
    "format": {
        "hardcover": "Hardcover",
        "paperback": "Paperback",
        "ebook": "eBook",
        "audiobook": "Audiobook",
    },
}


def _summary_filter_label(key: str, value: str) -> str:
    label = _SUMMARY_STATIC_FILTER_LABELS.get(key, {}).get(value)
    if label:
        return label
    if key == "year" and value == "unknown":
        return "Unknown Year"
    return value


def _canonical_status_filter(value, default="all") -> str | None:
    """Normalize status aliases and labels to the stored choice value."""
    raw_value = str(value or "").strip()
    if not raw_value:
        return default
    return STATUS_FILTER_ALIASES.get(raw_value.casefold(), default)


def _canonical_progress_filter(value, default="all") -> str:
    """Normalize progress aliases to the stored choice value."""
    raw_value = str(value or "").strip().casefold()
    if not raw_value:
        return default
    aliases = {
        "all": "all",
        "caught up": "caught_up",
        "caught_up": "caught_up",
        "not caught up": "not_caught_up",
        "not_caught_up": "not_caught_up",
    }
    return aliases.get(raw_value, default)


def describe_library_query(filters: dict, user, media_type: str) -> str:
    """Return a compact query-row summary for settings and home."""
    normalized = _normalized_filter_payload(filters, media_type)

    status_values = [value for value in (normalized.get("status") or []) if value]
    status_labels = dict(Status.choices)
    if status_values:
        parts = [" & ".join(status_labels.get(value, value) for value in status_values)]
    else:
        parts = ["Library"]

    if media_type in SUBVIEWS_BY_MEDIA_TYPE:
        subview_label = SUBVIEWS_BY_MEDIA_TYPE[media_type][1][
            _canonical_subview(normalized.get("subview"), media_type)
        ]
        if parts[0] == "Library":
            parts[0] = subview_label
        else:
            parts.insert(1, subview_label)

    if media_type == HOME_ALL_MEDIA_TYPE:
        for family in _all_media_type_selection(user, filters):
            if family in ALL_MEDIA_SUBVIEWS:
                level = normalized["media_subviews"][family]
                parts.append(f"{_media_type_group_label(family)}: {gettext(ALL_MEDIA_SUBVIEWS[family][1][level])}")

    for key in (
        "progress",
        "rating",
        "collection",
        "genre",
        "year",
        "release",
        "source",
        "language",
        "country",
        "platform",
        "origin",
        "format",
        "author",
    ):
        value = str(normalized.get(key, "") or "").strip()
        if not value or value in {"all", "Any"}:
            continue
        label = _summary_filter_label(key, value)
        parts.append(label)
        if len(parts) >= MAX_SUMMARY_FILTER_PARTS:
            break

    tag_values = [value for value in (normalized.get("tag") or []) if value]
    if tag_values and len(parts) < MIN_TAG_PARTS:
        tag_mode = normalized.get("tag_mode", "or")
        joined = " & " if tag_mode == "and" else " or "
        tag_label = joined.join(tag_values)
        if tag_mode == "not":
            tag_label = f"Not tagged {tag_label}"
        parts.append(tag_label)

    return " • ".join(parts)


def serialize_settings_sections(user) -> list[dict]:
    """Return Home Screen settings sections for the enabled sidebar media types.

    `filter_fields` is intentionally omitted here — it's expensive to compute
    (full smart-rule facet scan per media type) and the UI only needs it for
    whichever section the user actually expands, so it's fetched lazily via
    `home_screen_filter_fields` instead of eagerly for every section.
    """
    rows = ensure_home_screen_rows(user)
    rows_by_media_type: dict[str, list[HomeScreenRow]] = defaultdict(list)
    for row in rows:
        rows_by_media_type[row.media_type].append(row)

    sections = []
    for media_type in get_home_configurable_media_types(user):
        media_rows = rows_by_media_type.get(media_type, [])
        sections.append(
            {
                "media_type": media_type,
                "label": _media_type_group_label(media_type),
                "available_media_types": get_all_media_row_types(user)
                if media_type == HOME_ALL_MEDIA_TYPE
                else [],
                "icon_svg": str(
                    app_tags.icon(
                        _media_type_group_icon_name(media_type),
                        False,
                        "w-5 h-5 text-slate-300",
                    )
                ),
                "sort_choices": {
                    HomeScreenRowTypeChoices.LIBRARY_QUERY: get_allowed_sort_choices(
                        media_type,
                        HomeScreenRowTypeChoices.LIBRARY_QUERY,
                    ),
                    HomeScreenRowTypeChoices.CUSTOM_LIST: get_allowed_sort_choices(
                        media_type,
                        HomeScreenRowTypeChoices.CUSTOM_LIST,
                    ),
                    HomeScreenRowTypeChoices.UP_NEXT: get_allowed_sort_choices(
                        media_type,
                        HomeScreenRowTypeChoices.UP_NEXT,
                    ),
                },
                "filter_fields": [],
                "rows": [
                    _serialize_settings_row(user, row, media_type) for row in media_rows
                ],
            },
        )
    return sections


def serialize_settings_filter_fields(user, media_type: str) -> list[dict]:
    """Return the filter fields for one Home Screen settings section on demand."""
    tag_names = list(
        Tag.objects.filter(user=user).values_list("name", flat=True).order_by("name")
    )
    return build_filter_field_data(user, media_type, precomputed_tags=tag_names)


def row_title(row: HomeScreenRow, user) -> str:
    """Return the display title for a configured row."""
    custom_title = (row.title or "").strip()
    if custom_title:
        return custom_title
    if row.row_type == HomeScreenRowTypeChoices.UP_NEXT:
        return "Up Next"
    if row.row_type == HomeScreenRowTypeChoices.CUSTOM_LIST:
        if row.custom_list_id and row.custom_list:
            return row.custom_list.name
        return "List / Smart List"
    if row.row_type == HomeScreenRowTypeChoices.RECENTLY_UNRATED:
        return RECENTLY_UNRATED_LABEL
    return describe_library_query(row.filters or {}, user, row.media_type)


def row_summary(row: HomeScreenRow, user) -> str:
    """Return a compact subtitle for a configured row."""
    if row.row_type == HomeScreenRowTypeChoices.UP_NEXT:
        return ""
    if row.row_type == HomeScreenRowTypeChoices.CUSTOM_LIST:
        if row.custom_list_id and row.custom_list:
            return "List-backed row"
        return "Choose a list or smart list"
    if row.row_type == HomeScreenRowTypeChoices.RECENTLY_UNRATED:
        return "Recent unrated plays from this library"
    sort_choices = {
        choice["value"]: choice["label"]
        for choice in get_allowed_sort_choices(row.media_type, row.row_type)
    }
    sort_label = sort_choices.get(row.sort_by, row.sort_by.replace("_", " ").title())
    direction_label = (
        "Ascending" if row.direction == DirectionChoices.ASC else "Descending"
    )
    return f"Sorted by {sort_label} • {direction_label}"


def home_row_inline_summary(row: HomeScreenRow, user) -> str | None:
    """Return the inline sort label for the Home row header."""
    if row.row_type != HomeScreenRowTypeChoices.LIBRARY_QUERY:
        return None

    sort_choices = {
        choice["value"]: choice["label"]
        for choice in get_allowed_sort_choices(row.media_type, row.row_type)
    }
    return sort_choices.get(row.sort_by, row.sort_by.replace("_", " ").title())


def home_row_header_title_parts(row: HomeScreenRow, user) -> tuple[str, str | None]:
    """Return the main title and optional filter suffix for the Home row header."""
    title = row_title(row, user)
    if (row.title or "").strip():
        return title, None
    if row.row_type != HomeScreenRowTypeChoices.LIBRARY_QUERY:
        return title, None

    parts = title.split(" • ")
    if len(parts) <= 1:
        return title, None
    return parts[0], " • ".join(parts[1:])


def toggle_home_row_direction(user, row_id: int) -> HomeScreenRow:
    """Flip a library Home row's direction and persist it."""
    row = (
        HomeScreenRow.objects.filter(
            user=user,
            id=row_id,
            row_type=HomeScreenRowTypeChoices.LIBRARY_QUERY,
        )
        .select_related("custom_list")
        .first()
    )
    if not row:
        msg = "Home row not found."
        raise HomeScreenValidationError(msg)

    row.direction = (
        DirectionChoices.DESC
        if row.direction == DirectionChoices.ASC
        else DirectionChoices.ASC
    )
    row.save(update_fields=["direction"])
    return row


def _as_list(value) -> list:
    """Coerce a legacy scalar or a list/tuple into a list, leaving None distinct."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _normalize_status_list(raw_value, fallback: list[str]) -> list[str]:
    """Normalize a status list, falling back to `fallback` only when absent (None)."""
    if raw_value is None:
        return list(fallback)
    normalized_values = []
    seen = set()
    for entry in _as_list(raw_value):
        canonical = _canonical_status_filter(entry, None)
        if not canonical or canonical == "all" or canonical not in Status.values:
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        normalized_values.append(canonical)
    return normalized_values


def _normalized_filter_payload(filters: dict | None, media_type: str) -> dict:
    raw_filters = dict(filters or {})
    # subview (music, podcast) is not a smart-rule filter. handling separately
    raw_subview = raw_filters.pop("subview", None)
    raw_media_subviews = raw_filters.pop("media_subviews", {})
    raw_filters.pop("media_types", None)
    if "status" in raw_filters:
        raw_filters["status"] = _normalize_status_list(raw_filters.get("status"), [])

    normalized = smart_rules.normalize_rule_payload(
        {
            "media_types": [media_type],
            **HOME_QUERY_DEFAULT_FILTERS,
            **raw_filters,
        },
        owner=None,
    )
    normalized.pop("media_types", None)
    normalized["status"] = _normalize_status_list(
        raw_filters.get("status", normalized.get("status")),
        HOME_QUERY_DEFAULT_FILTERS["status"],
    )
    normalized["progress"] = _canonical_progress_filter(
        raw_filters.get("progress", normalized.get("progress")),
        HOME_QUERY_DEFAULT_FILTERS["progress"],
    )
    payload = {
        key: normalized.get(key, HOME_QUERY_DEFAULT_FILTERS.get(key, ""))
        for key in HOME_SCREEN_FILTER_KEYS
        if key not in {"subview", "media_types", "media_subviews"}
    }
    if media_type in SUBVIEWS_BY_MEDIA_TYPE:
        payload["subview"] = _canonical_subview(raw_subview, media_type)
    if media_type == HOME_ALL_MEDIA_TYPE:
        payload["media_subviews"] = {
            family: raw_media_subviews.get(family, definition[2])
            for family, definition in ALL_MEDIA_SUBVIEWS.items()
        } if isinstance(raw_media_subviews, dict) else {}
    return payload


def _all_media_type_selection(user, filters: dict | None) -> list[str]:
    """Resolve selected enabled families; missing media_types means all."""
    enabled_media_types = get_all_media_row_types(user)
    if not isinstance(filters, dict) or "media_types" not in filters:
        return enabled_media_types
    requested = {
        str(value or "").strip() for value in _as_list(filters.get("media_types"))
    }
    return [value for value in enabled_media_types if value in requested]


def _serialize_settings_row(user, row: HomeScreenRow, media_type: str) -> dict:
    """Return one Home Screen settings row for the client."""
    raw_filters = {
        key: value for key, value in (row.filters or {}).items() if key != "recent_show"
    }
    filters = _normalized_filter_payload(raw_filters, media_type)
    if media_type == HOME_ALL_MEDIA_TYPE:
        filters["media_types"] = _all_media_type_selection(user, row.filters)
    recent_show = ""
    if (
        row.media_type == MediaTypes.MUSIC.value
        and row.row_type == HomeScreenRowTypeChoices.RECENTLY_UNRATED
    ):
        recent_show = recent_show_mode(row)
    return {
        "id": row.id,
        "client_id": f"row-{row.id}",
        "enabled": row.enabled,
        "row_type": row.row_type,
        "custom_list_id": row.custom_list_id,
        "custom_list_name": row.custom_list.name if row.custom_list_id else "",
        "sort_by": row.sort_by,
        "direction": row.direction,
        "filters": filters,
        "recent_show": recent_show,
        "title": row_title(row, user),
        "custom_title": row.title or "",
        "summary": row_summary(row, user),
    }


def _row_payload_to_model(
    user, media_type: str, row_payload: dict, position: int
) -> HomeScreenRow:
    row_type = str(row_payload.get("row_type") or "").strip()
    if row_type not in HomeScreenRowTypeChoices.values:
        msg = f"Unsupported row type for {media_type}."
        raise HomeScreenValidationError(msg)
    if (
        media_type == HOME_ALL_MEDIA_TYPE
        and row_type not in {HomeScreenRowTypeChoices.LIBRARY_QUERY, HomeScreenRowTypeChoices.UP_NEXT}
    ):
        msg = gettext("All media supports library and Up Next rows.")
        raise HomeScreenValidationError(msg)

    if row_type == HomeScreenRowTypeChoices.UP_NEXT and (
        media_type not in {HOME_ALL_MEDIA_TYPE, MediaTypes.TV.value} or not user.tv_enabled
    ):
        msg = gettext("Up Next requires the TV library.")
        raise HomeScreenValidationError(msg)
    enabled = bool(row_payload.get("enabled", True))
    custom_list = None
    filters = {}

    if row_type == HomeScreenRowTypeChoices.CUSTOM_LIST:
        try:
            custom_list_id = int(row_payload.get("custom_list_id") or 0)
        except (TypeError, ValueError):
            custom_list_id = 0
        custom_list = (
            CustomList.objects.get_user_lists(user).filter(id=custom_list_id).first()
        )
        if not custom_list:
            msg = f"Choose an accessible list for {media_type}."
            raise HomeScreenValidationError(msg)
        sort_choices = get_allowed_sort_choices(media_type, row_type)
    elif row_type == HomeScreenRowTypeChoices.UP_NEXT:
        sort_choices = get_allowed_sort_choices(media_type, row_type)
    elif row_type == HomeScreenRowTypeChoices.RECENTLY_UNRATED:
        sort_choices = []
        if media_type == MediaTypes.MUSIC.value:
            filters = {"recent_show": _clean_recent_show(row_payload.get("recent_show"))}
    else:
        filters = validate_library_row_filters(
            row_payload.get("filters"), media_type, user=user
        )
        sort_choices = get_allowed_sort_choices(media_type, row_type)

    allowed_sort_values = {choice["value"] for choice in sort_choices}
    sort_by = str(row_payload.get("sort_by") or "").strip()
    if row_type == HomeScreenRowTypeChoices.RECENTLY_UNRATED:
        sort_by = HomeSortChoices.RECENT
        direction = _default_recent_row_direction()
    else:
        if row_type == HomeScreenRowTypeChoices.UP_NEXT and not sort_by:
            sort_by = UP_NEXT_SORT
        if sort_by not in allowed_sort_values:
            msg = f"Unsupported sort for {media_type}."
            raise HomeScreenValidationError(msg)
        direction = resolve_home_row_direction(sort_by, row_payload.get("direction"))
        if direction not in DirectionChoices.values:
            msg = f"Unsupported direction for {media_type}."
            raise HomeScreenValidationError(msg)

    custom_title = str(row_payload.get("custom_title") or "").strip()[:100]

    return HomeScreenRow(
        user=user,
        media_type=media_type,
        position=position,
        enabled=enabled,
        title=custom_title,
        row_type=row_type,
        custom_list=custom_list,
        sort_by=sort_by,
        direction=direction,
        filters=filters,
    )


def validate_library_row_filters(
    raw_filters: dict | None,
    media_type: str,
    *,
    user=None,
) -> dict:
    """Validate one library-query filter payload."""
    if raw_filters is None:
        raw_filters = {}
    if not isinstance(raw_filters, dict):
        msg = "Library row filters must be an object."
        raise HomeScreenValidationError(msg)

    supported = SUPPORTED_FILTERS_BY_MEDIA_TYPE.get(media_type, set())
    if "tag" in supported:
        supported = supported | {"tag_mode"}
    for key, value in raw_filters.items():
        if key not in HOME_SCREEN_FILTER_KEYS:
            msg = f"Unsupported filter '{key}' for {media_type}."
            raise HomeScreenValidationError(msg)
        if key not in supported and str(value or "").strip():
            if key == "progress" and _canonical_progress_filter(value, "all") == "all":
                continue
            msg = f"Filter '{key}' is not available for {media_type}."
            raise HomeScreenValidationError(msg)

    normalized = _normalized_filter_payload(raw_filters, media_type)
    if "media_subviews" in raw_filters:
        choices = raw_filters["media_subviews"]
        if not isinstance(choices, dict) or any(
            family not in ALL_MEDIA_SUBVIEWS or value not in ALL_MEDIA_SUBVIEWS[family][0]
            for family, value in choices.items()
        ):
            msg = "Unsupported All media display level."
            raise HomeScreenValidationError(msg)
    if media_type == HOME_ALL_MEDIA_TYPE and "media_types" in raw_filters:
        raw_media_types = raw_filters["media_types"]
        if not isinstance(raw_media_types, list):
            msg = "All media types must be a list."
            raise HomeScreenValidationError(msg)
        enabled_media_types = (
            get_all_media_row_types(user)
            if user is not None
            else [
                value
                for value in MediaTypes.values
                if value not in {MediaTypes.SEASON.value, MediaTypes.EPISODE.value}
            ]
        )
        requested_media_types = {str(value or "").strip() for value in raw_media_types}
        if requested_media_types - set(enabled_media_types):
            msg = "An All media type is not enabled."
            raise HomeScreenValidationError(msg)
        normalized["media_types"] = [
            value for value in enabled_media_types if value in requested_media_types
        ]
    if "status" in raw_filters:
        for raw_status in _as_list(raw_filters.get("status")):
            canonical_status = _canonical_status_filter(raw_status, None)
            if canonical_status not in STATUS_FILTER_VALUES:
                msg = f"Unsupported status filter for {media_type}."
                raise HomeScreenValidationError(msg)
    raw_rating = (
        str(raw_filters.get("rating", normalized["rating"]) or "").strip().lower()
    )
    if raw_rating and raw_rating not in {"all", "rated", "not_rated"}:
        msg = f"Unsupported rating filter for {media_type}."
        raise HomeScreenValidationError(msg)
    raw_progress_value = (
        str(raw_filters.get("progress", normalized["progress"]) or "")
        .strip()
        .casefold()
    )
    if raw_progress_value and raw_progress_value not in {
        "all",
        "caught up",
        "caught_up",
        "not caught up",
        "not_caught_up",
    }:
        msg = f"Unsupported progress filter for {media_type}."
        raise HomeScreenValidationError(msg)
    raw_progress = _canonical_progress_filter(raw_progress_value, None)
    if (
        raw_progress
        and raw_progress != "all"
        and media_type not in HOME_PROGRESS_FILTER_MEDIA_TYPES
    ):
        msg = f"Filter 'progress' is not available for {media_type}."
        raise HomeScreenValidationError(msg)
    raw_collection = (
        str(raw_filters.get("collection", normalized["collection"]) or "")
        .strip()
        .lower()
    )
    if raw_collection and raw_collection not in {"all", "collected", "not_collected"}:
        msg = f"Unsupported collection filter for {media_type}."
        raise HomeScreenValidationError(msg)
    raw_release = (
        str(raw_filters.get("release", normalized["release"]) or "").strip().lower()
    )
    if raw_release and raw_release not in {"all", "released", "not_released"}:
        msg = f"Unsupported release filter for {media_type}."
        raise HomeScreenValidationError(msg)
    raw_year = str(raw_filters.get("year", normalized["year"]) or "").strip().lower()
    if raw_year and raw_year != "unknown" and not raw_year.isdigit():
        msg = f"Unsupported year filter for {media_type}."
        raise HomeScreenValidationError(msg)
    raw_source = (
        str(raw_filters.get("source", normalized["source"]) or "").strip().lower()
    )
    if raw_source and raw_source not in Sources.values:
        msg = f"Unsupported source filter for {media_type}."
        raise HomeScreenValidationError(msg)
    raw_subview = str(raw_filters.get("subview", "") or "").strip().lower()
    if raw_subview and raw_subview not in SUBVIEWS_BY_MEDIA_TYPE.get(
        media_type, ((),)
    )[0]:
        msg = f"Unsupported media type for {media_type}."
        raise HomeScreenValidationError(msg)
    return normalized


def save_home_screen_configuration(user, raw_payload: str) -> None:
    """Validate and persist Home screen settings from a JSON payload."""
    try:
        parsed_payload = json.loads(raw_payload or "[]")
    except (TypeError, ValueError) as exc:
        msg = "Home Screen settings payload is invalid JSON."
        raise HomeScreenValidationError(msg) from exc

    if not isinstance(parsed_payload, list):
        msg = "Home Screen settings payload must be a list."
        raise HomeScreenValidationError(msg)

    allowed_media_types = set(get_home_configurable_media_types(user))
    replacement_rows: list[HomeScreenRow] = []
    seen_recent_rows: set[str] = set()
    media_type_order: list[str] = []

    for section in parsed_payload:
        if not isinstance(section, dict):
            msg = "Invalid Home Screen section payload."
            raise HomeScreenValidationError(msg)
        media_type = str(section.get("media_type") or "").strip()
        if media_type not in allowed_media_types:
            msg = f"Unsupported media type '{media_type}'."
            raise HomeScreenValidationError(msg)
        media_type_order.append(media_type)
        rows = section.get("rows")
        if not isinstance(rows, list):
            msg = f"Rows payload for {media_type} must be a list."
            raise HomeScreenValidationError(msg)

        for index, row_payload in enumerate(rows):
            if not isinstance(row_payload, dict):
                msg = f"Row {index + 1} for {media_type} is invalid."
                raise HomeScreenValidationError(msg)
            model_row = _row_payload_to_model(user, media_type, row_payload, index)
            if model_row.row_type == HomeScreenRowTypeChoices.RECENTLY_UNRATED:
                if media_type in seen_recent_rows:
                    msg = f"Only one '{RECENTLY_UNRATED_LABEL}' row is allowed for {media_type}."
                    raise HomeScreenValidationError(
                        msg,
                    )
                seen_recent_rows.add(media_type)
            replacement_rows.append(model_row)

    with transaction.atomic():
        HomeScreenRow.objects.filter(
            user=user, media_type__in=allowed_media_types
        ).delete()
        HomeScreenRow.objects.bulk_create(replacement_rows)
        user.home_screen_media_type_order = media_type_order
        user.save(update_fields=["home_screen_media_type_order"])


def search_home_screen_lists(user, query: str, media_type: str) -> list[dict]:
    """Return accessible list suggestions for Home row selection."""
    queryset = CustomList.objects.get_user_lists(user).order_by("name")
    normalized_query = str(query or "").strip()
    if normalized_query:
        queryset = queryset.filter(name__icontains=normalized_query)
    return [
        {
            "id": custom_list.id,
            "name": custom_list.name,
            "is_smart": custom_list.is_smart,
            "label": f"{custom_list.name} ({'Smart list' if custom_list.is_smart else 'List'})",
        }
        for custom_list in queryset[:12]
    ]


# Only the detail page renders watch providers; no home card does. The column
# holds TMDB's availability for every region it knows, around 146 KiB a title,
# and a custom-list row hydrates every item in the list before slicing ten
# cards -- twice, once for the items and once for their media. On an
# 11,008-item list that is the row's whole memory cost.
HOME_CARD_UNREAD_ITEM_FIELDS = ("watch_providers",)


def _item_matches_home_media_type(item: Item, media_type: str) -> bool:
    if media_type == HOME_ALL_MEDIA_TYPE:
        return True
    library_media_type = getattr(item, "library_media_type", "") or ""
    return media_type in (library_media_type, item.media_type)


def _annotate_home_card_images(media_items):
    """Annotate season/music cards with fallback art when needed."""
    season_items = [
        media
        for media in media_items
        if getattr(getattr(media, "item", None), "media_type", None)
        == MediaTypes.SEASON.value
    ]
    if season_items:
        BasicMedia.objects._fix_missing_season_images(season_items)

    music_items = [
        media
        for media in media_items
        if getattr(getattr(media, "item", None), "media_type", None)
        == MediaTypes.MUSIC.value
    ]
    if music_items:
        BasicMedia.objects._fix_missing_music_images(music_items)


def _music_shell_item(media_id: str, title: str, image: str | None) -> Item:
    """Get or refresh the lightweight Item used to render a music Home card.

    Album/artist tracking isn't backed by a media_type='music' Item, so we keep a
    stable manual-source shell Item per album/artist purely for card display.
    """
    item, _ = Item.objects.get_or_create(
        media_id=media_id,
        source=Sources.MANUAL.value,
        media_type=MediaTypes.MUSIC.value,
        defaults={"title": title, "image": image or settings.IMG_NONE},
    )
    desired_image = image or settings.IMG_NONE
    if item.title != title or item.image != desired_image:
        item.title = title
        item.image = desired_image
        item.save(update_fields=["title", "image"])
    return item


def _music_shell_items_bulk(
    specs: list[tuple[str, str, str | None]],
    persist: set[str] | None = None,
) -> dict[str, Item]:
    """Batch version of _music_shell_item — fetches/creates all shell items in 1-3 queries.

    With ``persist`` only those media ids are created/updated in the database; every
    other spec gets an equivalent in-memory Item, enough to sort on but never saved.
    """
    if not specs:
        return {}
    specs_map = {s[0]: s for s in specs}
    media_ids = list(specs_map)
    existing: dict[str, Item] = {
        item.media_id: item
        for item in Item.objects.filter(
            media_id__in=media_ids,
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MUSIC.value,
        )
    }
    write_ids = set(media_ids) if persist is None else persist & set(media_ids)
    missing_ids = [mid for mid in media_ids if mid not in existing]
    create_ids = [mid for mid in missing_ids if mid in write_ids]
    if create_ids:
        Item.objects.bulk_create(
            [
                Item(
                    media_id=mid,
                    source=Sources.MANUAL.value,
                    media_type=MediaTypes.MUSIC.value,
                    title=specs_map[mid][1],
                    image=specs_map[mid][2] or settings.IMG_NONE,
                )
                for mid in create_ids
            ],
            ignore_conflicts=True,
        )
        for item in Item.objects.filter(
            media_id__in=create_ids,
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MUSIC.value,
        ):
            existing[item.media_id] = item
    for mid in missing_ids:
        if mid not in write_ids:
            existing[mid] = Item(
                media_id=mid,
                source=Sources.MANUAL.value,
                media_type=MediaTypes.MUSIC.value,
                title=specs_map[mid][1],
                image=specs_map[mid][2] or settings.IMG_NONE,
            )
    to_update = []
    for media_id, item in existing.items():
        _, title, image = specs_map[media_id]
        desired_image = image or settings.IMG_NONE
        if item.title != title or item.image != desired_image:
            item.title = title
            item.image = desired_image
            if media_id in write_ids:
                to_update.append(item)
    if to_update:
        Item.objects.bulk_update(to_update, ["title", "image"])
    return existing


class _MusicTrackerAdapter:
    """Media-like wrapper around an Album/Artist tracker for Home card rendering."""

    def __init__(self, item: Item, tracker: object):
        self.item = item
        self.id = tracker.id
        self.status = tracker.status
        self.aggregated_status = tracker.status
        self.score = getattr(tracker, "score", None)
        self.next_event = None
        self.start_date = getattr(tracker, "start_date", None)
        self.end_date = getattr(tracker, "end_date", None)
        self.created_at = getattr(tracker, "created_at", None)
        self.last_played_at = getattr(tracker, "end_date", None) or getattr(
            tracker, "created_at", None
        )
        self.title = item.title


class _AlbumHomeAdapter(_MusicTrackerAdapter):
    def __init__(self, item: Item, tracker: object, album: object):
        super().__init__(item, tracker)
        self.album = album
        self.home_music_card = True
        artist = getattr(album, "artist", None)
        self.card_subtitle_text = getattr(artist, "name", "") or ""
        self.card_subtitle_date = getattr(tracker, "created_at", None)


class _ArtistHomeAdapter(_MusicTrackerAdapter):
    def __init__(self, item: Item, tracker: object, artist: object):
        super().__init__(item, tracker)
        self.artist = artist
        self.home_music_card = True
        self.card_subtitle_text = ""
        self.card_subtitle_date = getattr(tracker, "created_at", None)


RECENT_SHOW_ALBUM = "album"
RECENT_SHOW_ARTIST = "artist"
RECENT_SHOW_TRACK = "track"
RECENT_SHOW_ALL = "all"
RECENT_SHOW_MODES = frozenset(
    {
        RECENT_SHOW_ALBUM,
        RECENT_SHOW_ARTIST,
        RECENT_SHOW_TRACK,
        RECENT_SHOW_ALL,
    }
)


def recent_show_mode(row: HomeScreenRow) -> str:
    """Return which identities a music Recently Played row should show."""
    if row.media_type != MediaTypes.MUSIC.value:
        return RECENT_SHOW_ALBUM
    raw = str((row.filters or {}).get("recent_show") or RECENT_SHOW_ALBUM)
    if raw not in RECENT_SHOW_MODES:
        return RECENT_SHOW_ALBUM
    return raw


def _clean_recent_show(raw) -> str:
    """Return a stored Recently Played display mode."""
    value = str(raw or "").strip()
    if value in RECENT_SHOW_MODES:
        return value
    return RECENT_SHOW_ALBUM


def _play_artist(play):
    """Return the artist on a play, or the album's artist."""
    artist = getattr(play, "artist", None)
    if artist is not None:
        return artist
    album = getattr(play, "album", None)
    return getattr(album, "artist", None)


def _play_track_title(play) -> str:
    """Return the track title for a play."""
    track = getattr(play, "track", None)
    title = getattr(track, "title", None)
    if title:
        return title
    item = getattr(play, "item", None)
    return getattr(item, "title", None) or getattr(play, "title", None) or ""


def _play_track_url(play) -> str:
    """Return the track page for a play."""
    track = getattr(play, "track", None)
    if track is not None:
        url = app_tags.music_track_url(track)
        if url:
            return url
    item = getattr(play, "item", None)
    if item is not None:
        return app_tags.media_url(item)
    return ""


def _recent_link(label, url) -> dict | None:
    """Return one clickable card label, or nothing when either side is blank."""
    text = str(label or "").strip()
    href = str(url or "").strip()
    if not text or not href:
        return None
    return {"label": text, "url": href}


def _recent_part_links(play, mode: str) -> list[dict]:
    """Return the labels a recent-play card should show for this mode."""
    artist = _play_artist(play)
    album = getattr(play, "album", None)
    track_link = _recent_link(_play_track_title(play), _play_track_url(play))
    artist_link = _recent_link(
        getattr(artist, "name", ""),
        app_tags.music_artist_url(artist) if artist is not None else "",
    )
    album_link = _recent_link(
        getattr(album, "title", ""),
        app_tags.music_album_url(album) if album is not None else "",
    )
    if mode == RECENT_SHOW_ARTIST:
        chosen = (artist_link,)
    elif mode == RECENT_SHOW_TRACK:
        chosen = (track_link,)
    elif mode == RECENT_SHOW_ALL:
        chosen = (track_link, artist_link, album_link)
    else:
        chosen = (album_link,)
    return [link for link in chosen if link]


class _RecentAlbumAdapter:
    """Media-like wrapper around an Album for the recently-played music row."""

    def __init__(self, album, play_count, last_played_at, primary_track):
        self.album = album
        self.id = album.id
        self.play_count = play_count
        self.last_played_at = last_played_at
        self.created_at = last_played_at
        self.status = None
        self.end_date = last_played_at
        self.next_event = None
        self.score = None
        self.title = album.title
        self.item = _music_shell_item(f"album_{album.id}", album.title, album.image)
        self.primary_track = primary_track
        self.card_tile_url = _play_track_url(primary_track) or app_tags.music_album_url(
            album
        )
        self.card_links = _recent_part_links(primary_track, RECENT_SHOW_ALBUM)


class _RecentMusicPlayAdapter:
    """One recent play, with separate links for the parts the row is showing."""

    def __init__(self, play, mode: str, shell: Item | None = None):
        album = getattr(play, "album", None)
        self.id = getattr(play, "id", None)
        self.play_count = getattr(play, "repeats", None) or getattr(play, "progress", None) or 1
        self.last_played_at = getattr(play, "last_played_at", None) or getattr(
            play, "created_at", None
        )
        self.created_at = self.last_played_at
        self.status = None
        self.end_date = self.last_played_at
        self.next_event = None
        self.score = None
        self.card_links = _recent_part_links(play, mode)
        self.title = self.card_links[0]["label"] if self.card_links else _play_track_title(play)
        self.card_tile_url = _play_track_url(play) or app_tags.music_album_url(album)
        image = getattr(album, "image", None) or getattr(
            getattr(play, "item", None), "image", None
        )
        self.card_image_override = image
        self.item = shell if shell is not None else getattr(play, "item", None)


def _apply_music_tracker_rating_filter(trackers, rating_filter: str):
    if rating_filter == "rated":
        return trackers.filter(score__isnull=False)
    if rating_filter == "not_rated":
        return trackers.filter(score__isnull=True)
    return trackers


def _build_album_home_entries(
    user, filters: dict, sort_by: str, direction: str, offset: int, limit: int
) -> tuple[list[HomeRowEntry], int]:
    """Build Home entries from the user's tracked albums (AlbumTracker)."""
    from app.models import AlbumTracker

    status_filter = filters.get("status") or []
    trackers = AlbumTracker.objects.filter(user=user).select_related(
        "album",
        "album__artist",
    )
    if status_filter:
        trackers = trackers.filter(status__in=status_filter)
    trackers = list(
        _apply_music_tracker_rating_filter(trackers, filters.get("rating", "all"))
    )

    specs = [
        (f"album_{t.album.id}", t.album.title, t.album.image)
        for t in trackers
        if t.album
    ]
    items = _music_shell_items_bulk(specs, persist=set())

    entries = []
    for tracker in trackers:
        album = tracker.album
        if not album:
            continue
        item = items.get(f"album_{album.id}")
        if not item:
            continue
        entries.append(
            HomeRowEntry(
                item=item,
                media=_AlbumHomeAdapter(item, tracker, album),
                show_progress_controls=False,
            ),
        )
    entries = sort_home_entries(entries, sort_by, direction)
    window = entries[offset : offset + limit]
    # Only the shelf window needs a saved shell Item; the rest sorted in memory.
    persisted = _music_shell_items_bulk(
        [spec for spec in specs if spec[0] in {e.item.media_id for e in window}],
    )
    trackers_by_id = {t.id: t for t in trackers}
    return [
        HomeRowEntry(
            item=persisted[e.item.media_id],
            media=_AlbumHomeAdapter(
                persisted[e.item.media_id],
                trackers_by_id[e.media.id],
                e.media.album,
            ),
            show_progress_controls=False,
        )
        for e in window
    ], len(entries)


def _build_artist_home_entries(
    user, filters: dict, sort_by: str, direction: str, offset: int, limit: int
) -> tuple[list[HomeRowEntry], int]:
    """Build Home entries from the user's tracked artists (ArtistTracker)."""
    from app.models import ArtistTracker

    status_filter = filters.get("status") or []
    trackers = (
        ArtistTracker.objects.filter(user=user)
        .exclude(artist__name__isnull=True)
        .exclude(artist__name__exact="")
        .select_related("artist")
    )
    if status_filter:
        trackers = trackers.filter(status__in=status_filter)
    trackers = list(
        _apply_music_tracker_rating_filter(trackers, filters.get("rating", "all"))
    )

    specs = [
        (f"artist_{t.artist.id}", t.artist.name, getattr(t.artist, "image", None))
        for t in trackers
        if t.artist
    ]
    items = _music_shell_items_bulk(specs, persist=set())

    entries = []
    for tracker in trackers:
        artist = tracker.artist
        if not artist:
            continue
        item = items.get(f"artist_{artist.id}")
        if not item:
            continue
        entries.append(
            HomeRowEntry(
                item=item,
                media=_ArtistHomeAdapter(item, tracker, artist),
                show_progress_controls=False,
            ),
        )
    entries = sort_home_entries(entries, sort_by, direction)
    window = entries[offset : offset + limit]
    # Only the shelf window needs a saved shell Item; the rest sorted in memory.
    persisted = _music_shell_items_bulk(
        [spec for spec in specs if spec[0] in {e.item.media_id for e in window}],
    )
    trackers_by_id = {t.id: t for t in trackers}
    return [
        HomeRowEntry(
            item=persisted[e.item.media_id],
            media=_ArtistHomeAdapter(
                persisted[e.item.media_id],
                trackers_by_id[e.media.id],
                e.media.artist,
            ),
            show_progress_controls=False,
        )
        for e in window
    ], len(entries)


class _PodcastShowHomeAdapter(_MusicTrackerAdapter):
    """Media-like wrapper around a PodcastShowTracker for Home card rendering."""

    def __init__(self, item: Item, tracker: object, show: object):
        super().__init__(item, tracker)
        self.show = show
        self.home_music_card = True
        self.card_subtitle_text = show.author or ""
        self.card_subtitle_date = tracker.created_at
        self.last_played_at = tracker.updated_at


def _podcast_show_shell_items_bulk(
    shows: list[object], persist: set[tuple[str, str]] | None = None
) -> dict[tuple[str, str], Item]:
    """Return the card Item for each show, keyed by (source, podcast_uuid).

    With ``persist`` only those keys are created/updated in the database; the
    other shows get an equivalent in-memory Item for sorting.

    The same shell Item the Podcasts list page creates, so both pages share it.
    """
    existing = {
        (item.source, item.media_id): item
        for item in Item.objects.filter(
            media_type=MediaTypes.PODCAST.value,
            media_id__in=[show.podcast_uuid for show in shows],
        )
    }
    missing = [
        Item(
            media_id=show.podcast_uuid,
            source=show.source,
            media_type=MediaTypes.PODCAST.value,
            title=show.title,
            image=show.image or settings.IMG_NONE,
        )
        for show in shows
        if (show.source, show.podcast_uuid) not in existing
    ]
    if persist is not None:
        for item in missing:
            if (item.source, item.media_id) not in persist:
                existing[(item.source, item.media_id)] = item
        missing = [i for i in missing if (i.source, i.media_id) in persist]
    if missing:
        Item.objects.bulk_create(missing, ignore_conflicts=True)
        for item in Item.objects.filter(
            media_type=MediaTypes.PODCAST.value,
            media_id__in=[item.media_id for item in missing],
        ):
            existing[(item.source, item.media_id)] = item
    # Keep the shell in step with the show when its metadata changes (as the
    # Podcasts list page does), so the card and Title sort never go stale.
    stale = []
    for show in shows:
        item = existing.get((show.source, show.podcast_uuid))
        image = show.image or settings.IMG_NONE
        if item and (item.title != show.title or item.image != image):
            item.title = show.title
            item.image = image
            if persist is None or (show.source, show.podcast_uuid) in persist:
                stale.append(item)
    if stale:
        Item.objects.bulk_update(stale, ["title", "image"])
    return existing


def _build_podcast_show_home_entries(
    user, filters: dict, sort_by: str, direction: str, offset: int, limit: int
) -> tuple[list[HomeRowEntry], int]:
    """Build Home entries from the user's tracked podcast shows (PodcastShowTracker).

    A Home podcast shelf lists shows, like the Podcasts page, not every episode.
    """
    from app.models import PodcastShowTracker

    status_filter = filters.get("status") or []
    trackers = (
        PodcastShowTracker.objects.filter(user=user)
        .exclude(show__title__isnull=True)
        .exclude(show__title__exact="")
        .select_related("show")
    )
    if status_filter:
        trackers = trackers.filter(status__in=status_filter)
    trackers = _apply_music_tracker_rating_filter(trackers, filters.get("rating", "all"))
    if sort_by == MediaSortChoices.RELEASE_DATE:
        # A show's release date is its first episode's publication date.
        trackers = trackers.annotate(first_published=Min("show__episodes__published"))
    trackers = list(trackers)
    genre = (filters.get("genre") or "").strip().lower()
    if genre:
        trackers = [
            tracker
            for tracker in trackers
            if any(str(g).strip().lower() == genre for g in tracker.show.genres or [])
        ]
    language = (filters.get("language") or "").strip().lower()
    if language:
        trackers = [
            tracker
            for tracker in trackers
            if (tracker.show.language or "").strip().lower() == language
        ]

    items = _podcast_show_shell_items_bulk(
        [tracker.show for tracker in trackers], persist=set()
    )
    entries = []
    for tracker in trackers:
        show = tracker.show
        item = items.get((show.source, show.podcast_uuid))
        if not item:
            continue
        if sort_by == MediaSortChoices.RELEASE_DATE:
            item.release_datetime = tracker.first_published
        entries.append(
            HomeRowEntry(
                item=item,
                media=_PodcastShowHomeAdapter(item, tracker, show),
                use_podcast_show=True,
                podcast_show=show,
                show_progress_controls=False,
            ),
        )
    entries = sort_home_entries(entries, sort_by, direction)
    window = entries[offset : offset + limit]
    # Only the shelf window needs a saved shell Item; the rest sorted in memory.
    persisted = _podcast_show_shell_items_bulk(
        [e.podcast_show for e in window],
        persist={(e.podcast_show.source, e.podcast_show.podcast_uuid) for e in window},
    )
    trackers_by_id = {t.id: t for t in trackers}
    out = []
    for e in window:
        show = e.podcast_show
        item = persisted[(show.source, show.podcast_uuid)]
        if sort_by == MediaSortChoices.RELEASE_DATE:
            item.release_datetime = e.item.release_datetime
        out.append(
            HomeRowEntry(
                item=item,
                media=_PodcastShowHomeAdapter(item, trackers_by_id[e.media.id], show),
                use_podcast_show=True,
                podcast_show=show,
                show_progress_controls=False,
            ),
        )
    return out, len(entries)


def _build_recent_music_album_entries(media_items: list[object]) -> list[HomeRowEntry]:
    albums_by_id = {}
    album_play_counts = defaultdict(int)
    album_last_played = {}
    album_primary_track = {}

    for track in media_items:
        album = getattr(track, "album", None)
        if not album:
            continue
        album_id = album.id
        albums_by_id[album_id] = album
        play_count = getattr(track, "repeats", None) or 1
        album_play_counts[album_id] += play_count
        last_played = getattr(track, "last_played_at", None) or getattr(
            track, "created_at", None
        )
        if (
            album_id not in album_last_played
            or last_played > album_last_played[album_id]
        ):
            album_last_played[album_id] = last_played
            album_primary_track[album_id] = track

    entries = [
        HomeRowEntry(
            item=adapter.item,
            media=adapter,
            show_progress_controls=False,
        )
        for adapter in [
            _RecentAlbumAdapter(
                albums_by_id[album_id],
                album_play_counts[album_id],
                album_last_played[album_id],
                album_primary_track[album_id],
            )
            for album_id in albums_by_id
        ]
    ]
    entries.sort(
        key=lambda entry: (
            getattr(entry.media, "last_played_at", None)
            or getattr(entry.media, "created_at", None)
        ),
        reverse=True,
    )
    return entries


def _sort_recent_entries(entries: list[HomeRowEntry]) -> list[HomeRowEntry]:
    """Return recent-play cards newest first."""
    entries.sort(
        key=lambda entry: (
            getattr(entry.media, "last_played_at", None)
            or getattr(entry.media, "created_at", None)
        ),
        reverse=True,
    )
    return entries


def _build_recent_music_artist_entries(media_items: list[object]) -> list[HomeRowEntry]:
    """Return one card per artist, opening that artist's latest track."""
    artists = {}
    play_counts = defaultdict(int)
    last_played = {}
    primary_play = {}
    for play in media_items:
        artist = _play_artist(play)
        if artist is None or getattr(artist, "id", None) is None:
            continue
        artist_id = artist.id
        artists[artist_id] = artist
        play_counts[artist_id] += getattr(play, "repeats", None) or 1
        played_at = getattr(play, "last_played_at", None) or getattr(
            play, "created_at", None
        )
        if artist_id not in last_played or played_at > last_played[artist_id]:
            last_played[artist_id] = played_at
            primary_play[artist_id] = play

    shells = _music_shell_items_bulk(
        [
            (
                f"artist_{artist_id}",
                artists[artist_id].name,
                getattr(artists[artist_id], "image", None)
                or getattr(getattr(primary_play[artist_id], "album", None), "image", None),
            )
            for artist_id in artists
        ]
    )
    entries = []
    for artist_id, artist in artists.items():
        play = primary_play[artist_id]
        shell = shells.get(f"artist_{artist_id}")
        if shell is None:
            continue
        adapter = _RecentMusicPlayAdapter(play, RECENT_SHOW_ARTIST, shell)
        adapter.play_count = play_counts[artist_id]
        adapter.id = artist.id
        entries.append(
            HomeRowEntry(item=shell, media=adapter, show_progress_controls=False)
        )
    return _sort_recent_entries(entries)


def _build_recent_music_track_entries(
    media_items: list[object], mode: str
) -> list[HomeRowEntry]:
    """Return one card per play, showing the track or the track plus album and artist."""
    entries = []
    for play in media_items:
        item = getattr(play, "item", None)
        if item is None:
            continue
        adapter = _RecentMusicPlayAdapter(play, mode)
        if not adapter.card_links:
            continue
        entries.append(
            HomeRowEntry(item=item, media=adapter, show_progress_controls=False)
        )
    return _sort_recent_entries(entries)


def _build_recent_music_entries(
    media_items: list[object], mode: str
) -> list[HomeRowEntry]:
    """Return Recently Played music cards for the row's display mode."""
    if mode == RECENT_SHOW_ARTIST:
        return _build_recent_music_artist_entries(media_items)
    if mode in {RECENT_SHOW_TRACK, RECENT_SHOW_ALL}:
        return _build_recent_music_track_entries(media_items, mode)
    return _build_recent_music_album_entries(media_items)


def _media_lookup_for_items(
    user,
    items: list[Item],
    *,
    status_filter: list[str] | None = None,
) -> dict[int, object]:
    status_filter = status_filter or []
    items_by_media_type: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        items_by_media_type[item.media_type].append(item)

    lookup: dict[int, object] = {}
    for actual_media_type, type_items in items_by_media_type.items():
        model = apps.get_model("app", actual_media_type)
        item_ids = [item.id for item in type_items]
        if actual_media_type == MediaTypes.EPISODE.value:
            from app.library_query.trackers import episode_media_for_items

            lookup.update(episode_media_for_items(user, item_ids))
            continue
        queryset = (
            model.objects.filter(user=user, item_id__in=item_ids)
            .select_related("item")
            .defer(*(f"item__{field}" for field in HOME_CARD_UNREAD_ITEM_FIELDS))
        )
        if actual_media_type == MediaTypes.PODCAST.value:
            queryset = queryset.select_related("show", "episode")
        if actual_media_type == MediaTypes.MUSIC.value:
            queryset = queryset.select_related("album")
        queryset = BasicMedia.objects._apply_prefetch_related(
            queryset, actual_media_type, compact_episodes=True,
        )
        media_entries = list(queryset)

        grouped_entries: dict[int, list[object]] = defaultdict(list)
        for media_entry in media_entries:
            grouped_entries[media_entry.item_id].append(media_entry)

        candidate_entries = []
        for entries in grouped_entries.values():
            if actual_media_type == MediaTypes.PODCAST.value:
                entries = sorted(  # noqa: PLW2901  # deliberate in-loop normalisation
                    entries, key=lambda entry: entry.created_at, reverse=True
                )
            else:
                entries = sorted(  # noqa: PLW2901  # deliberate in-loop normalisation
                    entries, key=lambda entry: entry.created_at, reverse=True
                )
            primary_entry = entries[0]
            if actual_media_type != MediaTypes.PODCAST.value and len(entries) > 1:
                BasicMedia.objects._aggregate_item_data(primary_entry, entries)
            candidate_entries.append(primary_entry)

        if candidate_entries:
            BasicMedia.objects.annotate_max_progress(
                candidate_entries, actual_media_type
            )
            if actual_media_type == MediaTypes.SEASON.value:
                for primary_entry in candidate_entries:
                    if len(grouped_entries.get(primary_entry.item_id, [])) != 1:
                        continue
                    effective_status = (
                        primary_entry.derived_status_from_episode_progress()
                    )
                    if (
                        effective_status == Status.COMPLETED.value
                        and primary_entry.status != Status.COMPLETED.value
                    ):
                        primary_entry.promote_to_completed_if_fully_watched(
                            max_progress=getattr(primary_entry, "max_progress", None),
                        )
                    if not (
                        effective_status == Status.COMPLETED.value
                        and primary_entry.status == Status.IN_PROGRESS.value
                    ):
                        primary_entry.status = effective_status
                        primary_entry.aggregated_status = effective_status
                    else:
                        primary_entry.aggregated_status = primary_entry.status
            _annotate_home_card_images(candidate_entries)

            for primary_entry in candidate_entries:
                latest_status = getattr(
                    primary_entry, "aggregated_status", None
                ) or getattr(primary_entry, "status", None)
                if status_filter and latest_status not in status_filter:
                    continue
                if actual_media_type == MediaTypes.PODCAST.value:
                    primary_entry.use_podcast_show = bool(
                        getattr(primary_entry, "show", None)
                    )
                lookup[primary_entry.item_id] = primary_entry

    return lookup


def _wrap_media_entries(media_entries: list[object]) -> list[HomeRowEntry]:
    _annotate_home_card_images(media_entries)
    return [
        HomeRowEntry(
            item=media.item,
            media=media,
            use_podcast_show=bool(getattr(media, "use_podcast_show", False)),
            podcast_show=getattr(media, "show", None),
            show_progress_controls=True,
        )
        for media in media_entries
    ]


def _coerce_numeric(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            return timezone.localtime(value)
        return value.replace(tzinfo=UTC)
    return None


def _entry_title(entry: HomeRowEntry) -> str:
    return str(getattr(entry.item, "title", "") or "")


def _entry_media(entry: HomeRowEntry):
    return entry.media


def _entry_score(entry: HomeRowEntry):
    media = _entry_media(entry)
    if not media:
        return None
    aggregated = getattr(media, "aggregated_score", None)
    if aggregated is not None:
        return aggregated
    return getattr(media, "score", None)


def _entry_progress(entry: HomeRowEntry):
    media = _entry_media(entry)
    if not media:
        return None
    aggregated = getattr(media, "aggregated_progress", None)
    if aggregated is not None:
        return aggregated
    return getattr(media, "progress", None)


def _entry_authors(entry: HomeRowEntry):
    return smart_rules._extract_authors(entry.item)


def _entry_recent_timestamp(entry: HomeRowEntry):
    media = _entry_media(entry)
    if not media:
        return None
    progress = getattr(media, "progress", 0) or 0
    candidate = (
        getattr(media, "last_played_at", None)
        or getattr(media, "progressed_at", None)
        or (getattr(media, "created_at", None) if progress > 0 else None)
    )
    dt_value = _coerce_datetime(candidate)
    return dt_value.timestamp() if dt_value else None


def _entry_start_timestamp(entry: HomeRowEntry):
    media = _entry_media(entry)
    if not media:
        return None
    candidate = getattr(media, "aggregated_start_date", None) or getattr(
        media, "start_date", None
    )
    dt_value = _coerce_datetime(candidate)
    return dt_value.timestamp() if dt_value else None


def _entry_last_watched_timestamp(entry: HomeRowEntry):
    """Return the last activity for the "Last Watched" sort.

    A completed entry is dated by its end_date; an in-progress one by its
    last progress update. No activity stays null and sorts last.
    """
    media = _entry_media(entry)
    if not media:
        return None
    candidate = (
        getattr(media, "aggregated_last_watched", None)
        or getattr(media, "aggregated_end_date", None)
        or getattr(media, "end_date", None)
        or progress_activity(media)
    )
    dt_value = _coerce_datetime(candidate)
    return dt_value.timestamp() if dt_value else None


def _entry_date_added_timestamp(entry: HomeRowEntry):
    media = _entry_media(entry)
    if not media:
        return None
    dt_value = _coerce_datetime(
        getattr(media, "aggregated_created_at", None) or getattr(media, "created_at", None)
    )
    return dt_value.timestamp() if dt_value else None


def _entry_release_date(item):
    if not item:
        return None
    return getattr(item, "release_datetime", None) or getattr(
        item, "release_date", None
    )


def _entry_release_timestamp(entry: HomeRowEntry):
    dt_value = _coerce_datetime(getattr(entry.item, "release_datetime", None))
    return dt_value.timestamp() if dt_value else None


def _entry_next_event_timestamp(entry: HomeRowEntry):
    media = _entry_media(entry)
    next_event = getattr(media, "next_event", None) if media else None
    dt_value = _coerce_datetime(getattr(next_event, "datetime", None))
    return dt_value.timestamp() if dt_value else None


def _entry_next_episode_air_date_timestamp(entry: HomeRowEntry):
    media = _entry_media(entry)
    if not media:
        return None

    next_episode_air_date = getattr(media, "next_episode_air_date", None)
    if next_episode_air_date is None:
        next_episode_air_date = BasicMedia.objects._next_episode_air_date_value(media)
        if next_episode_air_date is not None:
            media.next_episode_air_date = next_episode_air_date

    dt_value = _coerce_datetime(next_episode_air_date)
    return dt_value.timestamp() if dt_value else None


def _sort_numeric(
    entries: list[HomeRowEntry], value_fn, direction: str
) -> list[HomeRowEntry]:
    descending = direction == DirectionChoices.DESC
    with_value = [e for e in entries if value_fn(e) is not None]
    without_value = [e for e in entries if value_fn(e) is None]
    with_value.sort(key=value_fn, reverse=descending)
    without_value.sort(
        key=lambda entry: _entry_title(entry).lower(), reverse=descending
    )
    return with_value + without_value


def _sort_string(
    entries: list[HomeRowEntry], value_fn, direction: str
) -> list[HomeRowEntry]:
    with_value = []
    without_value = []
    for entry in entries:
        value = str(value_fn(entry) or "").strip()
        if value:
            with_value.append(entry)
        else:
            without_value.append(entry)
    with_value.sort(
        key=lambda entry: (
            str(value_fn(entry) or "").lower(),
            _entry_title(entry).lower(),
        ),
        reverse=direction == DirectionChoices.DESC,
    )
    without_value.sort(key=lambda entry: _entry_title(entry).lower())
    return with_value + without_value


def sort_home_entries(
    entries: list[HomeRowEntry], sort_by: str, direction: str
) -> list[HomeRowEntry]:
    """Sort Home row wrappers with graceful handling for list rows lacking media."""
    if sort_by == HomeSortChoices.RANDOM:
        shuffled = list(entries)
        random.shuffle(shuffled)
        return shuffled
    media_entries = [entry.media for entry in entries if entry.media]
    if sort_by == HomeSortChoices.UPCOMING and media_entries:
        if all(
            getattr(getattr(media, "item", None), "media_type", None)
            == MediaTypes.SEASON.value
            for media in media_entries
        ):
            return _sort_numeric(
                entries,
                _entry_next_episode_air_date_timestamp,
                direction,
            )

        BasicMedia.objects._annotate_next_event(media_entries)
        with_events = []
        without_events = []
        for entry in entries:
            if _entry_next_event_timestamp(entry) is None:
                without_events.append(entry)
            else:
                with_events.append(entry)

        descending = direction == DirectionChoices.DESC

        def _upcoming_key(entry: HomeRowEntry):
            next_event_timestamp = _entry_next_event_timestamp(entry) or 0
            recent_timestamp = _entry_recent_timestamp(entry)
            return (
                -next_event_timestamp if descending else next_event_timestamp,
                0 if recent_timestamp is None else -recent_timestamp,
                _entry_title(entry).lower(),
            )

        with_events.sort(key=_upcoming_key)
        without_events.sort(
            key=lambda entry: (
                _entry_recent_timestamp(entry) is None,
                0
                if _entry_recent_timestamp(entry) is None
                else -_entry_recent_timestamp(entry),
                _entry_title(entry).lower(),
            ),
        )
        return with_events + without_events
    if sort_by == MediaSortChoices.NEXT_EPISODE_AIR_DATE:
        return _sort_numeric(entries, _entry_next_episode_air_date_timestamp, direction)
    if sort_by == HomeSortChoices.RECENT:
        descending = direction == DirectionChoices.DESC
        with_recent = [e for e in entries if _entry_recent_timestamp(e) is not None]
        without_recent = [e for e in entries if _entry_recent_timestamp(e) is None]
        with_recent.sort(key=_entry_recent_timestamp, reverse=descending)

        from events.models import is_released_datetime

        now = timezone.now()

        def _unstarted_recent_key(entry):
            release_dt = _coerce_datetime(_entry_release_date(entry.item))
            title = _entry_title(entry).lower()
            if release_dt is None:
                return (2, 0, title)
            timestamp = release_dt.timestamp()
            if is_released_datetime(release_dt, now):
                return (0, -timestamp, title)
            return (1, timestamp, title)

        without_recent.sort(key=_unstarted_recent_key)
        return with_recent + without_recent
    if sort_by == HomeSortChoices.COMPLETION:

        def completion_value(entry):
            media = _entry_media(entry)
            progress = _entry_progress(entry)
            max_progress = getattr(media, "max_progress", None) if media else None
            if progress is None or not max_progress:
                return None
            return (progress / max_progress) * 100

        return _sort_numeric(entries, completion_value, direction)
    if sort_by == HomeSortChoices.EPISODES_LEFT:

        def episodes_left(entry):
            media = _entry_media(entry)
            if not media:
                return None
            max_progress = getattr(media, "max_progress", None)
            progress = _entry_progress(entry)
            if max_progress is None or progress is None:
                return None
            return max_progress - progress

        return _sort_numeric(entries, episodes_left, direction)
    if sort_by == MediaSortChoices.SCORE:
        return _sort_numeric(entries, _entry_score, direction)
    if sort_by == MediaSortChoices.CRITIC_RATING:
        return _sort_numeric(
            entries,
            lambda entry: _coerce_numeric(getattr(entry.item, "provider_rating", None)),
            direction,
        )
    if sort_by == MediaSortChoices.TITLE:
        return sorted(
            entries,
            key=lambda entry: _entry_title(entry).lower(),
            reverse=direction == DirectionChoices.DESC,
        )
    if sort_by == MediaSortChoices.AUTHOR:
        return _sort_string(
            entries,
            lambda entry: _entry_authors(entry)[0] if _entry_authors(entry) else "",
            direction,
        )
    if sort_by == MediaSortChoices.POPULARITY:
        return _sort_numeric(
            entries,
            lambda entry: (
                _coerce_numeric(getattr(entry.item, "provider_popularity", None))
                if getattr(entry.item, "provider_popularity", None) is not None
                else (
                    None
                    if getattr(entry.item, "trakt_popularity_rank", None) is None
                    else -float(entry.item.trakt_popularity_rank)
                )
            ),
            direction,
        )
    if sort_by == MediaSortChoices.PROGRESS:
        return _sort_numeric(entries, _entry_progress, direction)
    if sort_by == MediaSortChoices.RUNTIME:
        prefill_episode_runtime_index(
            [
                _entry_media(entry)
                for entry in entries
                if _entry_media(entry) is not None
            ]
        )
        return _sort_numeric(
            entries,
            lambda entry: _coerce_numeric(
                getattr(_entry_media(entry), "total_runtime_minutes", None)
            ),
            direction,
        )
    if sort_by == MediaSortChoices.TIME_TO_BEAT:
        return _sort_numeric(
            entries,
            lambda entry: _coerce_numeric(
                getattr(entry.item, "game_time_to_beat_minutes", None)
            ),
            direction,
        )
    if sort_by == MediaSortChoices.PLAYS:
        return _sort_numeric(entries, _entry_progress, direction)
    if sort_by == MediaSortChoices.TIME_WATCHED:
        prefill_episode_runtime_index(
            [
                _entry_media(entry)
                for entry in entries
                if _entry_media(entry) is not None
            ]
        )
        return _sort_numeric(
            entries,
            lambda entry: _coerce_numeric(
                getattr(_entry_media(entry), "time_watched_minutes", None)
            ),
            direction,
        )
    if sort_by == MediaSortChoices.RELEASE_DATE:
        return _sort_numeric(entries, _entry_release_timestamp, direction)
    if sort_by == MediaSortChoices.DATE_ADDED:
        return _sort_numeric(entries, _entry_date_added_timestamp, direction)
    if sort_by == MediaSortChoices.START_DATE:
        return _sort_numeric(entries, _entry_start_timestamp, direction)
    if sort_by == MediaSortChoices.END_DATE:
        return _sort_numeric(entries, _entry_last_watched_timestamp, direction)
    if sort_by == MediaSortChoices.TIME_LEFT:

        def time_left(entry):
            media = _entry_media(entry)
            if not media:
                return None
            max_progress = getattr(media, "max_progress", None)
            progress = _entry_progress(entry)
            if max_progress is None or progress is None:
                return None
            return max_progress - progress

        return _sort_numeric(entries, time_left, direction)
    return sorted(
        entries,
        key=lambda entry: _entry_title(entry).lower(),
        reverse=direction == DirectionChoices.DESC,
    )


# -- Library shelves on the shared library-query engine ------------------------
#
# A shelf is a single-media-type library query: the engine filters, orders
# and pages it, and only the visible window is decorated as Home cards.


def _batch_entries(user, candidates) -> list[HomeRowEntry]:
    """Wrap a scan batch as Home entries around the engine's tracker rows."""
    return [HomeRowEntry(item=c.item, media=c.media) for c in candidates]


def _upcoming_values(user, candidates, direction):
    entries = _batch_entries(user, candidates)
    media_entries = [entry.media for entry in entries if entry.media]
    if media_entries:
        BasicMedia.objects._annotate_next_event(media_entries)
    descending = direction == DirectionChoices.DESC
    values = []
    for entry in entries:
        event = _entry_next_event_timestamp(entry)
        recent = _entry_recent_timestamp(entry)
        recent_key = (recent is None, 0 if recent is None else -recent)
        if event is None:
            values.append((1, *recent_key))
        else:
            values.append((0, -event if descending else event, recent_key[1], 0))
    return values


def _release_rank(item, now):
    """Return (group, key) ordering unstarted items: released, upcoming, undated."""
    from events.models import is_released_datetime

    release_dt = _coerce_datetime(_entry_release_date(item))
    if release_dt is None:
        return (2, 0)
    if is_released_datetime(release_dt, now):
        return (0, -release_dt.timestamp())
    return (1, release_dt.timestamp())


def _recent_values(user, candidates, direction):
    """Order by recent activity, then by release: newest released, soonest upcoming."""
    entries = _batch_entries(user, candidates)
    descending = direction == DirectionChoices.DESC
    now = timezone.now()
    values = []
    for entry in entries:
        recent = _entry_recent_timestamp(entry)
        recent_key = (1, 0) if recent is None else (0, -recent if descending else recent)
        values.append((recent_key, _release_rank(entry.item, now)))
    return values


def _recent_sql_order(ctx, seed, direction):
    """Order like ``_recent_values`` in SQL, from each item's newest row."""
    if len(ctx.sources) != 1:
        return None
    source = ctx.sources[0]
    if not (source.has_field("progressed_at") and source.has_field("progress")):
        return None  # Recent activity is derived in Python (TV reads seasons).
    now = timezone.now()
    newest = source.item_rows(ctx.user).order_by("-created_at", "-id")
    recent = Subquery(
        newest.annotate(
            value=Coalesce(
                F("progressed_at"),
                Case(When(progress__gt=0, then=F("created_at"))),
            ),
        ).values("value")[:1],
    )
    from events.models import released_q

    released = released_q("release_datetime", now)
    upcoming = Q(release_datetime__isnull=False) & ~released
    descending = direction == DirectionChoices.DESC
    recent_key = F("_home_recent")
    return (
        {"_home_recent": recent},
        [
            recent_key.desc(nulls_last=True) if descending else recent_key.asc(nulls_last=True),
            Case(
                When(released, then=Value(0)),
                When(upcoming, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            ).asc(),
            Case(When(released, then=F("release_datetime"))).desc(nulls_last=True),
            Case(When(upcoming, then=F("release_datetime"))).asc(nulls_last=True),
        ],
    )


def _entry_value_sort(value_fn, *, needs_runtime: bool = False):
    """Order by a Home entry value, nulls last, in the requested direction."""

    def values(user, candidates, direction):
        entries = _batch_entries(user, candidates)
        if needs_runtime:
            prefill_episode_runtime_index([e.media for e in entries if e.media is not None])
        return [value_fn(entry) for entry in entries]

    return values


def _completion_value(entry):
    media = _entry_media(entry)
    progress = _entry_progress(entry)
    max_progress = getattr(media, "max_progress", None) if media else None
    if progress is None or not max_progress:
        return None
    return (progress / max_progress) * 100


def _episodes_left_value(entry):
    media = _entry_media(entry)
    if not media:
        return None
    max_progress = getattr(media, "max_progress", None)
    progress = _entry_progress(entry)
    if max_progress is None or progress is None:
        return None
    return max_progress - progress


def _episode_feed_sort_values(user, candidates, direction):
    """Backlog ascending, then soonest release, then most recently aired."""
    entries = _episode_feed_entries(_batch_entries(user, candidates))
    values = {}
    for entry in entries:
        target = entry.next_episode
        if target["released"]:
            value = (0, entry.media.episodes_left)
        elif target.get("air_date"):
            value = (1, target["air_date"].timestamp())
        else:
            last_date = target.get("last_air_date")
            value = (2, -last_date.timestamp()) if last_date else (3, 0)
        values[entry.item.pk] = value
    missing = (3, 0)
    ordered = [values.get(candidate.item.pk, missing) for candidate in candidates]
    if direction == DirectionChoices.DESC:
        return [(-group, -key) for group, key in ordered]
    return ordered


_MEDIA = frozenset({NEEDS_MEDIA})
_MEDIA_AND_MAX = frozenset({NEEDS_MEDIA, NEEDS_MAX_PROGRESS})
for _definition in (
    SortDef(
        (UP_NEXT_SORT,),
        batch_values=_episode_feed_sort_values,
        direction_in_value=True,
        needs=_MEDIA_AND_MAX,
        tv_facts=True,
    ),
    SortDef(
        ("home_upcoming",),
        batch_values=_upcoming_values,
        direction_in_value=True,
        needs=_MEDIA,
        tv_facts=True,
    ),
    SortDef(
        ("home_recent",),
        batch_values=_recent_values,
        sql_order=_recent_sql_order,
        direction_in_value=True,
        needs=_MEDIA,
        tv_facts=True,
    ),
    SortDef(
        ("home_completion",),
        batch_values=_entry_value_sort(_completion_value),
        needs=_MEDIA_AND_MAX,
        tv_facts=True,
    ),
    SortDef(
        ("home_episodes_left",),
        batch_values=_entry_value_sort(_episodes_left_value),
        needs=_MEDIA_AND_MAX,
        tv_facts=True,
    ),
):
    register_sort(_definition)

HOME_ENGINE_SORT_KEYS = {
    HomeSortChoices.UPCOMING: "home_upcoming",
    HomeSortChoices.RECENT: "home_recent",
    HomeSortChoices.COMPLETION: "home_completion",
    HomeSortChoices.EPISODES_LEFT: "home_episodes_left",
}


def home_row_seed(row) -> int:
    """Return a fresh shuffle seed for a random shelf, else 0."""
    if row.sort_by == HomeSortChoices.RANDOM:
        return secrets.randbelow(RANDOM_MODULUS)
    return 0


def _library_row_executor(user, row, normalized_filters, *, seed: int):
    sort_key = HOME_ENGINE_SORT_KEYS.get(row.sort_by, row.sort_by)
    if row.sort_by == HomeSortChoices.UPCOMING and row.media_type == MediaTypes.SEASON.value:
        sort_key = MediaSortChoices.NEXT_EPISODE_AIR_DATE
    mixed = row.media_type == HOME_ALL_MEDIA_TYPE
    query = from_home_row_filters(
        user,
        normalized_filters,
        tuple(_all_media_type_selection(user, row.filters))
        if mixed
        else (row.media_type,),
        sort_key=sort_key,
        direction=resolve_home_row_direction(row.sort_by, row.direction),
        seed=seed,
        mixed=mixed,
    )
    return LibraryQueryExecutor(user, query, share_shows=True)


def _row_items(user, row, executor, offset, limit, *, seed):
    """Return (items, total) for one window of a shelf's query.

    A Python-ordered shelf ranks every candidate once; the compact id order is
    cached so load-more requests and later warm visits fetch one page. Random
    shelves keep the row lifetime: each new seed is a different order.
    """
    from django.core.cache import cache

    from app import cache_utils

    if executor.uses_sql:
        with boundary("home_materialize"):
            page = executor.page(offset, limit, defer=HOME_CARD_UNREAD_ITEM_FIELDS)
        return page.items, page.total

    updated = int(row.updated_at.timestamp()) if row.updated_at else 0

    def order_key_for(direction):
        return (
            f"{cache_utils.HOME_ROW_CACHE_PREFIX}_order_{user.id}_{row.id}"
            f"_{updated}_{direction}_{seed}"
        )

    saved_direction = resolve_home_row_direction(row.sort_by, row.direction)
    order_key = order_key_for(saved_direction)
    ranked_ids = cache.get(order_key)
    if ranked_ids is None:
        if row.sort_by == HomeSortChoices.RANDOM:
            with boundary("home_rank"):
                ranked_ids = executor.ranked_ids()
            cache.set(order_key, ranked_ids, cache_utils.HOME_ROW_CACHE_TTL)
            cache_utils.register_home_row_cache_key(user.id, order_key)
        else:
            # One scan serves both directions, so the first flip is as fast as later ones.
            with boundary("home_rank"):
                by_direction = executor.ranked_ids_both_directions()
            for saved in (DirectionChoices.ASC, DirectionChoices.DESC):
                key = order_key_for(saved)
                engine = home_engine_direction(row.sort_by, saved)
                cache.set(key, by_direction[engine], cache_utils.HOME_ROW_ORDER_CACHE_TTL)
                cache_utils.register_home_row_cache_key(user.id, key)
            ranked_ids = by_direction[executor.direction]
    window = ranked_ids[offset : offset + limit]
    by_id = Item.objects.defer(*HOME_CARD_UNREAD_ITEM_FIELDS).in_bulk(window)
    return [by_id[item_id] for item_id in window if item_id in by_id], len(ranked_ids)


def _row_entries(
    user, items, *, planning_subtitle: bool = False, podcast_episodes: bool = False,
) -> list[HomeRowEntry]:
    """Decorate one window of a shelf as Home cards."""
    with boundary("home_cards"):
        media_lookup = _media_lookup_for_items(user, items)
    from app.media_list_filters import attach_card_collection_platforms

    attach_card_collection_platforms(user, items)
    entries = []
    for item in items:
        media = media_lookup.get(item.pk)
        episode = item.media_type == MediaTypes.EPISODE.value
        subtitle = _entry_release_date(item) if planning_subtitle else None
        if episode and media:
            season = media.related_season
            show_item = getattr(season.related_tv, "item", None)
            show_title = getattr(show_item, "title", "")
            identity = f"S{item.season_number:02d}E{item.episode_number:02d}"
            subtitle = f"{show_title} • {identity}" if show_title else identity
            if not item.image or item.image == settings.IMG_NONE:
                item.image = season.item.image or getattr(show_item, "image", "") or settings.IMG_NONE
        elif podcast_episodes and item.media_type == MediaTypes.PODCAST.value:
            show_image = getattr(getattr(media, "show", None), "image", "")
            if show_image and (not item.image or item.image == settings.IMG_NONE):
                item.image = show_image
        entries.append(HomeRowEntry(
            item=item, media=media,
            use_podcast_show=bool(getattr(media, "use_podcast_show", False)) and not podcast_episodes,
            podcast_show=getattr(media, "show", None),
            show_progress_controls=media is not None and not episode,
            subtitle_override=subtitle,
        ))
    return entries


def _custom_list_row_executor(user, row, *, seed: int):
    """Query a list shelf: the list's saved members, ordered by the row's sort.

    Smart lists read their materialized membership, which the background sync
    keeps current, instead of re-evaluating their rules on every Home load.
    """
    sort_key = HOME_ENGINE_SORT_KEYS.get(row.sort_by, row.sort_by)
    query = LibraryQuery(
        media_types=(row.media_type,),
        filters=FilterValues(status_match=STATUS_MATCH_ANY),
        sort=SortSpec(
            key=last_watched_sort_key(sort_key) or "title",
            direction=home_engine_direction(
                row.sort_by,
                resolve_home_row_direction(row.sort_by, row.direction),
            ),
            seed=seed,
        ),
        list_id=row.custom_list_id,
        sort_list_id=row.custom_list_id,
        dedupe_cross_provider=False,
    )
    return LibraryQueryExecutor(user, query, share_shows=True)


def _custom_list_row_window(user, row, offset, limit, *, seed):
    """Return (entries, total) for one window of a custom- or smart-list shelf."""
    custom_list = row.custom_list
    if not custom_list:
        return [], 0
    if custom_list.is_smart:
        # Render current membership now; refresh it in the background so the
        # write-heavy sync never runs inside a GET request.
        from lists.tasks import schedule_smart_list_sync

        schedule_smart_list_sync(custom_list)
    executor = _custom_list_row_executor(user, row, seed=seed)
    items, total = _row_items(user, row, executor, offset, limit, seed=seed)
    return _row_entries(user, items), total


def _library_row_window(user, row, offset, limit, *, seed):
    """Return (entries, total) for one window of a library-query shelf."""
    normalized = _normalized_filter_payload(row.filters or {}, row.media_type)
    if row.media_type == HOME_ALL_MEDIA_TYPE and any(
        normalized["media_subviews"].get(family) != ALL_MEDIA_SUBVIEWS[family][2]
        for family in _all_media_type_selection(user, row.filters)
        if family in ALL_MEDIA_SUBVIEWS
    ):
        from users.home_mixed_rows import mixed_row_window

        return mixed_row_window(user, row, normalized, offset, limit, seed=seed)
    if row.media_type == MediaTypes.MUSIC.value:
        subview = _canonical_subview(normalized.get("subview"), row.media_type)
        if subview == MUSIC_SUBVIEW_ALBUMS:
            return _build_album_home_entries(
                user, normalized, row.sort_by, row.direction, offset, limit,
            )
        if subview == MUSIC_SUBVIEW_ARTISTS:
            return _build_artist_home_entries(
                user, normalized, row.sort_by, row.direction, offset, limit,
            )
    if (
        row.media_type == MediaTypes.PODCAST.value
        and _canonical_subview(normalized.get("subview"), row.media_type)
        == PODCAST_SUBVIEW_SHOWS
    ):
        return _build_podcast_show_home_entries(
            user, normalized, row.sort_by, row.direction, offset, limit,
        )
    if (
        row.media_type == HOME_ALL_MEDIA_TYPE
        and not _all_media_type_selection(user, row.filters)
    ):
        return [], 0
    executor = _library_row_executor(user, row, normalized, seed=seed)
    items, total = _row_items(user, row, executor, offset, limit, seed=seed)
    planning = (normalized.get("status") or []) == [Status.PLANNING.value]
    return _row_entries(user, items, planning_subtitle=planning, podcast_episodes=row.media_type == HOME_ALL_MEDIA_TYPE), total


def _recently_unrated_episode_entries(user, media_type: str) -> list[HomeRowEntry]:
    cutoff = timezone.now() - timedelta(days=RECENTLY_UNRATED_EPISODE_DAYS)
    episodes = (
        Episode.objects.filter(
            related_season__user=user.id,
            related_season__item__library_media_type=media_type,
            score__isnull=True,
            end_date__isnull=False,
            end_date__gte=cutoff,
        )
        .select_related(
            "item",
            "related_season__item",
            "related_season__related_tv__item",
        )
        .defer(
            *(
                f"{relation}__{field}"
                for relation in (
                    "item", "related_season__item", "related_season__related_tv__item"
                )
                for field in HOME_CARD_UNREAD_ITEM_FIELDS
            )
        )
        .order_by("-end_date")
    )
    placeholder = getattr(settings, "IMG_NONE", "")
    entries = []
    for ep in episodes:
        ep.last_played_at = ep.end_date
        season = ep.related_season
        show_item = getattr(getattr(season, "related_tv", None), "item", None)
        show_title = getattr(show_item, "title", "") or ""
        season_num = getattr(ep.item, "season_number", None)
        ep_num = getattr(ep.item, "episode_number", None)
        if show_title and season_num is not None and ep_num is not None:
            subtitle = f"{show_title} • S{season_num:02d}E{ep_num:02d}"
        elif show_title:
            subtitle = show_title
        else:
            subtitle = None
        if not ep.item.image or ep.item.image == placeholder:
            season_image = getattr(getattr(season, "item", None), "image", None)
            show_image = getattr(show_item, "image", None)
            ep.item.image = season_image or show_image or placeholder
        entries.append(
            HomeRowEntry(
                item=ep.item,
                media=ep,
                show_progress_controls=False,
                subtitle_override=subtitle,
            )
        )
    return entries


def _recently_unrated_entries(user, row: HomeScreenRow) -> list[HomeRowEntry]:
    if row.media_type in (MediaTypes.TV.value, MediaTypes.ANIME.value):
        entries = _recently_unrated_episode_entries(user, row.media_type)
        return sort_home_entries(entries, row.sort_by, row.direction)
    media_items = [
        media
        for media in BasicMedia.objects.get_recently_unrated(
            user, days=RECENTLY_UNRATED_DAYS
        )
        if _item_matches_home_media_type(media.item, row.media_type)
    ]
    if row.media_type == MediaTypes.MUSIC.value:
        return _build_recent_music_entries(media_items, recent_show_mode(row))
    entries = _wrap_media_entries(media_items)
    return sort_home_entries(entries, row.sort_by, row.direction)


# Filter values that are defaults/empty and not worth putting in the link.
_HOME_LINK_SKIP_FILTER_VALUES = frozenset({"", "all", "All", "ALL", None})


def _show_media_type_chip(user, row: HomeScreenRow, media_type: str) -> bool:
    """Use the poster corner for identity when a status row mixes media."""
    if media_type != HOME_ALL_MEDIA_TYPE or not user.home_media_type_chips_enabled:
        return False
    if row.row_type != HomeScreenRowTypeChoices.LIBRARY_QUERY:
        return False

    statuses = _normalize_status_list((row.filters or {}).get("status"), [])
    return len(statuses) == 1 and statuses[0] in {
        Status.IN_PROGRESS.value,
        Status.COMPLETED.value,
    }


def home_row_destination_url(row: HomeScreenRow, user) -> str:
    """Return the library/list URL a home row's title should link to.

    Library-query rows open the media list pre-sorted/filtered to match the row;
    custom-list rows open the list itself. Sort, direction, layout and filters are
    encoded in the URL (the media list persists them like any normal navigation).
    """
    if row.row_type == HomeScreenRowTypeChoices.UP_NEXT:
        return ""
    # Custom-list rows open the list detail page.
    if row.row_type == HomeScreenRowTypeChoices.CUSTOM_LIST and row.custom_list_id:
        base = row.custom_list.get_absolute_url()
        if row.sort_by in ListDetailSortChoices.values:
            query = urlencode({"sort": row.sort_by, "direction": row.direction})
            return f"{base}?{query}"
        return base

    # A mixed row has no single library destination.
    if row.media_type == HOME_ALL_MEDIA_TYPE:
        return ""

    # Library-query / recently-unrated rows open the media list, ordered the
    # way the row is (Home's "descending popularity" is the list's ascending
    # rank).
    query_pairs = [
        ("sort", row.sort_by),
        ("direction", home_engine_direction(row.sort_by, row.direction)),
        ("layout", getattr(user, f"{row.media_type}_layout", None) or "grid"),
    ]

    if row.row_type == HomeScreenRowTypeChoices.RECENTLY_UNRATED:
        query_pairs.append(("rating", "not_rated"))
        query_pairs.append(("status", MediaStatusChoices.ALL.value))
    else:
        normalized = _normalized_filter_payload(row.filters or {}, row.media_type)
        status_values = [value for value in (normalized.get("status") or []) if value]
        if status_values:
            query_pairs.extend(("status", value) for value in status_values)
        else:
            query_pairs.append(("status", MediaStatusChoices.ALL.value))

        tag_values = [value for value in (normalized.get("tag") or []) if value]
        for key, raw_value in normalized.items():
            if key in {"status", "tag", "tag_mode"}:
                continue
            value = raw_value
            if isinstance(value, (list, tuple)):
                value = value[0] if len(value) == 1 else None
            if value in _HOME_LINK_SKIP_FILTER_VALUES:
                continue
            query_pairs.append((key, value))

        if tag_values:
            query_pairs.extend(("tag", value) for value in tag_values)
            query_pairs.append(("tag_mode", normalized.get("tag_mode", "or")))

    base = reverse("medialist", args=[row.media_type])
    return f"{base}?{urlencode(query_pairs)}"


_HOME_ROW_EMPTY_SENTINEL = "__home_row_empty__"


def _build_row_section(
    user,
    row,
    media_type: str,
    items_limit: int,
    batch_start: int = 0,
    seed: int | None = None,
) -> dict | None:
    """Build a single home-row section dict, or None when the row is empty.

    Library shelves load only the requested window; ``seed`` keeps a random
    shelf's order fixed across its load-more requests.
    """
    if row.row_type == HomeScreenRowTypeChoices.UP_NEXT and not user.tv_enabled:
        return None
    if seed is None:
        seed = home_row_seed(row)
    if row.row_type == HomeScreenRowTypeChoices.UP_NEXT:
        section_entries, total = _episode_feed_row_window(
            user, row, batch_start, items_limit, seed,
        )
    elif row.row_type == HomeScreenRowTypeChoices.RECENTLY_UNRATED:
        # Bounded by its time window, so it is built whole and sliced.
        entries = _recently_unrated_entries(user, row)
        total = len(entries)
        section_entries = entries[batch_start : batch_start + items_limit]
    elif row.row_type == HomeScreenRowTypeChoices.CUSTOM_LIST:
        section_entries, total = _custom_list_row_window(
            user, row, batch_start, items_limit, seed=seed,
        )
    else:
        section_entries, total = _library_row_window(
            user, row, batch_start, items_limit, seed=seed,
        )

    if not total:
        return None

    prefill_display_release_years(section_entries)
    loaded_count = min(total, batch_start + len(section_entries))
    title_main, title_detail = home_row_header_title_parts(row, user)

    def _entry_missing_cover(entry):
        if getattr(entry, "use_podcast_show", False) and getattr(
            entry, "podcast_show", None
        ):
            image = entry.podcast_show.image
        else:
            image = (
                getattr(entry.media, "card_image_override", None) or entry.item.image
            )
        return not image or image == settings.IMG_NONE

    # A mixed row decides per entry: only its music and podcast cards poll.
    poll_for_covers = any(
        _entry_missing_cover(e)
        for e in section_entries
        if media_type in SQUARE_HOME_MEDIA_TYPES
        or (
            media_type == HOME_ALL_MEDIA_TYPE
            and e.item.media_type in SQUARE_HOME_MEDIA_TYPES
        )
    )
    return {
        "row_id": row.id,
        "row_type": row.row_type,
        "title": row_title(row, user),
        "title_main": title_main,
        "title_detail": title_detail,
        "url": home_row_destination_url(row, user),
        "summary": row_summary(row, user),
        "summary_inline": home_row_inline_summary(row, user),
        "direction": row.direction,
        "items": section_entries,
        "total": total,
        "seed": seed,
        "loaded_count": loaded_count,
        "show_played_chip": row.row_type == HomeScreenRowTypeChoices.RECENTLY_UNRATED,
        "show_media_type_chip": _show_media_type_chip(user, row, media_type),
        "card_width_class": "w-44",
        "mixed_media": media_type == HOME_ALL_MEDIA_TYPE,
        "video_landscape": media_type == "video",
        "grid_class": "media-grid media-grid-square"
        if media_type in SQUARE_HOME_MEDIA_TYPES
        else "media-grid",
        "poll_for_covers": poll_for_covers,
        "opposite_artwork": (
            _opposite_direction_artwork(user, row, seed=seed) if batch_start == 0 else []
        ),
    }


OPPOSITE_ARTWORK_MEDIA_TYPES = frozenset({
    MediaTypes.TV.value,
    MediaTypes.MOVIE.value,
    MediaTypes.ANIME.value,
    MediaTypes.GAME.value,
    MediaTypes.MANGA.value,
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
    MediaTypes.BOARDGAME.value,
})


def _opposite_direction_artwork(user, row, limit: int = 8, *, seed: int = 0) -> list[str]:
    """Return the first covers a direction flip would show, for the client to prefetch.

    The first flip otherwise waits for covers it could not know about. Only
    plain library shelves qualify; the opposite order is cheap because a
    Python-ordered shelf caches both directions together.
    """
    if (
        row.row_type != HomeScreenRowTypeChoices.LIBRARY_QUERY
        or row.media_type not in OPPOSITE_ARTWORK_MEDIA_TYPES
        or row.sort_by == HomeSortChoices.RANDOM
    ):
        return []
    flipped = copy.copy(row)
    current = resolve_home_row_direction(row.sort_by, row.direction)
    flipped.direction = (
        DirectionChoices.ASC if current == DirectionChoices.DESC else DirectionChoices.DESC
    )
    normalized = _normalized_filter_payload(row.filters or {}, row.media_type)
    executor = _library_row_executor(user, flipped, normalized, seed=seed)
    items, _total = _row_items(user, flipped, executor, 0, limit, seed=seed)
    # The same card-size URL the card will load, so the prefetch warms that file.
    return [
        card_image_url(item.image)
        for item in items
        if item.image and item.image != settings.IMG_NONE and item.image.startswith("http")
    ]


_HOME_ROW_BUILD_LOCK_TTL = 30


def cache_utils_key(user, row, items_limit: int = 14) -> str:
    """Cache key of a row's built section (shared with tests that stage the build marker)."""
    from app import cache_utils

    return cache_utils.build_home_row_cache_key(user.id, row.id, items_limit)


_HOME_ROW_WAIT_SECONDS = 4.0
_HOME_ROW_WAIT_STEP = 0.05


def _wait_for_row_in_flight(cache, cache_key):
    """Return the row another request is building, once it lands in the cache.

    A page loaded twice (two tabs, the installed app next to Safari, a retry)
    asks for the same cold row at the same moment, and each request used to
    build it. The first marks the row as building; later ones wait for its
    result instead of repeating the work, and build it themselves if it does
    not land in time, so a stalled builder never blocks a page. The marker is
    advisory, not a lock: two requests that both see none still both build.
    """
    if cache.get(f"{cache_key}_building") is None:
        return None
    deadline = time.monotonic() + _HOME_ROW_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(_HOME_ROW_WAIT_STEP)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        if cache.get(f"{cache_key}_building") is None:
            return cache.get(cache_key)
    return None


def _cached_row_section(
    user,
    row,
    media_type: str,
    items_limit: int,
    *,
    refresh: bool = False,
) -> dict | None:
    """Return a row section from cache, building and caching on miss.

    Empty rows are cached with a sentinel so their (potentially expensive)
    smart-rule scans are also skipped on warm loads.
    """
    from django.core.cache import cache

    from app import cache_utils

    cache_key = cache_utils.build_home_row_cache_key(user.id, row.id, items_limit)
    cached = None if refresh else cache.get(cache_key)
    if cached is None and not refresh:
        cached = _wait_for_row_in_flight(cache, cache_key)
    if cached is None:
        lock_key = f"{cache_key}_building"
        cache.set(lock_key, 1, _HOME_ROW_BUILD_LOCK_TTL)
        try:
            with boundary("home_row"):
                section = _build_row_section(user, row, media_type, items_limit)
            cache.set(
                cache_key,
                section if section is not None else _HOME_ROW_EMPTY_SENTINEL,
                cache_utils.HOME_ROW_CACHE_TTL,
            )
            cache_utils.register_home_row_cache_key(user.id, cache_key)
            if section is not None and section.get("poll_for_covers"):
                from app.home_artwork import record_cover_baseline

                record_cover_baseline(user)
        finally:
            cache.delete(lock_key)
        return section
    if cached == _HOME_ROW_EMPTY_SENTINEL:
        return None
    return cached


def _cached_released_ids(user, query) -> list[int]:
    """Return the Up Next membership scan, cached like a shelf's id order.

    The scan hydrates the whole in-progress library in Python (about 0.2 s per
    thousand shows), and it ran on every section build, so the 60 s section
    expiry re-paid it even while the shelf order itself was cached. It has the
    same media-write invalidation as that order; the shorter lifetime bounds
    how long a newly released episode can take to appear, since air dates move
    without any user write.
    """
    from django.core.cache import cache

    from app import cache_utils

    key = f"{cache_utils.HOME_ROW_CACHE_PREFIX}_released_{user.id}"
    released_ids = cache.get(key)
    if released_ids is None:
        with boundary("home_rank"):
            released_ids = LibraryQueryExecutor(user, query, share_shows=True).ranked_ids()
        cache.set(key, released_ids, cache_utils.HOME_ROW_RELEASED_CACHE_TTL)
        cache_utils.register_home_row_cache_key(user.id, key)
    return released_ids


def _episode_feed_row_window(user, row, offset, limit, seed):
    """Use the standard Home row window and cache for the TV episode queue."""
    query = from_home_row_filters(
        user,
        {"status": [Status.IN_PROGRESS.value], "progress": "not_caught_up"},
        (MediaTypes.TV.value,),
        sort_key=row.sort_by,
        direction=row.direction,
        seed=seed,
        mixed=True,
    )
    # Keep ended shows only while they have released episodes left. Use the
    # library's progress policy, rather than calculating a second watch state.
    released_ids = _cached_released_ids(
        user, query.with_sort(MediaSortChoices.TITLE, DirectionChoices.ASC),
    )
    continuing = Item.objects.annotate(
        production_status=Lower(Replace(Trim("status"), Value("_"), Value(" "))),
    ).exclude(production_status__in=TERMINAL_PRODUCTION_STATUSES)
    query = replace(
        query,
        filters=replace(query.filters, progress="all"),
        include_collection_only=False,
        within=Item.objects.filter(
            Q(pk__in=released_ids) | Q(pk__in=continuing.values("pk")),
        ).values("pk"),
    )
    items, total = _row_items(
        user,
        row,
        LibraryQueryExecutor(user, query, share_shows=True),
        offset,
        limit,
        seed=seed,
    )
    entries = _episode_feed_entries(_row_entries(user, items))
    return entries, total


def _episode_feed_entries(entries):
    """Decorate cards with the model manager's shared episode projection."""
    BasicMedia.objects.attach_episode_queue(
        [entry.media for entry in entries if entry.media]
    )
    for entry in entries:
        entry.next_episode = entry.media.next_episode_queue if entry.media else None
    return [entry for entry in entries if entry.next_episode]


def build_home_page_manifest(
    user,
    only_row_ids: set[int] | None = None,
) -> list[dict]:
    """Return enabled Home groups and rows in configured order, without sections.

    Cheap enough to call on every Home render: the shell uses it to lay out
    one deferred placeholder per row. Build sections with
    ``build_home_page_groups(..., groups=manifest)`` instead of rebuilding the
    configuration.
    """
    with boundary("home_config"):
        rows = ensure_home_screen_rows(user)
        enabled_media_types = get_home_configurable_media_types(user)
    rows_by_media_type: dict[str, list[HomeScreenRow]] = defaultdict(list)
    for row in rows:
        if row.enabled and (only_row_ids is None or row.id in only_row_ids):
            rows_by_media_type[row.media_type].append(row)

    groups = []
    for media_type in enabled_media_types:
        media_rows = rows_by_media_type.get(media_type, [])
        if not media_rows:
            continue
        groups.append(
            {
                "media_type": media_type,
                "label": _media_type_group_label(media_type),
                "icon_svg": str(
                    app_tags.icon(
                        _media_type_group_icon_name(media_type),
                        False,
                        "w-6 h-6 text-gray-300",
                    ),
                ),
                "rows": media_rows,
            },
        )
    return groups


def warm_first_group_split(user, manifest: list[dict], items_limit: int):
    """Split the manifest into an inline first group and the groups to defer.

    Returns ``(inline_index, defer_from)``. A group renders inline only when
    every row it needs is already cached, so the shell never computes a
    shelf; the first group with a cold row, and everything after it, loads as
    row fragments. ``inline_index`` is ``None`` when nothing is warm.
    """
    from django.core.cache import cache

    from app import cache_utils

    keys = {
        row.id: cache_utils.build_home_row_cache_key(user.id, row.id, items_limit)
        for group in manifest
        for row in group["rows"]
    }
    cached = cache.get_many(list(keys.values()))
    for index, group in enumerate(manifest):
        values = [cached.get(keys[row.id]) for row in group["rows"]]
        if any(value is None for value in values):
            return None, index
        if any(value != _HOME_ROW_EMPTY_SENTINEL for value in values):
            return index, index + 1
    return None, len(manifest)


def build_home_row(
    user,
    row_id: int,
    items_limit: int,
    *,
    refresh: bool = False,
) -> dict | None:
    """Build one enabled Home row for a deferred fragment request."""
    row = (
        HomeScreenRow.objects.filter(user=user, id=row_id, enabled=True)
        .select_related("custom_list")
        .first()
    )
    if row is None or row.media_type not in get_home_configurable_media_types(user):
        return None
    return _cached_row_section(
        user,
        row,
        row.media_type,
        items_limit,
        refresh=refresh,
    )


def build_home_page_groups(
    user,
    items_limit: int,
    load_row_id: int | None = None,
    load_row_offset: int = 0,
    *,
    load_row_seed: int | None = None,
    append_only: bool = False,
    only_row_id: int | None = None,
    only_row_ids: set[int] | None = None,
    refresh_row_cache: bool = False,
    first_group_only: bool = False,
    groups: list[dict] | None = None,
) -> list[dict]:
    """Build grouped home sections from persisted Home rows."""
    if only_row_id is not None:
        only_row_ids = (only_row_ids or set()) | {only_row_id}
    if groups is None:
        groups = build_home_page_manifest(user, only_row_ids)

    built_groups = []
    for group in groups:
        media_type = group["media_type"]
        row_sections = []
        for row in group["rows"]:
            if only_row_ids is not None and row.id not in only_row_ids:
                continue
            if load_row_id == row.id and append_only:
                # Offset pagination ("load more") bypasses the row cache.
                section = _build_row_section(
                    user,
                    row,
                    media_type,
                    items_limit,
                    batch_start=load_row_offset,
                    seed=load_row_seed,
                )
            else:
                section = _cached_row_section(
                    user,
                    row,
                    media_type,
                    items_limit,
                    refresh=refresh_row_cache,
                )
            if section is None:
                continue
            row_sections.append(section)
        if row_sections:
            built_groups.append(
                {
                    "media_type": media_type,
                    "label": group["label"],
                    "icon_svg": group["icon_svg"],
                    "rows": row_sections,
                },
            )
            if first_group_only:
                break
    return built_groups
