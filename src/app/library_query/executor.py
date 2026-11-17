"""Run a ``LibraryQuery``: one page of ordered items and the total, in bounded work.

Two paths, chosen from the filter and sort registries rather than a list of
exceptions:

- **SQL**: every active filter and the sort compile to SQL. Filtering,
  ordering, ``COUNT`` and ``LIMIT``/``OFFSET`` all run in the database; only
  the page's items are loaded.
- **Scan**: a filter or the sort needs Python. Candidates are narrowed by
  every SQL-capable condition first, then read in fixed-size batches that
  keep only ``(sort value, title, id)`` per match. The page's items are
  loaded at the end. Memory and hydration are bounded by the batch and the
  page; computing Python values is still one pass over the SQL-narrowed
  candidates.

The executor returns ``Item`` rows. Decorating them (tracker rows, card
images, progress) is the calling surface's job, and it only ever sees a page.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import cached_property
from itertools import batched, islice
from typing import TYPE_CHECKING

from django.db.models import Exists, F, OuterRef, Q
from django.db.models.functions import Coalesce, Lower
from django.utils import timezone

from app.library_query import filters as filter_registry
from app.library_query import sorts as sort_registry
from app.library_query.spec import ROUTING_MODEL, STATUS_MATCH_LATEST
from app.library_query.trackers import episode_media_for_items, tracker_sources
from app.models.choices import MediaTypes, Sources
from app.models.item import Item

# Only grouped shows (anime/seasons stored on TV rows) set library_media_type
# to another type; every other library matches on media_type alone, which a
# covering index answers without reading wide item rows.
_LIBRARY_BUCKET_TYPES = frozenset(
    (MediaTypes.TV.value, MediaTypes.ANIME.value, MediaTypes.SEASON.value),
)

if TYPE_CHECKING:
    from app.library_query.spec import LibraryQuery

DEFAULT_BATCH_SIZE = 256
ASC = "asc"
DESC = "desc"


@dataclass
class Candidate:
    """An item being evaluated, and its aggregated tracker row once loaded."""

    item: Item
    media: object | None = None


@dataclass
class Page:
    """One ordered page of items and the size of the full result."""

    items: list[Item]
    total: int
    used_sql: bool


class LibraryQueryExecutor:
    """Evaluate one ``LibraryQuery`` for one user."""

    def __init__(
        self,
        user,
        query: LibraryQuery,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        share_shows: bool = False,
    ):
        """Prepare ``query`` for ``user``; nothing runs until it is asked for.

        ``share_shows`` lets a scan that reads show facts reuse, and add to,
        the records other shelves of the user's Home computed
        (``app.tv_show_facts.SharedShows``). Only Home asks for it.
        """
        self.user = user
        self.query = query
        self.batch_size = batch_size
        self.share_shows = share_shows
        self.today = timezone.localdate()

    # -- compilation ----------------------------------------------------------

    @cached_property
    def contexts(self) -> list[filter_registry.TypeContext]:
        """Return one compilation context per requested media type."""
        return [
            filter_registry.TypeContext(
                user=self.user,
                media_type=media_type,
                sources=tuple(tracker_sources(
                    self.user, media_type, self.query.routing,
                    include_podcast_shows=self.query.include_podcast_shows,
                    include_albums=self.query.sort_list_id is not None,
                )),
                today=self.today,
                provider_region=self.query.provider_region,
                pinned_providers=self.query.pinned_providers,
                sort_list_id=self.query.sort_list_id,
                filters=self.query.filters,
            )
            for media_type in self.query.media_types
        ]

    def _active(self, ctx):
        return filter_registry.active_filters(self.query.filters, ctx.media_type)

    def _membership_q(self, ctx) -> Q:
        """Return the condition for an item being in this type's candidates."""
        values = self.query.filters
        active = self._active(ctx)
        scope_q = self._scope_q()
        if scope_q is not None:
            # A list or id scope keeps untracked items; tracker rows are only
            # required when a row condition asks something of them.
            membership = scope_q & self._type_q(ctx)
            if self._row_conditions(ctx, active, scoped=True):
                membership &= self._tracked_q(ctx, active, scoped=True)
            return membership

        membership = self._tracked_q(ctx, active)
        if self.query.include_collection_only and filter_registry.collection_only_allowed(
            values,
        ):
            membership |= self._collection_only_q(ctx)
        return membership

    def _type_q(self, ctx) -> Q:
        """Return: the item itself belongs to this type's library."""
        if (
            self.query.routing == ROUTING_MODEL
            or ctx.media_type not in _LIBRARY_BUCKET_TYPES
        ):
            return Q(media_type=ctx.media_type)
        return Q(media_type=ctx.media_type) | Q(library_media_type=ctx.media_type)

    def _scope_q(self) -> Q | None:
        """Return the candidate scope, or ``None`` for the user's library."""
        if self.query.list_id is not None:
            from lists.models import CustomListItem

            return Q(
                pk__in=CustomListItem.objects.filter(
                    custom_list_id=self.query.list_id,
                ).values("item_id"),
            )
        if self.query.within is not None:
            return Q(pk__in=self.query.within)
        return None

    def _row_conditions(self, ctx, active, *, scoped: bool) -> dict:
        """Return each tracker source's combined row condition, if any."""
        conditions = {}
        for source in ctx.sources:
            row_q = Q()
            for definition in active:
                if definition.row is None:
                    continue
                if scoped and definition.key == "status" and (
                    self.query.filters.status_match == STATUS_MATCH_LATEST
                ):
                    # "Has a status" defines library membership, not a list's.
                    continue
                condition = definition.row(self.query.filters, source, ctx)
                if condition is not None:
                    row_q &= condition
            if row_q:
                conditions[source] = row_q
        return conditions

    def _tracked_q(self, ctx, active, *, scoped: bool = False) -> Q:
        """Return: a tracker row of this type satisfies every row condition."""
        conditions = self._row_conditions(ctx, active, scoped=scoped)
        return filter_registry.any_q(
            [
                source.item_q
                & Q(pk__in=source.item_ids(self.user, conditions.get(source)))
                for source in ctx.sources
            ],
        )

    def _collection_only_q(self, ctx) -> Q:
        """Return collected or tagged items of this type that have no tracker row."""
        from app.models.discovery import CollectionEntry, ItemTag

        # Exists keeps the plan item-driven: a user can hold 100k+ collection
        # rows, and walking them into wide item rows costs seconds cold.
        collected = Exists(
            CollectionEntry.objects.filter(user=self.user, item_id=OuterRef("pk")),
        ) & ~Q(media_type=MediaTypes.EPISODE.value)
        if ctx.media_type in (MediaTypes.TV.value, MediaTypes.ANIME.value):
            collected |= Q(
                media_type__in=(MediaTypes.TV.value, MediaTypes.ANIME.value),
                pk__in=filter_registry.shows_with_collected_episodes(self.user),
            )
        values = self.query.filters
        if values.tags and values.tag_mode != "not":
            # A tag can sit on an item with no tracker and no collection entry;
            # asking for the tag must still find it (#1351).
            collected |= Q(
                pk__in=ItemTag.objects.filter(tag__user=self.user).values("item_id"),
            ) & ~Q(media_type=MediaTypes.EPISODE.value)
        untracked = ~filter_registry.any_row(ctx)
        return self._type_q(ctx) & collected & untracked

    def _item_q(self, ctx) -> Q:
        """Return every SQL condition for this media type."""
        condition = self._membership_q(ctx)
        for definition in self._active(ctx):
            if definition.sql is None:
                continue
            compiled = definition.sql(self.query.filters, ctx)
            if compiled is not None:
                condition &= compiled
        return condition

    @cached_property
    def _predicates_by_type(self) -> dict[str, list]:
        return {
            ctx.media_type: [d for d in self._active(ctx) if d.predicate is not None]
            for ctx in self.contexts
        }

    def _predicates(self, ctx):
        return self._predicates_by_type[ctx.media_type]

    @cached_property
    def _needs_scan(self) -> bool:
        return any(self._predicates_by_type.values())

    def _union_ids(self):
        """Return ids of items in the lists the query always includes."""
        from lists.models import CustomListItem

        return CustomListItem.objects.filter(
            custom_list_id__in=self.query.union_list_ids,
        ).values("item_id")

    @cached_property
    def filtered(self):
        """Return the ``Item`` queryset narrowed by every SQL condition."""
        if not self.contexts:
            queryset = Item.objects.none()
        else:
            queryset = Item.objects.filter(
                filter_registry.any_q([self._item_q(ctx) for ctx in self.contexts]),
            )
        hidden_ids = self._cross_provider_hidden_ids(queryset)
        if hidden_ids:
            queryset = queryset.exclude(pk__in=hidden_ids)
        if self.query.union_list_ids:
            queryset = Item.objects.filter(
                Q(pk__in=queryset.values("pk")) | Q(pk__in=self._union_ids()),
            )
        return queryset

    @cached_property
    def sort(self):
        """Return the definition of the requested sort key."""
        return sort_registry.sort_def(self.query.sort.key)

    @cached_property
    def direction(self) -> str:
        """Return the requested direction, or the key's default."""
        from app.models import BasicMedia

        if self.sort.direction_in_value:
            return "asc"
        return BasicMedia.objects.resolve_direction(
            self.query.sort.key,
            self.query.sort.direction,
        )

    @cached_property
    def tie_descending(self) -> bool:
        """Game group lists keep the web manager's ascending title ties."""
        grouped_games = self.query.media_types == (MediaTypes.GAME.value,) and self.query.filters.game_group_by != "item"
        ascending_group_ties = grouped_games and self.query.sort.key in {
            "score", "status", "release_date", "critic_rating", "popularity",
            "last_watched", "start_date", "end_date", "date_added", "updated", "progressed_at",
            "id", "itemid", "mediaid", "source", "type",
        }
        return self.direction == DESC and not ascending_group_ties

    @cached_property
    def uses_sql(self) -> bool:
        """Return whether filters and sort all compile to SQL."""
        sortable = self._sort_expressions is not None or self._sql_order_keys is not None
        return bool(self.contexts) and sortable and not self._needs_scan

    @cached_property
    def _sql_order_keys(self) -> list | None:
        if self.sort.sql_order is None or len(self.contexts) != 1:
            return None
        return self.sort.sql_order(
            self.contexts[0],
            self.query.sort.seed,
            self.query.sort.direction,
        )

    @cached_property
    def _sort_expressions(self) -> list | None:
        if self.sort.sql is None:
            return None
        expressions = [self.sort.sql(ctx, self.query.sort.seed) for ctx in self.contexts]
        if any(expression is None for expression in expressions):
            return None
        return expressions

    def _sort_expression(self):
        expressions = self._sort_expressions
        if len(expressions) == 1 or not self.sort.tracker:
            return expressions[0]
        return Coalesce(*expressions)

    def _ordered(self, queryset):
        if self._sql_order_keys is not None:
            annotations, keys = self._sql_order_keys
            return queryset.annotate(**annotations).order_by(
                *keys,
                *tie_breakers(descending=False),
            )
        expression = self._sort_expression()
        descending = self.direction == DESC
        queryset = queryset.annotate(_library_sort=expression)
        value = F("_library_sort")
        return queryset.order_by(
            value.desc(nulls_last=True) if descending else value.asc(nulls_last=True),
            *tie_breakers(descending=self.tie_descending),
        )

    # -- cross-provider aliases -----------------------------------------------

    def _cross_provider_hidden_ids(self, queryset) -> set[int]:
        """Return TMDB items hidden because their TVDB alias is also listed (#639)."""
        if not self.query.dedupe_cross_provider:
            return set()
        show_types = {
            MediaTypes.TV.value,
            MediaTypes.ANIME.value,
            MediaTypes.SEASON.value,
        }
        if not show_types.intersection(self.query.media_types):
            return set()
        from app.services.item_merge import dedupe_cross_provider_items

        rows = queryset.filter(
            media_type__in=(MediaTypes.TV.value, MediaTypes.SEASON.value),
            source__in=(Sources.TMDB.value, Sources.TVDB.value),
        ).only(
            "id",
            "media_id",
            "media_type",
            "season_number",
            "source",
            "provider_external_ids",
        )
        if not rows.filter(source=Sources.TVDB.value).exists():
            return set()
        items = list(rows)
        kept = dedupe_cross_provider_items(
            items,
            getattr(self.user, "tv_metadata_source_default", Sources.TMDB.value),
        )
        return {item.id for item in items} - {item.id for item in kept}

    # -- evaluation -----------------------------------------------------------

    def count(self) -> int:
        """Return how many items match."""
        if not self._needs_scan:
            return self.filtered.count()
        # A count is nearly always followed by ``page``; share its scan.
        return len(self._scan_ranked)

    def ids(self) -> set[int]:
        """Return every matching item id (for smart-list membership)."""
        if not self._needs_scan:
            return set(self.filtered.values_list("pk", flat=True))
        return {pk for *_rest, pk in self._scan(self.filtered, with_sort=False)}

    def ranked_ids(self) -> list[int]:
        """Return every matching item id in order (for caching an order)."""
        if self.uses_sql:
            return list(self._ordered(self.filtered).values_list("pk", flat=True))
        return [row[-1] for row in self._scan_ranked]

    def ranked_ids_both_directions(self) -> dict[str, list[int]]:
        """Return the ranked ids for both directions from one scan.

        The scan is the expensive part of a Python-ordered shelf; ordering its
        rows either way is cheap, so flipping a shelf's direction can be served
        from the order cached with the first one.
        """
        rows = self._scan(self.filtered, with_sort=True)
        return {
            direction: [row[-1] for row in order_rows(rows, descending=direction == DESC)]
            for direction in (ASC, DESC)
        }

    def rank_rows(self) -> list[tuple]:
        """Return compact sort values and item identities for a composed shelf.

        Uses the same SQL values or batched scan as page(), without decorating
        cards or retaining hydrated candidates.
        """
        return self._scan(self.filtered, with_sort=True)

    def matches(self):
        """Return the matches as a scope for another query's ``within``.

        A ``pk`` subquery when every filter is SQL, so nothing is loaded;
        the matching ids when a Python filter had to run.
        """
        if not self._needs_scan:
            return self.filtered.values("pk")
        return self.ids()

    def contains(self, item_id: int) -> bool:
        """Return whether one item matches, without evaluating the others."""
        candidates = self.filtered.filter(pk=item_id)
        if not self._needs_scan:
            return candidates.exists()
        return bool(self._scan(candidates, with_sort=False))

    def matching_among(self, item_ids, *, chunk_size: int) -> set[int]:
        """Return which of ``item_ids`` match, asking once per ``chunk_size`` ids.

        The set form of ``contains``: the same conditions, Python filters and
        cross-provider hiding, so each id gets the answer ``contains`` gives.
        """
        matched = set()
        for chunk in batched(dict.fromkeys(item_ids), max(1, chunk_size)):
            candidates = self.filtered.filter(pk__in=chunk)
            if not self._needs_scan:
                matched.update(candidates.values_list("pk", flat=True))
            else:
                matched.update(row[-1] for row in self._scan(candidates, with_sort=False))
        return matched

    def page(
        self,
        offset: int,
        limit: int,
        *,
        defer: tuple[str, ...] = (),
        total: int | None = None,
    ) -> Page:
        """Return ``limit`` items starting at ``offset``, and the total.

        ``defer`` names ``Item`` columns the caller will not read. Pass
        ``total`` when the caller already counted, to skip a second count.
        """
        offset = max(0, offset)
        if self.uses_sql:
            ordered = self._ordered(self.filtered)
            if total is None:
                total = ordered.count()
            # Sort on ids alone, then load the window: ordering a library
            # would otherwise drag every candidate's wide row through the sort.
            window = list(ordered.values_list("pk", flat=True)[offset : offset + limit])
            by_id = Item.objects.defer(*defer).in_bulk(window)
            return Page([by_id[i] for i in window if i in by_id], total, used_sql=True)

        ranked = self._scan_ranked
        selected_ids = [pk for *_rest, pk in ranked[offset : offset + limit]]
        by_id = Item.objects.defer(*defer).in_bulk(selected_ids)
        return Page([by_id[i] for i in selected_ids if i in by_id], len(ranked), False)

    def entry_page(self, offset: int, limit: int | None):
        """Page individual tracker rows, retaining compact ranks between batches.

        Item predicates use the same registry as grouped lists. Status, rating
        and dates apply to the *same entry*, rather than its latest sibling.
        Only the returned window keeps hydrated tracker and Item instances.
        """
        from app.media_list_filters import MediaListEntry
        from app.models import BasicMedia

        values = self.query.filters
        item_values = replace(values, statuses=(), rating="all", rating_min="", rating_max="",
                              date_added_from="", date_added_to="", completed_date_from="", completed_date_to="")
        narrowed = LibraryQueryExecutor(self.user, replace(self.query, filters=item_values))
        ranked = []
        sources = []
        needs = set(self.sort.needs)
        for ctx in self.contexts:
            for definition in self._predicates(ctx):
                needs |= definition.needs
        value_for = sort_registry.python_key(self.query.sort.key)
        for ctx in self.contexts:
            for source in ctx.sources:
                source_index = len(sources)
                sources.append(source)
                item_ids = narrowed.filtered.filter(source.item_q).values("pk")
                queryset = source.rows(self.user).filter(item_id__in=item_ids)
                condition = self._row_conditions(ctx, self._active(ctx), scoped=False).get(source)
                if condition is not None:
                    queryset = queryset.filter(condition)
                if values.rating == "not_rated":
                    queryset = queryset.filter(score__isnull=True)
                else:
                    if values.rating == "rated":
                        queryset = queryset.filter(score__isnull=False)
                    if values.rating_min:
                        queryset = queryset.filter(score__gte=values.rating_min)
                    if values.rating_max:
                        queryset = queryset.filter(score__lte=values.rating_max)
                queryset = queryset.select_related("item")
                if filter_registry.NEEDS_WATCH_PROVIDERS not in needs:
                    queryset = queryset.defer("item__watch_providers")
                queryset = BasicMedia.objects._apply_prefetch_related(
                    queryset, source.model._meta.model_name, list_mode=True,
                    compact_episodes=True, light_rows=True,
                )
                iterator = queryset.order_by("pk").iterator(chunk_size=self.batch_size)
                while batch := list(islice(iterator, self.batch_size)):
                    if filter_registry.NEEDS_MAX_PROGRESS in needs:
                        BasicMedia.objects.annotate_max_progress(batch, source.model._meta.model_name)
                    if filter_registry.NEEDS_RUNTIME in needs:
                        from app.models import prefill_episode_runtime_index

                        prefill_episode_runtime_index(batch)
                    candidates = [Candidate(row.item, row) for row in batch]
                    for definition in self._predicates(ctx):
                        if definition.prepare:
                            definition.prepare(candidates, values, ctx)
                    candidates = [candidate for candidate in candidates if all(
                        definition.predicate(candidate, values, ctx) for definition in self._predicates(ctx)
                    )]
                    batch_values = self.sort.batch_values
                    if batch_values:
                        sort_values = batch_values(self.user, candidates, self.query.sort.direction)
                    elif self.query.sort.key == "random":
                        sort_values = [sort_registry.random_rank(candidate.item.pk, self.query.sort.seed) for candidate in candidates]
                    else:
                        sort_values = [value_for(candidate) for candidate in candidates]
                    ranked.extend(
                        (*rank_row(value, candidate.item), source_index, candidate.media.pk)
                        for candidate, value in zip(candidates, sort_values, strict=True)
                    )
        if self.query.include_collection_only:
            untracked = self.filtered.exclude(filter_registry.any_q([
                filter_registry.any_row(ctx) for ctx in self.contexts
            ]))
            ranked.extend((*row, -1, row[-1]) for row in self._scan(untracked, with_sort=True))
        ranked = order_rows(ranked, descending=self.direction == DESC, ties_descending=self.tie_descending)
        window = ranked[max(0, offset):None if limit is None else max(0, offset) + limit]
        hydrated = {}
        for index, source in enumerate(sources):
            ids = [row[-1] for row in window if row[-2] == index]
            rows = source.rows(self.user).filter(pk__in=ids).select_related("item")
            rows = BasicMedia.objects._apply_prefetch_related(rows, source.model._meta.model_name, list_mode=True, compact_episodes=True, light_rows=True)
            hydrated.update({(index, row.pk): row for row in rows})
        untracked_items = Item.objects.in_bulk(row[-1] for row in window if row[-2] == -1)
        return [
            MediaListEntry(item=untracked_items[row[-1]]) if row[-2] == -1
            else MediaListEntry(item=hydrated[row[-2:]].item, media=hydrated[row[-2:]])
            for row in window
        ], len(ranked)

    @cached_property
    def _scan_ranked(self) -> list[tuple]:
        """Return a ``rank_row`` for every match, in order."""
        return order_rows(
            self._scan(self.filtered, with_sort=True),
            descending=self.direction == DESC,
            ties_descending=self.tie_descending,
        )

    def _scan(self, queryset, *, with_sort: bool) -> list[tuple]:
        """Read candidates in batches; keep a compact ``rank_row`` per match."""
        sql_sort = with_sort and bool(self.contexts) and self._sort_expressions is not None
        if sql_sort:
            queryset = queryset.annotate(_library_sort=self._sort_expression())
        if self.query.union_list_ids:
            queryset = queryset.annotate(
                _in_union=Exists(self._union_ids().filter(item_id=OuterRef("pk"))),
            )

        needs = set(self.sort.needs) if with_sort else set()
        for ctx in self.contexts:
            for definition in self._predicates(ctx):
                needs |= definition.needs
        if filter_registry.NEEDS_WATCH_PROVIDERS not in needs:
            queryset = queryset.defer("watch_providers")
        contexts_by_type = {ctx.media_type: ctx for ctx in self.contexts}
        python_key = None
        batch_values = self.sort.batch_values if with_sort and not sql_sort else None
        if with_sort and not sql_sort and batch_values is None:
            python_key = sort_registry.python_key(self.query.sort.key)
            if not self.sort.item_only:
                # A tracker value computed in Python reads the aggregated row.
                needs.add(filter_registry.NEEDS_MEDIA)
        requested_direction = self.query.sort.direction
        # Shows are attached as facts, not as hydrated trackers, when every
        # reader of the tracker row in this scan says facts are all it reads.
        show_facts = (sql_sort or not with_sort or self.sort.tv_facts) and all(
            definition.tv_facts
            for ctx in self.contexts
            for definition in self._predicates(ctx)
            if filter_registry.NEEDS_MEDIA in definition.needs
        )
        # Movies are attached as light rows when only the sort reads them.
        light_movies = (
            with_sort
            and not sql_sort
            and self.sort.movie_rows
            and not any(
                filter_registry.NEEDS_MEDIA in definition.needs
                for ctx in self.contexts
                for definition in self._predicates(ctx)
            )
        )

        shared = None
        if show_facts and self.share_shows:
            from app.tv_show_facts import SharedShows

            shared = SharedShows(self.user)

        rows = []
        iterator = queryset.iterator(chunk_size=self.batch_size)
        while True:
            batch = [Candidate(item) for item in islice(iterator, self.batch_size)]
            if not batch:
                break
            if filter_registry.NEEDS_MEDIA in needs:
                _attach_media(
                    self.user,
                    batch,
                    needs,
                    show_facts=show_facts,
                    light_movies=light_movies,
                    shared=shared,
                    include_albums=self.query.sort_list_id is not None,
                )
            for ctx in self.contexts:
                for definition in self._predicates(ctx):
                    if definition.prepare is not None:
                        definition.prepare(
                            [c for c in batch if _context_for(c.item, contexts_by_type) is ctx],
                            self.query.filters,
                            ctx,
                        )
            kept = []
            for candidate in batch:
                if not getattr(candidate.item, "_in_union", False):
                    ctx = _context_for(candidate.item, contexts_by_type)
                    if ctx is not None and not all(
                        definition.predicate(candidate, self.query.filters, ctx)
                        for definition in self._predicates(ctx)
                    ):
                        continue
                kept.append(candidate)
            if batch_values is not None:
                values = batch_values(self.user, kept, requested_direction)
                rows.extend(rank_row(value, c.item) for value, c in zip(values, kept, strict=True))
                continue
            for candidate in kept:
                if not with_sort:
                    value = None
                elif python_key is None:
                    value = candidate.item._library_sort
                elif candidate.media is None and self.sort.tracker:
                    # No tracker row, so no tracker value (not zero progress).
                    value = None
                else:
                    value = python_key(candidate)
                rows.append(rank_row(value, candidate.item))
        if shared is not None:
            shared.save()
        return rows


