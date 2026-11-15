"""Provider-backed validation for TV season/episode coordinates."""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Episode, MediaTypes, Season
from app.providers import services


class EpisodeCoordinateError(Exception):
    """Base class for episode-coordinate validation failures."""


class InvalidEpisodeCoordinateError(EpisodeCoordinateError):
    """The requested episode is not present in the season metadata."""


class EpisodeMetadataUnavailableError(EpisodeCoordinateError):
    """The season response did not contain an authoritative episode list."""


@dataclass(frozen=True, slots=True)
class EpisodeCoordinate:
    """An episode and the authoritative season metadata that contains it."""

    season_metadata: dict
    episode: dict


def _episode_map(season_metadata):
    """Return normalized episode metadata, or reject non-authoritative data."""
    if not isinstance(season_metadata, dict):
        raise EpisodeMetadataUnavailableError

    episodes = season_metadata.get("episodes")
    if not isinstance(episodes, list):
        raise EpisodeMetadataUnavailableError

    episode_map = {}
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        try:
            episode_number = int(episode["episode_number"])
        except (KeyError, TypeError, ValueError):
            continue
        episode_map[episode_number] = episode
    return episode_map


def resolve_episode_coordinate(
    media_id,
    source,
    season_number,
    episode_number,
    *,
    season_metadata=None,
    language=None,
):
    """Resolve an episode only when its number is in the season endpoint list."""
    if season_metadata is None:
        season_metadata = services.get_media_metadata(
            MediaTypes.SEASON.value,
            media_id,
            source,
            [season_number],
            language=language,
        )

    episode_map = _episode_map(season_metadata)
    normalized_episode_number = int(episode_number)
    episode = episode_map.get(normalized_episode_number)
    if episode is None:
        message = f"Episode S{int(season_number):02d}E{normalized_episode_number:02d} was not found."
        raise InvalidEpisodeCoordinateError(message)
    return EpisodeCoordinate(season_metadata=season_metadata, episode=episode)


def cleanup_episode_history_for_season(season, episode_number):
    """Delete detached history for one already-resolved tracked season."""
    if season.item.episode_order_id or season.order_archived:
        return 0
    return Episode.objects.filter(
        related_season=season,
        item__media_id=season.item.media_id,
        item__source=season.item.source,
        item__season_number=season.item.season_number,
        item__episode_number=int(episode_number),
    ).delete()[0]


def cleanup_episode_history_for_route(
    user,
    media_id,
    source,
    season_number,
    episode_number,
    *,
    library_media_type=None,
):
    """Delete detached history rows matching a user's episode route."""
    if str(media_id).startswith("order_"):
        return 0
    seasons = Season.objects.filter(
        user=user,
        item__media_id=media_id,
        item__source=source,
        item__media_type=MediaTypes.SEASON.value,
        item__season_number=season_number,
        item__episode_number__isnull=True,
        order_archived=False,
        item__episode_order__isnull=True,
    )
    if library_media_type:
        seasons = seasons.filter(item__library_media_type=library_media_type)

    return Episode.objects.filter(
        related_season__in=seasons,
        item__media_id=media_id,
        item__source=source,
        item__season_number=season_number,
        item__episode_number=int(episode_number),
    ).delete()[0]
