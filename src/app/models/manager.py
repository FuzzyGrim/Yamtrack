# ruff: noqa: S608, S610
# This module hand-writes the case-insensitive JSON-array match the ORM cannot
# express across postgres and sqlite. Every identifier goes through
# connection.ops.quote_name() and the only user value is bound via params.
import contextlib
import datetime
import logging
from collections import defaultdict
from datetime import timedelta
from types import SimpleNamespace

from django.apps import apps
from django.conf import settings
from django.db import connection, models
from django.db.models import (
    Case,
    Count,
    Exists,
    F,
    Max,
    Min,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    When,
    Window,
)
from django.db.models.fields.json import KeyTransform
from django.db.models.functions import Cast, RowNumber
from django.db.models.query import ModelIterable, ValuesIterable
from django.utils import timezone
from django.utils.dateparse import parse_datetime

import events
import users
from app.models.choices import MediaTypes, Sources, Status
from app.models.discovery import ItemTag
from app.models.item import Item
from integrations.import_scope import ImportScopedQuerySet

logger = logging.getLogger(__name__)

MIN_PLAUSIBLE_YEAR = 1900

# How long a show whose season lookup failed is left to the database episode
# count before the provider is asked again during page rendering.
SEASON_MAX_PROGRESS_RETRY_SECONDS = 10 * 60

# Sort keys whose value aggregates an item's rows the way
# _aggregate_item_data does; ``_aggregated_sort_subquery`` expresses them in
# SQL for the library-query engine's sort registry.
SQL_SORTABLE_AGGREGATED_KEYS = frozenset(
    {
        "start_date",
        "started",
        "end_date",
        "ended",
        "last_watched",
        "score",
        "progress",
        "plays",
    },
)

_MEDIA_LIST_DEFERRED_ITEM_FIELDS = (
    "item__isbn",
    "item__creators",
    "item__provider_keywords",
    "item__provider_external_ids",
    "item__provider_certification",
    "item__provider_collection_id",
    "item__provider_collection_name",
    "item__provider_game_lengths_match",
    "item__provider_game_lengths_fetched_at",
    "item__trakt_popularity_fetched_at",
    "item__metadata_fetched_at",
    "item__themes",
    "item__provider_popularity",
    "item__provider_rating_count",
    "item__trakt_rating",
    "item__trakt_rating_count",
    "item__trakt_popularity_score",
    "item__publishers",
    "item__source_material",
    "item__series_name",
)


def _media_list_deferred_item_fields(*, needs_watch_providers):
    """Return the item columns a media list never reads.

    watch_providers is the largest column on the table -- TMDB's availability
    for every region it knows, around 146 KiB a title -- and no media-list
    template renders it; only the detail page does. Loading it for a page of
    entries cost ~590 MiB of JSON decoding on a 1,400-title library. The one
    list-side reader is the provider filter, so it is kept only when that
    filter is active. Deferring is also the safe way round: an unforeseen
    reader loads the column late rather than seeing it missing.
    """
    if needs_watch_providers:
        return _MEDIA_LIST_DEFERRED_ITEM_FIELDS
    return (*_MEDIA_LIST_DEFERRED_ITEM_FIELDS, "item__watch_providers")


# Exactly what _aggregate_item_data reads off a duplicate entry. "item" is the
# foreign key column, not the related row: the aggregation never touches
# entry.item, and selecting the id alone is what keeps a whole-library
# aggregation from hydrating a whole library of Items.
_AGGREGATION_FIELDS = (
    "item",
    "progress",
    "status",
    "score",
    "start_date",
    "end_date",
    "progressed_at",
    "created_at",
)


def _aggregation_projection(model):
    """Return the aggregation fields this model actually stores as columns.

    Not every tracking model spells all of them as concrete fields -- TV
    derives ``progress`` from its episodes rather than storing it -- and
    ``only()`` rejects a name that is not a field. Intersecting keeps the
    projection narrow without needing a per-model list to be maintained
    alongside the aggregation that reads it. A derived attribute is still
    readable afterwards; it is simply not something to select.
    """
    concrete = {field.name for field in model._meta.concrete_fields}
    return tuple(name for name in _AGGREGATION_FIELDS if name in concrete)


def _normalize_media_list_filter_value(value):
    return str(value or "").strip().lower()


def _normalize_completed_date_filter(value):
    """Return a YYYY-MM-DD string or empty string."""
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    try:
        datetime.date.fromisoformat(normalized)
    except ValueError:
        return ""
    return normalized


def _filter_queryset_by_item_json_array_ci(
    queryset,
    item_json_field: str,
    normalized_target: str,
):
    """Match Item JSON string arrays with case-insensitive element compare.

    The raw SQL is attached to an ``Item`` queryset and the media queryset is
    narrowed by the ids it yields, rather than correlating an EXISTS back to
    the media table. Raw SQL cannot see Django's table aliasing: the previous
    version referenced the media table by its real name, so the moment the
    media queryset was nested as a subquery - which
    ``get_media_list_item_values`` does, via
    ``Item.objects.filter(pk__in=queryset.values("item_id"))`` - Django
    aliased that table to ``U0`` and the reference no longer resolved
    ("no such column: app_movie.item_id"). Referring only to the column of
    the queryset's own table keeps it alias-independent.
    """
    if not normalized_target:
        return queryset
    return queryset.filter(
        item_id__in=item_ids_with_json_array_value_ci(item_json_field, normalized_target),
    )


def item_ids_with_json_array_value_ci(item_json_field: str, normalized_target: str):
    """Return an ``Item`` id subquery for a case-insensitive JSON-array match.

    ``normalized_target`` must already be trimmed and lower-cased; stored
    elements are compared the same way. Only the column of the
    ``Item`` table itself is referenced, so the subquery stays valid wherever
    Django nests it (see ``_filter_queryset_by_item_json_array_ci``).
    """
    col = Item._meta.get_field(item_json_field).column
    cc = connection.ops.quote_name(col)
    if connection.vendor == "postgresql":
        where_sql = f"""
            EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(
                    COALESCE({cc}::jsonb, '[]'::jsonb)
                ) AS _arr_el
                WHERE LOWER(TRIM(_arr_el::text)) = %s
            )
        """
    elif connection.vendor == "sqlite":
        where_sql = f"""
            EXISTS (
                SELECT 1 FROM json_each(COALESCE({cc}, '[]'))
                WHERE LOWER(TRIM(json_each.value)) = %s
            )
        """
    else:
        kw = {f"{item_json_field}__contains": [normalized_target]}
        return Item.objects.filter(**kw).values("id")
    return Item.objects.extra(
        where=[where_sql],
        params=[normalized_target],
    ).values("id")


class _LightRow(SimpleNamespace):
    """A values() row that answers the few things prefetch reads off a model.

    Read-only ranking graphs hold tens of thousands of Episode and Event rows
    per scan. A model instance costs ~30 us on a NAS CPU (init, signals, the
    field tracker); this costs a fraction. Module-level classes keep the rows
    picklable, since Home caches the graphs it builds.
    """

    model_label = ""
    _state = SimpleNamespace(db=None, adding=False, fields_cache={})

    @property
    def _meta(self):
        # Prefetch asks every row; resolve the model once per class.
        cls = type(self)
        meta = cls.__dict__.get("_resolved_meta")
        if meta is None:
            meta = cls._resolved_meta = apps.get_model(cls.model_label)._meta
        return meta

    @property
    def pk(self):
        return self.id


def _RawDatetime(field):  # noqa: N802
    """Select a datetime column as text so rows that are only compared skip parsing."""
    return Cast(field, output_field=models.CharField())


def _parse_raw_datetime(raw):
    """Turn a ``_RawDatetime`` value into what the column's converter returns."""
    if raw is None:
        return None
    parsed = parse_datetime(raw)
    if parsed is not None and settings.USE_TZ and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, datetime.UTC)
    return parsed


class _LazyEvent:
    """A season event whose datetime is parsed only if something reads it."""

    __slots__ = ("_datetime", "content_number", "item", "raw_datetime")

    def __init__(self, *, content_number, raw_datetime, item):
        self.content_number = content_number
        self.raw_datetime = raw_datetime
        self.item = item
        self._datetime = _UNPARSED

    @property
    def datetime(self):
        if self._datetime is _UNPARSED:
            self._datetime = _parse_raw_datetime(self.raw_datetime)
        return self._datetime


class _LazyItem:
    """A season item shared by its events; release_datetime parses on read."""

    __slots__ = ("_release_datetime", "media_id", "raw_release_datetime", "season_number", "source")

    def __init__(self, *, media_id, source, season_number, raw_release_datetime):
        self.media_id = media_id
        self.source = source
        self.season_number = season_number
        self.raw_release_datetime = raw_release_datetime
        self._release_datetime = _UNPARSED

    @property
    def release_datetime(self):
        if self._release_datetime is _UNPARSED:
            self._release_datetime = _parse_raw_datetime(self.raw_release_datetime)
        return self._release_datetime


_UNPARSED = object()


class _ShowEventsProbe(list):
    """Stands in for a show's season events and records whether they were read."""

    used = False

    def __iter__(self):
        self.used = True
        return iter(())


class LightEpisode(_LightRow):
    """An Episode values() row."""

    model_label = "app.Episode"


class LightEvent(_LightRow):
    """An Event values() row."""

    model_label = "events.Event"


class LightItem(_LightRow):
    """An Item values() row."""

    model_label = "app.Item"


class _LightRows(ModelIterable):
    """Read a values() query as ``row_class`` rows (a prefetch-safe iterable).

    Prefetch only accepts a ModelIterable; the query already selects just the
    values() columns, so it is read with the dict iterable. Module-level so a
    cached queryset holding it still pickles.
    """

    row_class = _LightRow

    def __iter__(self):
        for row in ValuesIterable(
            self.queryset,
            chunked_fetch=self.chunked_fetch,
            chunk_size=self.chunk_size,
        ):
            yield self.row_class(**row)


class LightEpisodeRows(_LightRows):
    """Episode rows for a values() prefetch."""

    row_class = LightEpisode


class LightEventRows(_LightRows):
    """Event rows for a values() prefetch."""

    row_class = LightEvent


class _EpisodeItemsAfterSelection(ImportScopedQuerySet):
    """Episodes that load their catalogue Items once the rows are selected.

    Prefetch("item") would do the same, but Django 5.2 expands it into one
    "id = ? OR ..." term per distinct item and SQLite rejects the tree past
    depth 1000 (#1450). in_bulk batches its IN lists, so any size loads.
    """

    # Read-only ranking graphs ask for these columns from each episode's Item.
    # Loading the rest (synopsis, artwork, provider blobs) was most of the
    # per-build object graph on a large TV/anime shelf.
    compact_item_fields: tuple[str, ...] | None = None

    def _clone(self):
        clone = super()._clone()
        clone.compact_item_fields = self.compact_item_fields
        return clone

    def _fetch_all(self):
        loading = self._result_cache is None
        super()._fetch_all()
        if loading and hasattr(self._iterable_class, "row_class"):
            # Light rows (see _light_rows): attach each one's compact Item row.
            ids = {row.item_id for row in self._result_cache if row.item_id}
            item_row = LightItem
            items = {}
            for start in range(0, len(ids), 500):
                for values in Item.objects.filter(
                    pk__in=list(ids)[start : start + 500],
                ).values(*dict.fromkeys(("id", *self.compact_item_fields))):
                    items[values["id"]] = item_row(**values)
            for row in self._result_cache:
                row.item = items.get(row.item_id)
        elif loading:
            # Episode.item is nullable; leave those rows without an item.
            item_ids = {
                episode.item_id for episode in self._result_cache if episode.item_id
            }
            if self.compact_item_fields:
                items = Item.objects.only(*self.compact_item_fields).in_bulk(item_ids)
            else:
                items = Item.objects.defer("watch_providers").in_bulk(item_ids)
            for episode in self._result_cache:
                if episode.item_id:
                    episode.item = items[episode.item_id]