def tie_breakers(*, descending: bool) -> list:
    """Return the SQL ordering after the sort value: title, season, episode, id.

    Seasons and episodes of one show share a title, so they fall back to
    their numbers (a missing number first when ascending) before the id.
    """
    if descending:
        return [
            Lower("title").desc(),
            F("season_number").desc(nulls_last=True),
            F("episode_number").desc(nulls_last=True),
            F("pk").desc(),
        ]
    return [
        Lower("title").asc(),
        F("season_number").asc(nulls_first=True),
        F("episode_number").asc(nulls_first=True),
        F("pk").asc(),
    ]


def rank_row(value, item) -> tuple:
    """Return a scan row that orders like ``tie_breakers`` after ``value``."""
    season = -1 if item.season_number is None else item.season_number
    episode = -1 if item.episode_number is None else item.episode_number
    return (value, (item.title or "").lower(), season, episode, item.pk)


def order_rows(rows: list[tuple], *, descending: bool, ties_descending: bool | None = None) -> list[tuple]:
    """Order ``rank_row`` rows like the SQL path: value (nulls last), then ties."""
    present = [row for row in rows if row[0] is not None]
    missing = [row for row in rows if row[0] is None]
    ties_descending = descending if ties_descending is None else ties_descending
    present.sort(key=lambda row: row[1:], reverse=ties_descending)
    present.sort(key=lambda row: row[0], reverse=descending)
    missing.sort(key=lambda row: row[1:], reverse=ties_descending)
    return present + missing


