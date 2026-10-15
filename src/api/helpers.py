import calendar
import logging
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime
from http import HTTPStatus as HTTP  # noqa: N814
from urllib.parse import urlencode

from dateutil.relativedelta import relativedelta
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
from app.providers import (
    anilist,
    bgg,
    comicvine,
    fanart,
    hardcover,
    igdb,
    mal,
    mangadex,
    mangaupdates,
    openlibrary,
    tmdb,
)
from lists.models import CustomListItem
from users.models import WATCH_PROVIDER_REGION_UNSET, MediaStatusChoices

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

IMAGE_TYPES = ["posters", "backdrops", "screenshots", "logos"]

# Sources that provide images for each media type
IMAGE_SOURCES = {
    MediaTypes.MOVIE.value: [Sources.TMDB.value],
    MediaTypes.TV.value: [Sources.TMDB.value],
    MediaTypes.ANIME.value: [Sources.MAL.value],
    MediaTypes.MANGA.value: [Sources.MAL.value, Sources.MANGAUPDATES.value],
    MediaTypes.GAME.value: [Sources.IGDB.value],
    MediaTypes.BOOK.value: [Sources.OPENLIBRARY.value, Sources.HARDCOVER.value],
    MediaTypes.COMIC.value: [Sources.COMICVINE.value],
    MediaTypes.BOARDGAME.value: [Sources.BGG.value],
}

# Voice actor languages for the anime cast
VOICE_ACTOR_LANGUAGES = anilist.VOICE_ACTOR_LANGUAGES

# Sources that provide cast and crew for each media type
CAST_CREW_SOURCES = {
    MediaTypes.MOVIE.value: [Sources.TMDB.value],
    MediaTypes.TV.value: [Sources.TMDB.value],
    MediaTypes.ANIME.value: [Sources.MAL.value],
}

# Sources that provide creators or characters for each media type
CREDIT_SOURCES = {
    "creators": {
        MediaTypes.MANGA.value: [Sources.MAL.value, Sources.MANGAUPDATES.value],
        MediaTypes.BOOK.value: [Sources.OPENLIBRARY.value, Sources.HARDCOVER.value],
        MediaTypes.COMIC.value: [Sources.COMICVINE.value],
        MediaTypes.BOARDGAME.value: [Sources.BGG.value],
    },
    "characters": {
        MediaTypes.ANIME.value: [Sources.MAL.value],
        MediaTypes.MANGA.value: [Sources.MAL.value],
        MediaTypes.COMIC.value: [Sources.COMICVINE.value],
    },
}

# Sources that provide companies for each media type
COMPANY_SOURCES = {
    MediaTypes.GAME.value: [Sources.IGDB.value],
    MediaTypes.BOARDGAME.value: [Sources.BGG.value],
}

GAME_COMPANY_ROLES = ["developer", "publisher", "porting", "supporting"]


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

SOURCES_COMPLETE_VALID_LIST = [*SOURCES_VALID_LIST, "manual"]


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


def group_watch_providers(all_providers, region):
    """Group a region's watch providers into no charge and paid tiers."""
    region_providers = (all_providers or {}).get(region, {})

    def merge(*provider_types):
        # Dedupe by provider id across provider types
        providers = {}
        for provider_type in provider_types:
            for provider in region_providers.get(provider_type, []):
                providers[provider.get("provider_id")] = {
                    "provider_id": provider.get("provider_id"),
                    "provider_name": provider.get("provider_name"),
                    "image": tmdb.get_image_url(provider.get("logo_path")),
                    "display_priority": provider.get("display_priority"),
                }
        return sorted(
            providers.values(),
            key=lambda e: (
                e["display_priority"] if e["display_priority"] is not None else 999
            ),
        )

    return {
        "region": region,
        "link": region_providers.get("link"),
        "no_charge": merge("flatrate", "free", "ads"),
        "paid": merge("buy", "rent"),
    }