class MediaManager(models.Manager.from_queryset(ImportScopedQuerySet)):
    """Custom manager for media models."""

    def get_historical_models(self):
        """Return list of historical model names."""
        return [f"historical{media_type}" for media_type in MediaTypes.values]

    def resolve_direction(self, sort_filter, direction=None):
        """Normalize sort direction with per-field defaults."""
        normalized = (direction or "").lower()
        if normalized not in ("asc", "desc"):
            return self._default_direction(sort_filter)
        return normalized

    def _default_direction(self, sort_filter):
        """Return default direction for a sort key."""
        if sort_filter in (
            "author",
            "popularity",
            "runtime",
            "start_date",
            "title",
            "next_episode_air_date",
            "time_left",
            "time_to_beat",
            "platform",
        ):
            return "asc"
        return "desc"

    def _apply_completed_date_filter(self, queryset, media_type, date_from, date_to):
        """Filter by completion date (end_date), TV/Season via episode Exists."""
        if media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value):
            episode_model = apps.get_model(app_label="app", model_name="episode")
            episode_lookup = (
                "related_season"
                if media_type == MediaTypes.SEASON.value
                else "related_season__related_tv"
            )
            episode_filter = Q(**{episode_lookup: OuterRef("pk")})
            if date_from:
                episode_filter &= Q(end_date__date__gte=date_from)
            if date_to:
                episode_filter &= Q(end_date__date__lte=date_to)
            return queryset.filter(
                Exists(episode_model.objects.filter(episode_filter)),
            )

        if date_from:
            queryset = queryset.filter(end_date__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(end_date__date__lte=date_to)
        return queryset

    def _apply_list_sql_filters(self, queryset, user, media_type, filters):
        """Apply Item-level filters in SQL before window deduplication and materialization."""
        if not filters:
            return queryset

        from app.library_query.alphabet import initial_condition

        queryset = queryset.filter(
            initial_condition(filters.get("initial"), user, prefix="item__")
        )

        genre = str(filters.get("genre") or "").strip()
        if genre:
            queryset = _filter_queryset_by_item_json_array_ci(
                queryset,
                "genres",
                _normalize_media_list_filter_value(genre),
            )
        implied_genre = str(filters.get("implied_genre") or "").strip()
        if implied_genre and media_type == MediaTypes.MUSIC.value:
            queryset = _filter_queryset_by_item_json_array_ci(
                queryset,
                "implied_genres",
                _normalize_media_list_filter_value(implied_genre),
            )

        year = str(filters.get("year") or "").strip()
        if year:
            normalized_year = _normalize_media_list_filter_value(year)
            if normalized_year == "unknown":
                queryset = queryset.filter(item__release_datetime__isnull=True)
            else:
                with contextlib.suppress(TypeError, ValueError):
                    queryset = queryset.filter(item__release_datetime__year=int(year))

        completed_date_from = _normalize_completed_date_filter(
            filters.get("completed_date_from"),
        )
        completed_date_to = _normalize_completed_date_filter(
            filters.get("completed_date_to"),
        )
        if completed_date_from or completed_date_to:
            queryset = self._apply_completed_date_filter(
                queryset,
                media_type,
                completed_date_from,
                completed_date_to,
            )

        release = str(filters.get("release") or "all").strip().lower()
        today = timezone.localdate()
        if release == "released":
            queryset = queryset.filter(
                item__release_datetime__isnull=False,
                item__release_datetime__date__lte=today,
            )
        elif release == "not_released":
            queryset = queryset.filter(
                Q(item__release_datetime__isnull=True)
                | Q(item__release_datetime__date__gt=today),
            )

        source = str(filters.get("source") or "").strip()
        if source:
            queryset = queryset.filter(item__source=source)

        media_status = str(filters.get("media_status") or "").strip()
        if media_status:
            queryset = queryset.filter(item__status=media_status)

        if media_type in (
            MediaTypes.TV.value,
            MediaTypes.MOVIE.value,
            MediaTypes.ANIME.value,
        ):
            language = str(filters.get("language") or "").strip()
            if language:
                queryset = _filter_queryset_by_item_json_array_ci(
                    queryset,
                    "languages",
                    _normalize_media_list_filter_value(language),
                )
            country = str(filters.get("country") or "").strip()
            if country:
                queryset = queryset.filter(item__country__iexact=country)

        if media_type == MediaTypes.GAME.value:
            platform_values = filters.get("platform_values") or ()
            platform_mode = (filters.get("platform_mode") or "or").strip().lower()
            if platform_values:
                CollectionEntry = apps.get_model("app", "CollectionEntry")

                def _matching_platform_item_ids(platform):
                    normalized_platform = _normalize_media_list_filter_value(platform)
                    explicit_collection_platforms = CollectionEntry.objects.filter(
                        user=user,
                        item_id=OuterRef("item_id"),
                    ).exclude(resolution="")
                    matching_collection_platforms = explicit_collection_platforms.filter(
                        resolution__iexact=platform,
                    )
                    platform_json_qs = _filter_queryset_by_item_json_array_ci(
                        queryset,
                        "platforms",
                        normalized_platform,
                    )
                    matching_queryset = queryset.annotate(
                        has_collection_platform=Exists(explicit_collection_platforms),
                        matches_collection_platform=Exists(
                            matching_collection_platforms,
                        ),
                    ).filter(
                        Q(matches_collection_platform=True)
                        | Q(
                            has_collection_platform=False,
                            pk__in=platform_json_qs.values("pk"),
                        ),
                    )
                    return set(matching_queryset.values_list("item_id", flat=True))

                platform_id_sets = [
                    _matching_platform_item_ids(value) for value in platform_values
                ]
                if platform_mode == "and":
                    platform_included_ids = set.intersection(*platform_id_sets)
                    platform_excluded_ids = None
                elif platform_mode == "not":
                    platform_included_ids = None
                    platform_excluded_ids = set().union(*platform_id_sets)
                else:
                    platform_included_ids = set().union(*platform_id_sets)
                    platform_excluded_ids = None

                if platform_included_ids is not None:
                    queryset = queryset.filter(item_id__in=platform_included_ids)
                if platform_excluded_ids is not None:
                    queryset = queryset.exclude(item_id__in=platform_excluded_ids)

        tag_included_ids = filters.get("tag_included_ids")
        if tag_included_ids is not None:
            queryset = queryset.filter(item_id__in=tag_included_ids)

        tag_excluded_ids = filters.get("tag_excluded_ids")
        if tag_excluded_ids is not None:
            queryset = queryset.exclude(item_id__in=tag_excluded_ids)

        return queryset

    def get_media_list(
        self,
        user,
        media_type,
        status_filter,
        sort_filter,
        search=None,
        direction=None,
        *,
        list_sql_filters=None,
        needs_watch_providers=False,
        prefetch_graph=True,
    ):
        """Get a media list by type with filtering and sorting.

        Pages are served by ``app.library_query``; this full-list form remains
        for the surfaces whose unit is not one item (separate-entry mode, TV's
        time-left grouping).

        ``prefetch_graph=False`` returns the same rows in the same order with
        only their item attached. It is for a caller that reads tracking
        state from ``app.tv_show_facts`` instead of the season, episode and
        event graph; any reader of that graph on such a row queries per row.
        """
        model = apps.get_model(app_label="app", model_name=media_type)
        direction = self.resolve_direction(sort_filter, direction)
        dup_state = {}

        # Build base queryset
        queryset = model.objects.filter(user=user.id)

        # Apply status filter. Statusless media (an imported rating with no
        # tracking state) is never part of a status view, including "All"; it is
        # surfaced separately by the "No Status" filter.
        if isinstance(status_filter, (list, tuple, set, frozenset)):
            status_filters = [
                value
                for value in status_filter
                if value and value != users.models.MediaStatusChoices.ALL
            ]
        elif status_filter and status_filter != users.models.MediaStatusChoices.ALL:
            status_filters = [status_filter]
        else:
            status_filters = []

        if status_filters:
            queryset = queryset.filter(status__in=status_filters)
        else:
            queryset = queryset.exclude(status__isnull=True)

        # Apply search filter
        if search:
            queryset = queryset.filter(
                models.Q(item__title__icontains=search)
                | models.Q(item__media_id__icontains=search),
            )

        queryset = self._apply_list_sql_filters(
            queryset, user, media_type, list_sql_filters or {}
        )

        # Handle duplicate entries by selecting the most recent record for each item
        has_progress_field = any(
            getattr(field, "attname", "") == "progress"
            for field in model._meta.get_fields()
            if getattr(field, "concrete", False)
        )
        if sort_filter == "progress" and has_progress_field:
            # For progress sorting, select the record with highest individual progress
            queryset = queryset.annotate(
                repeats=Window(
                    expression=Count("id"),
                    partition_by=[F("item")],
                ),
                row_number=Window(
                    expression=RowNumber(),
                    partition_by=[F("item")],
                    order_by=F("progress").desc(),
                ),
            ).filter(row_number=1)
        else:
            # For non-progress sorting, select the most recent record
            queryset = queryset.annotate(
                repeats=Window(
                    expression=Count("id"),
                    partition_by=[F("item")],
                ),
                row_number=Window(
                    expression=RowNumber(),
                    partition_by=[F("item")],
                    order_by=F("created_at").desc(),
                ),
            ).filter(row_number=1)

        queryset = queryset.select_related("item").defer(
            *_media_list_deferred_item_fields(needs_watch_providers=needs_watch_providers),
        )
        if prefetch_graph:
            queryset = self._apply_prefetch_related(
                queryset, media_type, list_mode=True
            )

        requires_presort_aggregation = sort_filter in (
            "progress",
            "plays",
            "next_episode_air_date",
        ) and media_type not in (MediaTypes.TV.value, MediaTypes.SEASON.value)

        # Generic progress sorting uses Python and reads aggregated_progress, so
        # duplicates must be aggregated before sorting in that specific path.
        if requires_presort_aggregation:
            queryset = self._aggregate_duplicate_data(
                queryset, user, media_type, dup_state
            )

        # Apply sorting AFTER aggregation
        if sort_filter:
            queryset = self._sort_media_list(
                queryset, sort_filter, media_type, direction
            )

        # Re-apply duplicate aggregation because SQL queryset operations in sorting
        # can materialize fresh model instances and drop dynamic aggregated attrs.
        return self._aggregate_duplicate_data(queryset, user, media_type, dup_state)

    # The view spells an unconfigured region this way; keep one spelling.
    UNSET_WATCH_PROVIDER_REGION = "UNSET"

    def get_media_list_item_values(
        self,
        user,
        media_type,
        status_filter,
        search=None,
        *,
        list_sql_filters=None,
        provider_region=None,
    ):
        """Return narrow Item projections for a media-list filter menu.

        This intentionally does not hydrate tracker rows, related objects, or
        duplicate aggregation.  It mirrors the SQL candidate filters so a
        cold SQL-paginated request can build its menu without loading the
        user's complete media library into Python.
        """
        model = apps.get_model(app_label="app", model_name=media_type)
        queryset = model.objects.filter(user=user.id)

        if isinstance(status_filter, (list, tuple, set, frozenset)):
            status_filters = [
                value
                for value in status_filter
                if value and value != users.models.MediaStatusChoices.ALL
            ]
        elif status_filter and status_filter != users.models.MediaStatusChoices.ALL:
            status_filters = [status_filter]
        else:
            status_filters = []
        if status_filters:
            queryset = queryset.filter(status__in=status_filters)
        else:
            queryset = queryset.exclude(status__isnull=True)

        if search:
            queryset = queryset.filter(
                models.Q(item__title__icontains=search)
                | models.Q(item__media_id__icontains=search),
            )
        queryset = self._apply_list_sql_filters(
            queryset, user, media_type, list_sql_filters or {}
        )

        # Match the status semantics used by the rendered web list: when a
        # status is selected, it refers to the latest aggregated activity,
        # not merely to the existence of an older row in that status.
        if status_filters:
            activity = Case(
                When(end_date__isnull=False, then=F("end_date")),
                When(progressed_at__isnull=False, then=F("progressed_at")),
                default=F("created_at"),
                output_field=models.DateTimeField(),
            )
            latest_status = (
                model.objects.filter(item_id=OuterRef("item_id"), user=user.id)
                .annotate(_activity=activity)
                .order_by("-_activity", "-id")
                .values("status")[:1]
            )
            queryset = queryset.annotate(_latest_status=Subquery(latest_status)).filter(
                _latest_status__in=status_filters
            )

        item_queryset = Item.objects.filter(
            pk__in=queryset.values("item_id")
        ).order_by()
        return self.item_values_for_menu(item_queryset, provider_region)

    def item_values_for_menu(self, item_queryset, provider_region=None):
        """Return the narrow ``Item`` rows a media-list filter menu reads."""
        fields = [
            "id",
            "media_id",
            "media_type",
            "library_media_type",
            "title",
            "release_datetime",
            "genres",
            "implied_genres",
            "country",
            "languages",
            "platforms",
            "format",
            "authors",
            "source",
            "status",
        ]
        # watch_providers holds TMDB's availability for every region it knows
        # -- around 139 of them, and roughly 146 KiB per item. The filter menu
        # reads exactly one region out of it, so selecting the column meant
        # carrying about 204 MiB for a 1,400-title movie list and discarding
        # 138/139 of it. Ask SQL for the one region instead, and ask for
        # nothing at all where no provider filter is shown.
        if provider_region and provider_region != self.UNSET_WATCH_PROVIDER_REGION:
            return item_queryset.values(*fields).annotate(
                watch_providers_region=KeyTransform(
                    provider_region,
                    "watch_providers",
                    output_field=models.JSONField(),
                ),
            )
        return item_queryset.values(*fields)

    def _aggregated_sort_subquery(
        self, model, user, media_type, sort_key, outer_ref="item_id",
    ):
        """Return a Subquery matching _aggregate_item_data's per-item semantics.

        Correlated by item_id + user only (not status) — an item tracked as
        IN_PROGRESS can have an older DROPPED entry with an earlier
        start_date that should still win, exactly like the Python aggregation
        this replaces. Returns None for raw-column sort keys, which the
        caller orders on directly instead. ``outer_ref`` names the outer
        column holding the item id: ``item_id`` on a tracker queryset,
        ``pk`` on an ``Item`` queryset.
        """
        if sort_key not in SQL_SORTABLE_AGGREGATED_KEYS:
            return None
        base = model.objects.filter(item_id=OuterRef(outer_ref), user=user.id)

        if sort_key in ("start_date", "started"):
            inner = base.order_by().values("item_id").annotate(agg=Min("start_date"))
            return Subquery(inner.values("agg")[:1])
        if sort_key in ("end_date", "ended"):
            inner = base.order_by().values("item_id").annotate(agg=Max("end_date"))
            return Subquery(inner.values("agg")[:1])
        if sort_key == "last_watched":
            concrete = {field.attname for field in model._meta.concrete_fields}
            if "progressed_at" in concrete:
                # A completed entry with no watch date has no last watched
                # value; progressed_at only records when it was logged.
                activity = Case(
                    When(end_date__isnull=False, then=F("end_date")),
                    When(
                        ~Q(status=Status.COMPLETED.value),
                        progressed_at__isnull=False,
                        then=F("progressed_at"),
                    ),
                    default=None,
                    output_field=models.DateTimeField(),
                )
            else:
                activity = F("end_date")
            inner = base.order_by().values("item_id").annotate(agg=Max(activity))
            return Subquery(inner.values("agg")[:1])
        if sort_key in ("progress", "plays"):
            if media_type == MediaTypes.MOVIE.value:
                expr = Count(
                    "id",
                    filter=Q(end_date__isnull=False) | Q(status=Status.COMPLETED.value),
                )
            else:
                expr = Sum("progress")
            inner = base.order_by().values("item_id").annotate(agg=expr)
            return Subquery(inner.values("agg")[:1])
        if sort_key == "score":
            activity = Case(
                When(end_date__isnull=False, then=F("end_date")),
                When(progressed_at__isnull=False, then=F("progressed_at")),
                default=F("created_at"),
                output_field=models.DateTimeField(),
            )
            inner = (
                base.filter(score__isnull=False)
                .annotate(activity=activity)
                .order_by("-activity")
            )
            return Subquery(inner.values("score")[:1])
        return None

    def _aggregate_duplicate_data(self, queryset, user, media_type, dup_state=None):
        """Aggregate data from duplicate entries for each item."""
        # Materialize once — avoids re-evaluating the queryset (and re-firing all
        # prefetch queries) for both the ID-collection pass and the aggregation pass.
        # Lists pass through as-is, so callers that already hold a list are safe.
        media_list = queryset if isinstance(queryset, list) else list(queryset)

        queried_item_ids = frozenset(media.item_id for media in media_list)

        if not queried_item_ids:
            return media_list

        model = apps.get_model(app_label="app", model_name=media_type)

        if (
            dup_state is not None
            and dup_state.get("ids") == queried_item_ids
            and dup_state.get("groups") is not None
        ):
            media_by_item = dup_state["groups"]
        else:
            # Fetch ALL entries (across all statuses) for only the queried items.
            # Using all statuses is intentional: an item filtered as IN_PROGRESS may have
            # a more-recent COMPLETED entry that should determine its aggregated_status.
            #
            # _aggregate_item_data reads scalar fields off these entries and
            # nothing else: item.media_type is read from the *displayed* row,
            # which the caller's own queryset already select_related. So this
            # skips _apply_prefetch_related (that events/tags/seasons/episodes
            # bundle belongs to the displayed list, and re-fetching it here was
            # doubling those queries for every page), and it projects only the
            # columns the aggregation reads.
            #
            # The projection is the memory fix. This filter is by item id, not
            # by page, so on a list that is not paginated in SQL it returns a
            # row for every tracked title -- and with select_related("item") it
            # hydrated a full Item for each one, including synopsis and the
            # ~146 KiB watch_providers blob the surrounding querysets are
            # careful to defer. One SQL query, hundreds of MiB of JSON
            # decoding: exactly the shape production showed on
            # /medialist/movie, two minutes of wall time across eleven queries.
            #
            # Only an item with more than one row is ever aggregated (see the
            # ``len(entries) > 1`` check below), so find those with one
            # grouped count and hydrate just their rows. A library of
            # single-row items then costs one count query, not a model per
            # title.
            duplicate_item_ids = list(
                model.objects.filter(user=user.id, item_id__in=queried_item_ids)
                .order_by()
                .values("item_id")
                .annotate(row_count=Count("pk"))
                .filter(row_count__gt=1)
                .values_list("item_id", flat=True),
            )
            all_media = (
                model.objects.filter(
                    user=user.id,
                    item_id__in=duplicate_item_ids,
                )
                .only(*_aggregation_projection(model))
                # Media's default ordering is ["user", "item", "-created_at"],
                # which makes the database join users_user and app_item purely
                # to sort a result set that is about to be grouped into a dict
                # by item id. Only the order *within* a group is observable
                # here -- _aggregate_item_data breaks an activity tie by
                # keeping the entry it saw first -- and within a group both
                # leading keys are constant: the filter pins one user, and the
                # group key is the item. So "-created_at" alone produces the
                # identical grouping, without the joins or the sort.
                .order_by("-created_at")
            )

            # Group media by item_id
            media_by_item = {}
            for media in all_media:
                media_by_item.setdefault(media.item_id, []).append(media)

            if dup_state is not None:
                dup_state["ids"] = queried_item_ids
                dup_state["groups"] = media_by_item

        for media in media_list:
            entries = media_by_item.get(media.item_id, [])
            if len(entries) > 1:
                self._aggregate_item_data(media, entries)

        return media_list

    def _aggregate_item_data(self, display_media, all_media_entries):
        """Aggregate data from multiple media entries for the same item."""
        # Aggregate progress:
        # - Movies: count completed entries as plays (legacy rows may have progress=0)
        # - Other media: sum raw progress values
        if getattr(display_media.item, "media_type", None) == MediaTypes.MOVIE.value:
            completed_entries = [
                entry
                for entry in all_media_entries
                if entry.end_date or entry.status == Status.COMPLETED.value
            ]
            total_progress = len(completed_entries)
        else:
            total_progress = sum(entry.progress for entry in all_media_entries)
        display_media.aggregated_progress = total_progress

        # Date added is when the item was first tracked, not the latest entry
        display_media.aggregated_created_at = min(
            entry.created_at for entry in all_media_entries
        )

        # Aggregate start date (earliest start date)
        start_dates = [
            entry.start_date for entry in all_media_entries if entry.start_date
        ]
        if start_dates:
            display_media.aggregated_start_date = min(start_dates)
        else:
            display_media.aggregated_start_date = None

        # Aggregate end date (latest end date)
        end_dates = [entry.end_date for entry in all_media_entries if entry.end_date]
        if end_dates:
            display_media.aggregated_end_date = max(end_dates)
        else:
            display_media.aggregated_end_date = None

        # Last watched is the latest activity across rows: the completion date
        # when there is one, otherwise the last progress update. TV and Season
        # already derive end_date from their episodes, so progressed_at is only
        # read where the model stores it.
        has_progressed_at = any(
            field.attname == "progressed_at"
            for field in type(display_media)._meta.concrete_fields
        )
        last_watched_dates = []
        for entry in all_media_entries:
            activity = entry.end_date
            if (
                activity is None
                and has_progressed_at
                and entry.status != Status.COMPLETED.value
            ):
                activity = entry.progressed_at
            if activity is not None:
                last_watched_dates.append(activity)
        display_media.aggregated_last_watched = (
            max(last_watched_dates) if last_watched_dates else None
        )

        # Aggregate status (most recent status by activity)
        latest_status = None
        latest_status_activity = None

        # Aggregate rating (find the most recent rating among all entries)
        # Since created_at only represents when the entry was first created,
        # we need to use a different approach to find the most recent rating
        # We'll prioritize entries with more recent activity (end_date, progressed_at)
        latest_rating = None
        latest_rating_activity = None

        for entry in all_media_entries:
            if entry.score is not None:
                # Determine the most recent activity for this entry
                entry_activity = None
                if entry.end_date:
                    entry_activity = entry.end_date
                elif entry.progressed_at:
                    entry_activity = entry.progressed_at
                else:
                    entry_activity = entry.created_at

                # If this entry has more recent activity, use its rating
                if (
                    latest_rating_activity is None
                    or entry_activity > latest_rating_activity
                ):
                    latest_rating_activity = entry_activity
                    latest_rating = entry.score
            else:
                entry_activity = (
                    entry.end_date or entry.progressed_at or entry.created_at
                )

            if entry_activity and (
                latest_status_activity is None
                or entry_activity > latest_status_activity
            ):
                latest_status_activity = entry_activity
                latest_status = entry.status

        display_media.aggregated_status = latest_status or display_media.status

        if latest_rating is not None:
            display_media.aggregated_score = latest_rating
        else:
            display_media.aggregated_score = None

        # Store the number of repeats for display
        display_media.repeats = len(all_media_entries)

    def _apply_prefetch_related(
        self,
        queryset,
        media_type,
        list_mode=False,
        *,
        compact_episodes=False,
        light_rows=False,
        defer_item_fields=(),
    ):
        """Apply appropriate prefetch_related based on media type.

        list_mode=True narrows the episode queryset to only the fields required
        for list-view progress/date calculations, avoiding the cost of
        materializing full Item objects for every episode.

        defer_item_fields names Item columns to leave out of the season and
        episode items this prefetches (Statistics caches these rows).
        """
        TV = apps.get_model("app", "TV")
        Season = apps.get_model("app", "Season")
        Episode = apps.get_model("app", "Episode")
        episode_qs = Episode.objects.select_related("item")
        if defer_item_fields:
            episode_qs = episode_qs.defer(
                *(f"item__{field}" for field in defer_item_fields)
            )
        if compact_episodes and (queryset.model == TV or media_type == MediaTypes.SEASON.value):
            # Window sorting runs over every watch. Joining the catalogue here
            # copies its wide JSON/text columns into every intermediate row.
            # Fetch Items only after the window has selected its representatives.
            active = Episode.objects.all()
            episode_qs = _EpisodeItemsAfterSelection(
                model=active.model, query=active.query, using=active._db, hints=active._hints,
            )
            # Read-only card/ranking graphs need per-identity counts and date
            # bounds, not one Python Episode/Item pair per historical watch.
            partition = [F("related_season_id"), F("item_id")]
            aggregates = {
                "_card_total_count": Count("pk"),
                "_card_completed_count": Count("pk", filter=Episode.watch_q()),
                "_card_dropped_count": Count("pk", filter=Q(status=Status.DROPPED.value)),
                "_card_first_end": Min("end_date"),
                "_card_last_end": Max("end_date"),
            }
            season_lookup = "related_season__related_tv_id" if queryset.model == TV else "related_season_id"
            # Different Items sharing one episode number can interleave in
            # next-air-date ordering. Keep their raw rows rather than change
            # tie semantics during identity correction or alternate ordering.
            ambiguous_seasons = (
                Episode.objects.filter(**{f"{season_lookup}__in": queryset.order_by().values("pk")})
                .order_by().values("related_season_id", "item__episode_number")
                .annotate(item_count=Count("item_id", distinct=True))
                .filter(item_count__gt=1).values("related_season_id")
            )
            episode_qs = episode_qs.annotate(
                **{name: Window(expression, partition_by=partition) for name, expression in aggregates.items()},
                _card_position=Window(RowNumber(), partition_by=partition, order_by=F("pk").asc()),
                _card_is_summary=Case(
                    When(related_season_id__in=Subquery(ambiguous_seasons), then=models.Value(False)),
                    When(related_season__rewatch_started_at__isnull=True, then=models.Value(True)),
                    default=models.Value(False), output_field=models.BooleanField(),
                ),
            ).filter(Q(_card_is_summary=False) | Q(_card_position=1))
            # The ranking graph reads columns, not models (see _light_rows).
            # Home reads more of each episode (title, artwork), so it opts out.
            if light_rows:
                episode_qs = episode_qs.values(
                    "id",
                    "end_date",
                    "status",
                    "dropped",
                    "created_at",
                    "related_season_id",
                    "item_id",
                    "_card_is_summary",
                    "_card_position",
                    *aggregates,
                )
                episode_qs._iterable_class = LightEpisodeRows
                episode_qs.compact_item_fields = ("episode_number", "release_datetime")
        event_qs = events.models.Event.objects.all()
        if light_rows:
            # Ranking reads a few scalar columns of each event, never a model.
            event_qs = event_qs.values(
                "id", "item_id", "content_number", "datetime", "release_type", "region",
            )
            event_qs._iterable_class = LightEventRows
        # Apply media-specific prefetches
        if media_type == MediaTypes.TV.value or (
            media_type == MediaTypes.ANIME.value and queryset.model == TV
        ):
            if list_mode:
                # Load only the fields accessed in the list path:
                # ep.item.episode_number, ep.item.release_datetime (the
                # next-episode air date falls back to it), ep.end_date, and
                # ep.status. Deferring the remaining ~30 Item columns cuts
                # Django object instantiation time proportionally for large
                # libraries.
                if not hasattr(episode_qs._iterable_class, "row_class"):
                    episode_qs = episode_qs.only(
                        "id",
                        "end_date",
                        "status",
                        "dropped",
                        "created_at",
                        "related_season_id",
                        "item__id",
                        "item__episode_number",
                        "item__release_datetime",
                    )
                if isinstance(episode_qs, _EpisodeItemsAfterSelection):
                    # `.only` narrows the episode columns; this narrows the
                    # catalogue rows `_fetch_all` loads for them as well.
                    episode_qs.compact_item_fields = (
                        "id",
                        "episode_number",
                        "release_datetime",
                    )
            return queryset.prefetch_related(
                Prefetch(
                    "seasons",
                    queryset=Season.objects.select_related("item").defer(
                        *(("item__watch_providers",) if list_mode else ()),
                        *(f"item__{field}" for field in defer_item_fields),
                    ),
                ),
                Prefetch(
                    "seasons__item__event_set",
                    queryset=event_qs,
                    to_attr="prefetched_events",
                ),
                Prefetch(
                    "seasons__episodes",
                    queryset=episode_qs,
                ),
                Prefetch(
                    "item__item_tags",
                    queryset=ItemTag.objects.select_related("tag"),
                ),
            )

        base_queryset = queryset.prefetch_related(
            Prefetch(
                "item__event_set",
                queryset=event_qs,
                to_attr="prefetched_events",
            ),
            Prefetch(
                "item__item_tags",
                queryset=ItemTag.objects.select_related("tag"),
            ),
        )

        if media_type == MediaTypes.SEASON.value:
            return base_queryset.select_related("related_tv__item").prefetch_related(
                Prefetch(
                    "episodes",
                    queryset=episode_qs,
                ),
            )

        if media_type == MediaTypes.MUSIC.value:
            return base_queryset.select_related("album")

        return base_queryset

    def _sort_media_list(self, queryset, sort_filter, media_type=None, direction=None):
        """Sort media list using SQL sorting with annotations for calculated fields."""
        direction = self.resolve_direction(sort_filter, direction)
        if sort_filter == "last_watched" and media_type in (
            MediaTypes.TV.value,
            MediaTypes.SEASON.value,
        ):
            # These models derive end_date from their watched episodes, which
            # is already the last watched value.
            sort_filter = "end_date"
        if media_type == MediaTypes.TV.value:
            return self._sort_tv_media_list(queryset, sort_filter, direction)
        if media_type == MediaTypes.SEASON.value:
            return self._sort_season_media_list(queryset, sort_filter, direction)

        return self._sort_generic_media_list(queryset, sort_filter, direction)

    def _season_events_by_show(self, shows):
        """Return ``{(media_id, source): [event, ...]}`` for shows' season events.

        One query for all the shows, ordered by season then episode. Only what
        the next-episode date reads is loaded from each event's item.
        """
        shows = set(shows)
        events_by_show = defaultdict(list)
        media_ids = sorted({media_id for media_id, _source in shows})
        for start in range(0, len(media_ids), 500):
            season_events = (
                events.models.Event.objects.filter(
                    item__media_id__in=media_ids[start : start + 500],
                    item__source__in={source for _media_id, source in shows},
                    item__media_type=MediaTypes.SEASON.value,
                    item__season_number__gt=0,
                    content_number__isnull=False,
                )
                .order_by("item__season_number", "content_number")
                .values_list(
                    "content_number",
                    _RawDatetime("datetime"),
                    "item__media_id",
                    "item__source",
                    "item__season_number",
                    _RawDatetime("item__release_datetime"),
                )
            )
            season_items = {}
            # A large library has tens of thousands of these rows. Plain
            # namespaces carry the few attributes the next-episode date reads
            # without paying for a model instance (and its signals) per row.
            for (
                content_number,
                event_datetime,
                media_id,
                source,
                season_number,
                release_datetime,
            ) in season_events:
                key = (media_id, source)
                if key in shows:
                    season_item = season_items.get((key, season_number))
                    if season_item is None:
                        season_item = season_items[(key, season_number)] = _LazyItem(
                            media_id=media_id,
                            source=source,
                            season_number=season_number,
                            raw_release_datetime=release_datetime,
                        )
                    events_by_show[key].append(
                        _LazyEvent(
                            content_number=content_number,
                            raw_datetime=event_datetime,
                            item=season_item,
                        ),
                    )
        return events_by_show

    def next_episode_air_date_values(self, medias):
        """Return ``_next_episode_air_date_value`` for each media, in order.

        A show's calendar events are only read when one of its seasons has no
        events of its own or its watch position runs past them. A first pass
        finds those shows without loading anything; the bulk events query then
        covers just them and only they are valued again.
        """
        probes = {}
        for media in medias:
            if getattr(getattr(media, "item", None), "media_type", None) == MediaTypes.TV.value:
                probes[id(media)] = media.prefetched_show_season_events = _ShowEventsProbe()
        values = [self._next_episode_air_date_value(media) for media in medias]
        needy = [media for media in medias if getattr(probes.get(id(media)), "used", False)]
        if needy:
            self.attach_show_season_events(needy)
            redo = {id(media): self._next_episode_air_date_value(media) for media in needy}
            values = [redo.get(id(media), value) for media, value in zip(medias, values, strict=True)]
        return values

    def attach_show_season_events(self, medias):
        """Load the season events `_next_episode_air_date_value` may need, in bulk.

        Without this, each show that has watched everything it has tracked so
        far costs its own events query (and a query per event for its item).
        """
        tv_medias = [
            media
            for media in medias
            if getattr(getattr(media, "item", None), "media_type", None)
            == MediaTypes.TV.value
        ]
        events_by_show = self._season_events_by_show(
            [(media.item.media_id, media.item.source) for media in tv_medias],
        )
        for media in tv_medias:
            media.prefetched_show_season_events = events_by_show.get(
                (media.item.media_id, media.item.source),
                [],
            )

    def attach_episode_queue(self, medias):
        """Attach next released/future episode metadata in bulk, without writes.

        Keep watch-order policy in TV.next_episode_target and release data in
        the existing calendar/catalogue. Untracked seasons need no tracker row.
        """
        from events.models import SentinelDatetime, is_released_datetime

        now = timezone.now()
        today = timezone.localdate(now)
        show_keys = {}
        seasons_by_show = {}
        for media in medias:
            seasons = {season.item.season_number: season for season in media.seasons.all()}
            seasons_by_show[id(media)] = seasons
            item = next(iter(seasons.values())).item if seasons else media.item
            key = (item.media_id, item.source, media.active_episode_order_id)
            show_keys[id(media)] = key
        calendar = self._season_events_by_show(
            [(key[0], key[1]) for key in show_keys.values()],
        )
        records = defaultdict(dict)
        for key in set(show_keys.values()):
            for event in calendar.get(key[:2], []):
                if not MIN_PLAUSIBLE_YEAR <= event.datetime.year < SentinelDatetime.YEAR:
                    continue
                coordinate = (event.item.season_number, event.content_number)
                records[key][coordinate] = {"air_date": event.datetime, "title": ""}
        # The catalogue is read by provider identity, in the form the episode
        # index serves, and as columns: one condition per show made SQLite
        # weigh every show against every episode row, and a model per episode
        # is the largest thing a ranked shelf would otherwise build.
        wanted = set(show_keys.values())
        media_ids = sorted({key[0] for key in wanted})
        for start in range(0, len(media_ids), 500):
            for media_id, source, order_id, season_number, episode_number, title, date in (
                Item.objects.filter(
                    media_id__in=media_ids[start : start + 500],
                    source__in={key[1] for key in wanted},
                    media_type=MediaTypes.EPISODE.value,
                    library_media_type=MediaTypes.EPISODE.value,
                    season_number__gt=0,
                    episode_number__isnull=False,
                ).values_list(
                    "media_id",
                    "source",
                    "episode_order_id",
                    "season_number",
                    "episode_number",
                    "title",
                    "release_datetime",
                )
            ):
                key = (media_id, source, order_id)
                if key not in wanted:
                    continue
                record = records[key].setdefault(
                    (season_number, episode_number),
                    {"air_date": None},
                )
                record["title"] = title
                if date and MIN_PLAUSIBLE_YEAR <= date.year < SentinelDatetime.YEAR:
                    record["air_date"] = min(filter(None, (record["air_date"], date)))
        for media in medias:
            key = show_keys[id(media)]
            seasons = seasons_by_show[id(media)]
            episodes = records[key]
            released_numbers = defaultdict(list)
            eligible = []
            last_dates = []
            for coordinate, record in episodes.items():
                date = record["air_date"]
                if is_released_datetime(date, now):
                    released_numbers[coordinate[0]].append(coordinate[1])
                    last_dates.append(date)
                season = seasons.get(coordinate[0])
                if season is None or (
                    season.status != Status.DROPPED.value
                    and coordinate[1] > season.progress
                ):
                    eligible.append(coordinate)
            target = media.next_episode_target(episode_numbers_by_season=released_numbers)
            released = [
                coordinate
                for coordinate in eligible
                if is_released_datetime(episodes[coordinate]["air_date"], now)
            ]
            # Preserve the model's paused/planning-season policy when it found
            # a tracked target; an earlier untracked season still takes priority.
            if target:
                coordinate = (target[0].item.season_number, target[1])
                if coordinate in released:
                    released = [coordinate] + [c for c in released if c[0] not in seasons]
            coordinate = min(released, default=None)
            if coordinate is None:
                coordinate = min(
                    (
                        c
                        for c in eligible
                        if episodes[c]["air_date"]
                        and not is_released_datetime(episodes[c]["air_date"], now)
                    ),
                    key=lambda c: (episodes[c]["air_date"], c),
                    default=None,
                )
            projection = {
                "released": bool(released),
                "days_until": None,
                "last_air_date": max(last_dates, default=None),
            }
            if coordinate:
                record = episodes[coordinate]
                projection.update(
                    source=key[1],
                    media_id=key[0],
                    episode_order_id=key[2],
                    season_number=coordinate[0],
                    episode_number=coordinate[1],
                    title=record.get("title", ""),
                    air_date=record["air_date"],
                )
                if not projection["released"]:
                    projection["days_until"] = (
                        timezone.localdate(record["air_date"]) - today
                    ).days
            media.next_episode_queue = projection

    def _next_episode_air_date_value(self, media):
        """Return the air datetime for the next episode in watch order."""
        item = getattr(media, "item", None)
        if item is None:
            return None

        def _is_usable_datetime(value):
            return value is not None and getattr(value, "year", 0) >= MIN_PLAUSIBLE_YEAR

        def _progress_index():
            progress_value = getattr(media, "aggregated_progress", None)
            if progress_value is None:
                progress_value = getattr(media, "progress", 0) or 0
            try:
                index = int(progress_value)
            except (TypeError, ValueError):
                index = 0
            return max(index, 0)

        def _as_list(related):
            if related is None:
                return []
            if hasattr(related, "all"):
                return list(related.all())
            return list(related)

        def _ordered_events_for_item(source_item):
            events = [
                event
                for event in _as_list(getattr(source_item, "prefetched_events", None))
                if getattr(event, "content_number", None) is not None
            ]
            events.sort(key=lambda event: event.content_number)
            return events

        def _ordered_episodes_for_related(related):
            episodes = [
                episode
                for episode in _as_list(getattr(related, "episodes", None))
                if getattr(getattr(episode, "item", None), "episode_number", None)
                is not None
            ]
            episodes.sort(
                key=lambda episode: (
                    getattr(getattr(episode, "item", None), "episode_number", 0) or 0
                ),
            )
            # Preserve the old watch-row sequence for next-air-date lookup,
            # but carry only the prefix containing the requested position.
            # Repeated watches of one Item have the same release datetime.
            prefix = []
            needed = progress_index + 1
            for episode in episodes:
                count = episode._card_total_count if getattr(episode, "_card_is_summary", False) else 1
                prefix.extend([episode] * min(count, needed - len(prefix)))
                if len(prefix) >= needed:
                    break
            return prefix

        show_events = getattr(media, "prefetched_show_season_events", None)

        def _season_events_for_show():
            nonlocal show_events
            if show_events is None:
                show_events = self._season_events_by_show(
                    [(item.media_id, item.source)],
                ).get((item.media_id, item.source), [])
            return show_events

        def _ordered_show_events(season_number):
            return sorted(
                (
                    event
                    for event in _as_list(_season_events_for_show())
                    if getattr(event, "content_number", None) is not None
                    and getattr(getattr(event, "item", None), "season_number", None)
                    == season_number
                ),
                key=lambda event: event.content_number,
            )

        media_type = getattr(item, "media_type", None)
        progress_index = _progress_index()

        if media_type == MediaTypes.TV.value:
            candidates = []
            seasons = [
                season
                for season in _as_list(getattr(media, "seasons", None))
                if getattr(getattr(season, "item", None), "season_number", None)
                not in (None, 0)
            ]
            seasons.sort(
                key=lambda season: (
                    getattr(getattr(season, "item", None), "season_number", 0) or 0
                ),
            )

            # A dropped season's episodes are not counted in the show's progress
            # (TV.progress), so its events must not be counted as candidates
            # either: the position would land on one of its old episodes.
            for season in seasons:
                if season.status == Status.DROPPED.value:
                    continue
                season_item = getattr(season, "item", None)
                season_events = (
                    _ordered_events_for_item(season_item) if season_item else []
                )
                if not season_events and season_item is not None:
                    # The library can hold the same season identity on more
                    # than one Item row (different library buckets), and the
                    # calendar writes events to whichever row it resolves.
                    # Read the show's events by season number so a tracked row
                    # without them still dates the show (#1531).
                    season_events = _ordered_show_events(
                        getattr(season_item, "season_number", None),
                    )
                if season_events:
                    candidates.extend(season_events)
                    continue

                candidates.extend(_ordered_episodes_for_related(season))

            if progress_index >= len(candidates):
                # Seasons not started yet have no Season row, so their calendar
                # events are invisible above — without them a show whose next
                # episode opens an untracked season gets no air date and sorts
                # into the no-date tail.
                tracked_season_numbers = {
                    season.item.season_number
                    for season in seasons
                    if getattr(season, "item", None) is not None
                }
                candidates.extend(
                    event
                    for event in _season_events_for_show()
                    if event.item.season_number not in tracked_season_numbers
                )

            if progress_index >= len(candidates):
                return None

            candidate = candidates[progress_index]
            air_date = getattr(candidate, "datetime", None)
            if air_date is None:
                air_date = getattr(
                    getattr(candidate, "item", None), "release_datetime", None
                )
            return air_date if _is_usable_datetime(air_date) else None

        if media_type == MediaTypes.SEASON.value:
            candidates = _ordered_events_for_item(item)
            if not candidates:
                candidates = _ordered_episodes_for_related(media)

            if progress_index >= len(candidates):
                return None

            candidate = candidates[progress_index]
            air_date = getattr(candidate, "datetime", None)
            if air_date is None:
                air_date = getattr(
                    getattr(candidate, "item", None), "release_datetime", None
                )
            return air_date if _is_usable_datetime(air_date) else None

        if media_type == MediaTypes.ANIME.value:
            candidates = _ordered_events_for_item(item)
            if not candidates:
                candidates = _ordered_episodes_for_related(media)

            if progress_index >= len(candidates):
                return None

            candidate = candidates[progress_index]
            air_date = getattr(candidate, "datetime", None)
            if air_date is None:
                air_date = getattr(
                    getattr(candidate, "item", None), "release_datetime", None
                )
            return air_date if _is_usable_datetime(air_date) else None

        return None

    def _sort_media_items_by_next_episode_air_date(self, media_items, direction):
        """Sort media items by next episode air date with missing dates last."""
        with_dates = []
        without_dates = []

        media_items = list(media_items)
        self.attach_show_season_events(media_items)
        for media in media_items:
            next_episode_air_date = self._next_episode_air_date_value(media)
            media.next_episode_air_date = next_episode_air_date
            if next_episode_air_date is not None:
                with_dates.append((media, next_episode_air_date))
            else:
                without_dates.append(media)

        if direction == "desc":
            with_dates.sort(
                key=lambda entry: (
                    entry[1],
                    getattr(getattr(entry[0], "item", None), "title", "").lower(),
                ),
                reverse=True,
            )
        else:
            with_dates.sort(
                key=lambda entry: (
                    entry[1],
                    getattr(getattr(entry[0], "item", None), "title", "").lower(),
                ),
            )

        without_dates.sort(
            key=lambda media: getattr(
                getattr(media, "item", None), "title", ""
            ).lower(),
        )
        return [media for media, _air_date in with_dates] + without_dates

    def _sort_tv_media_list(self, queryset, sort_filter, direction):
        """Sort TV media list based on the sort criteria."""
        if sort_filter == "start_date":
            # Annotate with the minimum start_date from related seasons/episodes
            queryset = queryset.annotate(
                calculated_start_date=models.Min(
                    "seasons__episodes__end_date",
                    filter=models.Q(seasons__item__season_number__gt=0),
                ),
            )
            order = (
                models.F("calculated_start_date").asc(nulls_last=True)
                if direction == "asc"
                else models.F("calculated_start_date").desc(nulls_last=True)
            )
            return queryset.order_by(order, models.functions.Lower("item__title"))

        if sort_filter == "end_date":
            # Annotate with the maximum end_date from related seasons/episodes
            queryset = queryset.annotate(
                calculated_end_date=models.Max(
                    "seasons__episodes__end_date",
                    filter=models.Q(seasons__item__season_number__gt=0),
                ),
            )
            order = (
                models.F("calculated_end_date").asc(nulls_last=True)
                if direction == "asc"
                else models.F("calculated_end_date").desc(nulls_last=True)
            )
            return queryset.order_by(order, models.functions.Lower("item__title"))

        if sort_filter == "next_episode_air_date":
            return self._sort_media_items_by_next_episode_air_date(
                list(queryset), direction
            )

        if sort_filter == "progress":
            # Annotate with the sum of episodes watched (excluding season 0)
            queryset = queryset.annotate(
                # Count episodes in regular seasons (season_number > 0)
                calculated_progress=models.Count(
                    "seasons__episodes",
                    filter=models.Q(seasons__item__season_number__gt=0),
                ),
            )
            order = (
                models.F("calculated_progress").asc(nulls_last=True)
                if direction == "asc"
                else models.F("calculated_progress").desc(nulls_last=True)
            )
            return queryset.order_by(order, models.functions.Lower("item__title"))

        if sort_filter == "time_left":
            # For time_left sorting, we need custom Python sorting
            # Return queryset as-is for custom sorting in views
            return queryset

        # Default to generic sorting
        return self._sort_generic_media_list(queryset, sort_filter, direction)

    def _sort_season_media_list(self, queryset, sort_filter, direction):
        """Sort Season media list based on the sort criteria."""
        if sort_filter == "start_date":
            # Annotate with the minimum end_date from related episodes
            queryset = queryset.annotate(
                calculated_start_date=models.Min("episodes__end_date"),
            )
            order = (
                models.F("calculated_start_date").asc(nulls_last=True)
                if direction == "asc"
                else models.F("calculated_start_date").desc(nulls_last=True)
            )
            return queryset.order_by(order, models.functions.Lower("item__title"))

        if sort_filter == "end_date":
            # Annotate with the maximum end_date from related episodes
            queryset = queryset.annotate(
                calculated_end_date=models.Max("episodes__end_date"),
            )
            order = (
                models.F("calculated_end_date").asc(nulls_last=True)
                if direction == "asc"
                else models.F("calculated_end_date").desc(nulls_last=True)
            )
            return queryset.order_by(order, models.functions.Lower("item__title"))

        if sort_filter == "next_episode_air_date":
            return self._sort_media_items_by_next_episode_air_date(
                list(queryset), direction
            )

        if sort_filter == "progress":
            # Annotate with the maximum episode number
            queryset = queryset.annotate(
                calculated_progress=models.Max("episodes__item__episode_number"),
            )
            order = (
                models.F("calculated_progress").asc(nulls_last=True)
                if direction == "asc"
                else models.F("calculated_progress").desc(nulls_last=True)
            )
            return queryset.order_by(order, models.functions.Lower("item__title"))

        # Default to generic sorting
        return self._sort_generic_media_list(queryset, sort_filter, direction)

    def _sort_generic_media_list(self, queryset, sort_filter, direction):
        """Apply generic sorting logic for all media types."""
        if sort_filter == "author":
            return self._sort_media_list_by_author(list(queryset), direction)

        if sort_filter == "next_episode_air_date":
            return self._sort_media_items_by_next_episode_air_date(
                list(queryset), direction
            )

        # Handle progress sorting specially to use aggregated progress
        if sort_filter in ("progress", "plays"):
            # Since we're now sorting after aggregation, we can use the aggregated_progress attribute
            # Convert to list for Python-based sorting since aggregated_progress is a Python attribute
            media_list = list(queryset)
            return sorted(
                media_list,
                key=lambda x: (
                    getattr(x, "aggregated_progress", x.progress),
                    x.item.title.lower(),
                ),
                reverse=(direction == "desc"),
            )

        if sort_filter == "last_watched":
            activity = Case(
                When(end_date__isnull=False, then=F("end_date")),
                When(
                    ~Q(status=Status.COMPLETED.value),
                    progressed_at__isnull=False,
                    then=F("progressed_at"),
                ),
                default=None,
                output_field=models.DateTimeField(),
            )
            order = (
                activity.asc(nulls_last=True)
                if direction == "asc"
                else activity.desc(nulls_last=True)
            )
            return queryset.order_by(order, models.functions.Lower("item__title"))

        # Handle sorting by date fields with special null handling
        if sort_filter in ("start_date", "end_date", "date_added"):
            sort_field = "created_at" if sort_filter == "date_added" else sort_filter
            order = (
                models.F(sort_field).asc(nulls_last=True)
                if direction == "asc"
                else models.F(sort_field).desc(nulls_last=True)
            )
            return queryset.order_by(order, models.functions.Lower("item__title"))

        if sort_filter == "release_date":
            order = (
                models.F("item__release_datetime").asc(nulls_last=True)
                if direction == "asc"
                else models.F("item__release_datetime").desc(nulls_last=True)
            )
            return queryset.order_by(order, models.functions.Lower("item__title"))

        if sort_filter == "popularity":
            order = (
                models.F("item__trakt_popularity_rank").asc(nulls_last=True)
                if direction == "asc"
                else models.F("item__trakt_popularity_rank").desc(nulls_last=True)
            )
            return queryset.order_by(order, models.functions.Lower("item__title"))

        if sort_filter == "critic_rating":
            order = (
                models.F("item__provider_rating").asc(nulls_last=True)
                if direction == "asc"
                else models.F("item__provider_rating").desc(nulls_last=True)
            )
            return queryset.order_by(order, models.functions.Lower("item__title"))

        # Handle sorting by Item fields
        item_fields = [f.name for f in Item._meta.fields]
        if sort_filter in item_fields:
            if sort_filter == "title":
                # Case-insensitive title sorting
                expr = models.functions.Lower("item__title")
                order = expr.asc() if direction == "asc" else expr.desc()
                return queryset.order_by(order)
            # Default sorting for other Item fields
            order = (
                models.F(f"item__{sort_filter}").asc(nulls_last=True)
                if direction == "asc"
                else models.F(f"item__{sort_filter}").desc(nulls_last=True)
            )
            return queryset.order_by(order, models.functions.Lower("item__title"))

        # Default sorting by media field
        order = (
            models.F(sort_filter).asc(nulls_last=True)
            if direction == "asc"
            else models.F(sort_filter).desc(nulls_last=True)
        )
        return queryset.order_by(order, models.functions.Lower("item__title"))

    def _sort_media_list_by_author(self, media_list, direction):
        """Sort media items by their first persisted author, keeping missing authors last."""

        def _primary_author(media):
            authors = getattr(getattr(media, "item", None), "authors", None) or []
            if not isinstance(authors, list):
                authors = [authors]
            for author in authors:
                author_text = str(author).strip()
                if author_text:
                    return author_text
            return ""

        with_author = []
        without_author = []
        for media in media_list:
            if _primary_author(media):
                with_author.append(media)
            else:
                without_author.append(media)

        with_author.sort(
            key=lambda media: (
                _primary_author(media).lower(),
                getattr(getattr(media, "item", None), "title", "").lower(),
            ),
            reverse=direction == "desc",
        )
        without_author.sort(
            key=lambda media: getattr(
                getattr(media, "item", None), "title", ""
            ).lower(),
        )
        return with_author + without_author

    def get_in_progress(self, user, sort_by, items_limit, specific_media_type=None):
        """Get a media list of in progress media by type."""
        list_by_type = {}
        media_types = self._get_media_types_to_process(user, specific_media_type)

        # Get user preference for planned items display mode
        planned_mode = getattr(
            user,
            "show_planned_on_home",
            users.models.PlannedHomeDisplayChoices.DISABLED,
        )

        def filter_by_latest_status(media_list, desired_status):
            """Filter media entries by their most recent status across duplicates."""
            if not media_list:
                return media_list
            filtered = []
            for media in media_list:
                latest_status = getattr(media, "aggregated_status", None) or getattr(
                    media, "status", None
                )
                if latest_status == desired_status:
                    filtered.append(media)
            return filtered

        for media_type in media_types:
            base_media_type = media_type

            # Get base media list for in-progress media
            in_progress_list = self.get_media_list(
                user=user,
                media_type=base_media_type,
                status_filter=Status.IN_PROGRESS.value,
                sort_filter=None,
            )
            in_progress_list = list(in_progress_list)
            in_progress_list = filter_by_latest_status(
                in_progress_list, Status.IN_PROGRESS.value
            )

            # Get planned items if needed
            planned_list = []
            if planned_mode != users.models.PlannedHomeDisplayChoices.DISABLED:
                planned_queryset = self.get_media_list(
                    user=user,
                    media_type=base_media_type,
                    status_filter=Status.PLANNING.value,
                    sort_filter=None,
                )
                planned_list = filter_by_latest_status(
                    list(planned_queryset), Status.PLANNING.value
                )

            # Handle different modes
            if planned_mode == users.models.PlannedHomeDisplayChoices.DISABLED:
                # Only in-progress items
                media_list = in_progress_list
                if not media_list:
                    continue

                # Process in-progress items
                self.annotate_max_progress(media_list, base_media_type)
                self._annotate_next_event(media_list)

                if base_media_type == MediaTypes.SEASON.value:
                    self._fix_missing_season_images(media_list)

                sorted_list = self._sort_in_progress_media(media_list, sort_by)
                total_count = len(sorted_list)
                if specific_media_type:
                    paginated_list = sorted_list[items_limit:]
                else:
                    paginated_list = sorted_list[:items_limit]

                list_by_type[base_media_type] = {
                    "items": paginated_list,
                    "total": total_count,
                }

            elif planned_mode == users.models.PlannedHomeDisplayChoices.COMBINED:
                # Combine in-progress and planned items
                media_list = list(in_progress_list)
                existing_item_ids = {media.item.id for media in media_list}
                for planned_media in planned_list:
                    if planned_media.item.id not in existing_item_ids:
                        media_list.append(planned_media)
                        existing_item_ids.add(planned_media.item.id)

                if not media_list:
                    continue

                # Process combined items
                self.annotate_max_progress(media_list, base_media_type)
                self._annotate_next_event(media_list)

                if base_media_type == MediaTypes.SEASON.value:
                    self._fix_missing_season_images(media_list)

                sorted_list = self._sort_in_progress_media(media_list, sort_by)
                total_count = len(sorted_list)
                if specific_media_type:
                    paginated_list = sorted_list[items_limit:]
                else:
                    paginated_list = sorted_list[:items_limit]

                list_by_type[base_media_type] = {
                    "items": paginated_list,
                    "total": total_count,
                }

            elif planned_mode == users.models.PlannedHomeDisplayChoices.SEPARATED:
                # Separated mode: two distinct sections
                # Determine which sections to process based on specific_media_type request
                process_in_progress = True
                process_planned = True

                if specific_media_type:
                    if specific_media_type.endswith("_in_progress"):
                        process_planned = False
                    elif specific_media_type.endswith("_planned"):
                        process_in_progress = False

                # Handle in-progress section
                if process_in_progress and in_progress_list:
                    in_progress_processed = list(in_progress_list)
                    self.annotate_max_progress(in_progress_processed, base_media_type)
                    self._annotate_next_event(in_progress_processed)

                    if base_media_type == MediaTypes.SEASON.value:
                        self._fix_missing_season_images(in_progress_processed)

                    sorted_in_progress = self._sort_in_progress_media(
                        in_progress_processed, sort_by
                    )
                    total_in_progress = len(sorted_in_progress)

                    if specific_media_type and specific_media_type.endswith(
                        "_in_progress"
                    ):
                        paginated_in_progress = sorted_in_progress[items_limit:]
                    else:
                        paginated_in_progress = sorted_in_progress[:items_limit]

                    list_by_type[f"{base_media_type}_in_progress"] = {
                        "items": paginated_in_progress,
                        "total": total_in_progress,
                        "section_label": "In Progress",
                        "media_type": base_media_type,
                    }

                # Handle planned section
                if process_planned and planned_list:
                    planned_processed = list(planned_list)
                    self.annotate_max_progress(planned_processed, base_media_type)
                    self._annotate_next_event(planned_processed)

                    if base_media_type == MediaTypes.SEASON.value:
                        self._fix_missing_season_images(planned_processed)

                    sorted_planned = self._sort_in_progress_media(
                        planned_processed, sort_by
                    )
                    total_planned = len(sorted_planned)

                    if specific_media_type and specific_media_type.endswith("_planned"):
                        paginated_planned = sorted_planned[items_limit:]
                    else:
                        paginated_planned = sorted_planned[:items_limit]

                    list_by_type[f"{base_media_type}_planned"] = {
                        "items": paginated_planned,
                        "total": total_planned,
                        "section_label": "Planned",
                        "media_type": base_media_type,
                    }

        return list_by_type

    def get_recently_unrated(self, user, days=7):
        """Return recently played media items without a user score."""
        cutoff = timezone.now() - timedelta(days=days)
        recent_items = []
        media_types = self._get_media_types_to_process(user, None)

        def resolve_last_played(media):
            if media.item.media_type == MediaTypes.SEASON.value:
                return (
                    getattr(media, "last_watched", None)
                    or media.progressed_at
                    or media.created_at
                )
            return media.end_date or media.progressed_at or media.created_at

        for media_type in media_types:
            model = apps.get_model(app_label="app", model_name=media_type)

            rated_item_ids = model.objects.filter(
                user=user.id,
                score__isnull=False,
            ).values("item_id")

            queryset = model.objects.filter(
                user=user.id,
                score__isnull=True,
                status=Status.COMPLETED.value,
            ).exclude(item_id__in=rated_item_ids)

            if media_type == MediaTypes.SEASON.value:
                queryset = queryset.filter(
                    episodes__end_date__gte=cutoff,
                ).annotate(
                    last_watched=Max("episodes__end_date"),
                )
                order_by_fields = [
                    F("last_watched").desc(nulls_last=True),
                    F("created_at").desc(),
                ]
            else:
                queryset = queryset.filter(
                    end_date__gte=cutoff,
                )
                order_by_fields = [
                    F("progressed_at").desc(nulls_last=True),
                    F("end_date").desc(nulls_last=True),
                    F("created_at").desc(),
                ]

            select_related_fields = ["item"]
            if media_type == MediaTypes.PODCAST.value:
                select_related_fields.append("show")
                select_related_fields.append("episode")
            elif media_type == MediaTypes.MUSIC.value:
                select_related_fields.extend(
                    ["album", "album__artist", "artist", "track"]
                )

            queryset = (
                queryset.annotate(
                    repeats=Window(
                        expression=Count("id"),
                        partition_by=[F("item")],
                    ),
                    row_number=Window(
                        expression=RowNumber(),
                        partition_by=[F("item")],
                        order_by=order_by_fields,
                    ),
                )
                .filter(row_number=1)
                .select_related(*select_related_fields)
            )

            queryset = self._apply_prefetch_related(queryset, media_type)
            items = list(queryset)
            for media in items:
                media.last_played_at = resolve_last_played(media)
                media.use_podcast_show = (
                    media.item.media_type == MediaTypes.PODCAST.value
                    and getattr(media, "show", None)
                )
            recent_items.extend(items)

        return sorted(
            recent_items,
            key=lambda media: media.last_played_at or media.created_at,
            reverse=True,
        )

    def _get_media_types_to_process(self, user, specific_media_type):
        """Determine which media types to process based on user settings."""
        if specific_media_type:
            # Extract base media_type if it has a suffix (e.g., "movie_in_progress" -> "movie")
            if "_" in specific_media_type:
                base_type = specific_media_type.rsplit("_", 1)[0]
                return [base_type]
            return [specific_media_type]

        media_types = [
            media_type
            for media_type in user.get_active_media_types()
            if media_type != MediaTypes.TV.value
        ]

        # Home should continue to include TV seasons when TV shows are enabled.
        if (
            getattr(user, "tv_enabled", False)
            and MediaTypes.SEASON.value not in media_types
        ):
            media_types.insert(0, MediaTypes.SEASON.value)

        return media_types

    def _annotate_next_event(self, media_list):
        """Annotate next_event for media items."""
        from events.models import is_released_datetime

        current_time = timezone.now()

        for media in media_list:
            # Get future events sorted by datetime
            future_events = sorted(
                [
                    event
                    for event in getattr(media.item, "prefetched_events", [])
                    # Digital and physical dates are per region, so only the
                    # main release counts as a card's next event.
                    if not is_released_datetime(event.datetime, current_time)
                    and not getattr(event, "release_type", "")
                ],
                key=lambda e: e.datetime,
            )

            media.next_event = future_events[0] if future_events else None

    def _fix_missing_season_images(self, season_list):
        """Annotate season cards with a display fallback without persisting it."""
        from app import helpers

        for season in season_list:
            item = getattr(season, "item", None)
            if item is None:
                continue

            show_item = getattr(getattr(season, "related_tv", None), "item", None)
            if show_item is None:
                show_item = (
                    Item.objects.filter(
                        media_id=item.media_id,
                        source=item.source,
                        media_type=MediaTypes.TV.value,
                    )
                    .only("image")
                    .first()
                )
            show_image = getattr(show_item, "image", None)
            primary_image = item.image

            # Older rows may have the TV show's poster copied onto the season item.
            # Keep using it visually, but mark it as a fallback rather than real
            # season art when it matches the tracked show poster exactly.
            if (
                helpers.has_real_image(primary_image)
                and helpers.has_real_image(show_image)
                and primary_image == show_image
            ):
                primary_image = None

            image, image_source = helpers.resolve_image_with_fallback(
                primary_image,
                show_image,
            )
            season.card_image_override = image
            season.card_image_source = image_source

    def _fix_missing_music_images(self, music_list):
        """Annotate music track cards with an album-cover fallback without persisting it."""
        from app import helpers

        for music in music_list:
            item = getattr(music, "item", None)
            if item is None:
                continue

            album = getattr(music, "album", None)
            album_image = getattr(album, "image", None)

            image, image_source = helpers.resolve_image_with_fallback(
                item.image,
                album_image,
            )
            music.card_image_override = image
            music.card_image_source = image_source

    def _sort_in_progress_media(self, media_list, sort_by):
        """Sort in-progress media based on the sort criteria."""
        # Define primary sort functions based on sort_by
        primary_sort_functions = {
            users.models.HomeSortChoices.UPCOMING: lambda x: (
                x.next_event is None,
                x.next_event.datetime if x.next_event else None,
            ),
            users.models.HomeSortChoices.RECENT: lambda x: (
                x.progressed_at is None and not x.progress,
                -timezone.datetime.timestamp(
                    x.progressed_at if x.progressed_at is not None else x.created_at,
                )
                if (x.progressed_at is not None or x.progress)
                else 0,
            ),
            users.models.HomeSortChoices.COMPLETION: lambda x: (
                x.max_progress is None,
                -(
                    getattr(x, "progress_percentage", None)
                    if getattr(x, "progress_percentage", None) is not None
                    else (
                        x.progress / x.max_progress * 100
                        if x.max_progress and x.max_progress > 0
                        else 0
                    )
                ),
            ),
            users.models.HomeSortChoices.EPISODES_LEFT: lambda x: (
                x.max_progress is None,
                (x.max_progress - x.progress if x.max_progress else 0),
            ),
            users.models.HomeSortChoices.TITLE: lambda x: x.item.title.lower(),
        }

        primary_sort_function = primary_sort_functions[sort_by]

        return sorted(
            media_list,
            key=lambda x: (
                primary_sort_function(x),
                -timezone.datetime.timestamp(
                    x.progressed_at if x.progressed_at is not None else x.created_at,
                ),
                x.item.title.lower(),
            ),
        )

    def annotate_max_progress(self, media_list, media_type):
        """Annotate max_progress for all media items."""
        current_datetime = timezone.now()

        if media_type in (MediaTypes.MOVIE.value, MediaTypes.COMIC_ISSUE.value):
            for media in media_list:
                media.max_progress = 1
            return

        if media_type == MediaTypes.TV.value:
            self._annotate_tv_released_episodes(media_list, current_datetime)
            return

        if media_type == MediaTypes.ANIME.value:
            grouped_media = [
                media
                for media in media_list
                if getattr(getattr(media, "item", None), "media_type", None)
                == MediaTypes.TV.value
            ]
            flat_media = [media for media in media_list if media not in grouped_media]
            if grouped_media:
                self._annotate_tv_released_episodes(grouped_media, current_datetime)
            if not flat_media:
                return
            media_list = flat_media

        if media_type == MediaTypes.SEASON.value:
            # For seasons, use metadata max_progress instead of database annotation
            # The metadata value is more accurate as it reflects the actual total episodes
            # from the provider, not just episodes with release_datetime set
            self._annotate_season_metadata_max_progress(media_list, current_datetime)
            return

        if media_type == MediaTypes.BOOK.value:
            for media in media_list:
                media.max_progress = media.item.book_max_progress
            return

        if media_type in {
            MediaTypes.ANIME.value,
            MediaTypes.MANGA.value,
            MediaTypes.COMIC.value,
        }:
            from app import custom_metadata

            manual_item_ids = set()
            for media in media_list:
                if media.item.source != Sources.MANUAL.value:
                    continue
                details = custom_metadata.build_manual_detail_payload(media.item)
                manual_value = custom_metadata.manual_max_progress(
                    media.item,
                    details,
                    fallback_max_progress=getattr(media, "max_progress", None),
                )
                # Items without a manual max_progress fall through to the
                # events-derived annotation below.
                if manual_value is None:
                    continue
                media.max_progress = manual_value
                manual_item_ids.add(media.item.id)
            if all(media.item.id in manual_item_ids for media in media_list):
                return
        else:
            manual_item_ids = set()

        # For other media types, calculate max_progress from events
        # Create a dictionary mapping item_id to max content_number
        max_progress_dict = {}

        item_ids = [media.item.id for media in media_list]

        from events.models import released_q

        # Fetch all relevant events in a single query
        events_data = events.models.Event.objects.filter(
            item_id__in=item_ids,
        ).filter(released_q("datetime", current_datetime)).values(
            "item_id",
            "content_number",
        )

        # Process events to find max content number per item
        for event in events_data:
            item_id = event["item_id"]
            content_number = event["content_number"]
            if content_number is not None:
                current_max = max_progress_dict.get(item_id, 0)
                max_progress_dict[item_id] = max(current_max, content_number)

        for media in media_list:
            if media.item.id in manual_item_ids:
                continue
            media.max_progress = max_progress_dict.get(media.item.id)
            # AniList returns no airing schedule for many finished series, and a
            # new entry has no events until its calendar task runs, so a finished
            # anime falls back to the provider's episode count (#1254).
            if (
                media.max_progress is None
                and media_type == MediaTypes.ANIME.value
                and media.item.status == "Finished"
            ):
                media.max_progress = media.item.provider_episode_count

    def annotate_episode_progress(self, media_list, media_type=None):
        """Annotate released and provider-total episode counts in bulk.

        The API needs both the released count used by ``episodes_left`` and the
        provider's full count.  Season max-progress historically performs a
        provider lookup per row, which is appropriate for some web sorting
        paths but not for an API page.  This method deliberately uses the
        local release/event queries for seasons and the persisted provider
        count for the full denominator.
        """
        media_list = list(media_list)
        media_by_type = defaultdict(list)
        for media in media_list:
            item = getattr(media, "item", None)
            item_type = getattr(item, "media_type", None)
            if item_type in {
                MediaTypes.TV.value,
                MediaTypes.SEASON.value,
                MediaTypes.ANIME.value,
            }:
                if item_type == MediaTypes.SEASON.value:
                    route_type = MediaTypes.SEASON.value
                elif media_type == MediaTypes.ANIME.value:
                    route_type = MediaTypes.ANIME.value
                else:
                    route_type = media_type or item_type
                if route_type == MediaTypes.ANIME.value:
                    media_by_type[MediaTypes.ANIME.value].append(media)
                else:
                    media_by_type[item_type].append(media)

        current_datetime = timezone.now()
        for route_type, typed_media in media_by_type.items():
            if route_type == MediaTypes.SEASON.value:
                self._annotate_season_released_episodes(
                    typed_media,
                    current_datetime,
                )
            else:
                self.annotate_max_progress(typed_media, route_type)
            self._annotate_total_episode_count(typed_media, route_type)

        return media_list

    def _annotate_total_episode_count(self, media_list, media_type):
        """Annotate provider totals, excluding dropped TV seasons."""
        for media in media_list:
            item = getattr(media, "item", None)
            total = getattr(item, "provider_episode_count", None)

            if media_type == MediaTypes.TV.value or (
                media_type == MediaTypes.ANIME.value
                and getattr(item, "media_type", None) == MediaTypes.TV.value
            ):
                seasons = list(media.seasons.all())
                main_seasons = [
                    season
                    for season in seasons
                    if getattr(season.item, "season_number", None) not in (None, 0)
                ]
                dropped_seasons = [
                    season
                    for season in main_seasons
                    if season.status == Status.DROPPED.value
                ]
                if dropped_seasons:
                    active_counts = [
                        getattr(season.item, "provider_episode_count", None)
                        for season in main_seasons
                        if season.status != Status.DROPPED.value
                    ]
                    total = (
                        sum(active_counts)
                        if all(count is not None for count in active_counts)
                        else None
                    )
                elif total is None:
                    season_counts = [
                        getattr(season.item, "provider_episode_count", None)
                        for season in main_seasons
                    ]
                    if season_counts and all(count is not None for count in season_counts):
                        total = sum(season_counts)

            media.total_episode_count = total

    def _annotate_tv_released_episodes(self, tv_list, current_datetime):
        """Annotate TV shows with the number of released episodes."""
        tv_model = apps.get_model("app", "TV")
        stray_rows = [tv for tv in tv_list if not isinstance(tv, tv_model)]
        if stray_rows:
            # Only a show tracker has seasons to count. A season (or other)
            # row on a TV page means its item is mis-typed in the library;
            # leave the row unannotated and name it, instead of failing the
            # whole page.
            for row in stray_rows:
                logger.warning(
                    "TV list holds a non-show row: model=%s pk=%s item_id=%s "
                    "item_media_type=%s media_id=%s source=%s",
                    type(row).__name__,
                    row.pk,
                    getattr(row, "item_id", None),
                    getattr(getattr(row, "item", None), "media_type", None),
                    getattr(getattr(row, "item", None), "media_id", None),
                    getattr(getattr(row, "item", None), "source", None),
                )
                row.max_progress = None
            tv_list = [tv for tv in tv_list if isinstance(tv, tv_model)]
        if not tv_list:
            return

        released_by_show = self.released_episode_breakdowns(
            {(tv.item.media_id, tv.item.source) for tv in tv_list},
            current_datetime,
        )

        for tv in tv_list:
            breakdown = released_by_show.get((tv.item.media_id, tv.item.source), {})
            tv.released_episode_breakdown = breakdown
            dropped_season_numbers = (
                {
                    season.item.season_number
                    for season in tv.seasons.all()
                    if season.status == Status.DROPPED.value
                    and season.item.season_number != 0
                }
                if breakdown and tv.pk is not None
                else set()
            )
            tv.max_progress = self.released_max_progress(
                tv.item,
                breakdown,
                dropped_season_numbers,
            )

    def released_episode_breakdowns(self, media_keys, current_datetime=None):
        """Return ``{(media_id, source): {season_number: released_count}}``.

        Two grouped queries for all the shows, whatever their number. Specials
        (season 0) are not counted. A show with nothing released is absent.
        """
        current_datetime = current_datetime or timezone.now()
        media_ids = {media_id for media_id, _ in media_keys}
        media_sources = {source for _, source in media_keys}

        released_by_show: dict[tuple[str, str], dict[int, int]] = defaultdict(dict)

        from events.models import released_q

        # The calendar event is the authoritative air time for an episode.
        # Item.release_datetime is a fallback for episodes the calendar has
        # not dated, and it may still be a date-only value from another write
        # path; letting it participate here is what released an episode on its
        # listed day even when a precise event said it airs hours later.
        existing_event = events.models.Event.objects.filter(
            item__media_id=OuterRef("media_id"),
            item__source=OuterRef("source"),
            item__media_type=MediaTypes.SEASON.value,
            item__season_number=OuterRef("season_number"),
            content_number=OuterRef("episode_number"),
        )

        episode_rows = (
            Item.objects.filter(
                media_type=MediaTypes.EPISODE.value,
                media_id__in=media_ids,
                source__in=media_sources,
                release_datetime__isnull=False,
                season_number__gt=0,
            )
            .exclude(Exists(existing_event))
            .filter(released_q("release_datetime", current_datetime))
            .values("media_id", "source", "season_number")
            .annotate(released_count=models.Count("episode_number", distinct=True))
        )

        for row in episode_rows:
            key = (row["media_id"], row["source"])
            season_number = row["season_number"]
            released_count = row["released_count"] or 0
            released_by_show[key][season_number] = (
                released_by_show[key].get(season_number, 0) + released_count
            )

        released_events = (
            events.models.Event.objects.filter(
                item__media_id__in=media_ids,
                item__source__in=media_sources,
                item__media_type=MediaTypes.SEASON.value,
                item__season_number__gt=0,
                content_number__isnull=False,
            )
            .filter(released_q("datetime", current_datetime))
            .exclude(datetime__year__lt=1900)
            .values(
                "item__media_id",
                "item__source",
                "item__season_number",
            )
            .annotate(released_count=models.Count("content_number", distinct=True))
        )

        for row in released_events:
            key = (row["item__media_id"], row["item__source"])
            season_number = row["item__season_number"]
            released_count = row["released_count"] or 0
            released_by_show[key][season_number] = (
                released_by_show[key].get(season_number, 0) + released_count
            )

        return released_by_show

    def released_max_progress(self, item, breakdown, dropped_season_numbers):
        """Return a show's released episode count outside its dropped seasons.

        ``None`` when nothing is released, except for a manual show, which
        states its own episode count.
        """
        if breakdown:
            effective_count = sum(
                count
                for season_num, count in breakdown.items()
                if season_num not in dropped_season_numbers
            )
            return effective_count or None
        if item.source == Sources.MANUAL.value:
            from app import custom_metadata

            details = custom_metadata.build_manual_detail_payload(item)
            return custom_metadata.manual_max_progress(
                item,
                details,
                fallback_max_progress=None,
            )
        return None

    def _annotate_season_metadata_max_progress(self, season_list, current_datetime):
        """Annotate seasons with the provider's episode count.

        The provider count is more accurate than the database annotation: it
        reflects every episode, not only those with a release date stored. A
        list renders many seasons of the same show, so provider-backed seasons
        are read with one bundle call per show rather than one per season.
        A show whose lookup failed is not retried for a while, so a slow or
        unreachable provider costs one attempt, not one per season per render.
        """
        from django.core.cache import cache

        from app.providers import services

        batched_sources = {Sources.TMDB.value, Sources.TVDB.value}
        seasons_by_show = defaultdict(list)
        fallback = []

        def annotate_from_season_lookup(season):
            item = season.item
            try:
                season_metadata = services.get_media_metadata(
                    MediaTypes.SEASON.value,
                    item.media_id,
                    item.source,
                    [item.season_number],
                )
            except Exception:
                fallback.append(season)
                return
            metadata_max_progress = season_metadata.get("max_progress")
            if metadata_max_progress is None:
                fallback.append(season)
            else:
                season.max_progress = metadata_max_progress

        for season in season_list:
            item = season.item
            if item.source in batched_sources and item.season_number is not None:
                seasons_by_show[(item.source, item.media_id)].append(season)
            else:
                annotate_from_season_lookup(season)

        for (source, media_id), seasons in seasons_by_show.items():
            failed_key = f"season_max_progress_failed:{source}:{media_id}"
            if cache.get(failed_key):
                fallback.extend(seasons)
                continue
            season_numbers = sorted({season.item.season_number for season in seasons})
            try:
                bundle = services.get_media_metadata(
                    "tv_with_seasons",
                    media_id,
                    source,
                    season_numbers,
                )
            except Exception:
                cache.set(failed_key, True, SEASON_MAX_PROGRESS_RETRY_SECONDS)
                fallback.extend(seasons)
                continue
            for season in seasons:
                season_data = bundle.get(f"season/{season.item.season_number}")
                metadata_max_progress = (
                    season_data.get("max_progress")
                    if isinstance(season_data, dict)
                    else None
                )
                if metadata_max_progress is None:
                    # Not answered by the bundle: ask for the season on its
                    # own, as before, so the result never differs from a
                    # direct read.
                    annotate_from_season_lookup(season)
                else:
                    season.max_progress = metadata_max_progress

        self._annotate_season_released_episodes(fallback, current_datetime)

    def _annotate_season_released_episodes(self, season_list, current_datetime):
        """Annotate seasons with the number of released episodes."""
        if not season_list:
            return

        season_keys = {
            (season.item.media_id, season.item.source, season.item.season_number)
            for season in season_list
        }
        media_ids = {media_id for media_id, _, _ in season_keys}
        media_sources = {source for _, source, _ in season_keys}
        season_numbers = {
            season_number
            for _, _, season_number in season_keys
            if season_number is not None
        }

        released_by_season: dict[tuple[str, str, int], int] = {}

        from events.models import released_q

        # Events win over item fallbacks; see _annotate_tv_released_episodes.
        existing_event = events.models.Event.objects.filter(
            item__media_id=OuterRef("media_id"),
            item__source=OuterRef("source"),
            item__media_type=MediaTypes.SEASON.value,
            item__season_number=OuterRef("season_number"),
            content_number=OuterRef("episode_number"),
        )

        episode_rows = (
            Item.objects.filter(
                media_type=MediaTypes.EPISODE.value,
                media_id__in=media_ids,
                source__in=media_sources,
                season_number__in=season_numbers,
                release_datetime__isnull=False,
            )
            .exclude(Exists(existing_event))
            .filter(released_q("release_datetime", current_datetime))
            .values("media_id", "source", "season_number")
            .annotate(released_count=models.Count("episode_number", distinct=True))
        )

        for row in episode_rows:
            key = (row["media_id"], row["source"], row["season_number"])
            released_count = row["released_count"] or 0
            released_by_season[key] = released_by_season.get(key, 0) + released_count

        released_events = (
            events.models.Event.objects.filter(
                item__media_id__in=media_ids,
                item__source__in=media_sources,
                item__media_type=MediaTypes.SEASON.value,
                item__season_number__in=season_numbers,
                content_number__isnull=False,
            )
            .filter(released_q("datetime", current_datetime))
            .exclude(datetime__year__lt=1900)
            .values(
                "item__media_id",
                "item__source",
                "item__season_number",
            )
            .annotate(released_count=models.Count("content_number", distinct=True))
        )

        for row in released_events:
            key = (
                row["item__media_id"],
                row["item__source"],
                row["item__season_number"],
            )
            released_count = row["released_count"] or 0
            released_by_season[key] = released_by_season.get(key, 0) + released_count

        for season in season_list:
            key = (season.item.media_id, season.item.source, season.item.season_number)
            season.max_progress = released_by_season.get(key)
            if (
                season.max_progress is None
                and season.item.source == Sources.MANUAL.value
            ):
                from app import custom_metadata

                details = custom_metadata.build_manual_detail_payload(season.item)
                season.max_progress = custom_metadata.manual_max_progress(
                    season.item,
                    details,
                    fallback_max_progress=None,
                )

    def fetch_media_for_items(self, media_types, item_ids, user, status_filter=None):
        """Fetch media objects for given items, optionally filtering by status.

        Args:
            media_types: Iterable of media type strings to query
            item_ids: QuerySet or list of item IDs to fetch media for
            user: User to filter media by
            status_filter: Optional iterable of status values to filter by

        Returns:
            dict mapping item_id to media object
        """
        media_by_item_id = {}

        for media_type in media_types:
            model = apps.get_model("app", media_type)

            if media_type == MediaTypes.EPISODE.value:
                filter_kwargs = {
                    "item__in": item_ids,
                    "related_season__user": user,
                }
                if status_filter:
                    filter_kwargs["related_season__status__in"] = status_filter
            else:
                filter_kwargs = {
                    "item__in": item_ids,
                    "user": user,
                }
                if status_filter:
                    filter_kwargs["status__in"] = status_filter

            queryset = model.objects.filter(**filter_kwargs).select_related("item")
            queryset = self._apply_prefetch_related(queryset, media_type)
            self.annotate_max_progress(queryset, media_type)

            for entry in queryset:
                media_by_item_id.setdefault(entry.item_id, entry)

        return media_by_item_id

    def get_media(
        self,
        user,
        media_type,
        instance_id,
    ):
        """Get user media object given the media type and item."""
        if media_type == MediaTypes.ANIME.value:
            Anime = apps.get_model("app", "Anime")
            TV = apps.get_model("app", "TV")
            try:
                return Anime.objects.get(id=instance_id, user=user)
            except Anime.DoesNotExist:
                return TV.objects.get(
                    id=instance_id,
                    user=user,
                    item__library_media_type=MediaTypes.ANIME.value,
                )

        model = apps.get_model(app_label="app", model_name=media_type)
        params = self._get_media_params(
            user,
            media_type,
            instance_id,
        )

        return model.objects.get(**params)

    def get_media_prefetch(
        self,
        user,
        media_type,
        instance_id,
    ):
        """Get user media object with prefetch_related applied."""
        if media_type == MediaTypes.ANIME.value:
            Anime = apps.get_model("app", "Anime")
            TV = apps.get_model("app", "TV")
            anime_queryset = Anime.objects.filter(id=instance_id, user=user)
            if anime_queryset.exists():
                anime_queryset = self._apply_prefetch_related(
                    anime_queryset,
                    media_type,
                )
                self.annotate_max_progress(anime_queryset, media_type)
                return anime_queryset[0]

            tv_queryset = TV.objects.filter(
                id=instance_id,
                user=user,
                item__library_media_type=MediaTypes.ANIME.value,
            )
            tv_queryset = self._apply_prefetch_related(tv_queryset, media_type)
            self.annotate_max_progress(tv_queryset, media_type)
            return tv_queryset[0]

        model = apps.get_model(app_label="app", model_name=media_type)
        params = self._get_media_params(
            user,
            media_type,
            instance_id,
        )

        queryset = model.objects.filter(**params)

        queryset = self._apply_prefetch_related(queryset, media_type)
        self.annotate_max_progress(queryset, media_type)

        return queryset[0]

    def _get_media_params(
        self,
        user,
        media_type,
        instance_id,
    ):
        """Get the common filter parameters for media queries."""
        params = {"id": instance_id}

        if media_type == MediaTypes.EPISODE.value:
            params["related_season__user"] = user
        else:
            params["user"] = user

        return params

    def filter_media(
        self,
        user,
        media_id,
        media_type,
        source,
        season_number=None,
        episode_number=None,
        library_media_type=None,
    ):
        """Filter media objects based on parameters."""
        if media_type == MediaTypes.ANIME.value and source in {
            Sources.TMDB.value,
            Sources.TVDB.value,
        }:
            model = apps.get_model("app", "TV")
        else:
            model = apps.get_model(app_label="app", model_name=media_type)
        params = self._filter_media_params(
            media_type,
            media_id,
            source,
            user,
            season_number,
            episode_number,
        )

        # A user-scoped TV/season lookup is item-driven. Without SQLite
        # statistics the planner walks every row of the user's TV/seasons by
        # user_id and probes each item, which is thousands of random reads on
        # a large database; resolving the (indexed) item ids first lets it
        # seek by item instead.
        if "user" in params and media_type in (
            MediaTypes.TV.value,
            MediaTypes.SEASON.value,
            MediaTypes.ANIME.value,
        ):
            item_params = {
                key.removeprefix("item__"): value
                for key, value in params.items()
                if key.startswith("item__")
            }
            other_params = {
                key: value
                for key, value in params.items()
                if not key.startswith("item__")
            }
            item_queryset = apps.get_model("app", "Item").objects.filter(**item_params)
            if media_type != MediaTypes.ANIME.value:
                if library_media_type:
                    item_queryset = item_queryset.filter(
                        library_media_type=library_media_type,
                    )
                else:
                    item_queryset = item_queryset.exclude(
                        library_media_type=MediaTypes.ANIME.value,
                    )
            return model.objects.filter(
                item_id__in=list(item_queryset.values_list("id", flat=True)),
                **other_params,
            )

        queryset = model.objects.filter(**params)

        # FORK: bucket-aware scoping, mirrors api.helpers.resolve_item_queryset —
        # Item rows are bucketed by library_media_type (e.g. grouped-anime rows
        # share a season/episode number with a plain-TV row); default to the
        # non-anime bucket unless a bucket is explicitly requested.
        if media_type in (
            MediaTypes.TV.value,
            MediaTypes.SEASON.value,
            MediaTypes.EPISODE.value,
        ):
            if library_media_type:
                queryset = queryset.filter(
                    item__library_media_type=library_media_type,
                )
            else:
                queryset = queryset.exclude(
                    item__library_media_type=MediaTypes.ANIME.value,
                )

        return queryset

    def filter_media_prefetch(
        self,
        user,
        media_id,
        media_type,
        source,
        season_number=None,
        episode_number=None,
        library_media_type=None,
        annotate_progress=True,
    ):
        """Filter user media object with prefetch_related applied."""
        queryset = self.filter_media(
            user,
            media_id,
            media_type,
            source,
            season_number,
            episode_number,
            library_media_type,
        )
        queryset = self._apply_prefetch_related(queryset, media_type)
        queryset = queryset.select_related("item")
        if annotate_progress:
            self.annotate_max_progress(queryset, media_type)

        return queryset

    def get_serie_seasons(
        self,
        user,
        media_id,
        source,
        season_numbers=None,
        library_media_type=None,
        annotate_progress=True,
    ):
        """Return tracked season consumptions for a show."""
        queryset = self.filter_media_prefetch(
            user,
            media_id,
            MediaTypes.SEASON.value,
            source,
            library_media_type=library_media_type,
            annotate_progress=annotate_progress,
        )

        if season_numbers is not None:
            season_numbers = [number for number in season_numbers if number is not None]
            if not season_numbers:
                return queryset.none()
            queryset = queryset.filter(item__season_number__in=season_numbers)

        return queryset

    def get_serie_season_lists_by_number(self, user, tracked_seasons):
        """Return a dictionary mapping season numbers to their list memberships."""
        tracked_seasons = list(tracked_seasons)
        if user is None or not tracked_seasons:
            return {}

        custom_list_item_model = apps.get_model(
            app_label="lists",
            model_name="customlistitem",
        )
        lists_by_item_id = custom_list_item_model.objects.get_user_item_lists_map(
            user,
            [tracked.item_id for tracked in tracked_seasons],
        )

        lists_by_number = {}
        for tracked in tracked_seasons:
            season_number = getattr(tracked.item, "season_number", None)
            if season_number is None:
                continue
            lists_by_number[season_number] = lists_by_item_id.get(tracked.item_id, [])

        return lists_by_number

    def get_season_episodes(
        self,
        user,
        media_id,
        source,
        season_number=None,
        episode_numbers=None,
        library_media_type=None,
    ):
        """Return tracked episode consumptions for a show."""
        queryset = self.filter_media_prefetch(
            user,
            media_id,
            MediaTypes.EPISODE.value,
            source,
            season_number=season_number,
            library_media_type=library_media_type,
        )

        if episode_numbers is not None:
            episode_numbers = [
                number for number in episode_numbers if number is not None
            ]
            if not episode_numbers:
                return queryset.none()
            queryset = queryset.filter(item__episode_number__in=episode_numbers)

        return queryset

    def get_season_episode_lists_by_number(self, user, tracked_episodes):
        """Return a dictionary mapping episode numbers to their list memberships."""
        tracked_episodes = list(tracked_episodes)
        if user is None or not tracked_episodes:
            return {}

        custom_list_item_model = apps.get_model(
            app_label="lists",
            model_name="customlistitem",
        )
        lists_by_item_id = custom_list_item_model.objects.get_user_item_lists_map(
            user,
            [tracked.item_id for tracked in tracked_episodes],
        )

        lists_by_number = {}
        for tracked in tracked_episodes:
            episode_number = getattr(tracked.item, "episode_number", None)
            if episode_number is None:
                continue
            lists_by_number[episode_number] = lists_by_item_id.get(tracked.item_id, [])

        return lists_by_number

    def _filter_media_params(
        self,
        media_type,
        media_id,
        source,
        user,
        season_number=None,
        episode_number=None,
    ):
        """Get the common filter parameters for media queries."""
        params = {
            "item__media_type": media_type,
            "item__source": source,
            "item__media_id": media_id,
        }

        if media_type == MediaTypes.ANIME.value and source in {
            Sources.TMDB.value,
            Sources.TVDB.value,
        }:
            params["item__media_type"] = MediaTypes.TV.value
            params["item__library_media_type"] = MediaTypes.ANIME.value

        if media_type == MediaTypes.SEASON.value:
            if season_number is not None:
                params["item__season_number"] = season_number
            params["user"] = user
        elif media_type == MediaTypes.EPISODE.value:
            params["item__season_number"] = season_number
            if episode_number is not None:
                params["item__episode_number"] = episode_number
            params["related_season__user"] = user
        else:
            params["user"] = user

        return params
