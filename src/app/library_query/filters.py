"""The filter registry: one definition per filter, shared by every surface.

Each ``FilterDef`` states how it can be evaluated:

- ``row``: a condition on a single tracker row. All active row conditions are
  combined into one ``Exists`` per tracker source, so "a row that is
  Completed *and* was added last week" means the same row, as smart lists
  have always evaluated it.
- ``sql``: a condition on the ``Item`` row (it may correlate subqueries).
- ``predicate``: a Python check on a hydrated candidate, for the filters whose
  semantics live in Python (provider availability, author shapes, derived
  progress).

Whether a query can be paginated in SQL is derived from these declarations
(see ``executor``); nothing keeps a separate list of "SQL-safe" filters.
Adding a filter means adding one ``FilterDef`` here.
"""

from __future__ import annotations

import contextlib
import datetime
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import reduce
from operator import and_, or_
from typing import TYPE_CHECKING

from django.db.models import CharField, Exists, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce, Concat
from django.db.models.lookups import (
    GreaterThanOrEqual,
    In,
    IsNull,
    LessThanOrEqual,
)

from app.library_query.alphabet import initial_condition, normalize_initial
from app.library_query.spec import STATUS_MATCH_ANY, STATUS_MATCH_LATEST, FilterValues
from app.models.choices import MediaTypes, Status
from app.models.discovery import CollectionEntry, ItemTag
from app.models.item import Item
from app.models.manager import item_ids_with_json_array_value_ci

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.library_query.trackers import TrackerSource

NEEDS_MEDIA = "media"
NEEDS_MAX_PROGRESS = "max_progress"
NEEDS_WATCH_PROVIDERS = "watch_providers"
NEEDS_RUNTIME = "runtime"

# Shows are "collected" when any of their episodes is, as well as directly.
SHOW_COLLECTION_MEDIA_TYPES = frozenset(
    {MediaTypes.TV.value, MediaTypes.ANIME.value, MediaTypes.SEASON.value},
)
PROGRESS_MEDIA_TYPES = frozenset(
    {MediaTypes.TV.value, MediaTypes.ANIME.value, MediaTypes.SEASON.value},
)


@dataclass(frozen=True)
class TypeContext:
    """What a filter needs to know to compile for one media type."""

    user: object
    media_type: str
    sources: tuple[TrackerSource, ...]
    today: datetime.date
    provider_region: str = ""
    pinned_providers: tuple[str, ...] = ()
    sort_list_id: int | None = None
    filters: FilterValues | None = None


@dataclass(frozen=True)
class FilterDef:
    """How one filter narrows the candidates."""

    key: str
    active: Callable[[FilterValues], bool]
    row: Callable[[FilterValues, TrackerSource, TypeContext], Q | None] | None = None
    sql: Callable[[FilterValues, TypeContext], Q | None] | None = None
    predicate: Callable[[object, FilterValues, TypeContext], bool] | None = None
    needs: frozenset[str] = field(default_factory=frozenset)
    # Runs once per scan batch before ``predicate``, for bulk annotation that
    # only some candidates need.
    prepare: Callable[[list, FilterValues, TypeContext], None] | None = None
    # The predicate reads nothing from a show's tracker row beyond what
    # ``app.tv_show_facts.TrackedShow`` carries (see ``SortDef.tv_facts``).
    tv_facts: bool = False


def _norm(value) -> str:
    return str(value or "").strip().lower()


def _decimal(value) -> Decimal | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _date(value) -> datetime.date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        return None


def any_q(qs):
    """OR conditions together; nothing matches an empty list."""
    return reduce(or_, qs) if qs else Q(pk__in=[])


def all_q(qs):
    """AND conditions together; everything matches an empty list."""
    return reduce(and_, qs) if qs else Q()


