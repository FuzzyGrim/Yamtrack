"""Per-show tracking facts for TV, read as grouped rows instead of models.

A TV tracker's progress and last-watched date are derived from its seasons
and their plays (``TV.progress``, ``Season.progress``, ``TV.end_date``).
Reading them off the models means loading every season, every play and every
play's item for each show: about forty model instances a show on a typical
library, which is what made library-wide ranking slow and memory-hungry.

``tv_show_facts`` answers the same questions for any number of shows with a
fixed number of value queries:

1. the active seasons of the shows (number, status, rewatch start);
2. one grouped row per season over its plays (furthest episode, whether
   anything is finished, last play date);
3. the plays of seasons in an open rewatch only, because which of those count
   towards the current pass is decided by ``Season.play_counts_for_pass``.

The numbers are defined by the model properties named on each field. A
change to one of those properties must be made here too;
``app.tests.test_tv_show_facts`` compares the two on every case it can build.

``tracked_shows`` wraps the facts as ``TrackedShow`` rows, which answer what
library ranking reads from a ``TV`` tracker (Home's shelves and the caught-up
filter). The library-query engine attaches them instead of the hydrated graph
when every sort and filter of a query says it reads nothing else.

``SharedShows`` lets the shelves of one Home load share those rows: every
TV shelf reads the same shows, and each used to compute them again. It stores
plain values under the user's Home cache registry, so any tracker write clears
it, with the lifetime Home already gives orders that depend on air dates.
Nothing else here is cached.
"""

from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace

from django.db.models import Count, Max, Q

from app.models import TV, BasicMedia, Episode, Media, Season, Status

# Above this many shows the queries read the whole library of the user and
# keep the requested shows, rather than sending every id as a parameter.
# SQLite accepts a limited number of parameters in one statement.
MAX_SHOW_ID_PARAMETERS = 500

_REACHED_STATUSES = (Status.COMPLETED.value, Status.DROPPED.value)


@dataclass(slots=True)
class SeasonFacts:
    """What ranking and progress read from one tracked season."""

    season_number: int | None
    status: str | None
    # ``Season.progress``: the furthest episode reached in the current pass,
    # 0 until an episode of that pass is finished.
    progress: int = 0
    # Highest episode number among the season's plays, in any pass or status.
    max_episode_number: int = 0
    # ``Season.end_date``: the latest play date, in any pass.
    end_date: datetime | None = None
    # The season item's provider identity, which names the show's episode
    # catalogue (``BasicMedia.objects.attach_episode_queue``).
    media_id: str = ""
    source: str = ""

    @property
    def item(self):
        """Let readers written for a ``Season`` find its item's columns here."""
        return self

    def next_episode_number(self, episode_numbers):
        """Return ``Season.next_episode_number`` for the given released numbers."""
        return Season.next_episode_number(self, episode_numbers)


@dataclass(slots=True)
class ShowFacts:
    """What ranking and progress read from one tracked show."""

    seasons: list[SeasonFacts] = field(default_factory=list)

    @property
    def progress(self):
        """Return ``TV.progress``: episodes reached outside specials and dropped seasons."""
        return sum(
            season.progress
            for season in self.seasons
            if season.season_number != 0 and season.status != Status.DROPPED.value
        )

    @property
    def end_date(self):
        """Return ``TV.end_date``: the latest play date, specials included."""
        dates = [season.end_date for season in self.seasons if season.end_date]
        return max(dates) if dates else None

    @property
    def dropped_season_numbers(self):
        """Return the numbers of dropped seasons, specials excluded."""
        return {
            season.season_number
            for season in self.seasons
            if season.status == Status.DROPPED.value and season.season_number != 0
        }


