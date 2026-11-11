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
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Count, F, OrderBy, OuterRef, Subquery
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.timezone import is_naive, localdate, make_aware, now
from rest_framework.exceptions import (
    ParseError,
    PermissionDenied,
    Throttled,
    ValidationError,
)
from rest_framework.response import Response

from app.config import get_sources as get_media_type_sources
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
    Status,
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
    services,
    tmdb,
)
from lists.models import CustomListItem
from users.models import (
    WATCH_PROVIDER_REGION_UNSET,
    MediaSortChoices,
    MediaStatusChoices,
)

logger = logging.getLogger(__name__)


class InvalidSortError(ValueError):
    """Raised when a requested sort is not supported."""


MEDIA_STATUS_MAP = {
    Status.PLANNING.value: 0,
    Status.IN_PROGRESS.value: 1,
    Status.PAUSED.value: 2,
    Status.COMPLETED.value: 3,
    Status.DROPPED.value: 4,
}

MEDIA_STATUS_CHOICES = [
    (value, Status(status).label) for status, value in MEDIA_STATUS_MAP.items()
]

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


MAX_SEARCH_PROVIDER_PAGES = 10

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
    media_type: [
        *(source.value for source in get_media_type_sources(media_type)),
        Sources.MANUAL.value,
    ]
    for media_type in MEDIA_TYPE_COMPLETE_VALID_LIST
}

SOURCES_VALID_LIST = [source.value for source in Sources if source != Sources.MANUAL]

SOURCES_COMPLETE_VALID_LIST = [*SOURCES_VALID_LIST, Sources.MANUAL.value]


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


def validate_media_path(
    media_type,
    source=None,
    *,
    child_type=None,
    complete=False,
    sync=False,
):
    """Raise a ParseError if the media type or source of a request is invalid.

    `child_type` is the season or episode media type of the nested routes, which
    are only supported for TV shows. If `source` is None it is not checked, and
    with `sync` manual sources are rejected.
    """
    if not check_valid_type(media_type, complete=complete):
        msg = "Unsupported media type."
        raise ParseError(msg)

    if child_type is not None and media_type != MediaTypes.TV.value:
        msg = (
            f"{child_type.capitalize()}s are supported only for "
            f"'{MediaTypes.TV.value}' media type."
        )
        raise ParseError(msg)

    if source is None:
        return

    if sync and source == Sources.MANUAL.value:
        msg = "Manual items cannot be synced."
        raise ParseError(msg)

    if not check_source_type(media_type, source):
        action = "sync" if sync else "query"
        msg = f"Cannot {action} `{source}` for `{media_type}` media type"
        raise ParseError(msg)


def check_sync_available(cache_key):
    """Raise a Throttled error if the media was synced a few seconds ago."""
    ttl = cache.ttl(cache_key)
    if ttl is not None and ttl > (settings.CACHE_TIMEOUT - 3):
        raise Throttled(
            wait=ttl,
            detail="The data was recently synced, please wait a few seconds.",
        )

    cache.delete(cache_key)


def sync_season_episodes(metadata, media_id, source, season_number):
    """Update the title and image of the stored episodes from the season metadata."""
    metadata["episodes"] = tmdb.process_episodes(metadata, [])
    existing_episodes = {
        episode.episode_number: episode
        for episode in Item.objects.filter(
            source=source,
            media_type=MediaTypes.EPISODE.value,
            media_id=media_id,
            season_number=season_number,
        )
    }

    episodes_to_update = []
    for episode_data in metadata["episodes"]:
        episode_item = existing_episodes.get(episode_data["episode_number"])
        if episode_item is not None:
            episode_item.title = metadata["title"]
            episode_item.image = episode_data["image"]
            episodes_to_update.append(episode_item)

    if episodes_to_update:
        Item.objects.bulk_update(episodes_to_update, ["title", "image"], batch_size=100)


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

    if item.media_type == MediaTypes.SEASON.value:
        children = f"/{item.season_number}"
        media_type = MediaTypes.TV.value
    elif item.media_type == MediaTypes.EPISODE.value:
        children = f"/{item.season_number}/{item.episode_number}"
        media_type = MediaTypes.TV.value

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
    tv = MediaTypes.TV.value
    if item.media_type == MediaTypes.SEASON.value:
        return f"{tv}/{item.source}/{item.media_id}"
    if item.media_type == MediaTypes.EPISODE.value and hasattr(item, "season_number"):
        return f"{tv}/{item.source}/{item.media_id}/{item.season_number}"
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