def _context_for(item, contexts_by_type):
    for media_type in (item.library_media_type, item.media_type):
        ctx = contexts_by_type.get(media_type)
        if ctx is not None:
            return ctx
    return None


def _attach_media(
    user,
    batch: list[Candidate],
    needs: set[str],
    *,
    show_facts: bool = False,
    light_movies: bool = False,
    include_albums: bool = False,
    shared=None,
) -> None:
    """Load each candidate's aggregated tracker row, for this batch only.

    With ``show_facts``, a show gets an ``app.tv_show_facts.TrackedShow``
    instead: its numbers come from grouped rows, so no season, play or event
    is loaded for it. Only a scan whose sort and filters all declare
    ``tv_facts`` may ask for that. With ``light_movies``, a movie gets an
    ``app.movie_rows.MovieRow`` (its columns, no model); only a scan whose
    sort declares ``movie_rows`` and whose filters read no tracker may.
    """
    from django.apps import apps

    from app.models import BasicMedia

    by_type: dict[str, list[Candidate]] = {}
    for candidate in batch:
        by_type.setdefault(candidate.item.media_type, []).append(candidate)

    for media_type, candidates in by_type.items():
        model = apps.get_model("app", media_type)
        if media_type == MediaTypes.EPISODE.value:
            latest = episode_media_for_items(user, [candidate.item.pk for candidate in candidates])
            for candidate in candidates:
                candidate.media = latest.get(candidate.item.pk)
            continue
        if show_facts and media_type == MediaTypes.TV.value:
            from app.tv_show_facts import annotate_released_progress, tracked_shows

            items = [candidate.item for candidate in candidates]
            released = filter_registry.NEEDS_MAX_PROGRESS in needs
            if shared is not None:
                shows = shared.tracked(items, released=released)
            else:
                shows = tracked_shows(user, items)
                if released:
                    annotate_released_progress(list(shows.values()))
            if filter_registry.NEEDS_RUNTIME in needs:
                from app.models import prefill_episode_runtime_index

                prefill_episode_runtime_index(list(shows.values()))
            for candidate in candidates:
                candidate.media = shows.get(candidate.item.pk)
            continue
        if light_movies and media_type == MediaTypes.MOVIE.value:
            from app.movie_rows import movie_rows

            movies = movie_rows(user, [candidate.item for candidate in candidates])
            if filter_registry.NEEDS_MAX_PROGRESS in needs:
                BasicMedia.objects.annotate_max_progress(list(movies.values()), media_type)
            for candidate in candidates:
                candidate.media = movies.get(candidate.item.pk)
            continue
        rows = model.objects.filter(
            user=user,
            item_id__in=[candidate.item.pk for candidate in candidates],
        ).select_related("item")
        if filter_registry.NEEDS_WATCH_PROVIDERS not in needs:
            # The scan's candidate queryset defers this blob; the tracker rows
            # must not bring it back through their own select_related.
            rows = rows.defer("item__watch_providers")
        if media_type == MediaTypes.SEASON.value:
            # The season graph joins each row's show item, and with it a copy
            # of the show's provider blob per season. Ranking never reads the
            # show's providers (the provider filter reads the season's own),
            # so leave it in the database, as episode rows already do.
            rows = rows.defer("related_tv__item__watch_providers")
        # Progress and derived status read episodes and seasons; fetch them
        # once for the batch instead of once per row. Ranking only reads the
        # compact episode fields: runtime comes from the bulk runtime index
        # below, not from the prefetched episode items.
        rows = BasicMedia.objects._apply_prefetch_related(
            rows,
            media_type,
            list_mode=True,
            compact_episodes=True,
            light_rows=True,
        )
        aggregated = BasicMedia.objects._aggregate_duplicate_data(rows, user, media_type)
        # The newest row by creation, the higher id on a tie (bulk imports
        # share timestamps), as the "updated" sort's SQL form reads it.
        latest = {}
        for media in aggregated:
            current = latest.get(media.item_id)
            if current is None or (media.created_at, media.pk) > (current.created_at, current.pk):
                latest[media.item_id] = media
        if media_type == MediaTypes.PODCAST.value:
            from app.services.podcast_list_tracking import (
                podcast_show_trackers_for_items,
            )

            latest.update(podcast_show_trackers_for_items(user, [candidate.item for candidate in candidates]))
        if include_albums and media_type == MediaTypes.MUSIC.value:
            from app.services.music import album_trackers_for_list_items

            latest.update(album_trackers_for_list_items(user, [candidate.item for candidate in candidates]))
        tracked = []
        for candidate in candidates:
            candidate.media = latest.get(candidate.item.pk)
            if candidate.media is not None:
                tracked.append(candidate.media)
        if tracked and filter_registry.NEEDS_MAX_PROGRESS in needs:
            BasicMedia.objects.annotate_max_progress(tracked, media_type)
        if tracked and filter_registry.NEEDS_RUNTIME in needs:
            from app.models import prefill_episode_runtime_index

            prefill_episode_runtime_index(tracked)
