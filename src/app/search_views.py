import logging
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.db import connection, transaction
from django.db.models import Exists, F, OuterRef, Q
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext, ngettext
from django.views.decorators.http import require_GET

from app import helpers
from app.library_query import LibraryQueryExecutor
from app.library_query.adapters import from_media_list_filters
from app.library_query.filters import shows_with_collected_episodes
from app.library_query.spec import ROUTING_LIBRARY
from app.library_query.trackers import tracker_sources
from app.log_safety import exception_summary
from app.media_list_filters import (
    MediaListEntry,
    MediaListFilters,
    media_list_entries_for_items,
)
from app.models import (
    AlbumTracker,
    ArtistTracker,
    BasicMedia,
    CollectionEntry,
    CollectionField,
    Item,
    ItemTag,
    MediaTypes,
    PodcastShowTracker,
)
from app.providers import services
from app.services import metadata_resolution
from app.templatetags import app_tags
from app.templatetags.app_tags import (
    image_url,
    media_type_readable,
    media_type_readable_plural,
    media_url,
    music_album_url,
    music_artist_url,
)
from users.models import ALL_SEARCH_TYPE, VALID_SEARCH_TYPES, MediaStatusChoices

logger = logging.getLogger(__name__)

# Minimum characters before the search bar fires autocomplete suggestions.
MIN_SUGGESTION_QUERY_LENGTH = 2
# Release years at or below this are placeholders, not shown on a suggestion.
MIN_SUGGESTION_YEAR = 1800

# Per-type cap on the library-only "All" search page, which shows every
# enabled type at once (#1160).
LOCAL_GROUP_LIMIT = 12
ALL_SUGGESTIONS_PER_TYPE = 3


def _mark_grouped_anime_route(media_items):
    """Annotate grouped-anime rows so templates route them through the Anime UI."""
    for media in media_items or []:
        media.route_media_type = MediaTypes.ANIME.value
        item = getattr(media, "item", None)
        if item is not None:
            item.route_media_type = MediaTypes.ANIME.value
    return media_items


def _norm(text):
    return str(text or "").strip().casefold()


def _title_fields(item_obj):
    if isinstance(item_obj, dict):
        return (
            item_obj.get("title"),
            item_obj.get("original_title"),
            item_obj.get("localized_title"),
        )
    return (
        getattr(item_obj, "title", None),
        getattr(item_obj, "original_title", None),
        getattr(item_obj, "localized_title", None),
    )


def _display_title_for_user(item_obj, user):
    if hasattr(item_obj, "get_display_title"):
        return item_obj.get_display_title(user=user)

    title, original_title, localized_title = _title_fields(item_obj)
    title = str(title or "").strip()
    original_title = str(original_title or "").strip() or None
    localized_title = str(localized_title or "").strip() or None

    if not localized_title and title:
        localized_title = title

    preference = getattr(user, "title_display_preference", "localized")
    if preference == "original":
        return original_title or localized_title or title
    return localized_title or original_title or title


def _matched_title(item_obj, search_query, user):
    normalized_query = _norm(search_query)
    if not normalized_query:
        return None

    display_title = _display_title_for_user(item_obj, user)
    display_norm = _norm(display_title)

    title, original_title, localized_title = _title_fields(item_obj)
    candidates = []
    for candidate in (title, localized_title, original_title):
        text = str(candidate or "").strip()
        if text and text not in candidates:
            candidates.append(text)

    # Prefer exact, then prefix, then contains.
    for predicate in (
        lambda value: _norm(value) == normalized_query,
        lambda value: _norm(value).startswith(normalized_query),
        lambda value: normalized_query in _norm(value),
    ):
        for candidate in candidates:
            if _norm(candidate) == display_norm:
                continue
            if predicate(candidate):
                return candidate
    return None


def _tagged_untracked_items(user, media_type, query, library_ids):
    """Return the user's tagged ``media_type`` items the library query does not cover.

    A tag can sit on an item with no tracker and no collection entry (#1160),
    so the library query misses it. ``library_ids`` is what that query matched
    (a subquery or a set of ids). Kept as one relational query so the caller's
    slice bounds the work.
    """
    tagged = Q(pk__in=ItemTag.objects.filter(tag__user=user).values("item_id"))
    include_anime_in_anime, include_anime_in_tv = (
        metadata_resolution.anime_library_visibility(user)
    )
    grouped_anime = Q(
        media_type=MediaTypes.TV.value,
        library_media_type=MediaTypes.ANIME.value,
    )
    if media_type == MediaTypes.ANIME.value:
        type_filter = Q(media_type=MediaTypes.ANIME.value)
        if include_anime_in_anime:
            type_filter |= grouped_anime
    elif media_type == MediaTypes.TV.value:
        type_filter = Q(media_type=MediaTypes.TV.value)
        if not include_anime_in_tv:
            type_filter &= ~grouped_anime
    else:
        type_filter = Q(media_type=media_type)

    return (
        Item.objects.filter(
            type_filter,
            tagged,
            Q(title__icontains=query)
            | Q(original_title__icontains=query)
            | Q(localized_title__icontains=query),
        )
        .exclude(id__in=library_ids)
        .defer("watch_providers")
        .order_by("title", "id")
    )


_BARCODE_RE = re.compile(r"\d{8,18}|\d{9}[\dX]")


def _barcode_owned_items(user, media_type, query):
    """Return the user's ``media_type`` items whose Barcode field matches *query*.

    A scan reads the 12 or 13 digit code, while a CLZ export may carry a 2 or
    5 digit supplement after it, so a stored barcode matches on its start.
    """
    code = re.sub(r"[\s-]", "", query).upper()
    if not _BARCODE_RE.fullmatch(code):
        return []
    field_ids = [
        str(field_id)
        for field_id in CollectionField.objects.filter(
            group__user=user,
            label__iexact="Barcode",
        ).values_list("id", flat=True)
    ]
    if not field_ids:
        return []

    item_ids = {}
    entries = CollectionEntry.objects.filter(
        user=user,
        item__media_type=media_type,
    ).values_list("item_id", "custom_field_values")
    for item_id, values in entries:
        for field_id in field_ids:
            stored = re.sub(r"[\s-]", "", str((values or {}).get(field_id) or ""))
            if stored.upper().startswith(code):
                item_ids[item_id] = None
    return list(Item.objects.filter(id__in=item_ids).order_by("title", "id"))


def _merge_by_title(entries, extra_items, limit):
    """Merge ``MediaListEntry`` rows with media-less items by title, capped at ``limit``."""
    merged = list(entries)
    merged += [MediaListEntry(item=item, media=None) for item in extra_items[:limit]]
    merged.sort(key=lambda entry: (entry.item.title or "").lower())
    return merged[:limit]


