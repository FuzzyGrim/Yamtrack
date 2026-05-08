import logging
from calendar import monthrange
from datetime import date, datetime
from http import HTTPStatus as HTTP  # noqa: N814
from urllib.parse import urlencode

from django.db.models import Count, OuterRef, Subquery
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.timezone import is_naive, localdate, make_aware
from rest_framework.response import Response

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
    Manga,
    MediaTypes,
    Movie,
    Season,
    Sources,
)
from lists.models import CustomListItem
from users.models import MediaStatusChoices

logger = logging.getLogger(__name__)

MEDIA_MODIFIABLE_FIELDS = {
    MediaTypes.MOVIE.value: {"score", "status", "start_date", "end_date", "notes"},
    MediaTypes.TV.value: {"score", "status", "notes"},
    MediaTypes.SEASON.value: {"score", "status", "notes"},
    MediaTypes.EPISODE.value: {"end_date"},
    MediaTypes.ANIME.value: {
        "score",
        "status",
        "progress",
        "start_date",
        "end_date",
        "notes",
    },
    MediaTypes.MANGA.value: {
        "score",
        "status",
        "progress",
        "start_date",
        "end_date",
        "notes",
    },
    MediaTypes.GAME.value: {
        "score",
        "status",
        "progress",
        "start_date",
        "end_date",
        "notes",
    },
    MediaTypes.BOOK.value: {
        "score",
        "status",
        "progress",
        "start_date",
        "end_date",
        "notes",
    },
    MediaTypes.COMIC.value: {
        "score",
        "status",
        "progress",
        "start_date",
        "end_date",
        "notes",
    },
    MediaTypes.BOARDGAME.value: {
        "score",
        "status",
        "progress",
        "start_date",
        "end_date",
        "notes",
    },
}

MEDIA_STATUS_MAP = {
    "Planning": 0,
    "In progress": 1,
    "Paused": 2,
    "Completed": 3,
    "Dropped": 4,
}

MEDIA_STATUS_CHOICES = [(value, label) for label, value in MEDIA_STATUS_MAP.items()]

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
    "start_date",
    "end_date",
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
    MediaTypes.MANGA.value: ["mal", "mangaupdates", "manual"],
    MediaTypes.GAME.value: ["igdb", "manual"],
    MediaTypes.BOOK.value: ["openlibrary", "hardcover", "manual"],
    MediaTypes.COMIC.value: ["comicvine", "manual"],
    MediaTypes.BOARDGAME.value: ["bgg", "manual"],
}

SOURCES_VALID_LIST = [
    Sources.TMDB.value,
    Sources.MAL.value,
    Sources.MANGAUPDATES.value,
    Sources.IGDB.value,
    Sources.OPENLIBRARY.value,
    Sources.HARDCOVER.value,
    Sources.COMICVINE.value,
    Sources.BGG.value,
]


def build_item_id(item):
    """Build the item_id string for the given item."""
    if not item:
        return None
    media_type = item.media_type
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
    lists_by_item_id = {}

    for obj in objects:
        item = obj if isinstance(obj, Item) else getattr(obj, "item", None)
        if item is None:
            continue

        if item.id in lists_by_item_id:
            continue

        lists_by_item_id[item.id] = get_item_lists(
            user,
            item.media_id,
            item.source,
            item.media_type,
            season_number=item.season_number,
            episode_number=item.episode_number,
        )

    return lists_by_item_id


def build_parent_id(item):
    """Build the parent_id string for seasons and episodes."""
    if not item:
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


def fetch_media_list(user, media_type, status, sort_filter, search):
    """Return a plain list of the requested media."""
    if media_type == MediaTypes.EPISODE.value:
        qs = Episode.objects.filter(related_season__user=user)
        if status and status != MediaStatusChoices.ALL:
            try:
                qs = qs.filter(related_season__status=status)
            except Exception:  # noqa: BLE001
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


# TODO: move to lists/models.py
def get_item_lists(
    user,
    media_id,
    source,
    media_type,
    *,
    season_number=None,
    episode_number=None,
):
    """Return list membership payload for an item following API schema."""
    item = Item.objects.filter(
        media_id=media_id,
        source=source,
        media_type=media_type,
        season_number=season_number,
        episode_number=episode_number,
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
    """Transform the media status from integer to a valid class."""
    if reverse:
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
    params = {k: v for k, v in request.GET.items() if v is not None and v != ""}
    params["limit"] = str(limit)
    params["offset"] = str(new_offset)
    return request.build_absolute_uri(request.path + "?" + urlencode(params))


def paginate_data(request, results, limit, offset, *, total=None):
    """Paginate the results based on the limit and offset.

    Returns raw paginated data without serialization.
    Serialization should be handled by the view.
    """
    total_count = len(results) if total is None else total
    start = offset
    end = offset + limit
    paginated = results[start:end]

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
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        parsed = parse_datetime(value)
        if parsed is None:
            parsed_date = parse_date(value)
            if parsed_date is None:
                msg = "Invalid date format"
                raise ValueError(msg)
            parsed = datetime.combine(parsed_date, datetime.min.time())

    if is_naive(parsed):
        parsed = make_aware(parsed)

    return parsed


def _validate_score(filtered_body):
    """Validate and convert score field."""
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
        filtered_body, error = _validate_status(filtered_body)
        if error:
            return filtered_body, error

    filtered_body, error = _validate_dates(filtered_body)
    if error:
        return filtered_body, error

    return filtered_body, error


# ---- Sorting ----


def parse_sort_filter(sort_filter):
    """Parse a sort_filter string into (field, direction) tuple."""
    if sort_filter and sort_filter != "":
        parts = sort_filter.split("_", 1)
        if len(parts) == 2:  # noqa: PLR2004
            return parts[0], parts[1]
        return parts[0], ""
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


_AGGREGATED_MANUAL_SORT_KEYS = {
    "added": lambda media: media.created_at,
    "itemid": itemid_key_compare,
    "updated": lambda media: media.progressed_at,
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
    "id": lambda media: int(media.id),
    "itemid": itemid_key_compare,
    "mediaid": _sort_mediaid,
    "progress": lambda media: int(getattr(media, "progress", 0) or 0),
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