def latest_value(ctx: TypeContext, value_field: str, row_q: Q | None = None):
    """Return the item's value of ``value_field`` on its most recent row.

    An item belongs to exactly one tracker model, so coalescing the sources
    picks that model's answer.
    """
    subqueries = []
    for source in ctx.sources:
        rows = source.item_rows(ctx.user)
        if row_q is not None:
            rows = rows.filter(row_q)
        field_name = source.status_field if value_field == "status" else value_field
        value = Subquery(
                rows.annotate(_activity=source.activity())
                .order_by("-_activity", "-id")
                .values(field_name)[:1],
            )
        from app.library_query.trackers import AlbumSource

        if isinstance(source, AlbumSource):
            from django.db.models import Case, When

            value = Case(When(source.item_q, then=value), default=None)
        subqueries.append(value)
    if ctx.sort_list_id is not None and ctx.media_type == MediaTypes.PODCAST.value and value_field in {"status", "score"}:
        from app.models import PodcastShowTracker

        rows = PodcastShowTracker.objects.filter(
            user=ctx.user, show__podcast_uuid=OuterRef("media_id"),
            show__source=OuterRef("source"),
        )
        if row_q is not None:
            rows = rows.filter(row_q)
        subqueries.insert(0, Subquery(rows.values(value_field)[:1]))
    if len(subqueries) == 1:
        return subqueries[0]
    return Coalesce(*subqueries)


def any_row(ctx: TypeContext, row_q: Q | None = None) -> Q:
    """Return: the user has a row for the item matching ``row_q``."""
    return any_q([Q(pk__in=source.item_ids(ctx.user, row_q)) for source in ctx.sources])


# -- status -------------------------------------------------------------------


def _status_active(values: FilterValues) -> bool:
    return True


def _status_row(values: FilterValues, source: TrackerSource, ctx: TypeContext):
    statuses = [value for value in values.statuses if value and value != "all"]
    if ctx.sort_list_id is not None and ctx.media_type == MediaTypes.PODCAST.value:
        return None  # The list's podcast show tracker is matched in SQL below.
    if values.status_match == STATUS_MATCH_ANY:
        if statuses and not values.include_no_status:
            return Q(**{f"{source.status_field}__in": statuses})
        return None
    if not statuses and not values.include_no_status:
        # A statusless row (an imported rating with no tracking state) is
        # not part of any status view, including "All".
        return Q(**{f"{source.status_field}__isnull": False})
    if statuses and not values.include_no_status:
        # Implied by the latest-row check in ``_status_sql`` (the latest row
        # has one of these statuses, so some row does), and answered from the
        # (user, status) index - so the correlated check only runs on items
        # that can pass it. Seasons must also be stored in a requested
        # status; their episode history can only confirm it (see
        # ``season_effective_status``), which is how Home has always read it.
        return Q(**{f"{source.status_field}__in": statuses})
    return None


def season_effective_status(media) -> str | None:
    """Return the status a season reads as wherever it is displayed.

    A season's status follows its episode history
    (``derived_status_from_episode_progress``), except that a fully watched
    season still stored as In Progress keeps reading In Progress until it is
    promoted - Home's long-standing rule, now shared.
    """
    status = getattr(media, "status", None)
    derive = getattr(media, "derived_status_from_episode_progress", None)
    if derive is None or status in _SEASON_STORED_STATUSES:
        return status
    effective = derive(max_progress=getattr(media, "max_progress", None))
    if effective == Status.COMPLETED.value and status == Status.IN_PROGRESS.value:
        return status
    return effective


# A season stored with one of these reads as that status whatever its
# episodes say; only the others need their episode history derived.
_SEASON_STORED_STATUSES = frozenset(
    {Status.IN_PROGRESS.value, Status.DROPPED.value, Status.PAUSED.value},
)


def _prepare_season_status(candidates, values: FilterValues, ctx: TypeContext) -> None:
    from app.models import BasicMedia

    derived = [
        candidate.media
        for candidate in candidates
        if candidate.media is not None
        and candidate.media.status not in _SEASON_STORED_STATUSES
        and not hasattr(candidate.media, "max_progress")
    ]
    if derived:
        BasicMedia.objects.annotate_max_progress(derived, MediaTypes.SEASON.value)


def _season_status_predicate(candidate, values: FilterValues, ctx: TypeContext) -> bool:
    """Keep a season whose stored status matched only if it also reads that way."""
    statuses = {value for value in values.statuses if value and value != "all"}
    media = candidate.media
    status = season_effective_status(media) if media is not None else None
    if status is None:
        return values.include_no_status
    return status in statuses