def tv_show_facts(user, tv_ids=None):
    """Return ``{tv_id: ShowFacts}`` for the user's shows.

    ``tv_ids`` narrows the result to those trackers; ``None`` means every show
    the user tracks. Shows of other users are never read, whatever is passed.
    A show without seasons has no entry: treat it as ``ShowFacts()``.
    """
    scope = {"related_tv__user": user}
    wanted = None
    if tv_ids is not None:
        wanted = set(tv_ids)
        if not wanted:
            return {}
        if len(wanted) <= MAX_SHOW_ID_PARAMETERS:
            scope["related_tv_id__in"] = wanted

    facts = {}
    seasons = {}
    rewatch_started = {}
    for season_id, tv_id, season_number, status, rewatch_started_at, media_id, source in (
        Season.objects.filter(**scope)
        .order_by("pk")
        .values_list(
            "pk",
            "related_tv_id",
            "item__season_number",
            "status",
            "rewatch_started_at",
            "item__media_id",
            "item__source",
        )
    ):
        if wanted is not None and tv_id not in wanted:
            continue
        season = seasons[season_id] = SeasonFacts(
            season_number,
            status,
            media_id=media_id,
            source=source,
        )
        facts.setdefault(tv_id, ShowFacts()).seasons.append(season)
        if rewatch_started_at is not None:
            rewatch_started[season_id] = rewatch_started_at
    if not seasons:
        return facts

    play_scope = {f"related_season__{name}": value for name, value in scope.items()}
    for row in (
        Episode.objects.filter(**play_scope)
        .order_by()
        .values("related_season_id")
        .annotate(
            last_end=Max("end_date"),
            max_number=Max("item__episode_number"),
            reached=Max("item__episode_number", filter=Q(status__in=_REACHED_STATUSES)),
            completed=Count("pk", filter=Q(status=Status.COMPLETED.value)),
        )
    ):
        season = seasons.get(row["related_season_id"])
        if season is None:
            continue
        season.end_date = row["last_end"]
        season.max_episode_number = row["max_number"] or 0
        if row["completed"]:
            season.progress = row["reached"] or 0

    if rewatch_started:
        _apply_rewatch_progress(seasons, rewatch_started, play_scope)
    return facts


def _apply_rewatch_progress(seasons, rewatch_started, play_scope):
    """Recount progress for seasons in a rewatch from the plays of that pass."""
    passes = {}
    for season_id, started_at in rewatch_started.items():
        # ``Season.play_counts_for_pass`` reads these two attributes only.
        current_pass = SimpleNamespace(rewatch_started_at=started_at)
        current_pass.pass_started_on = Season.pass_started_on.fget(current_pass)
        passes[season_id] = current_pass
        seasons[season_id].progress = 0

    reached = {}
    completed = set()
    for season_id, episode_number, status, end_date, created_at in (
        Episode.objects.filter(
            **play_scope,
            related_season__rewatch_started_at__isnull=False,
        )
        .order_by()
        .values_list(
            "related_season_id",
            "item__episode_number",
            "status",
            "end_date",
            "created_at",
        )
    ):
        current_pass = passes.get(season_id)
        if current_pass is None or not Season.play_counts_for_pass(
            current_pass,
            SimpleNamespace(end_date=end_date, created_at=created_at),
        ):
            continue
        if status == Status.COMPLETED.value:
            completed.add(season_id)
        if status in _REACHED_STATUSES and episode_number:
            reached[season_id] = max(reached.get(season_id, 0), episode_number)

    for season_id in completed:
        seasons[season_id].progress = reached.get(season_id, 0)


class _Related(list):
    """A list that also answers ``.all()``, like a prefetched relation."""

    def all(self):
        """Return the rows."""
        return self


class TrackedShow:
    """A ``TV`` tracker as library ranking reads it, without its model graph.

    Carries the tracker's own columns, its item and its facts. Each attribute
    is defined by the ``TV`` or ``Media`` member of the same name, and the
    methods are the models' own. ``max_progress`` and
    ``released_episode_breakdown`` are unset until
    ``annotate_released_progress`` runs, as on a tracker.

    It is not a tracker: a reader of anything else (score, dates other than
    the last play, the plays themselves) needs the hydrated row.
    """

    __slots__ = (
        "active_episode_order_id",
        "created_at",
        "facts",
        "item",
        "max_progress",
        "next_episode_queue",
        "next_event",
        "pk",
        "released_episode_breakdown",
        "seasons",
        "status",
    )

    def __init__(self, pk, item, status, created_at, active_episode_order_id, facts):
        """Wrap one tracker row and the facts of its seasons."""
        self.pk = pk
        self.item = item
        self.status = status
        self.created_at = created_at
        self.active_episode_order_id = active_episode_order_id
        self.facts = facts
        self.seasons = _Related(facts.seasons)

    @property
    def item_id(self):
        """Return the tracked item's id."""
        return self.item.pk

    @property
    def progress(self):
        """Return ``TV.progress``."""
        return self.facts.progress

    @property
    def progressed_at(self):
        """Return ``TV.progressed_at``: the last play, the same date as ``TV.end_date``."""
        return self.facts.end_date

    end_date = progressed_at
    episodes_left = Media.episodes_left
    next_episode_target = TV.next_episode_target


