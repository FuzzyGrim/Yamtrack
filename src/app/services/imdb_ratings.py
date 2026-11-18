"""Sync IMDB's public aggregate ratings for movies, shows, and episodes.

Movies/shows already carry their IMDB id in ``provider_external_ids["imdb_id"]``
(resolved by TMDB). This module downloads IMDB's public ``title.ratings``
dataset filtered to those ids, and separately uses the public
``title.episode`` dataset to resolve each show's per-episode tconsts (by
season/episode number) so their ratings can be looked up too — no extra API
calls needed for either step.
"""

from __future__ import annotations

import logging

from app.models import Item, MediaTypes

logger = logging.getLogger(__name__)

_SHOW_MEDIA_TYPES = (MediaTypes.TV.value, MediaTypes.ANIME.value)

# The sync reads and writes only these columns. Loading whole Items also decodes
# watch_providers (TMDB's availability for every region, ~146 KiB a title), which
# held the task at a ~529 MiB high-water mark on a 1,400-title library.
_RATING_FIELDS = ("imdb_rating", "imdb_rating_count")


def _chunks(values, size=500):
    """Yield lists of at most ``size`` values (SQLite bound-variable limit)."""
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start : start + size]


def sync_movie_and_show_ratings() -> int:
    """Update imdb_rating/imdb_rating_count for movies and shows with a known imdb id.

    Returns the number of Items updated.
    """
    from app.providers import imdb_datasets

    items = list(
        Item.objects.filter(
            media_type__in=(MediaTypes.MOVIE.value, *_SHOW_MEDIA_TYPES),
            provider_external_ids__has_key="imdb_id",
        ).only("provider_external_ids", *_RATING_FIELDS),
    )
    if not items:
        return 0

    tconsts = {item.provider_external_ids["imdb_id"] for item in items}

    try:
        ratings = imdb_datasets.download_ratings(tconsts)
    except Exception:
        logger.warning("imdb_ratings: failed to download title ratings", exc_info=True)
        return 0

    updated = []
    for item in items:
        rating = ratings.get(item.provider_external_ids["imdb_id"])
        if rating is None:
            continue
        average_rating, num_votes = rating
        if item.imdb_rating == average_rating and item.imdb_rating_count == num_votes:
            continue
        item.imdb_rating = average_rating
        item.imdb_rating_count = num_votes
        updated.append(item)

    if updated:
        Item.objects.bulk_update(
            updated, ["imdb_rating", "imdb_rating_count"], batch_size=500
        )
        logger.info("imdb_ratings: updated %d movie/show ratings", len(updated))

    return len(updated)


def sync_episode_ratings() -> int:
    """Update imdb_rating/imdb_rating_count for episodes of tracked shows.

    Resolves each show's episode tconsts via the public title.episode
    dataset (keyed by season/episode number), then looks up ratings for
    those tconsts and matches them back to Episode-typed Items by
    media_id/source/season_number/episode_number. The resolved tconst is
    kept on the episode item's ``provider_external_ids["imdb_id"]`` so the
    detail page can link the rating chip to the IMDb episode page.

    Returns the number of Items updated.
    """
    from app.providers import imdb_datasets

    shows = list(
        Item.objects.filter(
            media_type__in=_SHOW_MEDIA_TYPES,
            provider_external_ids__has_key="imdb_id",
        ).values("media_id", "source", "provider_external_ids"),
    )
    if not shows:
        return 0

    show_tconst_by_key = {
        (show["media_id"], show["source"]): show["provider_external_ids"]["imdb_id"]
        for show in shows
    }
    parent_tconsts = set(show_tconst_by_key.values())

    try:
        episode_map = imdb_datasets.download_episode_map(parent_tconsts)
    except Exception:
        logger.warning("imdb_ratings: failed to download episode map", exc_info=True)
        return 0

    if not episode_map:
        return 0

    child_tconsts = {
        tconst
        for season_episode_map in episode_map.values()
        for tconst in season_episode_map.values()
    }

    try:
        ratings = imdb_datasets.download_ratings(child_tconsts)
    except Exception:
        logger.warning(
            "imdb_ratings: failed to download episode title ratings", exc_info=True
        )
        return 0

    if not ratings:
        return 0

    updated = []
    imdb_id_backfill = []
    media_ids = {media_id for media_id, _ in show_tconst_by_key}
    for media_id_chunk in _chunks(media_ids):
        episode_items = Item.objects.filter(
            media_id__in=media_id_chunk,
            media_type=MediaTypes.EPISODE.value,
        ).only(
            "media_id",
            "source",
            "season_number",
            "episode_number",
            "provider_external_ids",
            *_RATING_FIELDS,
        )
        for episode_item in episode_items.iterator(chunk_size=2000):
            parent_tconst = show_tconst_by_key.get(
                (episode_item.media_id, episode_item.source),
            )
            season_episode_map = episode_map.get(parent_tconst)
            if not season_episode_map:
                continue
            if (
                episode_item.season_number is None
                or episode_item.episode_number is None
            ):
                continue
            tconst = season_episode_map.get(
                (episode_item.season_number, episode_item.episode_number),
            )
            if tconst is None:
                continue
            rating = ratings.get(tconst)
            if rating is None:
                continue
            average_rating, num_votes = rating
            rating_changed = not (
                episode_item.imdb_rating == average_rating
                and episode_item.imdb_rating_count == num_votes
            )
            # Keep the resolved tconst on the item so the episode page can
            # link to IMDb. A separate bulk_update so steady-state rating
            # writes don't touch provider_external_ids, a sync-identity field
            # whose updates invalidate media-server sync contexts.
            external_ids = dict(episode_item.provider_external_ids or {})
            if external_ids.get("imdb_id") != tconst:
                external_ids["imdb_id"] = tconst
                episode_item.provider_external_ids = external_ids
                imdb_id_backfill.append(episode_item)
            if rating_changed:
                episode_item.imdb_rating = average_rating
                episode_item.imdb_rating_count = num_votes
                updated.append(episode_item)

    if imdb_id_backfill:
        Item.objects.bulk_update(
            imdb_id_backfill, ["provider_external_ids"], batch_size=500
        )
    if updated:
        Item.objects.bulk_update(
            updated, ["imdb_rating", "imdb_rating_count"], batch_size=500
        )
        logger.info("imdb_ratings: updated %d episode ratings", len(updated))

    return len(updated)