def _season_status_active(values: FilterValues) -> bool:
    # A season stored In Progress, Dropped or Paused always reads that way, so
    # asking only for those needs no episode history - the query stays in SQL.
    statuses = {value for value in values.statuses if value and value != "all"}
    return (
        values.season_effective_status
        and bool(statuses - _SEASON_STORED_STATUSES)
        and values.status_match == STATUS_MATCH_LATEST
    )


def _status_sql(values: FilterValues, ctx: TypeContext):
    """Match the statuses asked for, and statusless items with "no status".

    "No status" on its own means only statusless items: no tracker row, or
    rows that carry no status (an imported rating).
    """
    statuses = [value for value in values.statuses if value and value != "all"]
    if not statuses and not values.include_no_status:
        return None
    if ctx.sort_list_id is not None and ctx.media_type == MediaTypes.PODCAST.value and values.status_match == STATUS_MATCH_ANY:
        from app.models import PodcastShowTracker

        shows = PodcastShowTracker.objects.filter(
            user=ctx.user, show__podcast_uuid=OuterRef("media_id"),
            show__source=OuterRef("source"),
        )
        matching = any_row(ctx, Q(status__in=statuses)) | Q(Exists(shows.filter(status__in=statuses)))
        if values.include_no_status:
            has_status = any_row(ctx, Q(status__isnull=False)) | Q(Exists(shows.filter(status__isnull=False)))
            matching |= ~has_status
        return matching
    if values.status_match == STATUS_MATCH_ANY:
        if not values.include_no_status:
            return None  # A row condition; see ``_status_row``.
        with_status = any_q(
            [
                Q(
                    pk__in=source.item_ids(
                        ctx.user,
                        Q(**{f"{source.status_field}__in": statuses}),
                    ),
                )
                for source in ctx.sources
            ],
        ) if statuses else Q(pk__in=[])
        has_any_status = any_q(
            [
                Q(
                    pk__in=source.item_ids(
                        ctx.user,
                        Q(**{f"{source.status_field}__isnull": False}),
                    ),
                )
                for source in ctx.sources
            ],
        )
        return with_status | ~has_any_status
    latest = latest_value(ctx, "status")
    condition = Q(In(latest, statuses)) if statuses else Q(pk__in=[])
    if values.include_no_status:
        condition |= Q(IsNull(latest, True))
    return condition


# -- rows by date --------------------------------------------------------------


def _date_added_row(values: FilterValues, source: TrackerSource, ctx: TypeContext):
    date_from = _date(values.date_added_from)
    date_to = _date(values.date_added_to)
    condition = Q()
    if date_from:
        condition &= Q(created_at__date__gte=date_from)
    if date_to:
        condition &= Q(created_at__date__lte=date_to)
    return condition


def _updated_since(values: FilterValues) -> datetime.datetime | None:
    text = str(values.updated_since or "").strip()
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def _updated_since_row(values: FilterValues, source: TrackerSource, ctx: TypeContext):
    # Episode rows have no stamp of their own; changing one moves the season
    # and show that own it (see app.library_changes).
    if not source.has_field("updated_at"):
        return Q(pk__in=[])
    return Q(updated_at__gte=_updated_since(values))


def _completed_row(values: FilterValues, source: TrackerSource, ctx: TypeContext):
    date_from = _date(values.completed_date_from)
    date_to = _date(values.completed_date_to)
    tracker_type = source.model._meta.model_name
    if tracker_type in (MediaTypes.TV.value, MediaTypes.SEASON.value):
        from app.models import Episode

        episode_lookup = (
            "related_season"
            if tracker_type == MediaTypes.SEASON.value
            else "related_season__related_tv"
        )
        episode_q = Q(**{episode_lookup: OuterRef("pk")})
        if date_from:
            episode_q &= Q(end_date__date__gte=date_from)
        if date_to:
            episode_q &= Q(end_date__date__lte=date_to)
        return Q(Exists(Episode.objects.filter(episode_q)))
    condition = Q()
    if date_from:
        condition &= Q(end_date__date__gte=date_from)
    if date_to:
        condition &= Q(end_date__date__lte=date_to)
    return condition


# -- rating -------------------------------------------------------------------


def _rating_active(values: FilterValues) -> bool:
    return (
        values.rating != "all"
        or _decimal(values.rating_min) is not None
        or _decimal(values.rating_max) is not None
    )


