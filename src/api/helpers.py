import logging
from calendar import monthrange
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from http import HTTPStatus as HTTP  # noqa: N814

from django.db.models import Count, OuterRef, Subquery, prefetch_related_objects
from django.utils.dateparse import parse_date
from django.utils.timezone import localdate
from rest_framework.response import Response

from app.helpers import parse_completion_datetime, progress_activity
from app.models import (
    TV,
    Anime,
    BasicMedia,
    BoardGame,
    Book,
    Comic,
    Episode,
    Game,
    Item,
    ItemTag,
    Manga,
    MediaTypes,
    Movie,
    Season,
)
from app.services.episode_coordinates import (
    InvalidEpisodeCoordinateError,
    cleanup_episode_history_for_route,
    resolve_episode_coordinate,
)
from app.services.episode_scores import rate_episode
from lists.models import CustomListItem
from users.models import MediaStatusChoices

logger = logging.getLogger(__name__)


BODY_SLACK_BYTES = 1024 * 1024


def body_too_large_response(request, file_limit_bytes, message):
    """Return 413 when the declared body cannot hold a file within its limit.

    Call before `request.data` is touched: parsing reads the whole body, so a
    limit applied to the decoded file alone does not bound memory or time. A
    base64 body is 4/3 of the file, plus a margin for the other JSON fields.
    """
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        declared = 0
    if declared > file_limit_bytes * 4 // 3 + BODY_SLACK_BYTES:
        return Response({"detail": message}, status=HTTP.REQUEST_ENTITY_TOO_LARGE)
    return None


def resolve_episode_coordinate_for_request(
    user,
    media_id,
    source,
    season_number,
    episode_number,
    *,
    library_media_type=None,
    language=None,
):
    """Resolve an API episode coordinate and lazily remove detached history."""
    try:
        coordinate = resolve_episode_coordinate(
            media_id,
            source,
            season_number,
            episode_number,
            language=language,
        )
    except InvalidEpisodeCoordinateError:
        cleanup_episode_history_for_route(
            user,
            media_id,
            source,
            season_number,
            episode_number,
            library_media_type=library_media_type,
        )
        return None, Response(
            {"detail": "Episode not found."},
            status=HTTP.NOT_FOUND,
        )
    return coordinate, None

MEDIA_MODIFIABLE_FIELDS = {
    MediaTypes.MOVIE.value: {
        "score", "status", "start_date", "end_date", "notes", "entry_source",
    },
    MediaTypes.TV.value: {"score", "status", "notes", "entry_source"},
    MediaTypes.SEASON.value: {"score", "status", "notes", "entry_source"},
    MediaTypes.EPISODE.value: {"end_date", "entry_source"},
    MediaTypes.ANIME.value: {
        "score",
        "status",
        "progress",
        "start_date",
        "end_date",
        "notes",
        "entry_source",
    },
    MediaTypes.MANGA.value: {
        "score",
        "status",
        "progress",
        "start_date",
        "end_date",
        "notes",
        "entry_source",
    },
    MediaTypes.GAME.value: {
        "score",
        "status",
        "progress",
        "start_date",
        "end_date",
        "notes",
        "entry_source",
    },
    MediaTypes.BOOK.value: {
        "score",
        "status",
        "progress",
        "start_date",
        "end_date",
        "notes",
        "entry_source",
    },
    MediaTypes.COMIC.value: {
        "score",
        "status",
        "progress",
        "start_date",
        "end_date",
        "notes",
        "entry_source",
    },
    MediaTypes.BOARDGAME.value: {
        "score",
        "status",
        "progress",
        "start_date",
        "end_date",
        "notes",
        "entry_source",
    },
}

MEDIA_STATUS_MAP = {
    "Planning": 0,
    "In progress": 1,
    "Paused": 2,
    "Completed": 3,
    "Dropped": 4,
}

MEDIA_TYPE_COMPLETE_MODEL_MAP = {
    MediaTypes.TV.value: TV,
    MediaTypes.SEASON.value: Season,
    MediaTypes.EPISODE.value: Episode,
    MediaTypes.MOVIE.value: Movie,
    MediaTypes.ANIME.value: Anime,
    MediaTypes.MANGA.value: Manga,
    MediaTypes.GAME.value: Game,
    MediaTypes.BOOK.value: Book,
    MediaTypes.COMIC.value: Comic,
    MediaTypes.BOARDGAME.value: BoardGame,
}