def sync_season_ratings() -> int:
    """Derive season imdb_rating/imdb_rating_count from their episodes' ratings.

    IMDB's public dataset has no season-granular title id, so a season's rating
    is instead computed locally as a vote-weighted average of its episode
    Items' ratings (synced separately by sync_episode_ratings) and written
    back onto the season's Item row.

    Returns the number of Items updated.
    """
    # Item.save() defaults library_media_type to media_type when it was never
    # set explicitly, so an episode/season pair in the same (non-anime) bucket
    # can carry different raw values ("episode" vs "season"). Group by whether
    # the bucket is the anime one instead of by the raw stored value, mirroring
    # api.helpers.resolve_item_queryset's "anime vs everything else" split.
    # One pass over the rated episodes, grouped per (show, bucket, season).
    totals = {}
    for media_id, source, library_media_type, season_number, rating, count in (
        Item.objects.filter(
            media_type=MediaTypes.EPISODE.value,
            imdb_rating_count__isnull=False,
            imdb_rating__isnull=False,
            season_number__isnull=False,
        ).values_list(
            "media_id",
            "source",
            "library_media_type",
            "season_number",
            "imdb_rating",
            "imdb_rating_count",
        ).iterator(chunk_size=2000)
    ):
        key = (
            media_id,
            source,
            library_media_type == MediaTypes.ANIME.value,
            season_number,
        )
        weighted_sum, vote_count = totals.get(key, (0.0, 0))
        totals[key] = (weighted_sum + rating * count, vote_count + count)
    if not totals:
        return 0

    updated = []
    media_ids = {key[0] for key in totals}
    for media_id_chunk in _chunks(media_ids):
        season_items = Item.objects.filter(
            media_id__in=media_id_chunk,
            media_type=MediaTypes.SEASON.value,
            season_number__isnull=False,
        ).only("media_id", "source", "library_media_type", "season_number", *_RATING_FIELDS)
        for season_item in season_items.iterator(chunk_size=2000):
            weighted_sum, vote_count = totals.get(
                (
                    season_item.media_id,
                    season_item.source,
                    season_item.library_media_type == MediaTypes.ANIME.value,
                    season_item.season_number,
                ),
                (0.0, 0),
            )
            if vote_count == 0:
                continue
            average_rating = round(weighted_sum / vote_count, 1)
            if (
                season_item.imdb_rating == average_rating
                and season_item.imdb_rating_count == vote_count
            ):
                continue
            season_item.imdb_rating = average_rating
            season_item.imdb_rating_count = vote_count
            updated.append(season_item)

    if updated:
        Item.objects.bulk_update(
            updated, ["imdb_rating", "imdb_rating_count"], batch_size=500
        )
        logger.info("imdb_ratings: updated %d season ratings", len(updated))

    return len(updated)