def _rating_sql(values: FilterValues, ctx: TypeContext):
    rating_min = _decimal(values.rating_min)
    rating_max = _decimal(values.rating_max)
    if values.status_match == STATUS_MATCH_ANY:
        scored = Q(score__isnull=False)
        if rating_min is not None:
            scored &= Q(score__gte=rating_min)
        if rating_max is not None:
            scored &= Q(score__lte=rating_max)
        if values.rating == "not_rated":
            return ~any_row(ctx, Q(score__isnull=False))
        return any_row(ctx, scored)

    latest_score = latest_value(ctx, "score", Q(score__isnull=False))
    if values.rating == "not_rated":
        return Q(IsNull(latest_score, True))
    condition = Q(IsNull(latest_score, False))
    if rating_min is not None:
        condition &= Q(GreaterThanOrEqual(latest_score, rating_min))
    if rating_max is not None:
        condition &= Q(LessThanOrEqual(latest_score, rating_max))
    return condition


# -- collection ---------------------------------------------------------------


def collected_q(ctx: TypeContext) -> Q:
    """Return the condition for an item the user has collected."""
    return Q(
        pk__in=CollectionEntry.objects.filter(user=ctx.user).values("item_id"),
    ) | Q(pk__in=shows_with_collected_episodes(ctx.user))


def shows_with_collected_episodes(user):
    """Return ids of show and season items with an episode the user collected.

    Driven from the user's collection: the candidate shows are narrowed by
    the collected episodes' ids before the exact (media id, source) pair is
    checked, so no other user's items are visited.
    """
    # Keep SQLite on the owner's collection index. A bare item media_type
    # predicate picks the catalogue type index and probes the collection once
    # per episode in the entire database (even for a small collection).
    collected_episodes = (
        CollectionEntry.objects.filter(user=user)
        .alias(_collection_media_type=Concat("item__media_type", Value("")))
        .filter(_collection_media_type=MediaTypes.EPISODE.value)
    )
    # One non-correlated pair set: SQLite evaluates it once. A correlated
    # Exists here re-walks the collected episodes per candidate (143 s on a
    # 100k-episode collection, 0.1 s as a set).
    pair = Concat("media_id", Value("\x1f"), "source", output_field=CharField())
    return (
        Item.objects.filter(
            media_type__in=SHOW_COLLECTION_MEDIA_TYPES,
            media_id__in=collected_episodes.values("item__media_id"),
        )
        .annotate(_show_pair=pair)
        .filter(
            _show_pair__in=collected_episodes.annotate(
                _pair=Concat(
                    "item__media_id",
                    Value("\x1f"),
                    "item__source",
                    output_field=CharField(),
                ),
            ).values("_pair"),
        )
        .values("pk")
    )


def _collection_sql(values: FilterValues, ctx: TypeContext):
    if values.collection == "collected":
        return collected_q(ctx)
    return ~collected_q(ctx)


# -- item metadata --------------------------------------------------------------


def _json_array_q(field_name: str, value: str) -> Q:
    return Q(pk__in=item_ids_with_json_array_value_ci(field_name, _norm(value)))


def _year_sql(values: FilterValues, ctx: TypeContext):
    year = _norm(values.year)
    if year == "unknown":
        return Q(release_datetime__isnull=True)
    with contextlib.suppress(TypeError, ValueError):
        return Q(release_datetime__year=int(year))
    return None


def _release_sql(values: FilterValues, ctx: TypeContext):
    released = Q(release_datetime__isnull=False, release_datetime__date__lte=ctx.today)
    if values.release == "released":
        return released
    return ~released


def _release_window_sql(values: FilterValues, ctx: TypeContext):
    date_from = _date(values.release_date_from)
    date_to = _date(values.release_date_to)
    condition = Q(release_datetime__isnull=False)
    if date_from:
        condition &= Q(release_datetime__date__gte=date_from)
    if date_to:
        condition &= Q(release_datetime__date__lte=date_to)
    return condition


def _platform_q(ctx: TypeContext, platform: str, *, collected: bool) -> Q:
    """Match a platform, preferring the platform the user collected it on."""
    if not collected:
        return _json_array_q("platforms", platform)
    explicit = CollectionEntry.objects.filter(user=ctx.user).exclude(resolution="")
    return Q(pk__in=explicit.filter(resolution__iexact=platform).values("item_id")) | (
        ~Q(pk__in=explicit.values("item_id")) & _json_array_q("platforms", platform)
    )