def check_supported_source(supported_sources, media_type, source, feature):
    """Return an error response if the media type or source is not supported."""
    if media_type not in supported_sources:
        supported = ", ".join(f"'{key}'" for key in supported_sources)
        return Response(
            {
                "detail": (
                    f"No {feature} available for `{media_type}` media type. "
                    f"Supported media types: {supported}."
                ),
            },
            status=HTTP.BAD_REQUEST,
        )

    if source not in supported_sources[media_type]:
        return Response(
            {
                "detail": f"Cannot query `{source}` for `{media_type}` media type",
            },
            status=HTTP.BAD_REQUEST,
        )

    return None


def fetch_images(media_type, source, media_id):
    """Return the images of a media from its source and the extra artwork sources."""
    fetchers = {
        Sources.TMDB.value: lambda: tmdb.media_images(media_type, media_id),
        Sources.MAL.value: lambda: mal.media_pictures(media_type, media_id),
        Sources.MANGAUPDATES.value: lambda: mangaupdates.manga_extras(media_id)[
            "images"
        ],
        Sources.IGDB.value: lambda: igdb.game_extras(media_id)["images"],
        Sources.OPENLIBRARY.value: lambda: openlibrary.book_extras(media_id)["images"],
        Sources.HARDCOVER.value: lambda: hardcover.book_extras(media_id)["images"],
        Sources.COMICVINE.value: lambda: comicvine.comic_images(media_id),
        Sources.BGG.value: lambda: bgg.boardgame_extras(media_id)["images"],
    }
    images = tag_images(fetchers[source](), source)
    add_extra_images(images, get_extra_image_fetchers(media_type, source, media_id))
    return images


def fetch_season_images(media_id, season_number):
    """Return the posters of a tv season from TMDB and Fanart.tv."""
    images = tag_images(
        tmdb.media_images(
            MediaTypes.TV.value,
            media_id,
            season_number=season_number,
        ),
        Sources.TMDB.value,
    )
    extra_fetchers = []
    if fanart.is_enabled():
        extra_fetchers.append(
            (
                fanart.PROVIDER,
                lambda: fanart.tv_images(
                    tmdb.tv(media_id).get("tvdb_id"),
                    season_number=season_number,
                ),
            ),
        )
    add_extra_images(images, extra_fetchers)
    return images


def get_extra_image_fetchers(media_type, source, media_id):
    """Return the extra artwork sources available for a media."""
    fetchers = []
    if media_type == MediaTypes.MOVIE.value and fanart.is_enabled():
        fetchers.append((fanart.PROVIDER, lambda: fanart.movie_images(media_id)))
    elif media_type == MediaTypes.TV.value and fanart.is_enabled():
        fetchers.append(
            (
                fanart.PROVIDER,
                lambda: fanart.tv_images(tmdb.tv(media_id).get("tvdb_id")),
            ),
        )

    if source == Sources.MAL.value:
        fetchers.append(
            ("anilist", lambda: anilist.media_images(media_type, media_id)),
        )

    if media_type == MediaTypes.MANGA.value:
        get_title = (
            mal.manga if source == Sources.MAL.value else mangaupdates.manga_extras
        )
        fetchers.append(
            (
                mangadex.PROVIDER,
                lambda: mangadex.manga_covers(
                    get_title(media_id)["title"],
                    source,
                    media_id,
                ),
            ),
        )
    return fetchers


def tag_images(images, source):
    """Return a copy of the image groups with the source of every image."""
    return {
        group: [{**image, "source": source} for image in group_images]
        for group, group_images in images.items()
    }


def add_extra_images(images, extra_fetchers):
    """Append the images of extra sources, skipping duplicates and failures."""
    seen = {
        image["original"] for group_images in images.values() for image in group_images
    }
    for extra_source, fetch in extra_fetchers:
        # Extra artwork is optional, a failing source must not break the request
        try:
            extra_images = tag_images(fetch(), extra_source)
        except Exception:
            logger.exception("Failed to fetch extra images from %s", extra_source)
            continue

        for group, group_images in extra_images.items():
            for image in group_images:
                if image["original"] in seen:
                    continue
                seen.add(image["original"])
                images.setdefault(group, []).append(image)