MEDIA_TYPE_COMPLETE_VALID_LIST = list(MEDIA_TYPE_COMPLETE_MODEL_MAP.keys())

MEDIA_TYPE_MODEL_MAP = {
    MediaTypes.TV.value: TV,
    MediaTypes.MOVIE.value: Movie,
    MediaTypes.ANIME.value: Anime,
    MediaTypes.MANGA.value: Manga,
    MediaTypes.GAME.value: Game,
    MediaTypes.BOOK.value: Book,
    MediaTypes.COMIC.value: Comic,
    MediaTypes.BOARDGAME.value: BoardGame,
}

MEDIA_TYPE_VALID_LIST = list(MEDIA_TYPE_MODEL_MAP.keys())

MAX_RESULT_LIMIT = 200


MEDIA_EXISTING_SORTS = [
    "score",
    "start_date",
    "end_date",
    "last_watched",
] + [f.name for f in Item._meta.fields]

MEDIA_SEASONS_ADDITIONAL_SORTS = [
    "progress",
]

MEDIA_EPISODES_ADDITIONAL_SORTS = [
    "progress",
]

MEDIA_MANUAL_SORTS = [
    "added",
    "updated",
    "itemid",
]

LIST_SORTS = [
    "items",
    "name",
    "new",
    "update",
]

VALID_SOURCES = {
    MediaTypes.TV.value: ["tmdb", "manual"],
    MediaTypes.SEASON.value: ["tmdb", "manual"],
    MediaTypes.EPISODE.value: ["tmdb", "manual"],
    MediaTypes.MOVIE.value: ["tmdb", "manual"],
    MediaTypes.ANIME.value: ["mal", "manual"],
    MediaTypes.MANGA.value: ["mal", "mangaupdates", "mangabaka", "manual"],
    MediaTypes.GAME.value: ["igdb", "manual"],
    MediaTypes.BOOK.value: ["openlibrary", "hardcover", "googlebooks", "manual"],
    MediaTypes.COMIC.value: ["comicvine", "gcd", "manual"],
    MediaTypes.BOARDGAME.value: ["bgg", "manual"],
}


def build_item_id(item):
    """Build the item_id string for the given item."""
    if not item:
        return None
    media_type = getattr(item, "media_type", None)
    if media_type is None:
        return None
    children = ""

    if item.media_type == "season":
        children = f"/{item.season_number}"
        media_type = "tv"
    elif item.media_type == "episode":
        children = f"/{item.season_number}/{item.episode_number}"
        media_type = "tv"

    return f"{media_type}/{item.source}/{item.media_id}{children}"


# TODO: move to lists/models.py
def build_lists_by_item_id(user, objects):
    """Build a map of item id to list membership payload for serializer context."""
    if user is None:
        return {}

    item_ids = []
    seen_item_ids = set()
    for obj in objects:
        item = obj if isinstance(obj, Item) else getattr(obj, "item", None)
        if item is None or item.id in seen_item_ids:
            continue
        seen_item_ids.add(item.id)
        item_ids.append(item.id)

    # FORK: batched by item id in one query instead of one query per item —
    # a page of N media entries used to issue N CustomListItem queries.
    return CustomListItem.objects.get_user_item_lists_map(user, item_ids)


def build_tags_by_item_id(user, objects):
    """Map item id to the user's tag names for it, in one query, for a page of rows."""
    item_ids = {
        (obj if isinstance(obj, Item) else getattr(obj, "item", None)).id
        for obj in objects
        if (obj if isinstance(obj, Item) else getattr(obj, "item", None)) is not None
    }
    tags = defaultdict(list)
    if user is None or not item_ids:
        return tags
    rows = ItemTag.objects.filter(item_id__in=item_ids, tag__user=user).values_list(
        "item_id", "tag__name"
    )
    for item_id, name in sorted(rows, key=lambda row: row[1].casefold()):
        tags[item_id].append(name)
    return tags


def build_parent_id(item):
    """Build the parent_id string for seasons and episodes."""
    if not item or getattr(item, "media_type", None) is None:
        return None
    if item.media_type == "season":
        return f"tv/{item.source}/{item.media_id}"
    if item.media_type == "episode" and hasattr(item, "season_number"):
        return f"tv/{item.source}/{item.media_id}/{item.season_number}"
    return None


