"""Translate each surface's stored or requested filters into a ``LibraryQuery``.

Three shapes exist and none of them change on disk:

- media-list parameters (``MediaListFilters``, parsed from the URL or API);
- smart-list rules (the normalized JSON saved on a ``CustomList``);
- Home row filters (smart-rule shaped, normalized by the Home screen).
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from app.library_query.spec import (
    ROUTING_LIBRARY,
    ROUTING_MODEL,
    STATUS_MATCH_ANY,
    STATUS_MATCH_CHOICES,
    STATUS_MATCH_LATEST,
    FilterValues,
    LibraryQuery,
    SortSpec,
)

# Smart-rule JSON records which evaluation semantics it was saved under.
# Rules without the key predate the shared engine and keep the semantics they
# were built with, so no saved list changes membership:
#   1 - status and rating match any tracker row, platform and format read the
#       item only, and anime tracked on TV rows stays in TV;
#   2 - the media list's semantics (latest row, collected copies, the user's
#       anime library preference).
SMART_RULES_SEMANTICS_KEY = "semantics_version"
SMART_RULES_LEGACY_SEMANTICS = 1
SMART_RULES_CURRENT_SEMANTICS = 2

# The media list and Home label end_date "Last Watched" (or Last Played, Read,
# Listened). Those surfaces mean the item's most recent activity, which for an
# in-progress row is progressed_at rather than a completion date. Custom list
# detail keeps end_date's literal meaning: that sort is labelled "End Date".
LAST_WATCHED_SORT_KEYS = frozenset({"end_date", "ended"})


def last_watched_sort_key(sort_key: str) -> str:
    """Translate a surface's "Last Watched" choice to the engine's key."""
    return "last_watched" if sort_key in LAST_WATCHED_SORT_KEYS else sort_key


def smart_rules_use_legacy_semantics(rules: dict) -> bool:
    """Return whether saved rules predate the shared engine's semantics."""
    try:
        version = int(rules.get(SMART_RULES_SEMANTICS_KEY) or SMART_RULES_LEGACY_SEMANTICS)
    except (TypeError, ValueError):
        version = SMART_RULES_LEGACY_SEMANTICS
    return version < SMART_RULES_CURRENT_SEMANTICS

if TYPE_CHECKING:
    from app.media_list_filters import MediaListFilters


def _values(raw) -> tuple[str, ...]:
    if raw in (None, ""):
        return ()
    if isinstance(raw, (list, tuple, set, frozenset)):
        return tuple(str(value).strip() for value in raw if str(value or "").strip())
    return (str(raw).strip(),)


def filter_values_from_media_list_filters(filters: MediaListFilters) -> FilterValues:
    """Build filter values from a parsed media-list query string.

    Status compares against the item's latest row, as the media list shows it.
    """
    return FilterValues(
        statuses=tuple(filters.statuses),
        include_no_status=filters.include_no_status,
        status_match=STATUS_MATCH_LATEST,
        search=filters.search,
        initial=filters.initial,
        game_group_by=filters.group_by,
        game_group=filters.group,
        rating=filters.rating,
        rating_min=filters.rating_min,
        rating_max=filters.rating_max,
        collection=filters.collection,
        progress=filters.progress,
        genre=filters.genre,
        implied_genre=filters.implied_genre,
        year=filters.year,
        completed_date_from=filters.completed_date_from,
        completed_date_to=filters.completed_date_to,
        date_added_from=filters.date_added_from,
        date_added_to=filters.date_added_to,
        updated_since=filters.updated_since,
        release=filters.release,
        release_date_from=filters.release_date_from,
        release_date_to=filters.release_date_to,
        source=filters.source,
        media_status=filters.media_status,
        language=filters.language,
        country=filters.country,
        origin=filters.origin,
        platforms=tuple(filters.platforms),
        platform_mode=filters.platform_mode,
        format=filters.format,
        author=filters.author,
        provider=filters.provider,
        tags=tuple(filters.tags),
        tag_mode=filters.tag_mode,
    )


def from_media_list_filters(
    filters: MediaListFilters,
    media_types: tuple[str, ...],
    *,
    seed: int = 0,
) -> LibraryQuery:
    """Build the query for a media-list or API request."""
    return LibraryQuery(
        media_types=media_types,
        filters=filter_values_from_media_list_filters(filters),
        sort=SortSpec(key=filters.sort or "title", direction=filters.direction, seed=seed),
        include_collection_only=filters.include_no_status,
        provider_region=filters.provider_region,
        pinned_providers=tuple(filters.pinned_providers),
    )