def _platforms_sql(values: FilterValues, ctx: TypeContext):
    platform_qs = [
        _platform_q(ctx, value, collected=values.collection_attributes)
        for value in values.platforms
    ]
    if values.platform_mode == "and":
        return all_q(platform_qs)
    if values.platform_mode == "not":
        return ~any_q(platform_qs)
    return any_q(platform_qs)


def _format_sql(values: FilterValues, ctx: TypeContext):
    own_format = Q(format__iexact=values.format.strip())
    if not values.collection_attributes:
        return own_format
    return own_format | Q(
        pk__in=CollectionEntry.objects.filter(
            user=ctx.user,
            media_type__iexact=values.format.strip(),
        ).values("item_id"),
    )


def _tag_q(ctx: TypeContext, tag: str) -> Q:
    return Q(
        pk__in=ItemTag.objects.filter(
            tag__user=ctx.user,
            tag__name__iexact=tag,
        ).values("item_id"),
    )


def _tags_sql(values: FilterValues, ctx: TypeContext):
    tag_qs = [_tag_q(ctx, tag) for tag in values.tags]
    if values.tag_mode == "and":
        return all_q(tag_qs)
    if values.tag_mode == "not":
        return ~any_q(tag_qs)
    return any_q(tag_qs)


def item_authors(item) -> list[str]:
    """Return an item's author names, whatever shape the provider stored."""
    authors = getattr(item, "authors", None) or []
    if not isinstance(authors, list):
        authors = [authors]
    names = []
    for author in authors:
        name = author
        if isinstance(author, dict):
            name = author.get("name") or author.get("person") or author.get("author")
        if name and str(name).strip():
            names.append(str(name).strip())
    return names


def _author_predicate(candidate, values: FilterValues, ctx: TypeContext) -> bool:
    wanted = _norm(values.author)
    return any(_norm(name) == wanted for name in item_authors(candidate.item))


def _provider_predicate(candidate, values: FilterValues, ctx: TypeContext) -> bool:
    from app.providers import tmdb

    item = candidate.item
    # No configured region means no providers to match, not "any provider".
    providers = set(tmdb.item_watch_provider_names(item, ctx.provider_region) or [])
    if ctx.pinned_providers:
        providers |= {
            match["provider_name"]
            for match in tmdb.pinned_provider_matches(item, ctx.pinned_providers)
            if match.get("provider_name")
        }
    wanted = _norm(values.provider)
    return any(_norm(name) == wanted for name in providers)


def _progress_predicate(candidate, values: FilterValues, ctx: TypeContext) -> bool:
    from app import helpers

    media = candidate.media
    if media is None:
        return False
    caught_up = helpers.is_caught_up_media(media)
    if values.progress == "caught_up":
        return caught_up
    if values.progress_needs_released and media.item.media_type == MediaTypes.TV.value:
        return not caught_up and bool(getattr(media, "released_episode_breakdown", None))
    return not caught_up


def _text(key: str):
    return lambda values: bool(_norm(getattr(values, key)))


def _game_group_sql(values, ctx):
    if ctx.media_type != MediaTypes.GAME.value:
        return None
    stem = "provider_collection" if values.game_group_by == "series" else "provider_franchise"
    if values.game_group == "__ungrouped__":
        return Q(**{f"{stem}_id": "", f"{stem}_name": ""})
    kind, _, value = values.game_group.partition(":")
    if kind == "id":
        return Q(**{f"{stem}_id": value})
    if kind == "name":
        return Q(**{f"{stem}_id": "", f"{stem}_name__iexact": value})
    return Q(pk__in=[])


