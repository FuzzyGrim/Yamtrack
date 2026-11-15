"""The query a library surface asks for: which items, filtered how, in what order.

One ``LibraryQuery`` describes every list-shaped surface - the media list, the
API, a smart list, a Home shelf. Surfaces differ only in how they build it
(see ``adapters``) and in how they decorate the page the executor returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

STATUS_MATCH_LATEST = "latest"
STATUS_MATCH_ANY = "any"
STATUS_MATCH_CHOICES = frozenset({STATUS_MATCH_LATEST, STATUS_MATCH_ANY})

SET_MODES = frozenset({"and", "or", "not"})

# Which libraries an item belongs to. ``library`` follows the user's anime
# library preference, as the media list does: anime tracked on TV rows can
# show in Anime, TV, or both. ``model`` puts an item in its tracker model's
# type only, which is how smart lists saved before the engine behave.
ROUTING_LIBRARY = "library"
ROUTING_MODEL = "model"


@dataclass(frozen=True)
class FilterValues:
    """The canonical filter vocabulary shared by every library surface.

    Empty strings, empty tuples and ``"all"`` mean "not filtering". Values are
    stored as the user supplied them; each filter definition normalises the
    value it reads.

    ``status_match`` decides what a status filter compares against:

    - ``latest``: the item's most recent tracker row by activity. This is what
      the media list shows as the item's status.
    - ``any``: any tracker row. Smart lists created before the shared engine
      use this, so that their membership does not change.
    """

    statuses: tuple[str, ...] = ()
    include_no_status: bool = False
    status_match: str = STATUS_MATCH_LATEST
    search: str = ""
    initial: str = ""
    game_group_by: str = "item"
    game_group: str = ""
    rating: str = "all"
    rating_min: str = ""
    rating_max: str = ""
    collection: str = "all"
    progress: str = "all"
    # "Not caught up" skips shows with no released regular episode (future-only
    # or specials-only). Mixed shelves set it so such shows do not read as
    # unfinished next to movies and games.
    progress_needs_released: bool = False
    genre: str = ""
    implied_genre: str = ""
    year: str = ""
    completed_date_from: str = ""
    completed_date_to: str = ""
    date_added_from: str = ""
    date_added_to: str = ""
    # ISO 8601 moment: keep items with a tracking row changed at or after it.
    updated_since: str = ""
    release: str = "all"
    release_date_from: str = ""
    release_date_to: str = ""
    source: str = ""
    media_status: str = ""
    language: str = ""
    country: str = ""
    origin: str = ""
    platforms: tuple[str, ...] = ()
    platform_mode: str = "or"
    format: str = ""
    author: str = ""
    provider: str = ""
    tags: tuple[str, ...] = ()
    tag_mode: str = "or"
    # Whether a collected copy's platform and format count, as the media list
    # does. Smart lists saved before the shared engine read the item only.
    collection_attributes: bool = True
    # Whether a season's status filter also requires its episode history to
    # agree (``filters.season_effective_status``). Home reads seasons this way;
    # deriving it needs provider metadata per season, so the long lists read
    # the stored status.
    season_effective_status: bool = False


@dataclass(frozen=True)
class SortSpec:
    """A sort key and direction. ``seed`` fixes the order of ``random``."""

    key: str = "title"
    direction: str = "asc"
    seed: int = 0


@dataclass(frozen=True)
class LibraryQuery:
    """Items of ``media_types`` that the user tracks, filtered and ordered.

    Candidates come from one scope:

    - the user's library (the default): items with a tracker row, plus
      collected-but-untracked items when ``include_collection_only``;
    - ``list_id``: one custom list's members, tracked or not;
    - ``within``: the given item ids (a queryset of ``pk`` or an iterable),
      tracked or not - for example a smart list's live matches for its owner,
      shown with another user's tracking data.

    ``union_list_ids`` adds those lists' members regardless of the filters,
    the smart-list ``list`` rule. ``sort_list_id`` is the list whose
    membership dates the ``list_added`` sort reads.
    """

    media_types: tuple[str, ...]
    filters: FilterValues = field(default_factory=FilterValues)
    sort: SortSpec = field(default_factory=SortSpec)
    list_id: int | None = None
    within: object | None = None
    sort_list_id: int | None = None
    union_list_ids: tuple[int, ...] = ()
    include_collection_only: bool = False
    include_podcast_shows: bool = False
    dedupe_cross_provider: bool = True
    routing: str = ROUTING_LIBRARY
    provider_region: str = ""
    pinned_providers: tuple[str, ...] = ()

    def with_sort(self, key: str, direction: str, seed: int = 0) -> LibraryQuery:
        """Return a copy ordered by a different key."""
        return replace(self, sort=SortSpec(key=key, direction=direction, seed=seed))
