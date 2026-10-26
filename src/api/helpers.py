import calendar
import logging
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime
from http import HTTPStatus as HTTP  # noqa: N814
from urllib.parse import urlencode

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Count, OuterRef, Subquery
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.timezone import is_naive, localdate, make_aware
from rest_framework.exceptions import ParseError, ValidationError
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
from users.models import MediaSortChoices, MediaStatusChoices

logger = logging.getLogger(__name__)


class InvalidSortError(ValueError):
    """Raised when a requested sort is not supported."""


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


SORT_DIRECTIONS = ("asc", "desc")

MEDIA_EXISTING_SORTS = [
    *MediaSortChoices.values,
    "id",
    "media_id",
    "media_type",
    "source",
    "season_number",
    "episode_number",
]

MEDIA_ASCENDING_SORTS = {
    MediaSortChoices.TITLE.value,
    MediaSortChoices.START_DATE.value,
}

MEDIA_MANUAL_SORTS = [
    "added",
    "updated",
    "itemid",
]

EPISODE_SORTS = [
    "added",
    "end_date",
    "episode_number",
    "id",
    "itemid",
    "season_number",
    "title",
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

SOURCES_COMPLETE_VALID_LIST = [*SOURCES_VALID_LIST, "manual"]


def resolve_collaborators(collaborator_ids):
    """Return the users for the given collaborator ids.

    Duplicated ids are ignored. Raise a ValidationError if any id doesn't match
    an existing user.
    """
    unique_ids = set(collaborator_ids)
    collaborators = get_user_model().objects.filter(id__in=unique_ids)
    if collaborators.count() != len(unique_ids):
        raise ValidationError(
            {"collaborators": ["One or more collaborator IDs are invalid."]},
        )
    return collaborators


def error_response(detail, status, exc=None):
    """Build an API error response for a handled exception.

    The exception message is returned under `debug` only when DEBUG is enabled,
    server errors are always logged.
    """
    if exc is not None and status >= HTTP.INTERNAL_SERVER_ERROR:
        logger.error("%s", detail, exc_info=exc)

    data = {"detail": detail}
    if exc is not None and settings.DEBUG:
        data["debug"] = str(exc)
    return Response(data, status=status)


def require_update_fields(validated_data):
    """Raise a ParseError if the request has no updatable fields.

    Unknown fields are dropped by the serializers, so an empty validated_data
    means that the request has nothing to update.
    """
    if not validated_data:
        msg = "No valid fields to update."
        raise ParseError(msg)


def apply_validated_fields(instance, validated_data):
    """Set the validated request fields on a model instance.

    A null value clears a nullable field. Raise a ValidationError for fields that
    are not editable on the instance or that can't be null.
    """
    errors = {}
    for field, value in validated_data.items():
        try:
            model_field = instance._meta.get_field(field)
        except FieldDoesNotExist:
            errors[field] = ["This field can't be updated for this media type."]
            continue
        if value is None and not model_field.null:
            errors[field] = ["This field may not be null."]
            continue
        setattr(instance, field, value)

    if errors:
        raise ValidationError(errors)


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


def build_lists_by_item_id(user, objects):
    """Build a map of item id to list membership payload for serializer context.

    Items that aren't in any list are missing from the map. The memberships of
    all the objects are fetched with a single query.
    """
    item_ids = set()
    for obj in objects:
        item = obj if isinstance(obj, Item) else getattr(obj, "item", None)
        if item is not None:
            item_ids.add(item.id)

    return CustomListItem.objects.get_user_item_lists_map(user, item_ids)


def get_lists_by_number(user, media_id, source, numbers, *, season_number=None):
    """Return a map of season or episode number to its list memberships.

    Without `season_number` the numbers are season numbers of the show, otherwise
    they are episode numbers of that season. Numbers without lists are missing.
    """
    numbers = [number for number in numbers if number is not None]
    if not numbers:
        return {}

    if season_number is None:
        items = Item.objects.filter(
            media_type=MediaTypes.SEASON.value,
            season_number__in=numbers,
            episode_number__isnull=True,
        )
        number_field = "season_number"
    else:
        items = Item.objects.filter(
            media_type=MediaTypes.EPISODE.value,
            season_number=season_number,
            episode_number__in=numbers,
        )
        number_field = "episode_number"

    number_by_item_id = dict(
        items.filter(media_id=media_id, source=source).values_list("id", number_field),
    )
    lists_by_item_id = CustomListItem.objects.get_user_item_lists_map(
        user,
        list(number_by_item_id),
    )
    return {
        number_by_item_id[item_id]: lists for item_id, lists in lists_by_item_id.items()
    }


def get_latest_medias_by_item_id(user, items):
    """Return a map of item id to the user media shown for that item.

    It's equivalent to calling `BasicMedia.objects.filter_media_prefetch(...)
    .first()` for each item, but runs one query (plus prefetches) per media type
    instead of several queries per item. Untracked items are missing from the map.
    """
    items_by_type = {}
    for item in items:
        items_by_type.setdefault(item.media_type, []).append(item)

    medias_by_item_id = {}
    for media_type, type_items in items_by_type.items():
        model = MEDIA_TYPE_COMPLETE_MODEL_MAP.get(media_type)
        if model is None:
            continue

        user_filter = (
            {"related_season__user": user}
            if media_type == MediaTypes.EPISODE.value
            else {"user": user}
        )
        queryset = (
            model.objects.filter(item__in=type_items, **user_filter)
            .select_related("item")
            # Same order used by `.first()`
            .order_by(*(model._meta.ordering or ["pk"]))
        )
        queryset = BasicMedia.objects._apply_prefetch_related(queryset, media_type)

        for media in queryset:
            medias_by_item_id.setdefault(media.item_id, media)

    return medias_by_item_id


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
        qs = Episode.objects.filter(related_season__user=user).select_related("item")
        if status and status != MediaStatusChoices.ALL:
            qs = qs.filter(related_season__status=status)
        if search:
            qs = qs.filter(item__title__icontains=search)
        return list(qs)

    return list(
        BasicMedia.objects.get_media_list(
            user=user,
            media_type=media_type,
            status_filter=status,
            sort_filter=sort_filter,
            search=search,
        ),
    )


def fetch_results_for_type(user, media_type, status, sort, direction, search):
    """Fetch and sort results for a specific media type.

    Raise InvalidSortError if the sort is not supported for the media type.
    """
    if sort and sort not in get_sorts(media_type):
        raise InvalidSortError

    db_sort = (
        sort
        if sort in MEDIA_EXISTING_SORTS and media_type != MediaTypes.EPISODE.value
        else ""
    )
    results = fetch_media_list(user, media_type, status, db_sort, search)

    if db_sort:
        db_descending = db_sort not in MEDIA_ASCENDING_SORTS
        if db_descending != (direction == "desc"):
            results.reverse()
        return results

    if sort:
        return apply_aggregated_sort(results, sort, direction)
    return results


def fetch_results_all_types(user, status, sort, direction, search, exclude):
    """Fetch and sort results across all media types.

    Raise InvalidSortError if the sort is not supported.
    """
    if sort and sort not in get_sorts(None):
        raise InvalidSortError

    excluded_set = {e.strip().lower() for e in exclude if e and e.strip()}
    allowed_types = [t for t in MEDIA_TYPE_VALID_LIST if t not in excluded_set]

    results = []
    for t in allowed_types:
        results.extend(fetch_media_list(user, t, status, "", search))

    if sort:
        return apply_aggregated_sort(results, sort, direction)
    return results


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


def get_sorts(media_type=None):
    """Return the valid sorts for a media type, or for mixed media if None."""
    if media_type is None:
        return list(_AGGREGATED_SORT_KEYS)
    if media_type == MediaTypes.EPISODE.value:
        return EPISODE_SORTS.copy()
    return MEDIA_EXISTING_SORTS + MEDIA_MANUAL_SORTS


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


def get_timeline(user_media):
    """Build a timeline of media consumption organized by month-year."""
    timeline = defaultdict(list)

    # Process each media type
    for media_type, queryset in user_media.items():
        if media_type == MediaTypes.TV.value:
            continue
        for media in queryset:
            local_start_date = localdate(media.start_date)
            local_end_date = localdate(media.end_date)

            if media.start_date and media.end_date:
                # add media to all months between start and end
                current_date = local_start_date
                while current_date <= local_end_date:
                    year = current_date.year
                    month = current_date.month
                    month_name = calendar.month_name[month]
                    month_year = f"{month_name} {year}"

                    timeline[month_year].append(media)

                    # Move to next month
                    current_date += relativedelta(months=1)
                    current_date = current_date.replace(day=1)
            elif media.start_date:
                # If only start date, add to the start month
                year = local_start_date.year
                month = local_start_date.month
                month_name = calendar.month_name[month]
                month_year = f"{month_name} {year}"

                timeline[month_year].append(media)
            elif media.end_date:
                # If only end date, add to the end month
                year = local_end_date.year
                month = local_end_date.month
                month_name = calendar.month_name[month]
                month_year = f"{month_name} {year}"

                timeline[month_year].append(media)

    # Convert to sorted dictionary with media sorted by start date
    # Create a list sorted by year and month in reverse order
    sorted_items = []
    for month_year, media_list in timeline.items():
        month_name, year_str = month_year.split()
        year = int(year_str)
        month = list(calendar.month_name).index(month_name)
        sorted_items.append((month_year, media_list, year, month))

    # Sort by year and month in reverse chronological order
    sorted_items.sort(key=lambda x: (x[2], x[3]), reverse=True)

    # Create the final result dictionary
    result = {}
    for month_year, media_list, _, _ in sorted_items:
        # Sort the media list using our custom sort key
        result[month_year] = sorted(media_list, key=time_line_sort_key, reverse=True)
    return result


def time_line_sort_key(media):
    """Sort media items in the timeline."""
    if media.end_date is not None:
        return localdate(media.end_date)
    return localdate(media.start_date)


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


# ---- Sorting ----


def parse_sort_filter(sort_filter):
    """Parse a `<field>[_asc|_desc]` sort string into a (field, direction) tuple.

    The direction suffix is optional and defaults to ascending.
    """
    if not sort_filter:
        return "", ""
    field, _, direction = sort_filter.rpartition("_")
    if field and direction in SORT_DIRECTIONS:
        return field, direction
    return sort_filter, "asc"


def itemid_key_compare(media):
    """Key function for sorting by item_id."""
    item = _item_from_result(media)
    media_type = getattr(item, "media_type", "")
    source = getattr(item, "source", "")
    media_id_raw = getattr(item, "media_id", "")
    media_id_key = str(media_id_raw).lower() if media_id_raw is not None else ""
    return (media_type, source, media_id_key)


def _item_from_result(media):
    """Return Item object from a media result or the result itself if it already is."""
    return getattr(media, "item", media)


def _item_attr_key(attr):
    """Return a key function reading an attribute of the result's Item."""
    return lambda media: getattr(_item_from_result(media), attr, None)


def _media_attr_key(attr):
    """Return a key function reading an attribute of the media result."""
    return lambda media: getattr(media, attr, None)


def _sort_media_id(media):
    """Return lowercased media_id key from both media and item objects."""
    media_id = getattr(_item_from_result(media), "media_id", None)
    return str(media_id).lower() if media_id is not None else None


def _sort_title(media):
    """Return lowercased title key from both media and item objects."""
    title = getattr(_item_from_result(media), "title", None)
    return title.lower() if isinstance(title, str) else None


_AGGREGATED_SORT_KEYS = {
    "added": _media_attr_key("created_at"),
    "end_date": _media_attr_key("end_date"),
    "episode_number": _item_attr_key("episode_number"),
    "id": _item_attr_key("id"),
    "itemid": itemid_key_compare,
    "media_id": _sort_media_id,
    "media_type": _item_attr_key("media_type"),
    "progress": _media_attr_key("progress"),
    "score": _media_attr_key("score"),
    "season_number": _item_attr_key("season_number"),
    "source": _item_attr_key("source"),
    "start_date": _media_attr_key("start_date"),
    "title": _sort_title,
    "updated": _media_attr_key("progressed_at"),
}


def apply_aggregated_sort(results, sort, direction):
    """Sort media and item results in python, keeping null values last.

    Raise InvalidSortError if the sort is not supported.
    """
    if sort not in _AGGREGATED_SORT_KEYS:
        raise InvalidSortError
    key = _AGGREGATED_SORT_KEYS[sort]

    keyed = [(key(result), result) for result in results]
    present = [pair for pair in keyed if pair[0] is not None]
    missing = [result for value, result in keyed if value is None]
    present.sort(key=lambda pair: pair[0], reverse=direction == "desc")
    return [result for _, result in present] + missing


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