FILTERS: tuple[FilterDef, ...] = (
    FilterDef("game_group", lambda v: v.game_group_by in {"series", "franchise"} and bool(v.game_group), sql=_game_group_sql),
    FilterDef("status", _status_active, row=_status_row, sql=_status_sql),
    FilterDef(
        "season_status",
        _season_status_active,
        predicate=_season_status_predicate,
        prepare=_prepare_season_status,
        needs=frozenset({NEEDS_MEDIA}),
    ),
    FilterDef(
        "date_added",
        lambda v: bool(_date(v.date_added_from) or _date(v.date_added_to)),
        row=_date_added_row,
    ),
    FilterDef(
        "updated_since",
        lambda v: _updated_since(v) is not None,
        row=_updated_since_row,
    ),
    FilterDef(
        "completed_date",
        lambda v: bool(_date(v.completed_date_from) or _date(v.completed_date_to)),
        row=_completed_row,
    ),
    FilterDef(
        "search",
        _text("search"),
        sql=lambda v, ctx: Q(title__icontains=v.search.strip())
        | Q(original_title__icontains=v.search.strip())
        | Q(localized_title__icontains=v.search.strip())
        | Q(media_id__icontains=v.search.strip()),
    ),
    FilterDef(
        "initial",
        lambda v: bool(normalize_initial(v.initial)),
        sql=lambda v, ctx: initial_condition(v.initial, ctx.user),
    ),
    FilterDef("rating", _rating_active, sql=_rating_sql),
    FilterDef(
        "collection",
        lambda v: v.collection in ("collected", "not_collected"),
        sql=_collection_sql,
    ),
    FilterDef("genre", _text("genre"), sql=lambda v, ctx: _json_array_q("genres", v.genre)),
    FilterDef(
        "implied_genre",
        _text("implied_genre"),
        sql=lambda v, ctx: _json_array_q("implied_genres", v.implied_genre),
    ),
    FilterDef("year", _text("year"), sql=_year_sql),
    FilterDef("release", lambda v: v.release in ("released", "not_released"), sql=_release_sql),
    FilterDef(
        "release_date",
        lambda v: bool(_date(v.release_date_from) or _date(v.release_date_to)),
        sql=_release_window_sql,
    ),
    FilterDef("source", _text("source"), sql=lambda v, ctx: Q(source__iexact=v.source.strip())),
    FilterDef(
        "media_status",
        _text("media_status"),
        sql=lambda v, ctx: Q(status=v.media_status.strip()),
    ),
    FilterDef(
        "language",
        _text("language"),
        sql=lambda v, ctx: _json_array_q("languages", v.language),
    ),
    FilterDef("country", _text("country"), sql=lambda v, ctx: Q(country__iexact=v.country.strip())),
    FilterDef("origin", _text("origin"), sql=lambda v, ctx: Q(country__iexact=v.origin.strip())),
    FilterDef("platforms", lambda v: bool(v.platforms), sql=_platforms_sql),
    FilterDef("format", _text("format"), sql=_format_sql),
    FilterDef("tags", lambda v: bool(v.tags), sql=_tags_sql),
    FilterDef("author", _text("author"), predicate=_author_predicate),
    FilterDef(
        "provider",
        _text("provider"),
        predicate=_provider_predicate,
        needs=frozenset({NEEDS_WATCH_PROVIDERS}),
    ),
    FilterDef(
        "progress",
        lambda v: v.progress in ("caught_up", "not_caught_up"),
        predicate=_progress_predicate,
        needs=frozenset({NEEDS_MEDIA, NEEDS_MAX_PROGRESS}),
        tv_facts=True,
    ),
)

FILTERS_BY_KEY = {definition.key: definition for definition in FILTERS}


def active_filters(values: FilterValues, media_type: str) -> list[FilterDef]:
    """Return the filters that narrow this query for ``media_type``."""
    active = [definition for definition in FILTERS if definition.active(values)]
    if media_type not in PROGRESS_MEDIA_TYPES:
        # Progress is caught-up-ness against released episodes; other media
        # types have no such notion, so the filter does not narrow them.
        active = [definition for definition in active if definition.key != "progress"]
    if media_type != MediaTypes.SEASON.value:
        active = [definition for definition in active if definition.key != "season_status"]
    return active


# Collection-only items have no tracker row, so row conditions, statuses and
# ratings cannot describe them. They are offered when no status is asked for,
# or when "no status" is, and never with a rating.
def collection_only_allowed(values: FilterValues) -> bool:
    """Return whether untracked collected items can satisfy these filters."""
    statuses = [value for value in values.statuses if value and value != "all"]
    return (
        (not statuses or values.include_no_status)
        and values.collection != "not_collected"
        and not _rating_active(values)
    )