def filter_values_from_rules(
    rules: dict,
    *,
    default_status_match: str,
    collection_attributes: bool = True,
    season_effective_status: bool = False,
) -> FilterValues:
    """Build filter values from normalized smart-rule JSON.

    Relative date windows ("in the last N days") are resolved here, at
    evaluation time, so a saved rule keeps its meaning as time passes.
    """
    from lists.smart_rules import resolve_relative_date_windows

    rules = resolve_relative_date_windows(rules)
    status_match = rules.get("status_match") or default_status_match
    if status_match not in STATUS_MATCH_CHOICES:
        status_match = default_status_match
    platforms = _values(rules.get("platforms")) or _values(rules.get("platform"))
    return FilterValues(
        statuses=_values(rules.get("status")),
        status_match=status_match,
        search=str(rules.get("search") or ""),
        rating=str(rules.get("rating") or "all"),
        rating_min=str(rules.get("rating_min") or ""),
        rating_max=str(rules.get("rating_max") or ""),
        collection=str(rules.get("collection") or "all"),
        progress=str(rules.get("progress") or "all"),
        genre=str(rules.get("genre") or ""),
        implied_genre=str(rules.get("implied_genre") or ""),
        year=str(rules.get("year") or ""),
        completed_date_from=str(rules.get("completed_date_from") or ""),
        completed_date_to=str(rules.get("completed_date_to") or ""),
        date_added_from=str(rules.get("date_added_from") or ""),
        date_added_to=str(rules.get("date_added_to") or ""),
        release=str(rules.get("release") or "all"),
        release_date_from=str(rules.get("release_date_from") or ""),
        release_date_to=str(rules.get("release_date_to") or ""),
        source=str(rules.get("source") or ""),
        language=str(rules.get("language") or ""),
        country=str(rules.get("country") or ""),
        origin=str(rules.get("origin") or ""),
        platforms=platforms,
        platform_mode=str(rules.get("platform_mode") or "or"),
        format=str(rules.get("format") or ""),
        author=str(rules.get("author") or ""),
        provider=str(rules.get("provider") or ""),
        tags=_values(rules.get("tag")),
        tag_mode=str(rules.get("tag_mode") or "or"),
        collection_attributes=collection_attributes,
        season_effective_status=season_effective_status,
    )


def from_smart_rules(
    owner,
    rules: dict,
    media_types: tuple[str, ...],
    *,
    sort_key: str = "title",
    direction: str = "",
) -> LibraryQuery:
    """Build the query a smart list evaluates, under the semantics it was saved with."""
    legacy = smart_rules_use_legacy_semantics(rules)
    list_ids = tuple(int(value) for value in (rules.get("list") or []) if value)
    return LibraryQuery(
        media_types=media_types,
        filters=filter_values_from_rules(
            rules,
            default_status_match=STATUS_MATCH_ANY if legacy else STATUS_MATCH_LATEST,
            collection_attributes=not legacy,
        ),
        sort=SortSpec(key=sort_key or "title", direction=direction),
        union_list_ids=list_ids,
        include_podcast_shows=True,
        routing=ROUTING_MODEL if legacy else ROUTING_LIBRARY,
        provider_region=str(getattr(owner, "watch_provider_region", "") or ""),
    )


def home_engine_direction(sort_key: str, direction: str) -> str:
    """Translate a Home row's saved direction into the engine's.

    Home rows saved "descending popularity" to mean most popular first. The
    engine, like the media list, orders popularity by rank, where most popular
    first is ascending.
    """
    if sort_key == "popularity" and direction in ("asc", "desc"):
        return "asc" if direction == "desc" else "desc"
    return direction


def from_home_row_filters(
    owner,
    normalized_filters: dict,
    media_types: tuple[str, ...],
    *,
    sort_key: str,
    direction: str,
    seed: int = 0,
    mixed: bool = False,
) -> LibraryQuery:
    """Build the query a Home library shelf shows.

    Home rows show an item's current status, the same as the media list their
    title links to, so status compares against the latest row. ``mixed`` marks
    a shelf that combines media types.
    """
    return LibraryQuery(
        media_types=media_types,
        filters=replace(
            filter_values_from_rules(
                normalized_filters,
                default_status_match=STATUS_MATCH_LATEST,
                season_effective_status=True,
            ),
            progress_needs_released=mixed,
        ),
        sort=SortSpec(
            key=last_watched_sort_key(sort_key) or "title",
            direction=home_engine_direction(sort_key, direction),
            seed=seed,
        ),
        include_collection_only=True,
        provider_region=str(getattr(owner, "watch_provider_region", "") or ""),
        pinned_providers=tuple(getattr(owner, "pinned_watch_providers", None) or ()),
    )