def check_valid_type(media_type, *, complete=False):
    """Check if the media type is valid."""
    if complete:
        return media_type in MEDIA_TYPE_COMPLETE_VALID_LIST
    return media_type in MEDIA_TYPE_VALID_LIST


def check_source_type(media_type, source):
    """Check the source is valid for the given media type."""
    if media_type in VALID_SOURCES:
        return source in VALID_SOURCES[media_type]
    return False


# Pairs of media types that can represent the same underlying show/library
# (e.g. a TV series tracked under the "anime" bucket instead of "tv").
_ALTERNATE_LIBRARY_TYPE = {"anime": "tv", "tv": "anime"}


def get_media_type_availability(user, media_type):
    """Report whether media_type is enabled for user, with a redirect hint.

    Lets callers (notably automated agents) see, at the point they're about
    to act, whether the media type they're browsing is disabled for this
    user -- and if so, whether the same content is likely tracked under a
    different, enabled media type instead.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return {"media_type": media_type, "enabled": True, "message": None}

    enabled = getattr(user, f"{media_type}_enabled", True)
    message = None
    if not enabled:
        message = (
            f"{media_type.capitalize()} tracking is disabled in your account "
            "settings."
        )
        alt = _ALTERNATE_LIBRARY_TYPE.get(media_type)
        if alt and getattr(user, f"{alt}_enabled", True):
            message += (
                f" This title may also be available under '{alt}' -- "
                f"consider searching or logging it there instead."
            )
    return {"media_type": media_type, "enabled": enabled, "message": message}


def fetch_media_list(user, media_type, status, sort_filter, search):
    """Return a plain list of the requested media."""
    if media_type == MediaTypes.EPISODE.value:
        qs = Episode.objects.filter(related_season__user=user)
        if status and status != MediaStatusChoices.ALL:
            try:
                qs = qs.filter(related_season__status=status)
            except Exception:
                return []
        if search:
            qs = qs.filter(item__title__icontains=search)
        return qs

    return list(
        BasicMedia.objects.get_media_list(
            user=user,
            media_type=media_type,
            status_filter=status,
            sort_filter=sort_filter,
            search=search,
        ),
    )


def fetch_results_for_type(user, media_type, status, sort, search):
    """Fetch and sort results for a specific media type."""
    sort_list = get_sorts(media_type, sort_type="existing")
    media_sort = sort if sort in sort_list else ""
    already_sorted = bool(media_sort)

    results = fetch_media_list(user, media_type, status, media_sort, search)

    if not already_sorted and sort:
        if sort in get_sorts(media_type, sort_type="manual"):
            results = apply_manual_sort_for_type(results, sort)
        else:
            return None, True

    return results, False


def fetch_results_all_types(user, status, sort, search, exclude):
    """Fetch and sort results across all media types."""
    excluded_set = {e.strip().lower() for e in exclude if e and e.strip()}
    allowed_types = [t for t in MEDIA_TYPE_VALID_LIST if t not in excluded_set]

    results = []
    for t in allowed_types:
        results.extend(fetch_media_list(user, t, status, "", search))

    if sort:
        sort_list = get_sorts(None, sort_type="all")
        if sort in sort_list:
            results = apply_aggregated_sort(results, sort)
        else:
            return None, True

    return results, False


# FORK: Item rows are bucketed by library_media_type — the same show/season can
# have two rows differing only in that field (grouped anime stored on TV rows).
# Raw get()/get_or_create()/first() on media_id+source+media_type alone can raise
# MultipleObjectsReturned or silently pick/clobber the wrong bucket's row. This
# filter mirrors the fork's canonical pattern (app/metadata_sync_views.py):
# explicit bucket filter when requested, exclude the anime bucket by default for
# tv/season/episode, and deterministic ordering.
def filter_item_bucket(queryset, media_type, *, library_media_type=None):  # FORK
    """Apply bucket filtering to an Item queryset for the given media_type."""
    if media_type in (
        MediaTypes.TV.value,
        MediaTypes.SEASON.value,
        MediaTypes.EPISODE.value,
    ):
        if library_media_type:
            queryset = queryset.filter(library_media_type=library_media_type)
        else:
            queryset = queryset.exclude(
                library_media_type=MediaTypes.ANIME.value,
            )
    return queryset


def resolve_item_queryset(  # FORK
    media_id,
    source,
    media_type,
    *,
    season_number=None,
    episode_number=None,
    library_media_type=None,
):
    """Return a deterministic, bucket-aware Item queryset."""
    queryset = Item.objects.filter(
        media_id=media_id,
        source=source,
        media_type=media_type,
        season_number=season_number,
        episode_number=episode_number,
    )
    queryset = filter_item_bucket(
        queryset,
        media_type,
        library_media_type=library_media_type,
    )
    return queryset.order_by("id")


# TODO: move to lists/models.py
def get_item_lists(
    user,
    media_id,
    source,
    media_type,
    *,
    season_number=None,
    episode_number=None,
    library_media_type=None,
):
    """Return list membership payload for an item following API schema."""
    # FORK: bucket-aware, deterministic item resolution.
    item = resolve_item_queryset(
        media_id,
        source,
        media_type,
        season_number=season_number,
        episode_number=episode_number,
        library_media_type=library_media_type,
    ).first()

    if item is None or user is None:
        return []

    return CustomListItem.objects.get_user_item_lists(user, item)


def get_sorts(media_type, *, sort_type="all"):
    """Return the list of valid sorts for complete media types."""
    if sort_type == "all":
        sort_list = MEDIA_EXISTING_SORTS.copy()
        if media_type == MediaTypes.SEASON.value:
            sort_list += MEDIA_SEASONS_ADDITIONAL_SORTS
        if media_type == MediaTypes.EPISODE.value:
            sort_list += MEDIA_EPISODES_ADDITIONAL_SORTS
        sort_list += MEDIA_MANUAL_SORTS
        return sort_list
    if sort_type == "manual":
        return MEDIA_MANUAL_SORTS
    if sort_type == "existing":
        sort_list = MEDIA_EXISTING_SORTS.copy()
        if media_type == MediaTypes.SEASON.value:
            sort_list += MEDIA_SEASONS_ADDITIONAL_SORTS
        if media_type == MediaTypes.EPISODE.value:
            sort_list += MEDIA_EPISODES_ADDITIONAL_SORTS
        return sort_list
    return []


def get_media_status(status, *, reverse=False):
    """Transform the media status between its integer code and label."""
    if reverse:
        if isinstance(status, str):
            stripped = status.strip()
            if stripped.lstrip("-").isdigit():
                status = int(stripped)
            else:
                normalized = stripped.lower()
                for label in MEDIA_STATUS_MAP:
                    if label.lower() == normalized:
                        return label
                return None
        reverse_map = {v: k for k, v in MEDIA_STATUS_MAP.items()}
        return reverse_map.get(status)
    return MEDIA_STATUS_MAP.get(status)


def get_progress_from_status(status):
    """Return the progress value based on the media status."""
    if status == MEDIA_STATUS_MAP["Completed"]:
        return 1
    return 0


def make_page_url(request, limit, new_offset):
    """Build a page URL with the given limit and offset."""
    params = request.GET.copy()
    params["limit"] = str(limit)
    params["offset"] = str(new_offset)
    return request.build_absolute_uri(request.path + "?" + params.urlencode())


def paginate_data(request, results, limit, offset, *, total=None, already_sliced=False):
    """Paginate the results based on the limit and offset.

    Returns raw paginated data without serialization.
    Serialization should be handled by the view.

    `already_sliced=True` skips the results[offset:offset+limit] slice —
    for callers (the media-list SQL fast path, #1004) whose `results` were
    already paginated at the database layer, where re-slicing by `offset`
    against an already-page-sized list would wrongly return nothing.
    `total` must be passed alongside it, since len(results) is no longer
    the true total in that case.
    """
    total_count = len(results) if total is None else total
    start = offset
    end = offset + limit
    paginated = results if already_sliced else results[start:end]

    next_url = None
    prev_url = None
    if end < total_count:
        next_url = make_page_url(request, limit, end)
    if start > 0:
        prev_offset = max(0, start - limit)
        prev_url = make_page_url(request, limit, prev_offset)

    pagination = {
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "next": next_url,
        "previous": prev_url,
    }
    return {"pagination": pagination, "results": paginated}


def paginate_list_items(request, user, user_list):
    """Return one page of a custom list's items as media, and any error.

    Returns ``(paginated_data, error_response)``; exactly one is not None.

    Only the requested page is hydrated when the caller has not asked for an
    aggregated sort, because the database ordering is then already the response
    ordering. Hydrating the whole list first meant one media lookup per item to
    return twenty of them - on a 4,683-item list, 4,683 queries and 4,683
    hydrated objects per request.

    An aggregated sort still has to rank every item before it can say which
    ones are on the page, so that path is unchanged.
    """
    items = user_list.items.order_by(
        "customlistitem__date_added",
        "customlistitem__pk",
    )

    search_query = request.GET.get("search", "")
    if search_query:
        items = items.filter(title__icontains=search_query)

    # FORK: server-side media type filter for native list pages.
    media_type_filter = request.GET.get("media_type", "")
    if media_type_filter:
        items = items.filter(media_type=media_type_filter)

    limit, offset, err = parse_limit_offset(request)
    if err:
        return None, err

    sort = sort_order = None
    sort_filter = request.GET.get("sort", "")
    if sort_filter:
        sort, sort_order = parse_sort_filter(sort_filter)
        if sort not in get_sorts(None, sort_type="all"):
            return None, Response(
                {"detail": "Invalid sorting"},
                status=HTTP.NOT_FOUND,
            )

    total = None
    if sort is None:
        # No search/filter narrows ``items`` here, so count memberships without
        # joining app_item.
        total = (
            items.count()
            if (search_query or media_type_filter)
            else user_list.customlistitem_set.count()
        )
        items = items[offset : offset + limit]

    items = list(items)
    from app.services.music import album_trackers_for_list_items
    from app.services.podcast_list_tracking import podcast_show_trackers_for_items

    show_trackers = podcast_show_trackers_for_items(user, items)
    album_trackers = album_trackers_for_list_items(user, items)
    media_objects = []
    prefetch_groups = {}
    for item in items:
        if item.pk in album_trackers:
            media_objects.append(album_trackers[item.pk])
            continue
        if item.pk in show_trackers:
            media_objects.append(show_trackers[item.pk])
            continue
        # Shows info about the last consumption of the media if it's tracked
        queryset = BasicMedia.objects.filter_media_prefetch(
            user,
            item.media_id,
            item.media_type,
            item.source,
            season_number=item.season_number,
            episode_number=item.episode_number,
            annotate_progress=False,
        )
        # The prefetches run once per media type below instead of once per
        # item (four extra queries each): same lookups, one batched query each.
        lookups = queryset._prefetch_related_lookups
        media = queryset.prefetch_related(None).first()
        if media is not None:
            group = prefetch_groups.setdefault(
                (type(media), item.media_type),
                (lookups, []),
            )
            group[1].append(media)

        media_objects.append(media if media is not None else item)

    for lookups, group_media in prefetch_groups.values():
        if lookups:
            prefetch_related_objects(group_media, *lookups)

    BasicMedia.objects.annotate_episode_progress(
        [media for media in media_objects if getattr(media, "item", None) is not None],
    )

    if sort is None:
        return paginate_data(
            request,
            media_objects,
            limit,
            offset,
            total=total,
            already_sliced=True,
        ), None

    media_objects = apply_aggregated_sort(media_objects, sort)
    if isinstance(media_objects, Response):
        return None, media_objects
    if sort_order == "desc":
        media_objects.reverse()

    return paginate_data(request, media_objects, limit, offset), None


def parse_excluded_items(request):
    """Parse excluded items from the request query parameters."""
    exclude_param = request.GET.get("exclude", "")
    if exclude_param:
        return exclude_param.split(",")
    return []


def parse_limit_offset(request):
    """Parse and validate limit/offset query params.

    If no error, error_response is None. On validation error, returns a DRF Response.
    """
    raw_limit = request.GET.get("limit")
    raw_offset = request.GET.get("offset")

    if raw_limit in [None, ""]:
        limit = 20
    else:
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return (
                None,
                None,
                Response(
                    {"detail": "Invalid limit parameter"},
                    status=HTTP.BAD_REQUEST,
                ),
            )
    if raw_offset in [None, ""]:
        offset = 0
    else:
        try:
            offset = int(raw_offset)
        except (TypeError, ValueError):
            return (
                None,
                None,
                Response(
                    {"detail": "Invalid offset parameter"},
                    status=HTTP.BAD_REQUEST,
                ),
            )
    if limit <= 0 or offset < 0:
        # TODO: use raise instead of returning the error
        return (
            None,
            None,
            Response(
                {
                    "detail": "limit must be >0 and offset must be >=0",
                },
                status=HTTP.BAD_REQUEST,
            ),
        )

    limit = min(limit, MAX_RESULT_LIMIT)
    return limit, offset, None


def parse_max_entries_per_day(request):
    """Parse and validate the optional /history max_entries_per_day override.

    Returns `(value, error_response)`; `value` is None (caller falls back to
    the HISTORY_ENTRIES_PER_DAY_PAGE default) when the param is absent.
    Clamped to MAX_RESULT_LIMIT — the same ceiling `parse_limit_offset` uses
    — so this can't reintroduce the unbounded per-day response #1004 fixed.
    """
    raw_value = request.GET.get("max_entries_per_day")
    if raw_value in (None, ""):
        return None, None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None, Response(
            {"detail": "Invalid max_entries_per_day parameter"},
            status=HTTP.BAD_REQUEST,
        )
    if value <= 0:
        return None, Response(
            {"detail": "max_entries_per_day must be >0"},
            status=HTTP.BAD_REQUEST,
        )
    return min(value, MAX_RESULT_LIMIT), None


def parse_status_param(status):
    """Parse and validate status parameter."""
    if not status:
        return MediaStatusChoices.ALL
    try:
        return get_media_status(int(status), reverse=True)
    except (TypeError, ValueError):
        return None


def get_month_range(year, month):
    """Return the first and last day for a given month."""
    first_day = date(year, month, 1)
    last_day = date(year, month, monthrange(year, month)[1])
    return first_day, last_day


def get_current_month_range():
    """Return the date range for the current month."""
    current = localdate()
    return get_month_range(current.year, current.month)


def resolve_calendar_date_range(start_date, end_date, month_q, year_q):
    """Resolve calendar query params into a concrete (first_day, last_day) tuple."""
    if start_date or end_date:
        parsed_start = try_parse_date(start_date) if start_date else None
        parsed_end = try_parse_date(end_date) if end_date else None

        first_day = parsed_start or date(1970, 1, 1)
        if parsed_end:
            return first_day, parsed_end
        if parsed_start:
            _, last_day = get_month_range(parsed_start.year, parsed_start.month)
            return first_day, last_day
        return first_day, get_current_month_range()[1]

    if not year_q:
        return get_current_month_range()

    year = int(year_q)
    if not month_q:
        return date(year, 1, 1), date(year, 12, 31)

    month = int(month_q)
    return get_month_range(year, month)


def try_parse_date(value):
    """Parse a date string and raise ValueError if invalid."""
    parsed = parse_date(value)
    if not parsed:
        msg = "Invalid date format"
        raise ValueError(msg)
    return parsed


def try_parse_datetime_input(value):
    """Parse an ISO date or datetime value for writable DateTimeField inputs."""
    return parse_completion_datetime(value)


def _validate_score(filtered_body):
    """Validate and convert score field."""
    if filtered_body["score"] is None:
        return filtered_body, None
    try:
        score_value = float(filtered_body["score"])
        if score_value < 0 or score_value > 10:  # noqa: PLR2004
            return None, "Score must be between 0 and 10."
        filtered_body["score"] = score_value
    except (TypeError, ValueError):
        return None, "Invalid score value."

    return filtered_body, None


def _validate_status(filtered_body):
    """Validate and convert status field."""
    status_value = get_media_status(filtered_body["status"], reverse=True)
    if status_value is None:
        return None, "Invalid status value."
    filtered_body["status"] = status_value
    return filtered_body, None


def _validate_dates(filtered_body):
    """Validate and convert date fields."""
    if "start_date" in filtered_body:
        start_date = filtered_body["start_date"]
        if start_date in (None, ""):
            filtered_body["start_date"] = None
        else:
            try:
                filtered_body["start_date"] = try_parse_datetime_input(start_date)
            except (TypeError, ValueError):
                return None, "Invalid start_date format."

    if "end_date" in filtered_body:
        end_date = filtered_body["end_date"]
        if end_date in (None, ""):
            filtered_body["end_date"] = None
        else:
            try:
                filtered_body["end_date"] = try_parse_datetime_input(end_date)
            except (TypeError, ValueError):
                return None, "Invalid end_date format."

    return filtered_body, None


def apply_image_url(item, image_url):
    """Set an item's image from a client-supplied URL, if it changed."""
    if image_url and item.image != image_url:
        item.image = image_url
        item.save(update_fields=["image"])


def validate_body(body, media_type):
    """Validate and filter the request body for media updates."""
    allowed_fields = MEDIA_MODIFIABLE_FIELDS.get(media_type, set())
    filtered_body = {k: v for k, v in body.items() if k in allowed_fields}

    if not filtered_body:
        return filtered_body, "No valid fields to update."

    if "score" in filtered_body:
        filtered_body, error = _validate_score(filtered_body)
        if error:
            return filtered_body, error

    if "status" in filtered_body:
        if media_type == MediaTypes.COMIC_ISSUE.value and filtered_body["status"] in (None, ""):
            # ComicissueForm permits an explicitly statusless issue.
            filtered_body["status"] = None
        else:
            filtered_body, error = _validate_status(filtered_body)
            if error:
                return filtered_body, error

    filtered_body, error = _validate_dates(filtered_body)
    if error:
        return filtered_body, error

    return filtered_body, error


def unsupported_fields(record, validated_body):
    """Return requested fields the record has no column for.

    The exact-history records store fewer fields than the media row they hang
    off (a movie play has no notes or score), so writing them would be silently
    dropped while the request reported success (#1873).
    """
    return sorted(field for field in validated_body if not hasattr(record, field))


# ---- Sorting ----


def parse_sort_filter(sort_filter):
    """Parse a sort_filter string into (field, direction) tuple."""
    if sort_filter:
        for direction in ("asc", "desc"):
            suffix = f"_{direction}"
            if sort_filter.endswith(suffix):
                return sort_filter.removesuffix(suffix), direction
        return sort_filter, ""
    return "", ""


def itemid_key_compare(media):
    """Key function for sorting by item_id."""
    item = getattr(media, "item", media)
    media_type = getattr(item, "media_type", "")
    source = getattr(item, "source", "")
    media_id_raw = getattr(item, "media_id", "")
    media_id_key = str(media_id_raw).lower() if media_id_raw is not None else ""
    return (media_type, source, media_id_key)


def _item_from_result(media):
    """Return Item object from a media result or the result itself if it already is."""
    return getattr(media, "item", media)


def _sort_nullable(value):
    """Build sortable tuple handling null values consistently."""
    return (value is None, value)


def _sort_source(media):
    """Return source key from both media and item objects."""
    item = _item_from_result(media)
    return getattr(item, "source", "")


def _sort_mediaid(media):
    """Return item database id from both media and item objects."""
    item = _item_from_result(media)
    return getattr(item, "id", 0)


def _sort_title(media):
    """Return lowercased title key from both media and item objects."""
    item = _item_from_result(media)
    title = getattr(item, "title", "")
    return title.lower() if isinstance(title, str) else ""


def _sort_type(media):
    """Return media type key from both media and item objects."""
    item = _item_from_result(media)
    return getattr(item, "media_type", "")


# FORK: null-safe access — the fork's Episode model has no progressed_at (and
# history rows may lack created_at), so raw attribute access 500s on episode
# lists. Fall back through end_date and sort None values consistently.
_AGGREGATED_MANUAL_SORT_KEYS = {
    "added": lambda media: _sort_nullable(getattr(media, "created_at", None)),
    "itemid": itemid_key_compare,
    "updated": lambda media: _sort_nullable(
        getattr(media, "progressed_at", None) or getattr(media, "end_date", None),
    ),
}


def apply_manual_sort_for_type(results, sort):
    """Apply manual sorts used when a single media type is requested."""
    if sort not in _AGGREGATED_MANUAL_SORT_KEYS:
        return Response(
            {"detail": "Invalid sorting"},
            status=HTTP.BAD_REQUEST,
        )
    results.sort(key=_AGGREGATED_MANUAL_SORT_KEYS[sort])
    return results


_AGGREGATED_SORT_KEYS = {
    "added": lambda media: _sort_nullable(getattr(media, "created_at", None)),
    "ended": lambda media: _sort_nullable(getattr(media, "end_date", None)),
    "last_watched": lambda media: _sort_nullable(
        getattr(media, "aggregated_last_watched", None)
        or getattr(media, "end_date", None)
        or progress_activity(media),
    ),
    "id": lambda media: int(media.id),
    "itemid": itemid_key_compare,
    "mediaid": _sort_mediaid,
    "progress": lambda media: int(getattr(media, "progress", 0) or 0),
    "score": lambda media: _sort_nullable(
        getattr(media, "aggregated_score", getattr(media, "score", None)),
    ),
    "release_datetime": lambda media: _sort_nullable(
        getattr(_item_from_result(media), "release_datetime", None),
    ),
    "source": _sort_source,
    "started": lambda media: _sort_nullable(getattr(media, "start_date", None)),
    "title": _sort_title,
    "type": _sort_type,
    "updated": lambda media: _sort_nullable(getattr(media, "progressed_at", None)),
}


def apply_aggregated_sort(results, sort):
    """Apply sorting for the aggregated (multi-type) results."""
    if sort not in _AGGREGATED_SORT_KEYS:
        return Response(
            {"detail": "Invalid sorting"},
            status=HTTP.BAD_REQUEST,
        )
    results.sort(key=_AGGREGATED_SORT_KEYS[sort])
    return results


def apply_list_sort(queryset, sort, sort_order):
    """Apply sorting to a List."""
    if not sort:
        return queryset

    if sort not in LIST_SORTS:
        return None

    sort = "id" if sort == "new" else sort

    ordering = ("-" if sort_order == "desc" else "") + sort

    if sort == "update":
        return queryset.annotate(
            update=Subquery(
                CustomListItem.objects.filter(
                    custom_list=OuterRef("pk"),
                )
                .order_by("-date_added")
                .values("date_added")[:1],
            ),
        ).order_by(ordering, "name")

    if sort == "items":
        items_ordering = "-items_count" if sort_order == "desc" else "items_count"
        return queryset.annotate(
            items_count=Count("items", distinct=True),
        ).order_by(items_ordering, "name")

    return queryset.order_by(ordering)


def build_game_lengths_summary(payload):
    """Trim a persisted provider_game_lengths payload to a search-result summary."""
    if not payload:
        return None

    summary = {"active_source": payload.get("active_source") or ""}

    hltb = payload.get("hltb")
    if isinstance(hltb, dict) and isinstance(hltb.get("summary"), dict):
        summary["hltb_summary"] = hltb["summary"]

    igdb = payload.get("igdb")
    if isinstance(igdb, dict) and isinstance(igdb.get("summary"), dict):
        summary["igdb_summary"] = igdb["summary"]

    if "hltb_summary" not in summary and "igdb_summary" not in summary:
        return None
    return summary


def get_tracked_season(
    user,
    media_id,
    source,
    season_number,
    library_media_type=None,
):
    """Return the user's tracked Season row for a show/season, or None.

    ``library_media_type`` limits the match to one library bucket; without it
    the oldest matching Season is returned.
    """
    seasons = Season.objects.filter(
        item__media_id=media_id,
        item__source=source,
        item__season_number=season_number,
        item__episode_number=None,
        user=user,
    )
    if library_media_type:
        seasons = seasons.filter(item__library_media_type=library_media_type)
    return seasons.order_by("id").first()


def validate_episode_score(raw_score):
    """Parse and range-check a 0-10 episode score.

    Returns (score, None) on success, where score is a Decimal or None
    (explicit clear); returns (None, error_response) on failure.
    """
    if raw_score is None:
        return None, None
    try:
        score = Decimal(str(raw_score))
    except (InvalidOperation, TypeError, ValueError):
        return None, Response({"detail": "Invalid score."}, status=HTTP.BAD_REQUEST)
    if not (Decimal(0) <= score <= Decimal(10)):
        return None, Response(
            {"detail": "Score must be between 0 and 10."},
            status=HTTP.BAD_REQUEST,
        )
    return score, None


def apply_episode_score(season, episode_number, score):
    """Set `score` on a tracked episode within `season`.

    An episode with no play gets a rating-only row (not a watch). Returns True
    if a rating was stored or changed, False when there was nothing to clear.
    """
    return rate_episode(season, episode_number, score)
