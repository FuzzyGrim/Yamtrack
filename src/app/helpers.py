import ipaddress
import re
from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.encoding import iri_to_uri
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.timezone import is_naive, make_aware

from app.models import (
    TV,
    Anime,
    BasicMedia,
    BoardGame,
    Book,
    CollectionEntry,
    Comic,
    ComicIssue,
    Game,
    Manga,
    MediaTypes,
    Movie,
    Music,
    Status,
)

MODEL_MAP = {
    MediaTypes.TV.value: TV,
    MediaTypes.MOVIE.value: Movie,
    MediaTypes.ANIME.value: Anime,
    MediaTypes.MANGA.value: Manga,
    MediaTypes.GAME.value: Game,
    MediaTypes.BOOK.value: Book,
    MediaTypes.COMIC.value: Comic,
    MediaTypes.BOARDGAME.value: BoardGame,
    # Fork media types that support manual-source items
    MediaTypes.MUSIC.value: Music,
    MediaTypes.COMIC_ISSUE.value: ComicIssue,
}

_DEFAULT_IMAGE_SENTINEL = object()
_ENCODED_NAVIGATION_SEPARATOR_PATTERNS = (
    (re.compile(r"%3F", re.IGNORECASE), "?"),
    (re.compile(r"%26", re.IGNORECASE), "&"),
    (re.compile(r"%3D", re.IGNORECASE), "="),
    (re.compile(r"%23", re.IGNORECASE), "#"),
)


def get_page_number(request):
    """Return the ``page`` query parameter as an int, or 1 when it is not numeric."""
    try:
        return int(request.GET.get("page", 1))
    except (TypeError, ValueError):
        return 1


def is_htmx_fragment(request):
    """Return whether this HTMX request expects a partial fragment response.

    Boosted navigation (hx-boost) and history restores send HX-Request too,
    but they replace the whole page and need the full template.
    """
    return bool(
        request.headers.get("HX-Request")
        and not request.headers.get("HX-Boosted")
        and not request.headers.get("HX-History-Restore-Request"),
    )


def minutes_to_hhmm(total_minutes):
    """Convert total minutes to HH:MM format."""
    hours = int(total_minutes / 60)
    minutes = int(total_minutes % 60)
    if hours == 0:
        return f"{minutes}min"
    return f"{hours}h {minutes:02d}min"


def seconds_to_hm(total_seconds):
    """Convert a duration in seconds to "1h 9m", or to "46m" under an hour.

    Returns "" for a missing or zero duration.
    """
    if not total_seconds:
        return ""
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def has_real_image(image):
    """Return whether the value is a usable artwork URL."""
    return bool(image and image != settings.IMG_NONE)


def first_real_image(*images, default=_DEFAULT_IMAGE_SENTINEL):
    """Return the first usable artwork URL from the provided candidates."""
    for image in images:
        if has_real_image(image):
            return image
    if default is not _DEFAULT_IMAGE_SENTINEL:
        return default
    return settings.IMG_NONE


def resolve_image_with_fallback(primary_image, *fallback_images):
    """Return a display image plus whether it is primary, fallback, or none."""
    primary = first_real_image(primary_image, default=None)
    if primary:
        return primary, "primary"

    fallback = first_real_image(*fallback_images, default=None)
    if fallback:
        return fallback, "fallback"

    return settings.IMG_NONE, "none"


def get_tmdb_backdrop_image(media_type, media_id):
    """Return a TMDB backdrop URL when available."""
    media_id = str(media_id or "").strip()
    if not media_id:
        return None

    try:
        from app.backdrops import get_tmdb_backdrop

        backdrop = get_tmdb_backdrop(media_type, media_id)
    except Exception:
        return None

    return backdrop if has_real_image(backdrop) else None