class PodcastShowAdapter:
    """Adapter to make PodcastShowTracker compatible with media components."""

    def __init__(self, tracker, item=None):
        """Copy the tracker's fields and find or create the show's Item.

        ``item`` is the show's already-fetched Item, when the caller batched
        the lookup; otherwise it is fetched or created here.
        """
        self.tracker = tracker
        self.id = tracker.id
        self.status = tracker.status
        self.score = tracker.score
        self.start_date = tracker.start_date
        self.end_date = tracker.end_date
        self.notes = tracker.notes
        self.created_at = tracker.created_at
        self.updated_at = tracker.updated_at

        if item is None:
            item, _ = Item.objects.get_or_create(
                media_id=tracker.show.podcast_uuid,
                source=tracker.show.source,
                media_type=MediaTypes.PODCAST.value,
                defaults={
                    "title": tracker.show.title,
                    "image": tracker.show.image or settings.IMG_NONE,
                },
            )
        self.item = item
        show_image = tracker.show.image or settings.IMG_NONE
        if self.item.title != tracker.show.title or self.item.image != show_image:
            self.item.title = tracker.show.title
            self.item.image = show_image
            self.item.save(update_fields=["title", "image"])


def _local_podcast_results(user, query, limit):
    """Return ``(results, total)`` for the user's tracked or tagged podcast shows."""
    show_trackers = (
        PodcastShowTracker.objects.filter(user=user)
        .exclude(show__title__isnull=True)
        .exclude(show__title__exact="")
        .filter(show__title__icontains=query)
    )
    # A show can be tagged without being tracked (#1160).
    tagged_only = (
        Item.objects.filter(
            Exists(ItemTag.objects.filter(tag__user=user, item_id=OuterRef("pk"))),
            media_type=MediaTypes.PODCAST.value,
            title__icontains=query,
        )
        .exclude(
            media_id__in=PodcastShowTracker.objects.filter(user=user).values(
                "show__podcast_uuid",
            ),
        )
        .order_by("title", "id")
    )
    total = show_trackers.count() + tagged_only.count()
    page_trackers = list(
        show_trackers.select_related("show").order_by("show__title")[:limit],
    )
    # One query for the shows' existing Items instead of a get_or_create each.
    existing_items = {
        (item.media_id, item.source): item
        for item in Item.objects.filter(
            media_type=MediaTypes.PODCAST.value,
            media_id__in=[t.show.podcast_uuid for t in page_trackers],
        )
    }
    results = [
        {
            "item": media.item,
            "media": media,
            "matched_title": _matched_title(media.item, query, user),
        }
        for media in (
            PodcastShowAdapter(
                tracker,
                item=existing_items.get(
                    (tracker.show.podcast_uuid, tracker.show.source),
                ),
            )
            for tracker in page_trackers
        )
    ]
    results += [
        {
            "item": item,
            "media": None,
            "matched_title": _matched_title(item, query, user),
        }
        for item in tagged_only[:limit]
    ]
    results.sort(key=lambda result: (result["item"].title or "").lower())
    return results[:limit], total


def _local_music_results(user, query, limit):
    """Return the user's tracked artists and albums matching ``query``."""
    artist_trackers = (
        ArtistTracker.objects.filter(user=user)
        .exclude(artist__name__isnull=True)
        .exclude(artist__name__exact="")
        .filter(artist__name__icontains=query)
        .select_related("artist")
    )
    album_trackers = (
        AlbumTracker.objects.filter(user=user)
        .exclude(album__title__isnull=True)
        .exclude(album__title__exact="")
        .filter(
            Q(album__title__icontains=query) | Q(album__artist__name__icontains=query),
        )
        .select_related("album", "album__artist")
        .prefetch_related("album__artist_credits__artist")
    )
    return {
        "artists": list(artist_trackers.order_by("artist__name")[:limit]),
        "artists_total": artist_trackers.count(),
        "albums": list(album_trackers.order_by("album__title")[:limit]),
        "albums_total": album_trackers.count(),
    }


def _local_media_results(
    user,
    media_type,
    query,
    limit,
    *,
    annotate_progress=True,
    count_total=True,
):
    """Return ``(results, total)`` for tracked, collected or tagged ``media_type`` items.

    Only the first ``limit`` items are loaded; the total is counted in SQL
    unless ``count_total`` is false (typeahead, which never shows it).
    """
    # Every status plus "no status", so an imported rating with no status and
    # a collected-only item both count (the media list's "everything" view).
    filters = MediaListFilters(
        statuses=tuple(
            value
            for value in MediaStatusChoices.values
            if value != MediaStatusChoices.ALL
        ),
        include_no_status=True,
        search=query,
        sort="title",
        direction="asc",
    )
    library_query = from_media_list_filters(filters, (media_type,))
    # Search starts from the owner's item ids. The general collection-only
    # predicate uses a correlated EXISTS, which makes SQLite scan the entire
    # catalogue for an unfiltered title search, even for a tiny saved library.
    membership = Q(
        pk__in=CollectionEntry.objects.filter(user=user)
        .exclude(item__media_type=MediaTypes.EPISODE.value)
        .values("item_id"),
    )
    if media_type in (MediaTypes.TV.value, MediaTypes.ANIME.value):
        membership |= Q(
            media_type__in=(MediaTypes.TV.value, MediaTypes.ANIME.value),
            pk__in=shows_with_collected_episodes(user),
        )
    tracked = Q(pk__in=[])
    for source in tracker_sources(user, media_type, library_query.routing):
        item_ids = source.item_ids(user)
        # Collection-only membership must not bypass tracker routing (for
        # example, grouped anime that the user excludes from the TV library).
        membership &= ~Q(pk__in=item_ids)
        tracked |= source.item_q & Q(pk__in=item_ids)
    # UNION lets SQLite plan each indexed membership branch independently;
    # joining the branches with OR can revert to a full catalogue scan.
    scope = (
        Item.objects.filter(membership).order_by().values("pk")
        .union(Item.objects.filter(tracked).order_by().values("pk"))
    )
    executor = LibraryQueryExecutor(
        user,
        replace(library_query, within=scope),
    )
    library_total = executor.count() if count_total else 0
    items = executor.page(0, limit, total=library_total, defer=("watch_providers",)).items
    # Typeahead only reads item titles, artwork and URLs. Loading tracker
    # graphs here also loads every season/episode for each matching show.
    entries = (
        media_list_entries_for_items(user, items)
        if annotate_progress
        else [MediaListEntry(item=item, media=None) for item in items]
    )
    tagged_only = _tagged_untracked_items(user, media_type, query, executor.matches())
    total = library_total + tagged_only.count() if count_total else 0
    merged = _merge_by_title(entries, tagged_only, limit)

    if media_type == MediaTypes.ANIME.value:
        for entry in merged:
            if entry.item.media_type == MediaTypes.TV.value:
                _mark_grouped_anime_route(
                    [entry.media] if entry.media is not None else [entry.item],
                )
    if annotate_progress:
        BasicMedia.objects.annotate_max_progress(
            [entry.media for entry in merged if entry.media is not None],
            media_type,
        )
    results = [
        {
            "item": entry.item,
            "media": entry.media,
            "matched_title": _matched_title(entry.item, query, user),
        }
        for entry in merged
    ]
    return results, total