def order_media_queryset(queryset, direction):
    """Re-apply the queryset's primary ordering in the given direction."""
    primary, *tiebreakers = queryset.query.order_by
    if isinstance(primary, str):
        primary = F(primary.removeprefix("-"))
    elif isinstance(primary, OrderBy):
        primary = primary.expression

    if direction == "desc":
        primary = primary.desc(nulls_last=True)
    else:
        primary = primary.asc(nulls_last=True)
    return queryset.order_by(primary, *tiebreakers)


def fetch_media_list(user, media_type, status, sort_filter, search, direction="asc"):
    """Return a plain list of the requested media."""
    if media_type == MediaTypes.EPISODE.value:
        qs = Episode.objects.filter(related_season__user=user).select_related("item")
        if status and status != MediaStatusChoices.ALL:
            qs = qs.filter(related_season__status=status)
        if search:
            qs = qs.filter(item__title__icontains=search)
        return list(qs)

    qs = BasicMedia.objects.get_media_list(
        user=user,
        media_type=media_type,
        status_filter=status,
        sort_filter=sort_filter,
        search=search,
    )
    if sort_filter:
        qs = order_media_queryset(qs, direction)
    return list(qs)


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
    results = fetch_media_list(user, media_type, status, db_sort, search, direction)

    if db_sort:
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
    """Raise a ParseError if the media type or source is not supported."""
    if media_type not in supported_sources:
        supported = ", ".join(f"'{key}'" for key in supported_sources)
        msg = (
            f"No {feature} available for `{media_type}` media type. "
            f"Supported media types: {supported}."
        )
        raise ParseError(msg)

    if source not in supported_sources[media_type]:
        msg = f"Cannot query `{source}` for `{media_type}` media type"
        raise ParseError(msg)


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


def get_paginated_credits(request, media_type, source, media_id, credit_type):
    """Return the paginated creators or characters of a media."""
    limit, offset = parse_limit_offset(request)
    check_supported_source(CREDIT_SOURCES[credit_type], media_type, source, credit_type)

    credits_data = fetch_credits(credit_type, media_type, source, media_id)
    return paginate_data(request, credits_data, limit, offset)


def get_cover_state(item, provider_image):
    """Return the current cover of a media and whether it is a custom one."""
    custom_image = item.custom_image if item else ""
    return {
        "image": custom_image or provider_image,
        "custom_image": custom_image or None,
    }


def get_cover_metadata(media_type, source, media_id, season_number=None):
    """Return the metadata and the available images of a media or season."""
    if season_number is not None:
        metadata = services.get_media_metadata(
            MediaTypes.SEASON.value,
            media_id,
            source,
            [season_number],
        )
        images = fetch_season_images(media_id, season_number)
    else:
        metadata = services.get_media_metadata(media_type, media_id, source)
        images = fetch_images(media_type, source, media_id)
    return metadata, images


def can_change_cover(media_type, source):
    """Return whether a media has images to pick a custom cover from."""
    base_media_type = (
        MediaTypes.TV.value if media_type == MediaTypes.SEASON.value else media_type
    )
    return source in IMAGE_SOURCES.get(base_media_type, [])


def get_cover_item_filter(media_type, source, media_id, season_number=None):
    """Return the Item lookup of the media or season whose cover is customized."""
    return {
        "media_id": media_id,
        "source": source,
        "media_type": (
            MediaTypes.SEASON.value if season_number is not None else media_type
        ),
        "season_number": season_number,
    }


def set_custom_cover(item_filter, metadata, images, new_image):
    """Set the custom cover of a media, returning the item or an error message."""
    # Only images offered by the media images endpoint can be chosen
    available = {
        url
        for image_list in images.values()
        for image in image_list
        for url in (image["image"], image["original"])
    }
    if new_image not in available:
        return None, (
            "image must be one of the images returned by the media images endpoint."
        )

    item = Item.objects.filter(**item_filter, episode_number=None).first()
    if item is None:
        item = Item.objects.create(
            **item_filter,
            title=metadata.get("title"),
            image=new_image,
            custom_image=new_image,
        )
    else:
        item.custom_image = new_image
        item.save()
    return item, None