def fetch_cast(media_type, source, media_id, language=None):
    """Return the cast of a movie, tv serie or anime from its source."""
    if source == Sources.TMDB.value:
        return tmdb.media_credits(media_type, media_id)["cast"]

    # Anime cast are the voice actors of each character
    characters = anilist.media_credits(
        media_type,
        media_id,
        language=language or "japanese",
    )["characters"]
    return [
        {
            "person_id": actor["id"],
            "name": actor["name"],
            "character": character["name"],
            "image": actor["image"],
            "order": order,
            "episode_count": None,
        }
        for order, character in enumerate(characters)
        for actor in character["voice_actors"]
    ]


def fetch_crew(media_type, source, media_id):
    """Return the crew of a movie, tv serie or anime from its source."""
    if source == Sources.TMDB.value:
        return tmdb.media_credits(media_type, media_id)["crew"]

    staff = anilist.media_credits(media_type, media_id)["staff"]
    return [
        {
            "person_id": person["id"],
            "name": person["name"],
            "department": None,
            "jobs": person["roles"],
            "image": person["image"],
            "episode_count": None,
        }
        for person in staff
    ]


def fetch_credits(credit_type, media_type, source, media_id):
    """Return the creators or characters of a media from its source."""
    fetchers = {
        Sources.MAL.value: lambda: get_anilist_credits(
            credit_type,
            media_type,
            media_id,
        ),
        Sources.MANGAUPDATES.value: lambda: mangaupdates.manga_extras(media_id)[
            "creators"
        ],
        Sources.OPENLIBRARY.value: lambda: openlibrary.book_extras(media_id)[
            "creators"
        ],
        Sources.HARDCOVER.value: lambda: hardcover.book_extras(media_id)["creators"],
        Sources.COMICVINE.value: lambda: comicvine.comic_credits(media_id)[credit_type],
        Sources.BGG.value: lambda: bgg.boardgame_extras(media_id)["creators"],
    }
    return fetchers[source]()


def get_anilist_credits(credit_type, media_type, media_id):
    """Return the staff or characters of an anime or manga from AniList."""
    anilist_credits = anilist.media_credits(media_type, media_id)
    if credit_type == "creators":
        return [{**person, "issue_count": None} for person in anilist_credits["staff"]]
    return [
        {
            "id": character["id"],
            "name": character["name"],
            "roles": [character["role"]] if character["role"] else [],
            "image": character["image"],
            "issue_count": None,
            "source_url": character["source_url"],
        }
        for character in anilist_credits["characters"]
    ]


def fetch_companies(source, media_id):
    """Return the companies of a game or board game from its source."""
    if source == Sources.IGDB.value:
        return igdb.game_extras(media_id)["companies"]
    return bgg.boardgame_extras(media_id)["companies"]


def filter_images(images, image_type=None):
    """Return every image type, keeping only the given type if one is set."""
    return {
        key: (images.get(key, []) if image_type in (None, key) else [])
        for key in IMAGE_TYPES
    }


def filter_known_for(known_for, media_type=None, department=None):
    """Filter known for media by type and department, flattening their jobs."""
    results = []
    for media in known_for:
        if media_type and media["media_type"] != media_type:
            continue

        departments = media["departments"]
        if department:
            departments = {
                name: jobs
                for name, jobs in departments.items()
                if name and name.lower() == department.lower()
            }
            if not departments:
                continue

        jobs = []
        for department_jobs in departments.values():
            jobs.extend(job for job in department_jobs if job not in jobs)

        results.append(
            {
                **media,
                "character": media["character"] if "Acting" in departments else None,
                "departments": sorted(
                    departments,
                    key=lambda name: (name != "Acting", name),
                ),
                "jobs": jobs,
            },
        )
    return results


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


def parse_watch_provider_region(request):
    """Parse the watch provider region, falling back to the user's preference.

    If no error, error_response is None. On validation error, returns a DRF Response.
    """
    region = request.GET.get("region") or request.user.watch_provider_region
    if not region or region == WATCH_PROVIDER_REGION_UNSET:
        return (
            None,
            Response(
                {
                    "detail": (
                        "Watch provider region not set. Pass the `region` query "
                        "parameter or set it in the user preferences."
                    ),
                },
                status=HTTP.BAD_REQUEST,
            ),
        )

    return region.upper(), None


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
