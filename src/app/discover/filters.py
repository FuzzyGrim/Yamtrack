"""Filtering and dedupe helpers for Discover rows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.apps import apps

from app.models import (
    CollectionEntry,
    DiscoverFeedback,
    DiscoverFeedbackType,
    MediaTypes,
    Status,
)

if TYPE_CHECKING:
    from app.discover.schemas import CandidateItem

DEFAULT_BLOCKED_STATUSES = {
    Status.COMPLETED.value,
    Status.DROPPED.value,
    Status.IN_PROGRESS.value,
}

MEDIA_TYPE_TO_MODEL = {
    MediaTypes.MOVIE.value: "movie",
    MediaTypes.TV.value: "tv",
    MediaTypes.ANIME.value: "anime",
    MediaTypes.MUSIC.value: "music",
    MediaTypes.PODCAST.value: "podcast",
    MediaTypes.BOOK.value: "book",
    MediaTypes.COMIC.value: "comic",
    MediaTypes.MANGA.value: "manga",
    MediaTypes.GAME.value: "game",
    MediaTypes.BOARDGAME.value: "boardgame",
}


def _item_identity_keys(queryset) -> set[tuple[str, str, str]]:
    """Return the (media_type, source, media_id) of each row's item.

    Reads the three columns only. Building a model instance per row (and its
    field tracker) cost a third of a movie tab rebuild on a large library.
    """
    return {
        (str(media_type), str(source), str(media_id))
        for media_type, source, media_id in queryset.values_list(
            "item__media_type",
            "item__source",
            "item__media_id",
        )
    }


def get_tracked_keys_by_media_type(
    user,
    media_type: str,
    *,
    statuses: set[str] | None = None,
) -> set[tuple[str, str, str]]:
    """Return tracked keys for statuses excluded from most Discover rows."""
    model_name = MEDIA_TYPE_TO_MODEL.get(media_type)
    if not model_name:
        return set()

    model = apps.get_model("app", model_name)
    target_statuses = statuses or DEFAULT_BLOCKED_STATUSES
    return _item_identity_keys(
        model.objects.filter(user=user, status__in=target_statuses),
    )


def get_feedback_keys_by_media_type(
    user,
    media_type: str,
    *,
    feedback_type: str = DiscoverFeedbackType.NOT_INTERESTED.value,
) -> set[tuple[str, str, str]]:
    """Return hidden Discover feedback identities for a media type."""
    return _item_identity_keys(
        DiscoverFeedback.objects.filter(
            user=user,
            item__media_type=media_type,
            feedback_type=feedback_type,
        ),
    )


def get_owned_keys_by_media_type(user, media_type: str) -> set[tuple[str, str, str]]:
    """Return identity keys for items already in the user's owned collection."""
    return _item_identity_keys(
        CollectionEntry.objects.filter(user=user, item__media_type=media_type),
    )


def exclude_owned_items(
    candidates: list[CandidateItem],
    owned_keys: set[tuple[str, str, str]],
) -> list[CandidateItem]:
    """Filter out candidates already present in the user's owned collection."""
    if not owned_keys:
        return list(candidates)
    return [
        candidate for candidate in candidates if candidate.identity() not in owned_keys
    ]


def exclude_tracked_items(
    candidates: list[CandidateItem],
    tracked_keys: set[tuple[str, str, str]],
) -> list[CandidateItem]:
    """Filter out candidates already tracked in excluded statuses."""
    if not tracked_keys:
        return list(candidates)
    return [
        candidate
        for candidate in candidates
        if candidate.identity() not in tracked_keys
    ]


def dedupe_candidates(
    candidates: list[CandidateItem],
    *,
    seen_identities: set[tuple[str, str, str]],
) -> list[CandidateItem]:
    """Dedupe candidates against already-rendered higher-priority rows."""
    unique_items: list[CandidateItem] = []
    for candidate in candidates:
        identity = candidate.identity()
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        unique_items.append(candidate)
    return unique_items
