"""The sort registry: one definition per sort key, shared by every surface.

A ``SortDef`` has a SQL expression, or is computed in Python from the
hydrated candidate (reusing the media list's value function). Every surface
orders the same way: the value with nulls last, then the lower-cased title,
season and episode numbers, then the item id, all following the requested
direction (``executor.tie_breakers``). Equal values therefore cannot move
between pages.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.db.models import (
    BigIntegerField,
    Case,
    CharField,
    Count,
    DateTimeField,
    Exists,
    ExpressionWrapper,
    F,
    IntegerField,
    Max,
    Min,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from django.db.models.functions import Coalesce, Lower, NullIf
from django.db.models.lookups import Exact, IsNull

from app.library_query.filters import (
    NEEDS_MAX_PROGRESS,
    NEEDS_MEDIA,
    NEEDS_RUNTIME,
    TypeContext,
    latest_value,
)
from app.models.choices import Status

if TYPE_CHECKING:
    from collections.abc import Callable


# A seeded multiplicative hash: a fixed permutation of ids per seed, computed
# identically in SQL and Python, so a shuffled shelf pages without repeats or
# gaps. The seed is mixed in before a second multiply; adding it last would
# only rotate the same order. Every product stays inside a signed 64-bit int.
RANDOM_MODULUS = 4294967291
RANDOM_MULTIPLIER = 2654435761
RANDOM_MIXER = 1103515245


@dataclass(frozen=True)
class SortDef:
    """How one sort key orders candidates.

    ``sql`` returns ``None`` for a media type whose value it cannot express;
    the query is then ordered in Python.
    """

    keys: tuple[str, ...]
    sql: Callable[[TypeContext, int], object] | None = None
    needs: frozenset[str] = field(default_factory=frozenset)
    # Tracker-derived values differ per media type; the executor coalesces
    # them when a query spans several types.
    tracker: bool = False
    # Computes values for one batch of candidates, in order, for sorts whose
    # values need per-batch annotation. Receives (user, candidates, direction).
    batch_values: Callable[[object, list, str], list] | None = None
    # A sort whose value already encodes the direction is always ordered
    # ascending (for composite orders such as "upcoming, then recent").
    direction_in_value: bool = False
    # A composite order in SQL: returns ``(annotations, order_by keys)`` for
    # one media type, given (ctx, seed, requested direction), or None when
    # that type cannot express it. Keys refer to the annotations by name so an
    # expensive subquery is computed once per row. It must order exactly like
    # ``batch_values``, which still serves queries a Python filter scans.
    sql_order: Callable[[TypeContext, int, str], tuple | None] | None = None
    # The Python value reads nothing from a show's tracker row beyond what
    # ``app.tv_show_facts.TrackedShow`` carries, so a scan need not hydrate
    # seasons and plays to compute it. Other media types are unaffected.
    tv_facts: bool = False
    # The Python value reads the candidate's item and nothing of its tracker
    # rows, so a scan ordering by it loads no tracker. Only for a sort without
    # a SQL form; a filter that reads the tracker still loads it.
    item_only: bool = False
    # For a movie the Python value reads the item and the tracker's own
    # columns (status, progress, end date, creation), so a scan attaches an
    # ``app.movie_rows.MovieRow`` instead of a model. Other types unaffected.
    movie_rows: bool = False


def _field(name: str):
    return lambda ctx, seed: F(name)


def _tracker_aggregate(sort_key: str):
    """Order by the value ``_aggregate_item_data`` computes across rows."""
    field_name = {
        "start_date": "start_date",
        "end_date": "end_date",
        # Last watched is derived from the same rows, but falls back to
        # progressed_at for entries that are still in progress.
        "last_watched": "end_date",
        "progress": "progress",
    }[sort_key]

    def build(ctx: TypeContext, seed: int):
        from app.models import BasicMedia

        subqueries = []
        for source in ctx.sources:
            if source.is_episode:
                value = F(field_name)
                if sort_key == "last_watched":
                    value = Coalesce("end_date", Case(
                        When(status=Status.IN_PROGRESS.value, then=F("start_date")),
                    ))
                if sort_key != "progress":
                    aggregate = Min if sort_key == "start_date" else Max
                    subqueries.append(Subquery(
                        source.item_rows(ctx.user).order_by().values("item_id")
                        .annotate(value=aggregate(value)).values("value")[:1],
                    ))
                continue
            if not source.has_field(field_name):
                # The value is derived in Python (TV dates come from seasons).
                return None
            subquery = BasicMedia.objects._aggregated_sort_subquery(
                source.model,
                ctx.user,
                source.model._meta.model_name,
                sort_key,
                outer_ref="pk",
            )
            if subquery is not None:
                subqueries.append(subquery)
        if not subqueries:
            return Value(None)
        return subqueries[0] if len(subqueries) == 1 else Coalesce(*subqueries)

    return build


def _latest_score(ctx: TypeContext, seed: int):
    """Order by the score on the most recently active scored row."""
    return latest_value(ctx, "score", Q(score__isnull=False))


def _stores_progressed_at(model) -> bool:
    """Return whether ``progressed_at`` on this model is its stored column.

    TV and Season inherit the column but replace the attribute with a value
    derived from their episodes; Episode reports none. Only the column can be
    read in SQL.
    """
    from functools import cached_property

    stored = any(field.attname == "progressed_at" for field in model._meta.concrete_fields)
    attribute = inspect.getattr_static(model, "progressed_at", None)
    return stored and not isinstance(attribute, (property, cached_property))


def _progressed_at(ctx: TypeContext, seed: int):
    """Order by ``progressed_at`` on the row a scan displays.

    That is the item's newest tracker row by ``created_at`` (``_attach_media``
    keeps it; ``_aggregate_item_data`` leaves the value alone), not the
    latest ``progressed_at`` across repeat viewings.
    """
    from app.library_query.trackers import AlbumSource, PodcastShowSource
    from app.models import Podcast, PodcastShowTracker

    subqueries = []
    for source in ctx.sources:
        if source.is_episode:
            # An episode card reports no progressed_at.
            continue
        if isinstance(source, PodcastShowSource):
            # Handled below: a show tracker replaces the podcast row.
            continue
        if isinstance(source, AlbumSource) or not _stores_progressed_at(source.model):
            return None
        value = Subquery(
            source.item_rows(ctx.user)
            .order_by("-created_at", "-id")
            .values("progressed_at")[:1],
        )
        if source.model is Podcast:
            # A tracked show is displayed through its show tracker, which has
            # no progressed_at.
            show_tracked = PodcastShowTracker.objects.filter(
                user=ctx.user,
                show__podcast_uuid=OuterRef("media_id"),
                show__source=OuterRef("source"),
            )
            value = Case(
                When(Exists(show_tracked), then=Value(None)),
                default=value,
            )
        subqueries.append(value)
    if not subqueries:
        return Value(None, output_field=DateTimeField())
    return subqueries[0] if len(subqueries) == 1 else Coalesce(*subqueries)


def _first_created(ctx: TypeContext, seed: int):
    """Order by when the item was first tracked, not its latest row.

    A rewatch adds a newer tracker row; Date Added must stay the earliest one,
    matching the media list's ``aggregated_created_at``.
    """
    subqueries = [
        Subquery(
            source.item_rows(ctx.user)
            .order_by()
            .values("item_id")
            .annotate(value=Min("created_at"))
            .values("value")[:1],
        )
        for source in ctx.sources
    ]
    return subqueries[0] if len(subqueries) == 1 else Coalesce(*subqueries)


def _list_added(ctx: TypeContext, seed: int):
    """Order by when the item joined ``sort_list_id``."""
    from lists.models import CustomListItem

    if ctx.sort_list_id is None:
        return None
    return Subquery(
        CustomListItem.objects.filter(
            custom_list_id=ctx.sort_list_id,
            item_id=OuterRef("pk"),
        )
        .order_by("-date_added")
        .values("date_added")[:1],
    )


# Room for a list's positions below each tier rank in the ``list_tier`` value.
LIST_TIER_SPAN = 1_000_000


def _list_tier(ctx: TypeContext, seed: int):
    """Order by the item's tier in ``sort_list_id``, then its place in that tier.

    Tiers follow the list's tier order; Unranked comes last. Within a tier the
    list's own order (``date_added``, then id) applies, as in the custom sort.
    """
    from lists.models import CustomList, CustomListItem
    from lists.tiers import resolve_tiers

    if ctx.sort_list_id is None:
        return None
    custom_list = CustomList.objects.filter(pk=ctx.sort_list_id).first()
    tier_ids = [tier["id"] for tier in resolve_tiers(custom_list)] if custom_list else []
    members = CustomListItem.objects.filter(custom_list_id=ctx.sort_list_id)
    earlier = (
        members.filter(
            Q(date_added__lt=OuterRef("date_added"))
            | Q(date_added=OuterRef("date_added"), id__lt=OuterRef("id")),
        )
        .order_by()
        .values("custom_list_id")
        .annotate(n=Count("id"))
        .values("n")
    )
    mine = members.filter(item_id=OuterRef("pk")).annotate(
        place=Coalesce(Subquery(earlier), Value(0)),
    )
    tier = Subquery(mine.values("tier")[:1])
    rank = Case(
        *[When(Exact(tier, tier_id), then=Value(index)) for index, tier_id in enumerate(tier_ids)],
        default=Value(len(tier_ids)),
        output_field=IntegerField(),
    )
    return ExpressionWrapper(
        rank * Value(LIST_TIER_SPAN) + Subquery(mine.values("place")[:1]),
        output_field=BigIntegerField(),
    )


# Workflow order: what is planned, then under way, then done or set aside.
STATUS_RANK = (
    Status.PLANNING.value,
    Status.IN_PROGRESS.value,
    Status.COMPLETED.value,
    Status.PAUSED.value,
    Status.DROPPED.value,
)


def _status_rank(ctx: TypeContext, seed: int):
    latest = latest_value(ctx, "status")
    return Case(
        *[When(Exact(latest, status), then=Value(rank)) for rank, status in enumerate(STATUS_RANK)],
        default=Value(None),
        output_field=IntegerField(),
    )


def _platform(ctx: TypeContext, seed: int):
    """Order by the platform an item displays.

    The platform the user collected it on; else the platform an active filter
    asked for, when the item lists it; else the item's platform when it lists
    exactly one. Items listing several, with none chosen, have no platform.
    """
    from app.library_query.filters import _json_array_q
    from app.models.discovery import CollectionEntry

    collected = (
        CollectionEntry.objects.filter(user=ctx.user, item_id=OuterRef("pk"))
        .exclude(resolution="")
        .annotate(value=Lower("resolution"))
        .order_by("value")
        .values("value")[:1]
    )
    values = [Subquery(collected)]
    requested = ctx.filters.platforms if ctx.filters is not None else ()
    if requested and ctx.filters.platform_mode != "not":
        values.append(
            Case(
                When(_json_array_q("platforms", requested[0]), then=Value(requested[0].lower())),
                output_field=CharField(),
            ),
        )
    # An array index read as an expression compiles to ``->`` on Postgres and
    # ``json_extract`` on SQLite; the ``__isnull`` lookup would test for a
    # matching element instead of an index on Postgres.
    sole = Case(
        When(
            Q(IsNull(KeyTransform("1", "platforms"), True))
            & Q(IsNull(KeyTransform("0", "platforms"), False)),
            then=NullIf(Lower(KeyTextTransform("0", "platforms")), Value("")),
        ),
        output_field=CharField(),
    )
    values.append(sole)
    return Coalesce(*values, output_field=CharField())


def random_rank(item_id: int, seed: int) -> int:
    """Return an item's position key in the ``seed`` shuffle."""
    mixed = (item_id * RANDOM_MULTIPLIER + seed % RANDOM_MODULUS) % RANDOM_MODULUS
    return (mixed * RANDOM_MIXER) % RANDOM_MODULUS