def _local_library_groups(user, query, limit):
    """Return one result group per enabled media type the user has a match in.

    Library-only (no provider calls), so a global search stays cheap (#1160).
    """
    groups = []
    for media_type in user.get_enabled_media_types():
        if media_type == MediaTypes.SEASON.value:
            continue
        group = {
            "media_type": media_type,
            "label": media_type_readable_plural(media_type),
            "kind": "media",
        }
        if media_type == MediaTypes.MUSIC.value:
            group.update(_local_music_results(user, query, limit))
            group["kind"] = "music"
            group["total"] = group["artists_total"] + group["albums_total"]
        elif media_type == MediaTypes.PODCAST.value:
            group["results"], group["total"] = _local_podcast_results(
                user,
                query,
                limit,
            )
        else:
            group["results"], group["total"] = _local_media_results(
                user,
                media_type,
                query,
                limit,
            )
        if group["total"]:
            groups.append(group)
    return groups

# Types sold with a book barcode: the camera shows for these (and All).
BARCODE_SEARCH_TYPES = (
    MediaTypes.BOOK.value,
    MediaTypes.MANGA.value,
    MediaTypes.COMIC.value,
    MediaTypes.COMIC_ISSUE.value,
)


def _item_key(item):
    """Identify an Item or an enriched provider result dict."""
    get = item.get if isinstance(item, dict) else lambda k: getattr(item, k, None)
    return (get("media_type"), get("source"), str(get("media_id")))


def _all_scope_barcode_groups(request, query, groups):
    """Add saved Barcode matches and online ISBN results to All-scope groups.

    A checksum-valid ISBN is searched with each barcode-capable type in the
    user's search menu, using that type's default provider. One provider
    failing leaves the other types' results in place.
    """
    user = request.user
    by_type = {group["media_type"]: group for group in groups}

    def group_for(media_type):
        return by_type.setdefault(
            media_type,
            {
                "media_type": media_type,
                "label": media_type_readable_plural(media_type),
                "kind": "media",
                "results": [],
                "total": 0,
            },
        )

    enabled = [
        media_type
        for media_type in user.get_enabled_media_types()
        if media_type not in (MediaTypes.SEASON.value, MediaTypes.PODCAST.value)
    ]
    for media_type in enabled:
        group = None
        for item in _barcode_owned_items(user, media_type, query):
            group = group_for(media_type)
            if _item_key(item) not in {_item_key(r["item"]) for r in group["results"]}:
                group["results"].insert(
                    0,
                    {"item": item, "media": None, "matched_title": None},
                )
                group["total"] += 1

    isbn = services._normalize_isbn_candidate(query)
    if isbn:
        for media_type in (t for t in BARCODE_SEARCH_TYPES if t in enabled):
            source = metadata_resolution.metadata_default_source(user, media_type)
            try:
                with services.interactive_request_scope():
                    data = services.search(
                        media_type,
                        isbn,
                        1,
                        source,
                        user=user,
                        language=metadata_resolution.metadata_language_default(user),
                    )
            except (services.ProviderNotConfiguredError, services.ProviderAPIError) as exc:
                logger.warning(
                    "ISBN search skipped for media_type=%s: %s",
                    media_type,
                    exception_summary(exc),
                )
                continue
            online = helpers.enrich_items_with_user_data(
                request,
                data.get("results") or [],
                section_name="search",
            )
            if not online:
                continue
            group = group_for(media_type)
            shown = {_item_key(r["item"]) for r in group["results"]}
            fresh = [
                {**r, "matched_title": None}
                for r in online[:LOCAL_GROUP_LIMIT]
                if _item_key(r["item"]) not in shown
            ]
            group["results"] += fresh
            group["total"] += len(fresh)

    order = {t: i for i, t in enumerate(enabled)}
    return sorted(by_type.values(), key=lambda g: order.get(g["media_type"], 99))