def safe_return_url(request, destination, fallback="/"):
    """Keep user-supplied navigation on this host and preserve HTTPS."""
    if url_has_allowed_host_and_scheme(
        destination,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return destination
    return fallback


def redirect_back(request):
    """Redirect to the previous page, removing the 'page' parameter if present."""
    next_url = normalize_navigation_url(
        request.GET.get("next") or request.POST.get("next"),
    )
    if url_has_allowed_host_and_scheme(next_url, None):
        # Parse the URL
        parsed_url = urlparse(next_url)

        # Get the query parameters and remove params we don't want
        query_params = {
            k: v
            for k, v in parse_qsl(parsed_url.query, keep_blank_values=True)
            if not k.startswith("org.htmx.")
        }
        query_params.pop("page", None)
        query_params.pop("load_media_type", None)
        query_params.pop("load_row", None)
        query_params.pop("fragment", None)

        # Reconstruct the URL
        new_query = urlencode(query_params)
        new_parts = list(parsed_url)
        new_parts[4] = new_query  # index 4 is the query part

        # Convert back to a URL string
        clean_url = iri_to_uri(parsed_url._replace(query=new_query).geturl())

        return HttpResponseRedirect(clean_url)

    return redirect("home")


def normalize_navigation_url(url):
    """Decode encoded query separators in return/next URLs from modal form posts."""
    normalized_url = (url or "").strip()
    if not normalized_url or "%" not in normalized_url:
        return normalized_url

    for pattern, replacement in _ENCODED_NAVIGATION_SEPARATOR_PATTERNS:
        normalized_url = pattern.sub(replacement, normalized_url)

    return normalized_url


def form_error_messages(form, request):
    """Display form errors as messages."""
    for field, errors in form.errors.items():
        for error in errors:
            messages.error(
                request,
                f"{field.replace('_', ' ').title()}: {error}",
            )


def format_search_response(page, per_page, total_results, results):
    """Format the search response for pagination."""
    return {
        "page": page,
        "total_results": total_results,
        "total_pages": total_results // per_page + 1,
        "results": results,
    }


def is_caught_up_media(media) -> bool:
    """Return whether watched progress has caught up to released progress."""
    max_progress = getattr(media, "max_progress", None)
    if max_progress is None:
        return False

    try:
        max_progress_value = int(max_progress)
    except (TypeError, ValueError):
        return False

    if max_progress_value <= 0:
        return False

    try:
        progress_value = int(getattr(media, "progress", 0) or 0)
    except (TypeError, ValueError):
        return False

    # TV rows can keep dropped seasons in their raw progress/max_progress totals.
    # Mirror time-left behavior by removing dropped-season episodes from both sides
    # before deciding whether the show is actually caught up.
    item_media_type = getattr(getattr(media, "item", None), "media_type", None)
    if item_media_type == MediaTypes.TV.value:
        breakdown = getattr(media, "released_episode_breakdown", None) or {}
        seasons_manager = getattr(media, "seasons", None)
        if (
            breakdown
            and seasons_manager is not None
            and hasattr(seasons_manager, "all")
        ):
            seasons = [
                season
                for season in seasons_manager.all()
                if getattr(getattr(season, "item", None), "season_number", 0)
            ]
            if seasons:
                dropped_season_numbers = {
                    season.item.season_number
                    for season in seasons
                    if season.status == Status.DROPPED.value
                }
                if dropped_season_numbers:
                    filtered_breakdown = {
                        season_num: count
                        for season_num, count in breakdown.items()
                        if season_num not in dropped_season_numbers
                    }
                    if filtered_breakdown != breakdown:
                        included_progress = sum(
                            int(getattr(season, "progress", 0) or 0)
                            for season in seasons
                            if season.status != Status.DROPPED.value
                        )
                        remaining = max(
                            sum(filtered_breakdown.values()) - included_progress,
                            0,
                        )
                        return remaining == 0

    return progress_value >= max_progress_value


def _needs_image_refresh(item, new_image):
    """Return True when item.image should be replaced with new_image."""
    if item is None or not new_image or new_image == settings.IMG_NONE:
        return False
    return not item.image or item.image == settings.IMG_NONE


def refresh_item_image_if_missing(item, new_image):
    """Update an Item's stored image when it's missing and a real one is available."""
    if not _needs_image_refresh(item, new_image):
        return
    item.image = new_image
    item.save(update_fields=["image"])


_PROVIDER_ID_KEYS = {"tmdb": "tmdb_id", "imdb": "imdb_id", "tvdb": "tvdb_id"}


def build_provider_ids(item):
    """Map an Item's resolved external ids to the API's `ids` vocabulary."""
    if item is None:
        return {}
    stored = getattr(item, "provider_external_ids", None) or {}
    return {
        key: str(stored[field])
        for key, field in _PROVIDER_ID_KEYS.items()
        if stored.get(field)
    }


def enrich_items_with_user_data(
    request, items, section_name=None, user=None, library_media_type=None
):
    """Enrich a list of items with user tracking data.

    Args:
        library_media_type: When provided and media_type is SEASON, only return
            Season tracking objects whose Item has this library_media_type. Used
            to isolate anime season tracking from TV season tracking.
    """
    if not items:
        return []

    # Use provided user or fall back to request.user
    # If user is provided, use it (should be authenticated list owner)
    # If user is None and request.user is AnonymousUser, skip enrichment
    if user is not None:
        target_user = user
    elif request.user.is_authenticated:
        target_user = request.user
    else:
        # Anonymous user with no provided user - return items without enrichment
        return [{"item": item, "media": None} for item in items]

    # All items are the same media type
    media_type = items[0]["media_type"]
    source = items[0]["source"]

    # Build Q objects for all items
    q_objects = Q()
    for item in items:
        filter_params = {
            "item__media_id": item["media_id"],
            "item__media_type": media_type,
            "item__source": source,
        }

        if media_type == MediaTypes.SEASON.value:
            filter_params["item__season_number"] = item.get("season_number")

        q_objects |= Q(**filter_params)

    q_objects &= Q(user=target_user)

    # For season enrichment, optionally restrict to a specific library_media_type
    # so anime and TV seasons are tracked independently.
    if media_type == MediaTypes.SEASON.value and library_media_type is not None:
        q_objects &= Q(item__library_media_type=library_media_type)

    # Bulk fetch all media with prefetch
    model = apps.get_model(app_label="app", model_name=media_type)
    media_queryset = model.objects.filter(q_objects).select_related("item")
    media_queryset = BasicMedia.objects._apply_prefetch_related(
        media_queryset,
        media_type,
    )
    BasicMedia.objects.annotate_max_progress(media_queryset, media_type)

    # For podcasts, order by created_at descending to get most recent entry when multiple exist
    # This allows multiple plays of the same episode to be tracked separately in the DB
    # but we show the most recent one in the UI
    if media_type == MediaTypes.PODCAST.value:
        media_queryset = media_queryset.order_by(
            "item__media_id", "item__source", "-created_at"
        )

    # Create a lookup dictionary for fast matching
    # For podcasts with multiple entries, keep only the most recent one (first after ordering)
    media_lookup = {}
    media_grouped = {}
    for media in media_queryset:
        if media_type == MediaTypes.SEASON.value:
            key = (media.item.media_id, media.item.source, media.item.season_number)
        else:
            key = (media.item.media_id, media.item.source)

        media_grouped.setdefault(key, []).append(media)

        # Only store the first (most recent for podcasts) entry for each key
        if key not in media_lookup:
            media_lookup[key] = media

    # Aggregate duplicates for non-podcast media to expose total progress/recent status
    if media_type != MediaTypes.PODCAST.value:
        for key, entries in media_grouped.items():
            if len(entries) > 1 and key in media_lookup:
                BasicMedia.objects._aggregate_item_data(media_lookup[key], entries)

    # Enrich items with matched media
    enriched_items = []
    for item in items:
        if media_type == MediaTypes.SEASON.value:
            key = (str(item["media_id"]), item["source"], item.get("season_number"))
        else:
            key = (str(item["media_id"]), item["source"])

        media_item = media_lookup.get(key)
        if (
            getattr(target_user, "hide_completed_recommendations", False)
            and section_name == "recommendations"
            and media_item
            and media_item.status == Status.COMPLETED.value
        ):
            continue

        enriched_item = {
            "item": item,
            "media": media_item,
        }
        enriched_items.append(enriched_item)

    return enriched_items


def default_status_for_new_entry(*, has_in_progress_entry=False):
    """Return the status to pre-select when tracking media the user has no entry for.

    The model default is Completed, which only suits play-based writes. A new
    entry made by hand starts as Planning (#1305), unless the user already has
    an in-progress entry for the same media.
    """
    from app.models import Status

    if has_in_progress_entry:
        return Status.IN_PROGRESS.value
    return Status.PLANNING.value


MIN_VALID_RELEASE_YEAR = 1900


def extract_release_datetime(metadata):
    """Extract release datetime from metadata dict."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    date_str = None
    for field in ["release_date", "first_air_date", "start_date", "publish_date"]:
        if metadata.get("details", {}).get(field):
            date_str = metadata["details"][field]
            break
        if metadata.get(field):
            date_str = metadata[field]
            break

    if not date_str:
        year = metadata.get("details", {}).get("year") or metadata.get("year")
        if year:
            try:
                parsed_year = int(year)
                if parsed_year < MIN_VALID_RELEASE_YEAR:
                    return None
                return datetime(parsed_year, 1, 1, tzinfo=ZoneInfo("UTC"))
            except (ValueError, TypeError):
                return None
        return None

    if isinstance(date_str, datetime):
        if date_str.tzinfo is None:
            return date_str.replace(tzinfo=ZoneInfo("UTC"))
        return date_str

    date_str = str(date_str)
    format_lengths = {
        "%Y-%m-%d": 10,
        "%Y-%m": 7,
        "%Y": 4,
    }
    for fmt, length in format_lengths.items():
        try:
            dt = datetime.strptime(date_str[:length], fmt)  # noqa: DTZ007  # date-only value; no timezone applies
            if dt.year < MIN_VALID_RELEASE_YEAR:
                continue
            return dt.replace(tzinfo=ZoneInfo("UTC"))
        except (ValueError, TypeError):
            continue

    return None


def get_user_collection(user, media_type=None):
    """Get user's collection entries with optional media type filtering.

    Args:
        user: Django user object
        media_type: Optional media type to filter by

    Returns:
        QuerySet of CollectionEntry objects
    """
    queryset = CollectionEntry.objects.filter(user=user).select_related("item")
    if media_type:
        queryset = queryset.filter(item__media_type=media_type)
    return queryset


def get_item_collection_entries(user, item):
    """Return all collection entries for a specific user/item pair."""
    return CollectionEntry.objects.filter(
        user=user,
        item=item,
    ).order_by("-collected_at", "-id")


def is_item_collected(user, item):
    """Check if a specific item is in user's collection.

    Args:
        user: Django user object
        item: Item object to check

    Returns:
        Most recently collected CollectionEntry object if found, None otherwise
    """
    return get_item_collection_entries(user, item).first()


def get_album_collection_metadata(user, album):
    """Get aggregated collection metadata for an album from all its tracks.

    For music albums, we aggregate collection metadata from all tracks that have
    collection entries. Returns the most common values (or first non-empty value)
    for fields that should be consistent across tracks (like audio_codec, audio_channels).

    Args:
        user: Django user object
        album: Album object

    Returns:
        Dictionary with collection metadata:
        - audio_codec: Most common audio codec across collected tracks
        - audio_channels: Most common audio channels across collected tracks
        - media_type: Most common media_type across collected tracks
        - has_collection: Boolean indicating if any tracks are collected
        - collected_count: Number of tracks with collection entries
    """
    from app.models import Music

    # Get all Music entries for this album
    music_entries = Music.objects.filter(
        user=user,
        album=album,
    ).select_related("item")

    # Get collection entries for all items in this album
    item_ids = [m.item_id for m in music_entries if m.item_id]
    if not item_ids:
        return {
            "has_collection": False,
            "collected_count": 0,
            "audio_codec": None,
            "audio_channels": None,
            "bitrate": None,
            "media_type": None,
        }

    collection_entries = CollectionEntry.objects.filter(
        user=user,
        item_id__in=item_ids,
    ).select_related("item")

    if not collection_entries.exists():
        return {
            "has_collection": False,
            "collected_count": 0,
            "audio_codec": None,
            "audio_channels": None,
            "media_type": None,
        }

    # Aggregate metadata - find most common values
    audio_codecs = {}
    audio_channels_list = {}
    bitrates = {}
    media_types = {}

    for entry in collection_entries:
        if entry.audio_codec:
            audio_codecs[entry.audio_codec] = audio_codecs.get(entry.audio_codec, 0) + 1
        if entry.audio_channels:
            audio_channels_list[entry.audio_channels] = (
                audio_channels_list.get(entry.audio_channels, 0) + 1
            )
        if entry.bitrate:
            bitrates[entry.bitrate] = bitrates.get(entry.bitrate, 0) + 1
        if entry.media_type:
            media_types[entry.media_type] = media_types.get(entry.media_type, 0) + 1

    # Get most common value (or first if tie)
    audio_codec = (
        max(audio_codecs.items(), key=lambda x: x[1])[0] if audio_codecs else None
    )
    audio_channels = (
        max(audio_channels_list.items(), key=lambda x: x[1])[0]
        if audio_channels_list
        else None
    )
    bitrate = max(bitrates.items(), key=lambda x: x[1])[0] if bitrates else None
    media_type = (
        max(media_types.items(), key=lambda x: x[1])[0] if media_types else None
    )

    return {
        "has_collection": True,
        "collected_count": collection_entries.count(),
        "audio_codec": audio_codec,
        "audio_channels": audio_channels,
        "bitrate": bitrate,
        "media_type": media_type,
    }


def get_collection_stats(user):
    """Get collection statistics for a user.

    Args:
        user: Django user object

    Returns:
        Dictionary with collection statistics:
        - total: Total number of collection entries
        - by_media_type: Count by media type
        - by_format: Count by media_type (format) field
    """
    collection = CollectionEntry.objects.filter(user=user)
    stats = {
        "total": collection.count(),
        "by_media_type": {},
        "by_format": {},
    }

    # Count by media type (Item.media_type)
    for entry in collection.select_related("item"):
        item_media_type = entry.item.media_type
        stats["by_media_type"][item_media_type] = (
            stats["by_media_type"].get(item_media_type, 0) + 1
        )

        # Count by format (CollectionEntry.media_type)
        if entry.media_type:
            stats["by_format"][entry.media_type] = (
                stats["by_format"].get(entry.media_type, 0) + 1
            )

    return stats


def get_artist_collection_stats(user, artist):
    """Get collection statistics for an artist.

    Args:
        user: Django user object
        artist: Artist object

    Returns:
        Dictionary with collection statistics:
        - collected_albums: Number of distinct albums with at least one collected track
        - collected_tracks: Total number of collected tracks from this artist
    """
    from app.models import Album, Music

    albums = Album.objects.filter(artist=artist)
    total_albums = albums.count()
    # Every query below is the same size however many albums and plays the
    # artist has: counts and ids in SQL, never a model per play.
    music_entries = Music.objects.filter(user=user, album__in=albums)
    total_tracks = music_entries.count()
    no_collection = {
        "collected_albums": 0,
        "total_albums": total_albums,
        "collected_tracks": 0,
        "total_tracks": total_tracks,
    }
    if not total_tracks:
        return no_collection

    # One id per collection entry: a track collected twice counts twice.
    collected_item_ids = list(
        CollectionEntry.objects.filter(
            user=user,
            item_id__in=music_entries.filter(item__isnull=False).values("item_id"),
        ).values_list("item_id", flat=True),
    )
    if not collected_item_ids:
        return no_collection

    # A collected track belongs to the album of its first entry, in the
    # model's default order (what ``.first()`` per track used to return).
    album_by_item_id = {}
    for item_id, album_id in music_entries.filter(
        item_id__in=set(collected_item_ids),
    ).values_list("item_id", "album_id"):
        album_by_item_id.setdefault(item_id, album_id)
    collected_album_ids = {
        album_by_item_id.get(item_id) for item_id in collected_item_ids
    } - {None}

    return {
        "collected_albums": len(collected_album_ids),
        "total_albums": total_albums,
        "collected_tracks": len(collected_item_ids),
        "total_tracks": total_tracks,
    }


def get_tv_show_collection_stats(user, tv_item, metadata_episode_count=None):
    """Get collection statistics for a TV show.

    Args:
        user: Django user object
        tv_item: Item object with media_type='tv' or 'anime'
        metadata_episode_count: Optional episode count from metadata (e.g., TMDB) to match Details pane

    Returns:
        Dictionary with collection statistics:
        - collected_seasons: Number of distinct seasons with at least one collected episode
        - collected_episodes: Total number of collected episodes from this show
        - total_seasons: Total number of seasons for this show
        - total_episodes: Total number of episodes for this show
    """
    from app.models import Item, MediaTypes

    # Always count total seasons and episodes from Item objects (all available, not just tracked)
    # This matches what the Details pane shows
    # Exclude Season 0 (Specials) to match Details pane behavior
    all_season_items = list(
        Item.objects.filter(
            media_id=tv_item.media_id,
            source=tv_item.source,
            media_type=MediaTypes.SEASON.value,
        ).exclude(season_number=0)
    )
    total_seasons = len(all_season_items)

    # Exclude episodes from Season 0 to match Details pane
    all_episode_items = list(
        Item.objects.filter(
            media_id=tv_item.media_id,
            source=tv_item.source,
            media_type=MediaTypes.EPISODE.value,
        ).exclude(season_number=0)
    )

    # Provider totals can lag the episode records used by the season audit.
    # Never use a total smaller than that same locally known episode set.
    # Guard against providers handing back an episode list instead of a count:
    # a non-numeric value would be rendered verbatim by the Collection panel.
    if isinstance(metadata_episode_count, bool) or not isinstance(
        metadata_episode_count,
        int,
    ):
        metadata_episode_count = None
    total_episodes = max(metadata_episode_count or 0, len(all_episode_items))

    # Get collection entries for all seasons and episodes
    season_item_ids = [item.id for item in all_season_items]
    episode_item_ids = [item.id for item in all_episode_items]

    # Get collection entries for seasons
    season_collection_item_ids = (
        list(
            CollectionEntry.objects.filter(
                user=user,
                item_id__in=season_item_ids,
            ).values_list("item_id", flat=True)
        )
        if season_item_ids
        else []
    )

    # Get collection entries for episodes
    episode_collection_item_ids = (
        list(
            CollectionEntry.objects.filter(
                user=user,
                item_id__in=episode_item_ids,
            ).values_list("item_id", flat=True)
        )
        if episode_item_ids
        else []
    )

    # Match season_details fallback behavior: a show-level collection entry represents
    # the entire show only when no granular season/episode collection rows exist.
    if not season_collection_item_ids and not episode_collection_item_ids:
        if CollectionEntry.objects.filter(user=user, item=tv_item).exists():
            return {
                "collected_seasons": total_seasons,
                "total_seasons": total_seasons,
                "collected_episodes": total_episodes,
                "total_episodes": total_episodes,
            }

        return {
            "collected_seasons": 0,
            "total_seasons": total_seasons,
            "collected_episodes": 0,
            "total_episodes": total_episodes,
        }

    # Count distinct seasons that have at least one collected episode
    # A season is "collected" if either:
    # 1. The season Item itself has a collection entry, OR
    # 2. At least one episode in that season has a collection entry
    collected_season_ids = set()
    season_by_number = {
        item.season_number: item
        for item in all_season_items
        if item.season_number is not None
    }
    season_by_id = {item.id: item for item in all_season_items}
    episode_by_id = {item.id: item for item in all_episode_items}
    episode_ids_by_season_number = {}
    for item in all_episode_items:
        if item.season_number is None:
            continue
        episode_ids_by_season_number.setdefault(item.season_number, set()).add(item.id)
    collected_episode_ids = set(episode_collection_item_ids)

    # Add seasons that have direct collection entries
    collected_season_ids.update(season_collection_item_ids)
    for season_item_id in season_collection_item_ids:
        season_item = season_by_id.get(season_item_id)
        if season_item and season_item.season_number is not None:
            collected_episode_ids.update(
                episode_ids_by_season_number.get(season_item.season_number, set())
            )

    # Add seasons that have at least one collected episode
    for episode_item_id in episode_collection_item_ids:
        episode_item = episode_by_id.get(episode_item_id)
        if episode_item and episode_item.season_number is not None:
            season_item = season_by_number.get(episode_item.season_number)
            if season_item:
                collected_season_ids.add(season_item.id)

    return {
        "collected_seasons": len(collected_season_ids),
        "total_seasons": total_seasons,
        "collected_episodes": len(collected_episode_ids),
        "total_episodes": total_episodes,
    }


def _resolve_show_item_for_season(season_item):
    """Find the parent show's TV Item for a season, tolerating bucket duplicates.

    A show can legitimately have more than one TV-media-type Item (e.g. one in
    the 'tv' bucket, one in a 'season' bucket) once the fork's unique
    constraint scopes on library_media_type — see issue #1015. Prefer the row
    sharing the season's own bucket, then fall back to any other match rather
    than raising MultipleObjectsReturned.
    """
    from app.models import Item, MediaTypes

    tv_items = Item.objects.filter(
        media_id=season_item.media_id,
        source=season_item.source,
        media_type=MediaTypes.TV.value,
    )
    return (
        tv_items.filter(library_media_type=season_item.library_media_type).first()
        or tv_items.exclude(library_media_type=season_item.library_media_type)
        .order_by("id")
        .first()
    )


def get_season_collection_stats(user, season_item):
    """Get collection statistics for a specific season.

    Args:
        user: Django user object
        season_item: Item object with media_type='season'

    Returns:
        Dictionary with collection statistics:
        - collected_episodes: Number of collected episodes in this season
        - total_episodes: Total number of episodes in this season
    """
    from app.models import Item, MediaTypes

    # Get all episodes for this season
    all_episode_items = Item.objects.filter(
        media_id=season_item.media_id,
        source=season_item.source,
        media_type=MediaTypes.EPISODE.value,
        season_number=season_item.season_number,
    )
    total_episodes = all_episode_items.count()

    if total_episodes == 0:
        return {
            "collected_episodes": 0,
            "total_episodes": 0,
        }

    # Get collection entries for episodes in this season
    episode_item_ids = list(all_episode_items.values_list("id", flat=True))
    episode_collection_entries = CollectionEntry.objects.filter(
        user=user,
        item_id__in=episode_item_ids,
    )

    # Count episodes, not copies: a Blu-ray and a digital copy are one episode.
    collected_count = episode_collection_entries.values("item_id").distinct().count()

    # If no episode-level entries exist, fall back to broader collection rows.
    # A manual season-level entry represents the whole season in the current UI.
    if collected_count == 0:
        season_collection_entry = CollectionEntry.objects.filter(
            user=user,
            item=season_item,
        ).exists()
        if season_collection_entry:
            collected_count = total_episodes

    show_has_granular_collection = CollectionEntry.objects.filter(
        user=user,
        item__media_id=season_item.media_id,
        item__source=season_item.source,
        item__media_type__in=[MediaTypes.SEASON.value, MediaTypes.EPISODE.value],
    ).exists()

    # If no episode or season-level entries exist anywhere for the show,
    # a show-level collection entry can still represent the whole season.
    if collected_count == 0:
        tv_item = _resolve_show_item_for_season(season_item)
        if tv_item is not None:
            show_collection_entry = CollectionEntry.objects.filter(
                user=user,
                item=tv_item,
            ).exists()

            if show_collection_entry and not show_has_granular_collection:
                collected_count = total_episodes

    return {
        "collected_episodes": collected_count,
        "total_episodes": total_episodes,
    }


def get_collection_completeness_map(user, show_items):
    """Get TV/anime collection completeness for multiple shows in a batch.

    Mirrors the fallback logic of get_tv_show_collection_stats and
    get_season_collection_stats (show-level entry counts as fully collected
    when no season/episode rows exist; a season-level entry counts as fully
    collected when it has no episode rows), but computes it for many shows
    using a fixed number of queries instead of looping per show/season, so
    it is safe to call once per page of collection rows.

    Args:
        user: Django user object
        show_items: iterable of Item objects with media_type in (tv, anime)

    Returns:
        Dict keyed by show Item id and by season Item id (for seasons with
        at least one episode), each mapping to:
        {"collected": int, "total": int, "is_partial": bool}
        A row is "is_partial" only when 0 < collected < total.
    """
    from app.models import Item, MediaTypes

    show_items = list(show_items)
    if not show_items:
        return {}

    show_keys = {(item.media_id, item.source) for item in show_items}
    media_ids = {media_id for media_id, _source in show_keys}
    sources = {source for _media_id, source in show_keys}

    all_season_items = [
        item
        for item in Item.objects.filter(
            media_id__in=media_ids,
            source__in=sources,
            media_type=MediaTypes.SEASON.value,
        ).exclude(season_number=0)
        if (item.media_id, item.source) in show_keys
    ]
    all_episode_items = [
        item
        for item in Item.objects.filter(
            media_id__in=media_ids,
            source__in=sources,
            media_type=MediaTypes.EPISODE.value,
        ).exclude(season_number=0)
        if (item.media_id, item.source) in show_keys
    ]

    def collected_item_ids(item_ids):
        if not item_ids:
            return set()
        item_ids = list(item_ids)
        chunk_size = 500
        collected = set()
        for offset in range(0, len(item_ids), chunk_size):
            collected.update(
                CollectionEntry.objects.filter(
                    user=user,
                    item_id__in=item_ids[offset : offset + chunk_size],
                ).values_list("item_id", flat=True)
            )
        return collected

    collected_season_ids = collected_item_ids([item.id for item in all_season_items])
    collected_episode_ids = collected_item_ids([item.id for item in all_episode_items])
    collected_show_ids = collected_item_ids([item.id for item in show_items])

    seasons_by_show = {}
    for item in all_season_items:
        seasons_by_show.setdefault((item.media_id, item.source), []).append(item)
    episodes_by_show = {}
    for item in all_episode_items:
        episodes_by_show.setdefault((item.media_id, item.source), []).append(item)

    result = {}
    for show_item in show_items:
        key = (show_item.media_id, show_item.source)
        season_items_for_show = seasons_by_show.get(key, [])
        episode_items_for_show = episodes_by_show.get(key, [])
        total_episodes = len(episode_items_for_show)

        episode_ids_by_season_number = {}
        for item in episode_items_for_show:
            if item.season_number is not None:
                episode_ids_by_season_number.setdefault(
                    item.season_number,
                    set(),
                ).add(item.id)

        show_seasons_collected = {
            item.id for item in season_items_for_show if item.id in collected_season_ids
        }
        show_episodes_collected = {
            item.id
            for item in episode_items_for_show
            if item.id in collected_episode_ids
        }
        show_has_granular_collection = bool(
            show_seasons_collected or show_episodes_collected,
        )

        if not show_has_granular_collection:
            collected_episodes_count = (
                total_episodes if show_item.id in collected_show_ids else 0
            )
        else:
            collected_episode_id_set = set(show_episodes_collected)
            for season_item in season_items_for_show:
                if season_item.id in show_seasons_collected:
                    collected_episode_id_set.update(
                        episode_ids_by_season_number.get(
                            season_item.season_number,
                            set(),
                        ),
                    )
            collected_episodes_count = len(collected_episode_id_set)

        result[show_item.id] = {
            "collected": collected_episodes_count,
            "total": total_episodes,
            "is_partial": 0 < collected_episodes_count < total_episodes,
        }

        for season_item in season_items_for_show:
            season_episode_ids = episode_ids_by_season_number.get(
                season_item.season_number,
                set(),
            )
            season_total = len(season_episode_ids)
            if season_total == 0:
                continue
            season_collected = len(season_episode_ids & show_episodes_collected)
            if season_collected == 0 and season_item.id in show_seasons_collected:
                season_collected = season_total
            if (
                season_collected == 0
                and not show_has_granular_collection
                and show_item.id in collected_show_ids
            ):
                season_collected = season_total
            result[season_item.id] = {
                "collected": season_collected,
                "total": season_total,
                "is_partial": 0 < season_collected < season_total,
            }

    return result


def get_season_collection_metadata(user, season_item):
    """Get aggregated collection metadata for a season from all its episodes.

    Similar to get_album_collection_metadata, this aggregates collection metadata
    from all episodes in the season that have collection entries. Returns the most
    common values for fields that should be consistent across episodes.

    Args:
        user: Django user object
        season_item: Item object with media_type='season'

    Returns:
        Dictionary with collection metadata (or None if no episodes are collected):
        - resolution: Most common resolution across collected episodes
        - hdr: Most common HDR format across collected episodes
        - audio_codec: Most common audio codec across collected episodes
        - audio_channels: Most common audio channels across collected episodes
        - bitrate: Most common bitrate across collected episodes
        - media_type: Most common media_type across collected episodes
        - is_3d: True if any episode is 3D
        - collected_at: Earliest collected_at date from all episodes
    """
    from django.db.models import Count

    from app.models import Item, MediaTypes

    # Get all episodes for this season
    all_episode_items = Item.objects.filter(
        media_id=season_item.media_id,
        source=season_item.source,
        media_type=MediaTypes.EPISODE.value,
        season_number=season_item.season_number,
    )

    if not all_episode_items.exists():
        return None

    # Get collection entries for episodes in this season
    episode_item_ids = list(all_episode_items.values_list("id", flat=True))
    collected_episodes = CollectionEntry.objects.filter(
        user=user,
        item_id__in=episode_item_ids,
    )

    if not collected_episodes.exists():
        # Check if there's a season-level or show-level collection entry
        season_collection_entry = CollectionEntry.objects.filter(
            user=user,
            item=season_item,
        ).first()

        if season_collection_entry:
            # Return the season-level entry metadata
            return {
                "resolution": season_collection_entry.resolution or "",
                "hdr": season_collection_entry.hdr or "",
                "audio_codec": season_collection_entry.audio_codec or "",
                "audio_channels": season_collection_entry.audio_channels or "",
                "bitrate": season_collection_entry.bitrate,
                "media_type": season_collection_entry.media_type or "",
                "is_3d": season_collection_entry.is_3d,
                "collected_at": season_collection_entry.collected_at,
            }

        # Check for show-level entry
        show_has_granular_collection = CollectionEntry.objects.filter(
            user=user,
            item__media_id=season_item.media_id,
            item__source=season_item.source,
            item__media_type__in=[MediaTypes.SEASON.value, MediaTypes.EPISODE.value],
        ).exists()
        tv_item = _resolve_show_item_for_season(season_item)
        if tv_item is not None:
            show_collection_entry = CollectionEntry.objects.filter(
                user=user,
                item=tv_item,
            ).first()

            if show_collection_entry and not show_has_granular_collection:
                # Return the show-level entry metadata
                return {
                    "resolution": show_collection_entry.resolution or "",
                    "hdr": show_collection_entry.hdr or "",
                    "audio_codec": show_collection_entry.audio_codec or "",
                    "audio_channels": show_collection_entry.audio_channels or "",
                    "bitrate": show_collection_entry.bitrate,
                    "media_type": show_collection_entry.media_type or "",
                    "is_3d": show_collection_entry.is_3d,
                    "collected_at": show_collection_entry.collected_at,
                }

        return None

    # Aggregate the most common values for each metadata field
    def get_most_common(queryset, field_name):
        counts = (
            queryset.exclude(**{field_name: ""})
            .exclude(**{field_name: None})
            .values(field_name)
            .annotate(count=Count(field_name))
            .order_by("-count")
            .first()
        )
        return counts[field_name] if counts else None

    # Get most common values
    resolution = get_most_common(collected_episodes, "resolution")
    hdr = get_most_common(collected_episodes, "hdr")
    audio_codec = get_most_common(collected_episodes, "audio_codec")
    audio_channels = get_most_common(collected_episodes, "audio_channels")
    media_type = get_most_common(collected_episodes, "media_type")

    # For bitrate, get the most common non-null value
    bitrate_counts = (
        collected_episodes.exclude(bitrate=None)
        .values("bitrate")
        .annotate(count=Count("bitrate"))
        .order_by("-count")
        .first()
    )
    bitrate = bitrate_counts["bitrate"] if bitrate_counts else None

    # For is_3d, check if any episode is 3D
    is_3d = collected_episodes.filter(is_3d=True).exists()

    # Get earliest collected_at date
    earliest_collected = collected_episodes.order_by("collected_at").first()
    collected_at = earliest_collected.collected_at if earliest_collected else None

    return {
        "resolution": resolution or "",
        "hdr": hdr or "",
        "audio_codec": audio_codec or "",
        "audio_channels": audio_channels or "",
        "bitrate": bitrate,
        "media_type": media_type or "",
        "is_3d": is_3d,
        "collected_at": collected_at,
    }


def get_configured_app_url():
    """Return the configured public application origin, if one is available."""
    for url in getattr(settings, "URLS", []):
        if url:
            return url.rstrip("/")

    base_url = getattr(settings, "BASE_URL", None)
    parsed_base_url = urlparse(base_url or "")
    if parsed_base_url.scheme and parsed_base_url.netloc:
        return base_url.rstrip("/")

    return None


def build_absolute_app_url(request, path):
    """Build an absolute URL using the configured public origin when possible."""
    parsed_path = urlparse(path)
    if parsed_path.scheme and parsed_path.netloc:
        return path

    configured_app_url = get_configured_app_url()
    if configured_app_url:
        return urljoin(f"{configured_app_url}/", path.lstrip("/"))

    if request is None:
        return None

    return request.build_absolute_uri(path)


def supports_oauth_redirect(callback_url):
    """Whether an OAuth provider will accept this callback URL.

    Trakt (and a growing number of providers) reject redirect URIs that are not
    HTTPS unless the host is loopback, so an instance served over plain HTTP on
    a LAN address cannot use the browser-redirect flow at all (#681).
    """
    if not callback_url:
        return False

    parsed = urlparse(callback_url)
    if parsed.scheme == "https":
        return True
    if parsed.scheme != "http":
        return False

    hostname = parsed.hostname
    if not hostname:
        return False
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def parse_completion_datetime(value):
    """Return an aware datetime for a user-supplied completion date.

    Accepts a datetime, a date, an ISO datetime string, or an ISO date string,
    and raises ``ValueError`` for anything else. A date without a time is read
    as midnight, and a naive value is made aware in the active timezone.

    The web forms and the REST API both take a completion date from the user
    and must agree on what a given string means. Keeping one reader here is
    what stops them drifting: the API used to reject an unparsable date while
    the web form quietly discarded it and recorded something else.
    """
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


def split_pass_history(tracked_plays, season):
    """Return (plays in the season's current rewatch pass, every play).

    Outside a pass the two are the same, so callers can key display off the
    first and lifetime counts off the second without special-casing.
    """
    if season is None:
        return tracked_plays, tracked_plays
    return [
        play for play in tracked_plays if season.play_counts_for_pass(play)
    ], tracked_plays


def entry_activity_key(entry):
    """Sort key that puts the most recently active tracked entry last."""
    dates = [d for d in (entry.end_date, entry.start_date) if d]
    primary_date = max(dates) if dates else entry.created_at
    return (
        primary_date,
        entry.start_date or entry.created_at,
        entry.created_at,
    )


def progress_activity(media):
    """Return when an entry last progressed, for the Last Watched fallback.

    A completed entry without a watch date has none: its progress time only
    records when it was logged, so it must not rank as just watched.
    """
    if getattr(media, "status", None) == Status.COMPLETED.value:
        return None
    return getattr(media, "progressed_at", None)
