"""
Utility functions and constants shared across list views.

Nothing in this module handles HTTP requests directly — all symbols are
pure helpers called by views in views.py (and its submodules).
"""

import logging
from dataclasses import replace

from django.apps import apps
from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count, F, Max, Q
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import ngettext

from app.models import (
    CollectionEntry,
    Item,
    ItemProviderLink,
    MediaManager,
    MediaTypes,
    Sources,
    Status,
)
from app.providers import services
from app.services import metadata_resolution
from integrations.imports import helpers as import_helpers
from integrations.models import TraktAccount
from lists.models import CustomList, CustomListItem
from users.models import ListDetailSortChoices, ListSortChoices

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

LIST_REFERENCE_PLACEHOLDER = "__LIST_REFERENCE__"

_MEDIA_TYPE_COLORS = {
    "movie": "#6366f1",
    "tv": "#8b5cf6",
    "season": "#a855f7",
    "episode": "#c084fc",
    "anime": "#ec4899",
    "manga": "#f43f5e",
    "game": "#f97316",
    "book": "#eab308",
    "comic": "#22c55e",
    "boardgame": "#14b8a6",
    "music": "#06b6d4",
    "podcast": "#3b82f6",
}

ASCENDING_LIST_SORTS = {
    ListSortChoices.NAME,
    ListDetailSortChoices.TITLE,
    ListDetailSortChoices.MEDIA_TYPE,
    ListDetailSortChoices.RELEASE_DATE,
    ListDetailSortChoices.START_DATE,
    ListDetailSortChoices.PLATFORM,
    ListDetailSortChoices.TIER,
}


def _build_list_count_trigger(count: int) -> dict:
    """Return the HTMX payload for an updated list item count."""
    label = ngettext("%(count)s item", "%(count)s items", count) % {
        "count": count,
    }
    return {"listCountUpdated": {"count": count, "label": label}}


# ---------------------------------------------------------------------------
# Media type / list metadata helpers
# ---------------------------------------------------------------------------


