"""Request-scoped policy for grouping matching media-list entries."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from django.apps import apps
from django.db import models

from app.models.choices import MediaTypes
from app.models.manager import (
    MediaManager,
    _media_list_deferred_item_fields,
)
from users.models import MediaStatusChoices

ENTRY_GROUPING_PREFERENCE_FIELDS = {
    MediaTypes.MOVIE.value: "movie_show_each_play",
    MediaTypes.ANIME.value: "anime_show_each_play",
    MediaTypes.MANGA.value: "manga_show_each_play",
    MediaTypes.GAME.value: "game_show_each_play",
    MediaTypes.BOOK.value: "book_show_each_play",
}

_ENTRY_GROUPING_MODE: ContextVar[bool | None] = ContextVar(
    "media_list_entry_grouping_mode",
    default=None,
)
_POST_SORT_KEYS = frozenset({"progress", "plays", "next_episode_air_date"})

def preference_field(media_type: str) -> str | None:
    """Return the saved preference field for a supported media type."""
    return ENTRY_GROUPING_PREFERENCE_FIELDS.get(media_type)


def show_separate_entries(user, media_type: str) -> bool:
    """Return whether the user wants matching entries as separate rows."""
    field_name = preference_field(media_type)
    return bool(field_name and getattr(user, field_name, False))


def active_entry_grouping_mode() -> bool | None:
    """Return this request's grouping mode: separate, grouped, or not offered."""
    return _ENTRY_GROUPING_MODE.get()


def entry_grouping_is_separate() -> bool:
    """Return whether the current request must preserve separate tracker rows."""
    return _ENTRY_GROUPING_MODE.get() is True


@contextmanager
def entry_grouping_mode(enabled: bool | None):
    """Set the list-entry grouping mode for one request context."""
    token = _ENTRY_GROUPING_MODE.set(enabled)
    try:
        yield
    finally:
        _ENTRY_GROUPING_MODE.reset(token)


def _normalized_status_filters(status_filter) -> list[str]:
    if isinstance(status_filter, (list, tuple, set, frozenset)):
        return [
            value
            for value in status_filter
            if value and value != MediaStatusChoices.ALL
        ]
    if status_filter and status_filter != MediaStatusChoices.ALL:
        return [status_filter]
    return []


def _get_separate_media_list(
    manager,
    user,
    media_type,
    status_filter,
    sort_filter,
    search,
    direction,
    list_sql_filters,
    *,
    needs_watch_providers=False,
):
    """Return raw tracking rows without duplicate reduction or aggregation."""
    model = apps.get_model(app_label="app", model_name=media_type)
    direction = manager.resolve_direction(sort_filter, direction)
    queryset = model.objects.filter(user=user.id)

    status_filters = _normalized_status_filters(status_filter)
    if status_filters:
        queryset = queryset.filter(status__in=status_filters)
    else:
        queryset = queryset.exclude(status__isnull=True)

    if search:
        queryset = queryset.filter(
            models.Q(item__title__icontains=search)
            | models.Q(item__media_id__icontains=search),
        )

    queryset = manager._apply_list_sql_filters(
        queryset,
        user,
        media_type,
        list_sql_filters or {},
    )
    # The same deferral the grouped path uses, from the same definition. A
    # local copy of the list had drifted from it and no longer deferred
    # item__watch_providers -- roughly 146 KiB of JSON a title, decoded for
    # the whole library, on a page that never renders it.
    queryset = queryset.select_related("item").defer(
        *_media_list_deferred_item_fields(
            needs_watch_providers=needs_watch_providers,
        ),
    )
    queryset = manager._apply_prefetch_related(
        queryset,
        media_type,
        list_mode=True,
    )

    if not sort_filter:
        return list(queryset)

    if (
        sort_filter in _POST_SORT_KEYS
        and media_type not in {MediaTypes.TV.value, MediaTypes.SEASON.value}
    ):
        queryset = list(queryset)

    return list(
        manager._sort_media_list(
            queryset,
            sort_filter,
            media_type,
            direction,
        )
    )


def _install_media_list_policy() -> None:
    current_method = MediaManager.get_media_list
    if hasattr(current_method, "_supports_entry_grouping_context"):
        return

    @wraps(current_method)
    def get_media_list(
        manager,
        user,
        media_type,
        status_filter,
        sort_filter,
        search=None,
        direction=None,
        *,
        list_sql_filters=None,
        needs_watch_providers=False,
        prefetch_graph=True,
    ):
        """Return media rows under the active entry-grouping policy."""
        if _ENTRY_GROUPING_MODE.get() is not True:
            return current_method(
                manager,
                user,
                media_type,
                status_filter,
                sort_filter,
                search,
                direction,
                list_sql_filters=list_sql_filters,
                needs_watch_providers=needs_watch_providers,
                prefetch_graph=prefetch_graph,
            )

        return _get_separate_media_list(
            manager,
            user,
            media_type,
            status_filter,
            sort_filter,
            search,
            direction,
            list_sql_filters,
            needs_watch_providers=needs_watch_providers,
        )

    get_media_list._supports_entry_grouping_context = True
    MediaManager.get_media_list = get_media_list


_install_media_list_policy()