def _search_context(request):
    """Return the template context for a search request, whatever its media type."""
    requested_media_type = request.GET.get("media_type", "")
    if request.user.is_authenticated:
        media_type = request.user.update_preference(
            "last_search_type",
            requested_media_type,
        )
    elif requested_media_type in VALID_SEARCH_TYPES:
        media_type = requested_media_type
    else:
        media_type = MediaTypes.TV.value
    query = request.GET.get("q", "")
    page = helpers.get_page_number(request)
    layout = request.GET.get("layout", "grid")

    if media_type == ALL_SEARCH_TYPE:
        local_groups = []
        isbn_search = False
        if request.user.is_authenticated and query:
            try:
                local_groups = _local_library_groups(
                    request.user,
                    query,
                    LOCAL_GROUP_LIMIT,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Local search failed: %s", exception_summary(exc))
        if request.user.is_authenticated and query:
            local_groups = _all_scope_barcode_groups(request, query, local_groups)
            isbn_search = bool(services._normalize_isbn_candidate(query))
        return {
            "user": request.user,
            "media_type": media_type,
            "layout": layout,
            "local_groups": local_groups,
            "isbn_search": isbn_search,
            "local_group_limit": LOCAL_GROUP_LIMIT,
        }

    local_results = []
    local_results_total = 0
    local_results_limit = 24
    local_results_kind = "media"
    local_music_artists = []
    local_music_artists_total = 0
    local_music_albums = []
    local_music_albums_total = 0
    if request.user.is_authenticated and query and page == 1:
        try:
            if media_type == MediaTypes.PODCAST.value:
                local_results, local_results_total = _local_podcast_results(
                    request.user,
                    query,
                    local_results_limit,
                )
            elif media_type == MediaTypes.MUSIC.value:
                music = _local_music_results(request.user, query, local_results_limit)
                local_music_artists = music["artists"]
                local_music_artists_total = music["artists_total"]
                local_music_albums = music["albums"]
                local_music_albums_total = music["albums_total"]
                local_results_total = (
                    local_music_artists_total + local_music_albums_total
                )
                local_results_kind = "music"
            else:
                local_results, local_results_total = _local_media_results(
                    request.user,
                    media_type,
                    query,
                    local_results_limit,
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Local search failed: %s", exception_summary(exc))

    if (
        request.user.is_authenticated
        and page == 1
        and local_results_kind == "media"
        and media_type != MediaTypes.PODCAST.value
    ):
        shown = {result["item"].id for result in local_results}
        owned = [
            item
            for item in _barcode_owned_items(request.user, media_type, query)
            if item.id not in shown
        ]
        local_results = [
            {"item": item, "media": None, "matched_title": None} for item in owned
        ] + local_results
        local_results_total += len(owned)

    source_options = metadata_resolution.available_metadata_sources(
        media_type,
        request.user,
    )
    default_source = metadata_resolution.metadata_default_source(
        request.user,
        media_type,
    )
    # only receives source when searching with secondary source
    source = request.GET.get("source", default_source)
    if source not in {option.value for option in source_options} and source_options:
        source = source_options[0].value

    search_page = 1 if media_type == MediaTypes.MUSIC.value else page
    online_sets = []
    online_sources = []
    unavailable_providers = []
    data = {"page": 1, "total_results": 0, "total_pages": 0, "results": []}
    if media_type == MediaTypes.VIDEO.value:
        # No provider to search: videos arrive from clients, so only the
        # local library results above apply.
        pass
    elif media_type == MediaTypes.MUSIC.value:
        try:
            with services.interactive_request_scope():
                data = services.search(
                    media_type,
                    query,
                    search_page,
                    source,
                    user=request.user,
                    language=metadata_resolution.metadata_language_default(request.user),
                )
        except services.ProviderNotConfiguredError as exc:
            logger.warning("Search skipped for media_type=%s: %s", media_type, exception_summary(exc))
            messages.error(request, str(exc))
        except services.ProviderAPIError as exc:
            logger.warning("Search failed for media_type=%s: %s", media_type, exception_summary(exc))
            messages.error(
                request,
                gettext("%(value_1)s is currently unavailable. Please try again shortly.")
                % {"value_1": exc.provider_label},
            )
    else:
        # One row per provider, streamed in by the page after it loads (the row
        # endpoint searches one provider); a provider that is not set up, is down
        # or has nothing for this search simply ends up with no row.
        online_sources = [
            {"source": option.value, "label": option.label} for option in source_options
        ]

    if media_type == MediaTypes.MUSIC.value:
        return {
            "user": request.user,
            "data": data,
            "music_online_artists": data.get("artists", []),
            "music_online_releases": data.get("releases", []),
            "source": source,
            "source_options": source_options,
            "media_type": media_type,
            "layout": layout,
            "local_results": local_results,
            "local_results_total": local_results_total,
            "local_results_limit": local_results_limit,
            "local_results_kind": local_results_kind,
            "local_music_artists": local_music_artists,
            "local_music_artists_total": local_music_artists_total,
            "local_music_albums": local_music_albums,
            "local_music_albums_total": local_music_albums_total,
        }

    fallback_source = data.get("fallback_source")
    return {
        "user": request.user,
        "data": data,
        "online_sets": online_sets,
        "online_sources": online_sources,
        "unavailable_providers": unavailable_providers,
        "source": source,
        "source_options": source_options,
        "source_label": metadata_resolution.metadata_provider_label(source),
        "fallback_source_label": (
            metadata_resolution.metadata_provider_label(fallback_source)
            if fallback_source
            else ""
        ),
        "media_type": media_type,
        "layout": layout,
        "local_results": local_results,
        "local_results_total": local_results_total,
        "local_results_limit": local_results_limit,
        "local_results_kind": local_results_kind,
    }


def _provider_result_sets(request, media_type, query, sources, page):
    """Search every listed provider and return the ones that have results.

    Each set is ``{"source", "label", "data"}`` with the results enriched with the
    user's tracking data, as the search page always did for its one provider. Also
    returns the labels of providers that failed (a missing key is not a failure).
    """
    sets = []
    unavailable = []
    language = metadata_resolution.metadata_language_default(request.user)

    def fetch(source, in_thread=False):
        # Each provider is its own network call, so they run side by side.
        try:
            with services.interactive_request_scope():
                return services.search(
                    media_type, query, page, source, user=request.user, language=language
                ), None
        except Exception as exc:  # returned apart from the data, handled by the caller
            return None, exc
        finally:
            if in_thread:
                connection.close()

    if len(sources) > 1:
        with ThreadPoolExecutor(max_workers=len(sources)) as pool:
            fetched = list(pool.map(lambda source: fetch(source, True), sources))
    else:
        fetched = [fetch(source) for source in sources]
    for source, (data, error) in zip(sources, fetched, strict=True):
        if isinstance(error, services.ProviderNotConfiguredError):
            continue
        if isinstance(error, services.ProviderAPIError):
            logger.warning(
                "Search failed for media_type=%s source=%s: %s",
                media_type,
                source,
                exception_summary(error),
            )
            unavailable.append(metadata_resolution.metadata_provider_label(source))
            continue
        if error is not None:
            raise error
        if not data.get("results"):
            continue
        if data.get("fallback_source") and data["fallback_source"] != source:
            # These are another provider's results, which has its own row.
            continue
        data["results"] = helpers.enrich_items_with_user_data(
            request,
            data["results"],
            section_name="search",
        )
        for result in data["results"]:
            result["matched_title"] = _matched_title(result.get("item"), query, request.user)
        sets.append(
            {
                "source": source,
                "label": metadata_resolution.metadata_provider_label(source),
                "data": data,
            },
        )
    return sets, unavailable


@require_GET
def media_search(request):
    """Return the media search page."""
    return render(request, "app/search.html", _search_context(request))


def _safe_url(builder, target):
    """Return a detail URL for a suggestion, or None if it can't be built."""
    try:
        url = builder(target)
    except Exception:  # pragma: no cover - defensive against reverse failures
        return None
    return url or None


@contextmanager
def without_postgres_jit():
    """Run the block with PostgreSQL's JIT compiler off, for this transaction only.

    The library query's correlated subqueries give PostgreSQL a cost estimate
    above its JIT threshold, so it spends about a second compiling a query that
    runs in tens of milliseconds (measured at 20k items: 0.9 s and 1.3 s of JIT
    for two 60 ms anime queries). ``SET LOCAL`` ends with the transaction, so
    pooled connections and other queries keep the server's setting. SQLite has
    no JIT; there this does nothing.
    """
    if connection.vendor != "postgresql":
        yield
        return
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL jit = off")
        yield


_SUGGESTION_BOX_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_SUGGESTION_SEQUENCE_SECONDS = 120


def suggestion_superseded_check(user, box_id):
    """Return a check that turns true once the same search box asks again.

    The browser cancels a superseded request, but the server keeps working on
    it. Each search box sends a random id; every request bumps that box's
    counter, and an older request stops before its next query once the
    counter has moved on. Costs one cache increment, then one read per check.
    Returns None (never superseded) without an id or a working cache.
    """
    if not box_id or not _SUGGESTION_BOX_ID.match(box_id):
        return None
    key = f"search-suggestions:{user.pk}:{box_id}"
    try:
        cache.add(key, 0, _SUGGESTION_SEQUENCE_SECONDS)
        sequence = cache.incr(key)
    except Exception:
        return None

    def superseded():
        try:
            return cache.get(key) != sequence
        except Exception:
            return False

    return superseded


def _all_saved_suggestions(user, query, limit, superseded=None):
    """Return suggestions across every enabled type, each labelled with its type."""
    suggestions = []
    for media_type in user.get_enabled_media_types():
        if media_type == MediaTypes.SEASON.value:
            continue
        # A newer request from the same search box has replaced this one,
        # and its caller has stopped listening: skip the remaining types.
        if superseded is not None and superseded():
            return []
        label = _suggestion_type_label(media_type)
        for suggestion in get_saved_suggestions(
            user,
            media_type,
            query,
            limit=ALL_SUGGESTIONS_PER_TYPE,
        ):
            if suggestion.pop("labelled", False):
                suggestions.append(suggestion)
                continue
            subtitle = suggestion["subtitle"]
            suggestion["subtitle"] = f"{label} · {subtitle}" if subtitle else label
            suggestions.append(suggestion)
        if len(suggestions) >= limit:
            break
    return suggestions[:limit]


def _suggestion_type_label(kind):
    """Return the short type name a suggestion shows ("TV", not "TV Show")."""
    return gettext("TV") if kind == MediaTypes.TV.value else media_type_readable(kind)


def _suggestion_statuses(user, media_type, items):
    """Return ``{item_id: status}`` for the few suggested items, in one query per tracker model.

    Reads only the stored status column. The media list's richer status loads every
    season and episode of a show, which a typeahead must not do.
    """
    item_ids = [item.id for item in items if item is not None]
    if not item_ids:
        return {}
    try:
        sources = tracker_sources(user, media_type, ROUTING_LIBRARY)
    except LookupError:
        return {}
    statuses = {}
    for source in sources:
        # Match the owner as ``owner + 0`` so the lookup goes through item_id (see TrackerSource.item_rows).
        rows = (
            source.model.objects.filter(item_id__in=item_ids)
            .alias(_owner=F(source.user_lookup) + 0)
            .filter(_owner=user.pk)
            .order_by("-created_at")
            .values_list("item_id", source.status_field)
        )
        for item_id, status in rows:
            statuses.setdefault(item_id, status)
    return statuses


def _suggestion_subtitle(item, query, user, status=None):
    """Return "Type · Year · Status · matched title", the detail line the native app shows."""
    kind = getattr(item, "library_media_type", None) or item.media_type
    released = getattr(item, "release_datetime", None)
    year = released.year if released and released.year > MIN_SUGGESTION_YEAR else None
    parts = [
        _suggestion_type_label(kind),
        str(year) if year else None,
        gettext(status) if status else None,
        _matched_title(item, query, user),
    ]
    return " · ".join(part for part in parts if part)


def get_saved_suggestions(user, media_type, query, limit=8, superseded=None):
    """Return compact autocomplete suggestions from the user's saved library.

    Saved items only, scoped to ``media_type``. Each suggestion is a dict of
    ``{title, subtitle, image, url}``. Mirrors the local-results queries used by
    :func:`media_search` but capped small and side-effect free for typeahead.
    """
    if media_type == ALL_SEARCH_TYPE:
        return _all_saved_suggestions(user, query, limit, superseded)

    suggestions = []

    if media_type == MediaTypes.PODCAST.value:
        show_trackers = (
            PodcastShowTracker.objects.filter(user=user)
            .exclude(show__title__isnull=True)
            .exclude(show__title__exact="")
            .filter(show__title__icontains=query)
            .select_related("show")
            .order_by("show__title")[:limit]
        )
        for tracker in show_trackers:
            show = tracker.show
            url = _safe_url(
                media_url,
                {
                    "media_type": MediaTypes.PODCAST.value,
                    "source": show.source,
                    "media_id": show.podcast_uuid,
                    "title": show.title,
                },
            )
            if url:
                suggestions.append(
                    {
                        "title": show.title,
                        "subtitle": None,
                        "image": show.image or None,
                        "url": url,
                    }
                )
        return suggestions

    if media_type == MediaTypes.MUSIC.value:
        artist_trackers = (
            ArtistTracker.objects.filter(user=user)
            .exclude(artist__name__isnull=True)
            .exclude(artist__name__exact="")
            .filter(artist__name__icontains=query)
            .select_related("artist")
            .order_by("artist__name")[:limit]
        )
        for tracker in artist_trackers:
            url = _safe_url(music_artist_url, tracker.artist)
            if url:
                suggestions.append(
                    {
                        "title": tracker.artist.name,
                        "subtitle": "Artist",
                        "image": getattr(tracker.artist, "image", None) or None,
                        "url": url,
                    }
                )

        album_trackers = (
            AlbumTracker.objects.filter(user=user)
            .exclude(album__title__isnull=True)
            .exclude(album__title__exact="")
            .filter(
                Q(album__title__icontains=query)
                | Q(album__artist__name__icontains=query),
            )
            .select_related("album", "album__artist")
            .order_by("album__title")[:limit]
        )
        for tracker in album_trackers:
            url = _safe_url(music_album_url, tracker.album)
            if url:
                album_artist = getattr(tracker.album, "artist", None)
                artist_name = getattr(album_artist, "name", None)
                suggestions.append(
                    {
                        "title": tracker.album.title,
                        "subtitle": artist_name or "Album",
                        "image": getattr(tracker.album, "image", None) or None,
                        "url": url,
                    }
                )
        return suggestions[:limit]

    results, _total = _local_media_results(
        user,
        media_type,
        query,
        limit,
        annotate_progress=False,
        count_total=False,
    )
    local_items = [result["item"] for result in results]

    statuses = _suggestion_statuses(user, media_type, local_items)
    for item in local_items:
        if item is None:
            continue
        url = _safe_url(media_url, item)
        if not url:
            continue
        suggestions.append(
            {
                "title": _display_title_for_user(item, user),
                "subtitle": _suggestion_subtitle(item, query, user, statuses.get(item.id)),
                "labelled": True,
                "identity": {
                    "mediaType": getattr(item, "library_media_type", None) or item.media_type,
                    "source": item.source,
                    "mediaId": item.media_id,
                    "title": item.title,
                },
                "image": getattr(item, "image", None) or None,
                "url": url,
            }
        )
    return suggestions


def _suggestion_json(suggestions):
    """Map saved-library suggestions to the universal autocomplete contract."""
    return [
        {
            "id": str(index),
            "title": suggestion["title"],
            "subtitle": suggestion["subtitle"],
            "image": image_url(suggestion["image"]) if suggestion["image"] else None,
            "url": suggestion["url"],
            "identity": suggestion.get("identity"),
        }
        for index, suggestion in enumerate(suggestions)
    ]


@require_GET
def search_suggestions(request):
    """Return suggestions as an HTML fragment or the JSON presentation contract."""
    query = request.GET.get("q", "").strip()
    media_type = request.GET.get("media_type", "")
    wants_json = request.GET.get("format") == "json"

    if (
        not request.user.is_authenticated
        or len(query) < MIN_SUGGESTION_QUERY_LENGTH
        or media_type not in {*MediaTypes.values, ALL_SEARCH_TYPE}
    ):
        if wants_json:
            return JsonResponse({"suggestions": []})
        return render(request, "app/components/search_suggestions.html")

    try:
        with without_postgres_jit():
            suggestions = get_saved_suggestions(
                request.user,
                media_type,
                query,
                superseded=suggestion_superseded_check(request.user, request.GET.get("sid")),
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Suggestion search failed: %s", exception_summary(exc))
        suggestions = []

    if wants_json:
        return JsonResponse({"suggestions": _suggestion_json(suggestions)})

    return render(
        request,
        "app/components/search_suggestions.html",
        {
            "suggestions": suggestions,
            "query": query,
            "media_type": media_type,
        },
    )


# ---------------------------------------------------------------------------
# Shared React results surface
# ---------------------------------------------------------------------------

_PAGINATION_WINDOW = 2


_ACTION_GLYPHS = {
    "track": "✎",
    "lists": "☰",
    "history": "◷",
}


def _plain_title(item):
    """Return an item's title for either a stored item or a provider dict."""
    if isinstance(item, dict):
        return str(item.get("title") or "")
    return str(getattr(item, "title", None) or "")


def _add_action_glyphs(model):
    """Give shared card actions a portable glyph for native and web renderers."""
    actions = model.get("actions") if isinstance(model, dict) else None
    if not actions:
        return model
    for action in actions.get("items") or []:
        if action.get("glyph"):
            continue
        if action.get("modal") in _ACTION_GLYPHS:
            action["glyph"] = _ACTION_GLYPHS[action["modal"]]
        elif action.get("kind") == "link":
            action["glyph"] = _ACTION_GLYPHS["history"]
        else:
            action["glyph"] = "•"
    return model


def _search_card(result, *, user, request, layout):
    """Return one result as the shared card model, list row or poster."""
    from app.card_surfaces import card_context
    from app.media_card_model import row_card_model

    item = result.get("item")
    media = result.get("media")
    title = _plain_title(item)
    matched = result.get("matched_title")
    if layout == "list":
        model = row_card_model(item, media, title, matched, user, request)
    else:
        page_context = {
            "user": user,
            "request": request,
            "csrf_token": get_token(request),
        }
        card = card_context(
            page_context,
            "search",
            {
                "item": item,
                "media": media,
                "title": title,
                "matched_title": matched,
            },
        )
        model = card["react_props"]["model"]
    return _add_action_glyphs(model)


def _search_cards(results, *, user, request, layout):
    return [
        _search_card(result, user=user, request=request, layout=layout)
        for result in results or []
    ]


def _result_count_label(total):
    return ngettext(
        "%(value_1)s result",
        "%(value_1)s results",
        total,
    ) % {"value_1": total}


def _shown_label(count):
    return gettext("· Showing first %(value_1)s") % {"value_1": count}


def _search_url(request, **overrides):
    """Return a search URL with the current query and the given overrides."""
    params = request.GET.copy()
    params.pop("page", None)
    for key, value in overrides.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = value
    return f"{reverse('search')}?{params.urlencode()}"


def _page_url(request, page):
    params = request.GET.copy()
    params["page"] = page
    return f"{reverse('search')}?{params.urlencode()}"


def _pagination_pages(request, page, total_pages):
    pages = []
    for number in range(1, total_pages + 1):
        if number in (1, total_pages) or abs(number - page) <= _PAGINATION_WINDOW:
            pages.append(
                {
                    "number": number,
                    "href": _page_url(request, number),
                    "current": number == page,
                },
            )
        elif pages and not pages[-1].get("ellipsis"):
            pages.append({"ellipsis": True})
    return pages


def _music_lines(user, item, media):
    from users.card_metadata import card_lines

    return [
        {
            "clamp": True,
            "dormant": bool(line.get("dormant")),
            "parts": [
                {"text": part["text"], **({"score": True} if part.get("score") else {})}
                for part in line.get("parts") or []
            ],
        }
        for line in card_lines(user, MediaTypes.MUSIC.value, item, media)
    ]


def _music_rating(score, rate_url, user):
    if score is None:
        return None
    label = app_tags.score_display(score, user)
    if label is None:
        return None
    try:
        value = float(score)
    except (TypeError, ValueError):
        value = None
    return {
        "label": str(label),
        "value": value,
        "rateUrl": rate_url,
        "scaleMax": app_tags.rating_scale_max(user),
        "rateLabel": gettext("Rate"),
    }


def _music_actions(*, href, request, track_url, track_target, view_title, view_glyph, history_url):
    return {
        "href": href,
        "items": [
            {
                "kind": "modal",
                "modal": "track",
                "tone": "indigo",
                "title": gettext("Edit tracking"),
                "glyph": _ACTION_GLYPHS["track"],
                "url": track_url,
                "vals": {"return_url": request.get_full_path()},
                "target": track_target,
            },
            {
                "kind": "link",
                "tone": "emerald",
                "title": view_title,
                "glyph": view_glyph,
                "href": href,
            },
            {
                "kind": "link",
                "tone": "amber",
                "title": gettext("View listening history"),
                "glyph": _ACTION_GLYPHS["history"],
                "href": history_url,
            },
        ],
        "ordering": None,
        "modals": {},
        "csrfToken": get_token(request),
        "clickable": bool(getattr(request.user, "clickable_media_cards", False)),
    }


def _local_music_artist_model(tracker, user, request):
    artist = tracker.artist
    href = app_tags.music_artist_url(artist)
    return {
        "mediaType": MediaTypes.MUSIC.value,
        "shape": "poster",
        "href": href,
        "image": {
            "src": app_tags.image_url(artist.image),
            "fallback": "",
            "source": "primary",
        },
        "title": artist.name,
        "altTitle": None,
        "statusChip": {
            "kind": "status",
            "status": tracker.status,
            "label": tracker.status,
            "colorClass": app_tags.status_color(tracker.status),
        },
        "cornerChips": [],
        "titleLinks": [],
        "progressBar": None,
        "lines": _music_lines(user, artist, tracker),
        "rating": _music_rating(
            tracker.score,
            reverse("update_artist_score", args=[artist.id]),
            user,
        ),
        "ratingEditable": True,
        "csrfToken": get_token(request),
        "actions": _music_actions(
            href=href,
            request=request,
            track_url=reverse("artist_track_modal", args=[artist.id]),
            track_target=f"#artist-track-modal-{artist.id}",
            view_title=gettext("View artist"),
            view_glyph=">",
            history_url=f"{reverse('history')}?artist={artist.id}",
        ),
    }


def _local_music_album_model(tracker, user, request):
    album = tracker.album
    href = app_tags.music_album_url(album)
    return {
        "mediaType": MediaTypes.MUSIC.value,
        "shape": "square",
        "href": href,
        "image": {
            "src": app_tags.image_url(album.image),
            "fallback": "",
            "source": "primary",
        },
        "title": album.title,
        "altTitle": None,
        "statusChip": {
            "kind": "status",
            "status": tracker.status,
            "label": tracker.status,
            "colorClass": app_tags.status_color(tracker.status),
        },
        "cornerChips": [],
        "titleLinks": [],
        "progressBar": None,
        "lines": _music_lines(user, album, tracker),
        "rating": _music_rating(
            tracker.score,
            reverse("update_album_score", args=[album.id]),
            user,
        ),
        "ratingEditable": True,
        "csrfToken": get_token(request),
        "actions": _music_actions(
            href=href,
            request=request,
            track_url=reverse("album_track_modal", args=[album.id]),
            track_target=f"#album-track-modal-{album.id}",
            view_title=gettext("View album"),
            view_glyph=">",
            history_url=f"{reverse('history')}?album={album.id}",
        ),
    }


def _online_music_artist_model(artist):
    artist_id = artist.get("artist_id") if isinstance(artist, dict) else None
    name = (artist.get("name") if isinstance(artist, dict) else None) or ""
    disambiguation = artist.get("disambiguation") if isinstance(artist, dict) else None
    image = artist.get("image") if isinstance(artist, dict) else None
    return {
        "mediaType": MediaTypes.MUSIC.value,
        "shape": "poster",
        "href": reverse("create_artist_from_search", args=[artist_id])
        if artist_id
        else None,
        "image": {
            "src": app_tags.image_url(image),
            "fallback": "",
            "source": "primary",
        },
        "title": name,
        "altTitle": None,
        "statusChip": None,
        "cornerChips": [],
        "titleLinks": [],
        "progressBar": None,
        "lines": [
            {"clamp": True, "parts": [{"text": disambiguation}], "dormant": True}
        ]
        if disambiguation
        else [],
        "rating": None,
        "ratingEditable": False,
        "actions": None,
    }


def _online_music_release_model(release):
    release_id = release.get("release_id") if isinstance(release, dict) else None
    title = (release.get("title") if isinstance(release, dict) else None) or ""
    artist_name = release.get("artist_name") if isinstance(release, dict) else None
    image = release.get("image") if isinstance(release, dict) else None
    return {
        "mediaType": MediaTypes.MUSIC.value,
        "shape": "square",
        "href": reverse("create_album_from_search", args=[release_id])
        if release_id
        else None,
        "image": {
            "src": app_tags.image_url(image),
            "fallback": "",
            "source": "primary",
        },
        "title": title,
        "altTitle": None,
        "statusChip": None,
        "cornerChips": [],
        "titleLinks": [],
        "progressBar": None,
        "lines": [{"parts": [{"text": artist_name}], "dormant": True}]
        if artist_name
        else [],
        "rating": None,
        "ratingEditable": False,
        "actions": None,
    }


def _music_subsection(key, title, items, *, total=None, shown=None, layout="grid"):
    if not items:
        return None
    subsection = {"key": key, "title": title, "items": items, "layout": layout}
    if total is not None:
        subsection["totalLabel"] = _result_count_label(total)
        if shown is not None and total > shown:
            subsection["shownLabel"] = _shown_label(shown)
    return subsection


def _media_subsection(results, *, user, request, layout):
    return {
        "key": "results",
        "items": _search_cards(results, user=user, request=request, layout=layout),
        "layout": "list" if layout == "list" else "grid",
    }


def _build_local_group_section(group, *, user, request, layout):
    section = {
        "key": group["media_type"],
        "title": group["label"],
        "subsections": [],
    }
    if group["kind"] == "music":
        section["totalLabel"] = _result_count_label(group["total"])
        artists = _music_subsection(
            "artists",
            gettext("Artists"),
            [
                _local_music_artist_model(tracker, user, request)
                for tracker in group.get("artists") or []
            ],
            total=group.get("artists_total"),
            shown=len(group.get("artists") or []),
        )
        albums = _music_subsection(
            "albums",
            gettext("Albums"),
            [
                _local_music_album_model(tracker, user, request)
                for tracker in group.get("albums") or []
            ],
            total=group.get("albums_total"),
            shown=len(group.get("albums") or []),
        )
        section["subsections"] = [item for item in (artists, albums) if item]
    else:
        section["totalLabel"] = _result_count_label(group["total"])
        if group["total"] > len(group.get("results") or []):
            section["shownLabel"] = _shown_label(len(group.get("results") or []))
        section["subsections"] = [
            _media_subsection(
                group.get("results") or [],
                user=user,
                request=request,
                layout=layout,
            ),
        ]
    section["searchOnline"] = {
        "label": gettext("Search online"),
        "href": _search_url(
            request,
            media_type=group["media_type"],
            source=None,
        ),
    }
    return section


def _build_typed_music_sections(context, *, user, request, layout):
    sections = []
    local_artists = context.get("local_music_artists") or []
    local_albums = context.get("local_music_albums") or []
    if local_artists or local_albums:
        section = {
            "key": "local",
            "title": gettext("In Your Library"),
            "subsections": [
                item
                for item in (
                    _music_subsection(
                        "artists",
                        gettext("Artists"),
                        [
                            _local_music_artist_model(tracker, user, request)
                            for tracker in local_artists
                        ],
                        total=context.get("local_music_artists_total"),
                        shown=len(local_artists),
                    ),
                    _music_subsection(
                        "albums",
                        gettext("Albums"),
                        [
                            _local_music_album_model(tracker, user, request)
                            for tracker in local_albums
                        ],
                        total=context.get("local_music_albums_total"),
                        shown=len(local_albums),
                    ),
                )
                if item
            ],
        }
        sections.append(section)

    online_artists = context.get("music_online_artists") or []
    online_releases = context.get("music_online_releases") or []
    if online_artists or online_releases:
        section = {
            "key": "online",
            "title": gettext("Online Results"),
            "subsections": [
                item
                for item in (
                    _music_subsection(
                        "artists",
                        gettext("Artists"),
                        [_online_music_artist_model(artist) for artist in online_artists],
                    ),
                    _music_subsection(
                        "albums",
                        gettext("Albums"),
                        [
                            _online_music_release_model(release)
                            for release in online_releases
                        ],
                    ),
                )
                if item
            ],
        }
        sections.append(section)
    return sections


def _build_typed_sections(context, *, user, request, layout):
    media_type = context.get("media_type")
    if media_type == MediaTypes.MUSIC.value:
        return _build_typed_music_sections(
            context,
            user=user,
            request=request,
            layout=layout,
        )

    sections = []
    local_results = context.get("local_results") or []
    local_total = context.get("local_results_total") or 0
    if local_results:
        section = {
            "key": "local",
            "title": gettext("In Your Library"),
            "totalLabel": _result_count_label(local_total),
            "subsections": [
                _media_subsection(
                    local_results,
                    user=user,
                    request=request,
                    layout=layout,
                ),
            ],
        }
        if local_total > len(local_results):
            section["shownLabel"] = _shown_label(len(local_results))
        sections.append(section)

    data = context.get("data") or {}
    online_results = data.get("results") or []
    if online_results:
        section = {
            "key": "online",
            "title": gettext("Online Results"),
            "subsections": [
                _media_subsection(
                    online_results,
                    user=user,
                    request=request,
                    layout=layout,
                ),
            ],
        }
        if media_type == MediaTypes.PODCAST.value:
            source_label = context.get("source_label") or gettext("Online Results")
            fallback_label = context.get("fallback_source_label")
            if fallback_label:
                section["title"] = fallback_label
                section["tip"] = {
                    "label": gettext("About these results"),
                    "text": gettext(
                        "%(value_1)s had no match for this search, so these results "
                        "come from %(value_2)s. Paste an Apple Podcasts link or an RSS "
                        "feed address to add a show directly.",
                    )
                    % {"value_1": source_label, "value_2": fallback_label},
                }
            else:
                section["title"] = source_label
                section["tip"] = {
                    "label": gettext("About these results"),
                    "text": gettext(
                        "These results come from %(value_1)s. Paste an Apple Podcasts "
                        "link or an RSS feed address to add a show directly.",
                    )
                    % {"value_1": source_label},
                }
        sections.append(section)
    return sections


def _build_empty_state(context):
    query = context.get("query") or ""
    media_type = context.get("media_type")
    empty = {
        "title": gettext("No results found"),
        "body": gettext(
            'We couldn\'t find any matches for "%(value_1)s". '
            "Try checking your spelling or using different keywords.",
        )
        % {"value_1": query},
    }
    if media_type == ALL_SEARCH_TYPE:
        empty["body"] = gettext(
            "Nothing in your library matches “%(value_1)s”. "
            "Pick a media type to search online.",
        ) % {"value_1": query}
    elif media_type == MediaTypes.PODCAST.value:
        empty["extra"] = gettext(
            "Can't find it? Paste the show's RSS feed address into the "
            "search box to add it directly.",
        )
    return empty


def _search_row(row_id, title, items, *, total, title_detail=None, page=None, view_all=None):
    """Return one search row in the shape Home's shelves render."""
    row = {
        "rowId": row_id,
        "rowType": "search",
        "title": title,
        "titleMain": title,
        "titleDetail": title_detail,
        "summaryInline": None,
        "direction": None,
        "items": items,
        "total": total,
        "loadedCount": len(items),
        "seed": 0,
        "page": page,
        "pollForCovers": False,
    }
    if view_all:
        row["viewAll"] = view_all
    return row


def _card_items(row_id, cards, page=0):
    """Wrap cards as row items; ``page`` keeps fallback ids unique across a row's pages."""
    return [
        {"id": card.get("cardKey") or f"{row_id}:{page}:{index}", "card": card}
        for index, card in enumerate(cards)
    ]


def _section_rows(section):
    """Turn a grouped section (All media, music) into rows of cards."""
    subsections = [
        sub for sub in section.get("subsections") or [] if sub.get("items")
    ]
    rows = []
    for sub in subsections:
        many = len(subsections) > 1
        row_id = f"{section['key']}:{sub['key']}" if many else str(section["key"])
        title = (
            f"{section['title']} • {sub['title']}"
            if many and sub.get("title")
            else section["title"]
        )
        items = _card_items(row_id, sub["items"])
        rows.append(
            _search_row(
                row_id,
                title,
                items,
                total=len(items),
                view_all=section.get("searchOnline"),
            ),
        )
    return rows


def _typed_rows(context, *, user, request):
    """Rows for one media type: your library, then one row per provider."""
    rows = []
    local_results = context.get("local_results") or []
    if local_results:
        cards = _search_cards(local_results, user=user, request=request, layout="grid")
        rows.append(
            _search_row(
                "library",
                gettext("In Your Library"),
                _card_items("library", cards),
                total=context.get("local_results_total") or len(cards),
            ),
        )
    for online in context.get("online_sets") or []:
        data = online["data"]
        row_id = f"online:{online['source']}"
        cards = _search_cards(data.get("results"), user=user, request=request, layout="grid")
        rows.append(
            _search_row(
                row_id,
                gettext("Online Results"),
                _card_items(row_id, cards),
                total=data.get("total_results") or len(cards),
                title_detail=online["label"],
                page=data.get("page") or 1,
            ),
        )
    return rows


def build_search_results_props(context):
    """Build the serialisable props for the shared React search results."""
    request = context.get("request")
    user = context.get("user")
    media_type = context.get("media_type")
    query = request.GET.get("q", "") if request else ""

    if media_type == ALL_SEARCH_TYPE:
        rows = [
            row
            for group in context.get("local_groups") or []
            for row in _section_rows(
                _build_local_group_section(group, user=user, request=request, layout="grid"),
            )
        ]
    elif media_type == MediaTypes.MUSIC.value:
        rows = [
            row
            for section in _build_typed_music_sections(
                context, user=user, request=request, layout="grid",
            )
            for row in _section_rows(section)
        ]
    else:
        rows = _typed_rows(context, user=user, request=request)

    online_sources = context.get("online_sources") or []
    notes = []
    if media_type == MediaTypes.PODCAST.value:
        notes.append(
            gettext(
                "Search by name, or paste an Apple Podcasts link or an RSS feed "
                "address to add a show directly.",
            ),
        )
    if media_type == ALL_SEARCH_TYPE:
        notes.append(
            gettext(
                "Searching your library only: items you track, collect or tag. "
                "Pick a media type to search online.",
            ),
        )
    # A provider that is not set up is skipped quietly; one that failed is named.
    notes.extend(
        gettext("%(value_1)s is currently unavailable. Please try again shortly.")
        % {"value_1": label}
        for label in context.get("unavailable_providers") or []
    )

    return {
        "title": gettext("Search results"),
        "query": query,
        "mediaType": media_type,
        "state": "ready" if rows or online_sources else "empty",
        "rows": rows,
        "onlineSources": [
            {"rowId": f"online:{item['source']}", "label": item["label"]} for item in online_sources
        ],
        "notes": notes,
        "empty": _build_empty_state({**context, "query": query}),
        "labels": {
            "emptyTitle": gettext("No results found"),
            "loading": gettext("Loading results…"),
            "searchingOnline": gettext("Searching online…"),
            "errorTitle": gettext("Search could not load"),
            "errorBody": gettext("Please try again."),
            "retry": gettext("Try again"),
            "loadMore": gettext("Load more"),
            "loadingMore": gettext("Loading more…"),
            "rate": gettext("Rate"),
            "close": gettext("Close"),
            "open": gettext("Open"),
            "altTitle": gettext("Show alternative title"),
            "nextEpisode": gettext("Jump to next episode"),
            "noStatus": gettext("No Status"),
        },
    }


SEARCH_ROW_PAGE = 24


def _search_row_page(request):
    """Return the next page of one row: more of your library or the next provider page."""
    user = request.user
    media_type = request.GET.get("media_type", "")
    query = request.GET.get("q", "")
    row_id = request.GET.get("row", "")
    if row_id == "library":
        offset = max(int(request.GET.get("offset") or 0), 0)
        if media_type == MediaTypes.PODCAST.value:
            results, total = _local_podcast_results(user, query, offset + SEARCH_ROW_PAGE)
        else:
            results, total = _local_media_results(user, media_type, query, offset + SEARCH_ROW_PAGE)
        cards = _search_cards(results[offset:], user=user, request=request, layout="grid")
        return _search_row(
            "library",
            gettext("In Your Library"),
            _card_items("library", cards, page=offset),
            total=total,
        ), None
    if row_id.startswith("online:"):
        source = row_id.split(":", 1)[1]
        page = max(int(request.GET.get("page") or 1), 1)
        sets, unavailable = _provider_result_sets(request, media_type, query, [source], page)
        if not sets:
            return None, unavailable[0] if unavailable else None
        data = sets[0]["data"]
        cards = _search_cards(data.get("results"), user=user, request=request, layout="grid")
        return _search_row(
            row_id,
            gettext("Online Results"),
            _card_items(row_id, cards, page=page),
            total=data.get("total_results") or len(cards),
            title_detail=sets[0]["label"],
            page=data.get("page") or page,
        ), None
    return None, None


@require_GET
def search_results_json(request):
    """Return the search rows (or one row's next page) for the native app."""
    if request.GET.get("row"):
        row, unavailable = _search_row_page(request)
        return JsonResponse({"row": row, "unavailable": unavailable})
    context = _search_context(request)
    return JsonResponse(build_search_results_props({**context, "request": request}))