def _build_media_type_breakdown(custom_list):
    total = custom_list.items.count()
    if not total:
        return []
    raw = (
        custom_list.items.values("media_type")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    return [
        {
            "value": row["media_type"],
            "label": MediaTypes(row["media_type"]).label,
            "count": row["count"],
            "percent": round(row["count"] / total * 100),
            "color": _MEDIA_TYPE_COLORS.get(row["media_type"], "#6b7280"),
        }
        for row in raw
    ]


def _build_list_url_template(request):
    """Return an absolute list URL template with a replaceable reference placeholder."""
    return request.build_absolute_uri(
        reverse("list_detail", args=[LIST_REFERENCE_PLACEHOLDER]),
    )


def get_public_list_for_item(list_reference, item):
    """Return the requested public list only when it contains the item."""
    if not list_reference or item is None:
        return None

    custom_list = CustomList.objects.get_public_list(list_reference)
    if custom_list is None or not custom_list.items.filter(pk=item.pk).exists():
        return None
    return custom_list


def _get_completed_item_ids(user, item_ids):
    """Return the subset of item_ids that the user has marked Completed in any media type.

    ``item_ids`` may be a subquery of ids (``values("item_id")``), which keeps
    the queries the same size however long the list is.
    """
    completed = set()
    for media_type in MediaTypes.values:
        if media_type == MediaTypes.EPISODE.value:
            continue  # Episode has no status/user field
        try:
            model = apps.get_model("app", media_type)
        except LookupError:
            continue
        completed.update(
            model.objects.filter(
                item_id__in=item_ids,
                user=user,
                status="Completed",
            )
            .values_list("item_id", flat=True)
            .distinct()
        )
    from app.library_query.spec import ROUTING_MODEL
    from app.library_query.trackers import tracker_sources

    source = tracker_sources(user, MediaTypes.MUSIC.value, ROUTING_MODEL, include_albums=True)[0]
    completed.update(
        Item.objects.filter(pk__in=item_ids).filter(pk__in=source.item_ids(user, Q(status=Status.COMPLETED.value)))
        .values_list("pk", flat=True)
    )
    return completed


def _get_item_last_watched_dates(user, item_ids):
    """Return the latest watched timestamp for each item ID for the current user."""
    if not item_ids:
        return {}

    item_ids_by_media_type = {}
    for item_id, media_type in Item.objects.filter(id__in=item_ids).values_list(
        "id",
        "media_type",
    ):
        item_ids_by_media_type.setdefault(media_type, set()).add(item_id)

    item_last_watched = {}
    try:
        episode_model = apps.get_model("app", MediaTypes.EPISODE.value)
    except LookupError:
        episode_model = None

    if episode_model is not None:
        episode_item_ids = item_ids_by_media_type.get(MediaTypes.EPISODE.value, set())
        if episode_item_ids:
            watch_rows = episode_model.objects.filter(
                item_id__in=episode_item_ids,
                related_season__user=user,
                end_date__isnull=False,
            ).values_list("item_id", "end_date")
            for item_id, end_date in watch_rows:
                current_latest = item_last_watched.get(item_id)
                if current_latest is None or end_date > current_latest:
                    item_last_watched[item_id] = end_date

        season_item_ids = item_ids_by_media_type.get(MediaTypes.SEASON.value, set())
        if season_item_ids:
            watch_rows = episode_model.objects.filter(
                related_season__item_id__in=season_item_ids,
                related_season__user=user,
                end_date__isnull=False,
            ).values_list("related_season__item_id", "end_date")
            for item_id, end_date in watch_rows:
                current_latest = item_last_watched.get(item_id)
                if current_latest is None or end_date > current_latest:
                    item_last_watched[item_id] = end_date

        tv_item_ids = item_ids_by_media_type.get(MediaTypes.TV.value, set())
        if tv_item_ids:
            watch_rows = episode_model.objects.filter(
                related_season__related_tv__item_id__in=tv_item_ids,
                related_season__user=user,
                end_date__isnull=False,
            ).values_list("related_season__related_tv__item_id", "end_date")
            for item_id, end_date in watch_rows:
                current_latest = item_last_watched.get(item_id)
                if current_latest is None or end_date > current_latest:
                    item_last_watched[item_id] = end_date

    for media_type, media_item_ids in item_ids_by_media_type.items():
        if media_type in {
            MediaTypes.TV.value,
            MediaTypes.SEASON.value,
            MediaTypes.EPISODE.value,
        }:
            continue

        try:
            model = apps.get_model("app", media_type)
        except LookupError:
            continue

        field_names = {field.name for field in model._meta.fields}
        if not {"item", "user", "end_date"}.issubset(field_names):
            continue

        watch_rows = model.objects.filter(
            item_id__in=media_item_ids,
            user=user,
            end_date__isnull=False,
        ).values_list("item_id", "end_date")

        for item_id, end_date in watch_rows:
            current_latest = item_last_watched.get(item_id)
            if current_latest is None or end_date > current_latest:
                item_last_watched[item_id] = end_date

    return item_last_watched


def _get_list_completed_counts(user, list_ids):
    """Count completed memberships in SQL without loading the list contents."""
    completed = Q(pk__in=[])
    for media_type in MediaTypes.values:
        if media_type == MediaTypes.EPISODE.value:
            continue
        try:
            model = apps.get_model("app", media_type)
        except LookupError:
            continue
        completed |= Q(
            item_id__in=model.objects.filter(
                user=user,
                status=Status.COMPLETED.value,
            ).values("item_id"),
        )
    from django.db.models import Exists, OuterRef

    from app.models import PodcastShowTracker

    show_tracker = PodcastShowTracker.objects.filter(
        user=user,
        status=Status.COMPLETED.value,
        show__podcast_uuid=OuterRef("media_id"),
        show__source=OuterRef("source"),
    )
    completed |= Q(item_id__in=Item.objects.filter(
        media_type=MediaTypes.PODCAST.value,
    ).filter(Exists(show_tracker)).values("pk"))
    from app.library_query.spec import ROUTING_MODEL
    from app.library_query.trackers import tracker_sources

    source = tracker_sources(user, MediaTypes.MUSIC.value, ROUTING_MODEL, include_albums=True)[0]
    completed |= Q(item_id__in=source.item_ids(user, Q(status=Status.COMPLETED.value)))
    return dict(
        CustomListItem.objects.filter(completed, custom_list_id__in=list_ids)
        .order_by()
        .values("custom_list_id")
        .annotate(completed_count=Count("item_id", distinct=True))
        .values_list("custom_list_id", "completed_count"),
    )


def _get_list_last_watched_dates(user, list_ids):
    """Aggregate watch dates per list in SQL, retaining only one date per list."""
    if not list_ids:
        return {}

    latest_by_list = {}
    for media_type in MediaTypes.values:
        try:
            model = apps.get_model("app", media_type)
        except LookupError:
            continue
        if media_type in {
            MediaTypes.TV.value,
            MediaTypes.SEASON.value,
            MediaTypes.EPISODE.value,
        }:
            model = apps.get_model("app", MediaTypes.EPISODE.value)
            item_path = {
                MediaTypes.TV.value: "related_season__related_tv__item",
                MediaTypes.SEASON.value: "related_season__item",
                MediaTypes.EPISODE.value: "item",
            }[media_type]
            user_path = "related_season__user"
        else:
            if not {"item", "user", "end_date"}.issubset(
                field.name for field in model._meta.fields
            ):
                continue
            item_path = "item"
            user_path = "user"
        list_path = f"{item_path}__customlistitem__custom_list_id"
        rows = (
            model.objects.filter(
                **{
                    user_path: user,
                    f"{item_path}__media_type": media_type,
                    f"{list_path}__in": list_ids,
                    "end_date__isnull": False,
                },
            )
            .order_by()
            .values(list_path)
            .annotate(latest=Max("end_date"))
            .values_list(list_path, "latest")
        )
        for list_id, watched_at in rows.iterator(chunk_size=500):
            previous = latest_by_list.get(list_id)
            if previous is None or watched_at > previous:
                latest_by_list[list_id] = watched_at
    return latest_by_list


# ---------------------------------------------------------------------------
# Sort / direction helpers
# ---------------------------------------------------------------------------


def _default_list_sort_direction(sort_by):
    return "asc" if sort_by in ASCENDING_LIST_SORTS else "desc"


def _resolve_list_sort_direction(sort_by, direction):
    if direction in {"asc", "desc"}:
        return direction
    return _default_list_sort_direction(sort_by)


def _order_expression(field_name, direction, *, nulls_last=True):
    field = F(field_name)
    if direction == "asc":
        return field.asc(nulls_last=nulls_last)
    return field.desc(nulls_last=nulls_last)


# ---------------------------------------------------------------------------
# Card image / episode title helpers
# ---------------------------------------------------------------------------


def _resolve_list_card_image_override(item, *, season_item=None):
    """Return a season-first poster override for episode cards when available."""
    if getattr(item, "media_type", None) != MediaTypes.EPISODE.value:
        return None

    media = getattr(item, "media", None)
    related_season = getattr(media, "related_season", None) if media else None
    related_tv = getattr(related_season, "related_tv", None) if related_season else None

    for candidate in (
        getattr(getattr(related_season, "item", None), "image", None),
        getattr(season_item, "image", None),
        getattr(getattr(related_tv, "item", None), "image", None),
        getattr(item, "image", None),
    ):
        if candidate and candidate != settings.IMG_NONE:
            return candidate

    return None


def _list_item_title_fields_from_metadata(media_type, metadata):
    """Return item title fields, preferring episode titles for episode items."""
    metadata = metadata or {}
    if media_type == MediaTypes.EPISODE.value:
        return Item.title_fields_from_episode_metadata(
            metadata,
            fallback_title=metadata.get("title") or "",
        )
    return Item.title_fields_from_metadata(metadata)


def _episode_title_needs_backfill(item, *, season_item=None):
    """Return whether an episode item is still using a parent show title."""
    if getattr(item, "media_type", None) != MediaTypes.EPISODE.value:
        return False
    if (
        getattr(item, "season_number", None) is None
        or getattr(item, "episode_number", None) is None
    ):
        return False

    media = getattr(item, "media", None)
    related_season = getattr(media, "related_season", None) if media else None
    related_tv = getattr(related_season, "related_tv", None) if related_season else None

    current_title = Item._normalize_title_value(getattr(item, "title", None))
    parent_titles = {
        Item._normalize_title_value(getattr(season_item, "title", None)),
        Item._normalize_title_value(
            getattr(getattr(related_season, "item", None), "title", None)
        ),
        Item._normalize_title_value(
            getattr(getattr(related_tv, "item", None), "title", None)
        ),
    }
    parent_titles.discard(None)

    return not current_title or current_title in parent_titles


def _episode_title_fields_from_season_metadata(item, season_metadata):
    """Return episode title fields from a season payload when available."""
    episodes = (season_metadata or {}).get("episodes") or []
    target_episode = str(getattr(item, "episode_number", ""))
    for episode in episodes:
        if str(episode.get("episode_number")) != target_episode:
            continue
        return Item.title_fields_from_episode_metadata(
            episode,
            fallback_title=getattr(item, "title", ""),
        )
    return None


def list_metadata_language(user, fallback_user=None):
    """Return the metadata language list search and add flows should request.

    Anonymous visitors (public recommendations) fall back to the list owner's
    language so titles match what the list owner sees.
    """
    if user is not None and getattr(user, "is_authenticated", False):
        return metadata_resolution.metadata_language_default(user)
    return metadata_resolution.metadata_language_default(fallback_user)


def _maybe_backfill_episode_title(
    item, *, season_item=None, season_metadata=None, force=False, language=None
):
    """Resolve malformed episode item titles that still store the show title."""
    if not force and not _episode_title_needs_backfill(item, season_item=season_item):
        return

    title_fields = _episode_title_fields_from_season_metadata(item, season_metadata)

    if title_fields is None:
        try:
            season_metadata = services.get_media_metadata(
                MediaTypes.SEASON.value,
                item.media_id,
                item.source,
                [item.season_number],
                language=language,
            )
        except Exception as exc:
            logger.debug(
                "Could not fetch season metadata for episode title backfill on item %s: %s",
                item.id,
                exc,
            )
        else:
            title_fields = _episode_title_fields_from_season_metadata(
                item, season_metadata
            )

    if title_fields is None:
        try:
            metadata = services.get_media_metadata(
                item.media_type,
                item.media_id,
                item.source,
                [item.season_number],
                item.episode_number,
                language=language,
            )
        except Exception as exc:
            logger.debug(
                "Could not backfill episode title for item %s: %s",
                item.id,
                exc,
            )
            return
        title_fields = _list_item_title_fields_from_metadata(item.media_type, metadata)

    if not title_fields:
        return

    update_fields = []
    for field_name, value in title_fields.items():
        if getattr(item, field_name) != value:
            setattr(item, field_name, value)
            update_fields.append(field_name)

    if update_fields:
        item.save(update_fields=update_fields)


def _attach_list_card_overrides(item_list):
    """Attach shared card overrides used by list grid cards."""
    episode_keys = {
        (str(item.media_id), item.source, item.season_number)
        for item in item_list
        if (
            getattr(item, "media_type", None) == MediaTypes.EPISODE.value
            and getattr(item, "season_number", None) is not None
        )
    }

    season_item_by_key = {}
    if episode_keys:
        season_filters = Q()
        for media_id, source, season_number in episode_keys:
            season_filters |= Q(
                media_id=media_id,
                source=source,
                media_type=MediaTypes.SEASON.value,
                season_number=season_number,
            )
        season_item_by_key = {
            (
                str(season_item.media_id),
                season_item.source,
                season_item.season_number,
            ): season_item
            for season_item in Item.objects.filter(season_filters)
        }

    season_metadata_by_key = {}
    for item in item_list:
        item_key = (str(item.media_id), item.source, item.season_number)
        season_item = season_item_by_key.get(item_key)
        item.card_image_override = _resolve_list_card_image_override(
            item,
            season_item=season_item,
        )
        if item_key not in season_metadata_by_key and _episode_title_needs_backfill(
            item, season_item=season_item
        ):
            try:
                season_metadata_by_key[item_key] = services.get_media_metadata(
                    MediaTypes.SEASON.value,
                    item.media_id,
                    item.source,
                    [item.season_number],
                )
            except Exception as exc:
                logger.debug(
                    "Could not prefetch season metadata for episode title backfill on item %s: %s",
                    item.id,
                    exc,
                )
                season_metadata_by_key[item_key] = None
        _maybe_backfill_episode_title(
            item,
            season_item=season_item,
            season_metadata=season_metadata_by_key.get(item_key),
        )


# ---------------------------------------------------------------------------
# List search result normalization
# ---------------------------------------------------------------------------


def _extract_list_search_results(media_type, data):
    """Normalize provider search payloads for list and recommendation UIs."""
    if media_type != MediaTypes.MUSIC.value:
        return data.get("results", []), data.get("total_pages", 1)

    # MusicBrainz combined search returns tracks under a nested payload.
    track_payload = data.get("tracks") if isinstance(data, dict) else None
    if isinstance(track_payload, dict):
        return track_payload.get("results", []), track_payload.get("total_pages", 1)

    return data.get("results", []), data.get("total_pages", 1)


# ---------------------------------------------------------------------------
# Table adapter helpers
# ---------------------------------------------------------------------------


class _ListTableRowAdapter:
    """Expose list items through the shared media-table row contract."""

    def __init__(self, list_item, collection_platforms_by_item_id=None):
        self._list_item = list_item
        self._source_media = getattr(list_item, "media", None)
        self.item = list_item
        self.id = getattr(self._source_media, "id", None)
        self.track_media_id = self.id
        self.created_at = getattr(list_item, "list_date_added", None)
        self.repeats = getattr(self._source_media, "repeats", 1) or 1
        self.display_platform = _extract_display_platform(
            list_item, collection_platforms_by_item_id
        )

    def __getattr__(self, attr):
        if self._source_media is not None and hasattr(self._source_media, attr):
            return getattr(self._source_media, attr)
        return getattr(self._list_item, attr)


def _adapt_list_items_for_table(items_page, collection_platforms_by_item_id=None):
    """Replace page rows with adapters that satisfy shared media-table cells."""
    items_page.object_list = [
        _ListTableRowAdapter(item, collection_platforms_by_item_id)
        for item in items_page.object_list
    ]
    return items_page


def _build_collection_platforms_by_item_id(user, item_ids):
    """Return {item_id: {platform, ...}} from the user's game collection entries."""
    platforms_by_item_id = {}
    if not user or not getattr(user, "is_authenticated", False) or not item_ids:
        return platforms_by_item_id
    for item_id, resolution in CollectionEntry.objects.filter(
        user=user,
        item_id__in=item_ids,
        item__media_type=MediaTypes.GAME.value,
    ).values_list("item_id", "resolution"):
        platform_value = str(resolution or "").strip()
        if platform_value:
            platforms_by_item_id.setdefault(item_id, set()).add(platform_value)
    return platforms_by_item_id


def _resolve_list_table_media_type(selected_media_types, filtered_media_types):
    if len(selected_media_types) == 1:
        return selected_media_types[0]

    unique_filtered_media_types = list(dict.fromkeys(filtered_media_types))
    if len(unique_filtered_media_types) == 1:
        return unique_filtered_media_types[0]

    return "all"


# ---------------------------------------------------------------------------
# Trakt credential helper
# ---------------------------------------------------------------------------


def _get_trakt_credentials(user):
    """Return decrypted Trakt ``(client_id, client_secret)`` for a user, if set.

    The secret is empty for a PKCE app, which has none.
    """
    trakt_account = TraktAccount.objects.filter(user=user).first()
    if not trakt_account or not trakt_account.client_id:
        return None
    try:
        client_id = import_helpers.decrypt(trakt_account.client_id)
        client_secret = (
            import_helpers.decrypt(trakt_account.client_secret)
            if trakt_account.client_secret
            else ""
        )
    except Exception:
        logger.exception(
            "Failed to decrypt Trakt credentials for user %s", user.username
        )
        return None
    return client_id, client_secret


# ---------------------------------------------------------------------------
# Media aggregation helpers (shared by smart-list and regular-list detail views)
# ---------------------------------------------------------------------------


# List sort choices, expressed as library-query sort keys.
LIST_SORT_KEYS = {
    ListDetailSortChoices.DATE_ADDED: "list_added",
    ListDetailSortChoices.CUSTOM: "list_added",
    ListDetailSortChoices.TITLE: "title",
    ListDetailSortChoices.MEDIA_TYPE: "type",
    ListDetailSortChoices.RATING: "score",
    ListDetailSortChoices.PROGRESS: "progress",
    ListDetailSortChoices.STATUS: "status",
    ListDetailSortChoices.RELEASE_DATE: "release_date",
    ListDetailSortChoices.START_DATE: "start_date",
    ListDetailSortChoices.END_DATE: "end_date",
    ListDetailSortChoices.PLATFORM: "platform",
    ListDetailSortChoices.TIER: "list_tier",
}


def paginate_list_items(
    *,
    custom_list,
    media_user,
    candidates,
    filters,
    sort_by,
    direction,
    page,
    page_size=16,
    media_types=None,
    initial="",
    title_user=None,
    include_alphabet=False,
):
    """Order and paginate a list's items with the library-query engine.

    ``candidates`` are the items the list shows (its members, or a smart
    list's live matches) as an ``Item`` id queryset or ids. Tracking values -
    status, rating, progress - are read for ``media_user``. Only the requested
    page is loaded and decorated. ``media_types`` is the candidates' distinct
    media types when the caller already read them, which saves re-evaluating
    ``candidates``. Returns ``(items_page, filtered_count)``.
    """
    from app.library_query import LibraryQuery, LibraryQueryExecutor, SortSpec
    from app.library_query.alphabet import (
        initial_condition,
        normalize_initial,
        query_other_initial,
    )
    from app.library_query.spec import ROUTING_MODEL

    if media_types is None:
        media_types = Item.objects.filter(pk__in=candidates).order_by().values_list(
            "media_type", flat=True
        ).distinct()
    media_types = tuple(media_types)
    if sort_by == ListDetailSortChoices.CUSTOM:
        direction = "asc"  # The list's own order has no direction.
    query = LibraryQuery(
        media_types=media_types,
        filters=filters,
        sort=SortSpec(key=LIST_SORT_KEYS.get(sort_by, "list_added"), direction=direction),
        within=candidates,
        sort_list_id=custom_list.id,
        routing=ROUTING_MODEL,
        dedupe_cross_provider=False,
        provider_region=str(getattr(media_user, "watch_provider_region", "") or ""),
        pinned_providers=tuple(getattr(media_user, "pinned_watch_providers", None) or ()),
    )
    other_initial = query_other_initial(media_user, query, display_user=title_user) if include_alphabet else None
    if normalize_initial(initial):
        query = replace(query, within=Item.objects.filter(pk__in=candidates).filter(
            initial_condition(initial, title_user or media_user)
        ).values("pk"))
    executor = LibraryQueryExecutor(media_user, query)
    total = executor.count()
    items_page = Paginator(range(total), page_size).get_page(page)
    offset = (items_page.number - 1) * page_size
    items = executor.page(offset, page_size, total=total).items
    added = {
        item_id: (date_added, rank)
        for item_id, date_added, rank in CustomListItem.objects.filter(
            custom_list=custom_list,
            item_id__in=[item.pk for item in items],
        ).values_list("item_id", "date_added", "rank")
    }
    for item in items:
        item.list_date_added, item.list_rank = added.get(item.pk, (None, None))
    items_page.object_list = items
    items_page.alphabet_other_initial = other_initial
    _attach_media_with_aggregation(items_page, media_user)
    return items_page, total


def build_tier_columns(custom_list, items):
    """Group one page of list items by tier.

    Returns ``(columns, unranked)``: one ``{"tier", "items"}`` per tier in the
    list's tier order, then the items with no (or an unknown) tier. Each group
    keeps the order of ``items``.
    """
    from lists.tiers import ink_for, resolve_tiers

    tier_by_item = dict(
        CustomListItem.objects.filter(
            custom_list=custom_list,
            item_id__in=[item.pk for item in items],
        ).values_list("item_id", "tier"),
    )
    columns = [
        {"tier": {**tier, "ink": ink_for(tier["color"])}, "items": []}
        for tier in resolve_tiers(custom_list)
    ]
    column_by_id = {column["tier"]["id"]: column for column in columns}
    unranked = []
    for item in items:
        column = column_by_id.get(tier_by_item.get(item.pk, ""))
        (column["items"] if column else unranked).append(item)
    return columns, unranked


def _attach_media_with_aggregation(item_list, media_user):
    """Attach `.media` to each item in item_list using the given user's library data.

    Uses the smart-list version of Episode normalization so that Episode entries
    expose `status`, `score`, `progress`, and `max_progress` compatible with list
    card templates.
    """
    media_by_item_id = {}
    media_types_in_items = {item.media_type for item in item_list}
    media_manager = MediaManager()

    for media_type in media_types_in_items:
        model = apps.get_model("app", media_type)
        item_ids = [item.id for item in item_list if item.media_type == media_type]
        if not item_ids:
            continue

        if media_type == MediaTypes.EPISODE.value:
            filter_kwargs = {
                "item_id__in": item_ids,
                "related_season__user": media_user,
            }
        else:
            filter_kwargs = {
                "item_id__in": item_ids,
                "user": media_user,
            }

        select_related_fields = ["item"]
        if media_type == MediaTypes.EPISODE.value:
            select_related_fields.extend(
                [
                    "related_season",
                    "related_season__item",
                    "related_season__related_tv",
                    "related_season__related_tv__item",
                ],
            )
        queryset = model.objects.filter(**filter_kwargs).select_related(
            *select_related_fields
        )
        queryset = media_manager._apply_prefetch_related(queryset, media_type)
        media_manager.annotate_max_progress(queryset, media_type)

        entries_by_item = {}
        for entry in queryset:
            if media_type == MediaTypes.EPISODE.value:
                # Episode does not inherit Media; expose compatible fields for list templates.
                if not hasattr(entry, "status"):
                    entry.status = getattr(entry.related_season, "status", None)
                if not hasattr(entry, "score"):
                    entry.score = None
                if not hasattr(entry, "progress"):
                    entry.progress = entry.item.episode_number
                if not hasattr(entry, "max_progress"):
                    entry.max_progress = getattr(
                        entry.related_season, "max_progress", None
                    )
            entries_by_item.setdefault(entry.item_id, []).append(entry)

        for item_id, entries in entries_by_item.items():
            entries.sort(key=lambda entry: entry.created_at, reverse=True)
            display_media = entries[0]
            if len(entries) > 1:
                media_manager._aggregate_item_data(display_media, entries)
            media_by_item_id[item_id] = display_media

    from app.services.podcast_list_tracking import podcast_show_trackers_for_items

    media_by_item_id.update(podcast_show_trackers_for_items(media_user, item_list))
    from app.services.music import album_trackers_for_list_items

    media_by_item_id.update(album_trackers_for_list_items(media_user, item_list))
    for item in item_list:
        item.media = media_by_item_id.get(item.id)
    from app.media_list_filters import attach_card_collection_platforms

    attach_card_collection_platforms(media_user, item_list)
    _attach_list_card_overrides(item_list)


def _attach_kometa_episode_urls(items_page):
    """Attach TVDB-shaped episode links for Kometa list-page discovery.

    Kometa's Floppy/Yamtrack builder extracts IDs from detail-page anchors. Its
    episode-aware parser recognises TVDB paths, while a TMDB episode path is
    reduced to the parent show. Keep Floppy's normal link as the visible target
    and add a parser-compatible link only when the local library already knows
    the show's numeric TVDB ID.
    """
    items = list(getattr(items_page, "object_list", items_page) or [])
    episode_items = [
        item for item in items if item.media_type == MediaTypes.EPISODE.value
    ]
    if not episode_items:
        return

    parent_keys = {(item.source, item.media_id) for item in episode_items}
    parent_items = Item.objects.filter(
        source__in={source for source, _media_id in parent_keys},
        media_id__in={media_id for _source, media_id in parent_keys},
        media_type__in=(MediaTypes.TV.value, MediaTypes.ANIME.value),
        season_number__isnull=True,
        episode_number__isnull=True,
    ).only("id", "source", "media_id", "title", "provider_external_ids")

    tvdb_by_parent_key = {}
    parent_item_ids = []
    parent_key_by_id = {}
    parent_id_by_key = {}
    parent_title_by_key = {}
    for parent in parent_items:
        parent_item_ids.append(parent.id)
        parent_key = (parent.source, parent.media_id)
        parent_key_by_id[parent.id] = parent_key
        parent_id_by_key[parent_key] = parent.id
        parent_title_by_key[parent_key] = parent.title
        tvdb_id = (parent.provider_external_ids or {}).get("tvdb_id")
        if str(tvdb_id or "").isdigit():
            tvdb_by_parent_key.setdefault(parent_key, (str(tvdb_id), parent.title))

    if parent_item_ids:
        provider_links = ItemProviderLink.objects.filter(
            item_id__in=parent_item_ids,
            provider=Sources.TVDB.value,
            provider_media_type=MediaTypes.TV.value,
            season_number__isnull=True,
        ).values_list("item_id", "provider_media_id")
        for parent_id, provider_media_id in provider_links:
            parent_key = parent_key_by_id.get(parent_id)
            if parent_key and str(provider_media_id or "").isdigit():
                tvdb_by_parent_key.setdefault(
                    parent_key,
                    (str(provider_media_id), parent_title_by_key.get(parent_key)),
                )

    missing_tvdb_parent_ids = set()
    for item in episode_items:
        parent_key = (item.source, item.media_id)
        tvdb_id, parent_title = tvdb_by_parent_key.get(parent_key, ("", None))
        if not tvdb_id.isdigit():
            parent_id = parent_id_by_key.get(parent_key)
            if parent_id is not None:
                missing_tvdb_parent_ids.add(parent_id)
            continue

        item.kometa_episode_url = reverse(
            "episode_details",
            kwargs={
                "source": Sources.TVDB.value,
                "media_id": tvdb_id,
                "title": slugify(parent_title or "") or tvdb_id,
                "season_number": item.season_number,
                "episode_number": item.episode_number,
            },
        )

    if missing_tvdb_parent_ids:
        _enqueue_tvdb_id_backfill(missing_tvdb_parent_ids)


def _enqueue_tvdb_id_backfill(item_ids):
    """Best-effort queue a metadata refresh for shows missing a TVDB id.

    Reuses the existing external-IDs backfill sweep (which fetches provider
    metadata and persists tvdb_id as a side effect) so future page loads can
    resolve the Kometa episode anchor. Failures here must never break list
    rendering.
    """
    try:
        from app.tasks_external_ids import enqueue_external_ids_backfill_items

        enqueue_external_ids_backfill_items(list(item_ids))
    except Exception:
        logger.exception("Failed to enqueue TVDB id backfill for items")




def _extract_display_platform(item, collection_platforms_by_item_id=None):
    """Resolve a single display platform: collection data > sole IGDB platform."""
    if not item:
        return ""
    if collection_platforms_by_item_id:
        collected = collection_platforms_by_item_id.get(item.id, set())
        if collected:
            return sorted(collected, key=lambda value: value.lower())[0]
    platforms = getattr(item, "platforms", None)
    if not platforms:
        return ""
    if isinstance(platforms, str):
        return platforms.strip()
    if isinstance(platforms, list):
        normalized = [str(p).strip() for p in platforms if str(p).strip()]
        return normalized[0] if len(normalized) == 1 else ""
    return ""


def list_has_item_identity(custom_list, item):
    """Return True if the list holds this title in any library bucket.

    The same title can be stored as several Item rows (one per library bucket),
    so checking only ``item.id`` would let a second bucket's row in twice.
    """
    return custom_list.items.filter(
        media_id=item.media_id,
        source=item.source,
        media_type=item.media_type,
        season_number=item.season_number,
        episode_number=item.episode_number,
    ).exists()