def _random_sql(ctx: TypeContext, seed: int):
    mixed = (F("pk") * RANDOM_MULTIPLIER + seed % RANDOM_MODULUS) % RANDOM_MODULUS
    return ExpressionWrapper(
        (mixed * RANDOM_MIXER) % RANDOM_MODULUS,
        output_field=BigIntegerField(),
    )


# Measurements where zero means "not measured": such items sort last.
MEASURED_SORT_KEYS = frozenset({"runtime", "time_watched", "time_to_beat"})


def _next_episode_air_date_values(user, candidates, direction):
    """Return each candidate's next-episode air date, reading events per batch."""
    from app.models import BasicMedia

    medias = [c.media for c in candidates if c.media is not None]
    values = iter(BasicMedia.objects.next_episode_air_date_values(medias))
    return [None if candidate.media is None else next(values) for candidate in candidates]


def _media_list_value(sort_key: str):
    """Reuse the media list's Python value for keys that live in Python."""

    def value(candidate):
        from app.media_list_filters import MediaListEntry, _sort_value

        result = _sort_value(
            MediaListEntry(item=candidate.item, media=candidate.media),
            sort_key,
            None,
        )
        if sort_key in MEASURED_SORT_KEYS and not result:
            return None
        return result

    return value


_MEDIA = frozenset({NEEDS_MEDIA})
_MEDIA_RUNTIME = frozenset({NEEDS_MEDIA, NEEDS_MAX_PROGRESS, NEEDS_RUNTIME})