def tracked_shows(user, items):
    """Return ``{item_id: TrackedShow}`` for the items the user tracks as shows.

    Four value queries whatever the number of items, and no model.
    """
    by_id = {item.pk: item for item in items}
    rows = list(
        TV.objects.filter(user=user, item_id__in=by_id)
        .order_by()
        .values_list("pk", "item_id", "status", "created_at", "active_episode_order_id"),
    )
    facts = tv_show_facts(user, [row[0] for row in rows])
    return {
        item_id: TrackedShow(
            pk,
            by_id[item_id],
            status,
            created_at,
            order_id,
            facts.get(pk) or ShowFacts(),
        )
        for pk, item_id, status, created_at, order_id in rows
    }


def annotate_released_progress(shows, now=None):
    """Set ``max_progress`` and the released breakdown, as ``annotate_max_progress`` does."""
    if not shows:
        return
    breakdowns = BasicMedia.objects.released_episode_breakdowns(
        {(show.item.media_id, show.item.source) for show in shows},
        now,
    )
    for show in shows:
        _apply_released(show, breakdowns.get((show.item.media_id, show.item.source), {}))


def _apply_released(show, breakdown):
    show.released_episode_breakdown = breakdown
    show.max_progress = BasicMedia.objects.released_max_progress(
        show.item,
        breakdown,
        show.facts.dropped_season_numbers if breakdown else set(),
    )


# One shared record per show: the tracker columns, its seasons as tuples and,
# once a shelf has needed it, the released breakdown (None until then).
_SEASON_FIELDS = (
    "season_number",
    "status",
    "progress",
    "max_episode_number",
    "end_date",
    "media_id",
    "source",
)


def _record(show, *, released):
    return (
        show.pk,
        show.status,
        show.created_at,
        show.active_episode_order_id,
        tuple(tuple(getattr(season, name) for name in _SEASON_FIELDS) for season in show.facts.seasons),
        dict(show.released_episode_breakdown) if released else None,
    )


def _show_from_record(item, record, *, released):
    pk, status, created_at, order_id, seasons, breakdown = record
    facts = ShowFacts([SeasonFacts(*season) for season in seasons])
    show = TrackedShow(pk, item, status, created_at, order_id, facts)
    if released:
        _apply_released(show, breakdown)
    return show


class SharedShows:
    """``tracked_shows`` (and their released progress) shared by Home's shelves.

    A Home load ranks several TV shelves over the same shows, each in its own
    request. The first computes each show's record; the others read it. Only
    values are stored (no model): the tracker's columns, its seasons' facts
    and the released breakdown. The candidate's own item is attached on read.

    Lifetime: ``HOME_ROW_RELEASED_CACHE_TTL``, the bound Home already accepts
    for orders that depend on air dates (a calendar refresh moves them without
    a user write). Any tracker write clears it with the rest of the user's
    Home rows. Use one instance per scan and call ``save`` after it.
    """

    def __init__(self, user):
        """Prepare to read and extend the user's shared show records."""
        self.user = user
        self._records = None
        self._dirty = False

    @property
    def key(self):
        """Return the user's cache key."""
        from app import cache_utils

        return cache_utils.build_home_tv_shows_cache_key(self.user.id)

    def _load(self):
        if self._records is None:
            from django.core.cache import cache

            cached = cache.get(self.key)
            self._records = dict(cached) if isinstance(cached, dict) else {}
        return self._records

    def tracked(self, items, *, released):
        """Return ``{item_id: TrackedShow}`` like ``tracked_shows``, from the shared records.

        With ``released``, each show also carries ``max_progress`` and its
        released breakdown, as after ``annotate_released_progress``.
        """
        records = self._load()
        missing = [
            item
            for item in items
            if item.pk not in records
            or (released and records[item.pk] is not None and records[item.pk][5] is None)
        ]
        if missing:
            shows = tracked_shows(self.user, missing)
            if released:
                annotate_released_progress(list(shows.values()))
            for item in missing:
                show = shows.get(item.pk)
                records[item.pk] = None if show is None else _record(show, released=released)
            self._dirty = True
        return {
            item.pk: _show_from_record(item, records[item.pk], released=released)
            for item in items
            if records.get(item.pk) is not None
        }

    def save(self):
        """Store what this scan added, for the next shelf."""
        if not self._dirty:
            return
        from django.core.cache import cache

        from app import cache_utils

        cache.set(self.key, self._records, cache_utils.HOME_ROW_RELEASED_CACHE_TTL)
        cache_utils.register_home_row_cache_key(self.user.id, self.key)
        self._dirty = False