def resolve_cover(request, media_type, source, media_id, season_number=None):
    """Get, set or reset the custom cover of a media or season.

    Return the cover state of the media. Raise a ParseError if the request is
    not valid.
    """
    check_supported_source(IMAGE_SOURCES, media_type, source, "covers")

    if season_number is not None and media_type != MediaTypes.TV.value:
        msg = "Seasons are supported only for 'tv' media type."
        raise ParseError(msg)

    new_image = (request.data or {}).get("image")
    if request.method == "PUT" and (not new_image or not isinstance(new_image, str)):
        msg = "image is required."
        raise ParseError(msg)

    metadata, images = get_cover_metadata(media_type, source, media_id, season_number)

    item_filter = get_cover_item_filter(media_type, source, media_id, season_number)
    item = Item.objects.filter(**item_filter, episode_number=None).first()

    if request.method == "PUT":
        item, error = set_custom_cover(item_filter, metadata, images, new_image)
        if error:
            raise ParseError(error)
    elif request.method == "DELETE":
        reset_custom_cover(item, metadata)

    return get_cover_state(item, metadata.get("image"))


def reset_custom_cover(item, metadata):
    """Remove the custom cover of an item, restoring the provider image."""
    if item is None or not item.custom_image:
        return
    item.custom_image = ""
    item.image = metadata.get("image") or settings.IMG_NONE
    item.save(update_fields=["custom_image", "image"])


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


def paginate_data(request, results, limit, offset, *, total=None, sliced=False):
    """Paginate the results based on the limit and offset.

    If `sliced` is True, `results` is already the requested page.
    Returns raw paginated data without serialization.
    Serialization should be handled by the view.
    """
    total_count = len(results) if total is None else total
    start = offset
    end = offset + limit
    paginated = results if sliced else results[start:end]

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

    Return the (limit, offset) tuple, with the limit capped to MAX_RESULT_LIMIT.
    Raise a ParseError if a param is not valid.
    """
    raw_limit = request.GET.get("limit")
    raw_offset = request.GET.get("offset")

    try:
        limit = 20 if raw_limit in [None, ""] else int(raw_limit)
    except (TypeError, ValueError) as e:
        msg = "Invalid limit parameter"
        raise ParseError(msg) from e

    try:
        offset = 0 if raw_offset in [None, ""] else int(raw_offset)
    except (TypeError, ValueError) as e:
        msg = "Invalid offset parameter"
        raise ParseError(msg) from e

    if limit <= 0 or offset < 0:
        msg = "limit must be >0 and offset must be >=0"
        raise ParseError(msg)

    return min(limit, MAX_RESULT_LIMIT), offset


def parse_watch_provider_region(request):
    """Return the watch provider region, falling back to the user's preference.

    Raise a ParseError if neither is set.
    """
    region = request.GET.get("region") or request.user.watch_provider_region
    if not region or region == WATCH_PROVIDER_REGION_UNSET:
        msg = (
            "Watch provider region not set. Pass the `region` query "
            "parameter or set it in the user preferences."
        )
        raise ParseError(msg)

    return region.upper()


def parse_status_param(status):
    """Parse and validate status parameter."""
    if not status:
        return MediaStatusChoices.ALL
    try:
        parsed = get_media_status(int(status), reverse=True)
    except (TypeError, ValueError) as e:
        msg = "Invalid status"
        raise ParseError(msg) from e

    if parsed is None:
        msg = "Invalid status"
        raise ParseError(msg)
    return parsed


def fetch_search_results(
    media_type,
    query,
    source,
    limit,
    offset,
    user,
    *,
    presliced=False,
):
    """Search a provider and return the (results, total) tuple.

    Paginated providers are queried page by page until the requested offset and
    limit are covered, up to MAX_SEARCH_PROVIDER_PAGES pages. If `presliced` is
    True, the provider already applies limit and offset and only one request is
    made.
    """
    results = []
    last_response = None

    for page in range(1, MAX_SEARCH_PROVIDER_PAGES + 1):
        last_response = services.search(
            media_type,
            query,
            page,
            source,
            limit=limit,
            offset=offset,
            user=user,
        )
        if not isinstance(last_response, dict) or "results" not in last_response:
            break
        page_results = last_response.get("results", []) or []
        results.extend(page_results)

        total_pages = last_response.get("total_pages")
        if (
            presliced
            or not page_results
            or len(results) >= offset + limit
            or (total_pages and page >= total_pages)
        ):
            break

    total = (
        last_response.get("total_results") if isinstance(last_response, dict) else None
    )
    return results, total or len(results)


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


def forbid_demo_user(user):
    """Raise PermissionDenied if the user is a demo account (view-only settings)."""
    if user.is_demo:
        msg = "This section is view-only for demo accounts."
        raise PermissionDenied(msg)


def revoke_user_sessions(user):
    """Sign the user out of every web login session."""
    user_id = str(user.pk)
    for session in Session.objects.filter(expire_date__gt=now()):
        if session.get_decoded().get("_auth_user_id") == user_id:
            session.delete()