SORTS: tuple[SortDef, ...] = (
    SortDef(("title", ""), sql=lambda ctx, seed: Lower("title")),
    SortDef(("release_date", "release_datetime"), sql=_field("release_datetime")),
    SortDef(("critic_rating",), sql=_field("provider_rating")),
    SortDef(("popularity",), sql=_field("trakt_popularity_rank")),
    SortDef(("id", "itemid", "mediaid"), sql=_field("media_id")),
    SortDef(("source",), sql=_field("source")),
    SortDef(("type",), sql=_field("media_type")),
    SortDef(("date_added", "added", "created_at"), sql=_first_created, tracker=True),
    # A show has no SQL form for these (its dates and progress come from its
    # seasons' plays) and one tracker row, so its value is the ``TV``
    # property, which ``TrackedShow`` reads from grouped facts.
    SortDef(("start_date", "started"), sql=_tracker_aggregate("start_date"), tracker=True, tv_facts=True),
    SortDef(("end_date", "ended"), sql=_tracker_aggregate("end_date"), tracker=True, tv_facts=True),
    SortDef(("last_watched",), sql=_tracker_aggregate("last_watched"), tracker=True, tv_facts=True),
    SortDef(("score",), sql=_latest_score, tracker=True),
    SortDef(("progress", "plays"), sql=_tracker_aggregate("progress"), tracker=True, tv_facts=True),
    SortDef(("random",), sql=_random_sql),
    SortDef(("list_added",), sql=_list_added),
    SortDef(("list_tier",), sql=_list_tier),
    SortDef(("status",), sql=_status_rank, tracker=True),
    SortDef(("platform",), sql=_platform),
    # The rest are computed in Python from the hydrated candidate.
    # Shows rank from facts (plus the episode runtime index), movies from
    # their columns; other types hydrate their tracker rows.
    SortDef(("runtime",), needs=_MEDIA_RUNTIME, tv_facts=True, movie_rows=True),
    SortDef(("time_watched",), needs=_MEDIA_RUNTIME, tv_facts=True, movie_rows=True),
    SortDef(("time_to_beat",), item_only=True),
    SortDef(("author",), item_only=True),
    # SQL for types that store the column; TV and seasons scan.
    SortDef(("updated", "progressed_at"), sql=_progressed_at, needs=_MEDIA, tracker=True),
    # Episodes left (``max_progress - progress``), both of which a
    # ``TrackedShow`` carries. The web list's grouped time-left order is its
    # own pipeline (``app.tv_sort``); this one serves the API and Expo.
    SortDef(("time_left",), needs=frozenset({NEEDS_MEDIA, NEEDS_MAX_PROGRESS}), tv_facts=True),
    SortDef(
        ("next_episode_air_date",),
        needs=_MEDIA,
        batch_values=_next_episode_air_date_values,
    ),
)

SORTS_BY_KEY = {key: definition for definition in SORTS for key in definition.keys}


def register(definition: SortDef) -> None:
    """Add a surface's own sort keys (for example Home's "upcoming")."""
    for key in definition.keys:
        existing = SORTS_BY_KEY.get(key)
        if existing is not None and existing is not definition:
            msg = f"Sort key {key!r} is already registered."
            raise ValueError(msg)
        SORTS_BY_KEY[key] = definition


def sort_def(sort_key: str) -> SortDef:
    """Return the definition for ``sort_key``, falling back to title."""
    return SORTS_BY_KEY.get(sort_key or "", SORTS_BY_KEY["title"])


def python_key(sort_key: str):
    """Return the Python value function for a key without a SQL expression."""
    return _media_list_value(sort_key)
