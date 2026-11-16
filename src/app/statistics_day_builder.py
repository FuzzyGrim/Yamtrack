"""Per-day statistics builder — extracted from statistics_cache.py."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import models
from django.db.models import Q
from django.utils import timezone

from app.metadata_utils import ANIME_SUPPLEMENT_GENRE, genre_list_has_name
from app.models import MediaTypes, Sources
from app.statistics_day_cache import (
    STATISTICS_DAY_CACHE_TIMEOUT,
    _day_cache_key,
    _get_history_version,
    _normalize_day_value,
)
from app.statistics_talent import (
    _resolve_missing_credit_item_ids,
    _safe_runtime_minutes,
)
from app.stats_music import _country_name_from_code
from app.stats_podcast import _get_podcast_runtime_minutes
from app.stats_time import _get_music_runtime_minutes
from app.stats_utils import (
    _coerce_genre_list,
    _get_entry_play_dates,
    _localize_datetime,
)

logger = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400


def _day_bounds(day_value):
    day = _normalize_day_value(day_value)
    if not day:
        return None, None
    tz = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.combine(day, datetime.min.time()), tz)
    day_end = day_start + timedelta(days=1)
    return day_start, day_end


def _day_boundary_datetime(day_value, *, end_of_day=False):
    """Return an aware datetime at the day boundary in the current timezone."""
    day = _normalize_day_value(day_value)
    if not day:
        return None
    tz = timezone.get_current_timezone()
    boundary_time = datetime.max.time() if end_of_day else datetime.min.time()
    return timezone.make_aware(datetime.combine(day, boundary_time), tz)


def _iter_day_range(start_date, end_date):
    if not start_date or not end_date:
        return []
    start_day = start_date.date() if hasattr(start_date, "date") else start_date
    end_day = end_date.date() if hasattr(end_date, "date") else end_date
    if start_day > end_day:
        start_day, end_day = end_day, start_day
    day_count = (end_day - start_day).days + 1
    return [start_day + timedelta(days=offset) for offset in range(day_count)]


def _overlap_day_filter(day_start, day_end):
    return (
        (
            Q(start_date__isnull=False, end_date__isnull=False)
            & ~(Q(end_date__lt=day_start) | Q(start_date__gt=day_end))
        )
        | (
            Q(start_date__isnull=False, end_date__isnull=True)
            & Q(start_date__gte=day_start, start_date__lt=day_end)
        )
        | (
            Q(start_date__isnull=True, end_date__isnull=False)
            & Q(end_date__gte=day_start, end_date__lt=day_end)
        )
    )


def _bucket_overlap_rows(rows, day_list_set):
    """Bucket already-fetched Media-style rows by every day they overlap.

    Mirrors `_overlap_day_filter`'s per-day semantics (a row with both
    start_date and end_date lands on every calendar day in between; a row
    with only one of the two lands on that field's single day) so a single
    range-wide query can stand in for what used to be one query per day.
    """
    buckets = defaultdict(list)
    for row in rows:
        start_dt = row.get("start_date")
        end_dt = row.get("end_date")
        if start_dt and end_dt:
            start_day = _normalize_day_value(start_dt)
            end_day = _normalize_day_value(end_dt)
            if not start_day or not end_day:
                continue
            if start_day > end_day:
                start_day, end_day = end_day, start_day
            for day in _iter_day_range(start_day, end_day):
                if day in day_list_set:
                    buckets[day].append(row)
        elif start_dt:
            day = _normalize_day_value(start_dt)
            if day in day_list_set:
                buckets[day].append(row)
        elif end_dt:
            day = _normalize_day_value(end_dt)
            if day in day_list_set:
                buckets[day].append(row)
    return buckets


def _bucket_single_field_rows(rows, field, day_list_set):
    """Bucket already-fetched rows by the calendar day of a single datetime field."""
    buckets = defaultdict(list)
    for row in rows:
        value = row.get(field)
        if not value:
            continue
        day = _normalize_day_value(value)
        if day in day_list_set:
            buckets[day].append(row)
    return buckets


def _fetch_overlap_rows(
    model,
    user,
    day,
    day_start,
    day_end,
    values_fields,
    chunk_size,
    prefetch_key,
    prefetch,
):
    """Return rows for one media model/day, from `prefetch` if supplied, else a live query."""
    if prefetch is not None:
        return prefetch.get(prefetch_key, {}).get(day, [])
    return list(
        model.objects.filter(user=user)
        .filter(_overlap_day_filter(day_start, day_end))
        .values(*values_fields)
        .iterator(chunk_size=chunk_size),
    )


def _build_prefetch_for_range(user, day_list):
    """Bulk-fetch every media model once across `day_list`'s full span, bucketed by day.

    `build_stats_for_day` normally issues ~13 separate queries *per day* (one
    per media type, scoped to that single day's window). For a full-history
    rebuild spanning hundreds or thousands of active days, that's thousands
    of serial round trips for the same handful of query shapes. This fetches
    each model exactly once across the whole range and buckets the rows by
    day in Python, so `build_stats_for_day(..., prefetch=...)` can look up
    its per-day slice instead of hitting the DB again.
    """
    if not day_list:
        return None

    active_media_types = set(getattr(user, "get_active_media_types", list)())
    if not active_media_types:
        active_media_types = set(MediaTypes.values)

    day_list_set = set(day_list)
    range_start, _ = _day_bounds(min(day_list))
    _, range_end = _day_bounds(max(day_list))

    prefetch = {}
    credit_candidates = set()

    if (
        MediaTypes.TV.value in active_media_types
        or MediaTypes.SEASON.value in active_media_types
    ):
        Episode = apps.get_model("app", "Episode")
        episode_rows = list(
            Episode.all_objects.filter(
                Episode.watch_q(),
                related_season__user=user,
                end_date__gte=range_start,
                end_date__lt=range_end,
            )
            .values(
                "item_id",
                "end_date",
                "item__runtime_minutes",
                "item__media_id",
                "item__source",
                "item__season_number",
                "related_season_id",
                "related_season__item_id",
                "related_season__status",
                "related_season__score",
                "related_season__created_at",
                "related_season__related_tv_id",
                "related_season__related_tv__item_id",
                "related_season__related_tv__status",
                "related_season__related_tv__score",
                "related_season__related_tv__created_at",
                "related_season__related_tv__item__genres",
                "related_season__related_tv__item__library_media_type",
                "related_season__related_tv__item__release_datetime",
            )
            .iterator(chunk_size=2000),
        )
        prefetch["episode"] = _bucket_single_field_rows(
            episode_rows, "end_date", day_list_set
        )
        for row in episode_rows:
            if row.get("item__source") == Sources.TMDB.value:
                for key in (
                    "item_id",
                    "related_season__related_tv__item_id",
                    "related_season__item_id",
                ):
                    candidate = row.get(key)
                    if candidate:
                        credit_candidates.add(candidate)

    if MediaTypes.MOVIE.value in active_media_types:
        Movie = apps.get_model("app", "Movie")
        movie_rows = list(
            Movie.objects.filter(user=user)
            .filter(_overlap_day_filter(range_start, range_end))
            .values(
                "id",
                "end_date",
                "start_date",
                "created_at",
                "status",
                "score",
                "item_id",
                "item__source",
                "item__runtime_minutes",
                "item__genres",
                "item__release_datetime",
            )
            .iterator(chunk_size=2000),
        )
        prefetch["movie"] = _bucket_overlap_rows(movie_rows, day_list_set)
        for row in movie_rows:
            if row.get("item__source") == Sources.TMDB.value and row.get("item_id"):
                credit_candidates.add(row["item_id"])

    if MediaTypes.ANIME.value in active_media_types:
        Anime = apps.get_model("app", "Anime")
        anime_rows = list(
            Anime.objects.filter(user=user)
            .filter(_overlap_day_filter(range_start, range_end))
            .values(
                "id",
                "item_id",
                "end_date",
                "start_date",
                "created_at",
                "status",
                "score",
                "progress",
                "item__runtime_minutes",
                "item__genres",
            )
            .iterator(chunk_size=2000),
        )
        prefetch["anime"] = _bucket_overlap_rows(anime_rows, day_list_set)

    if MediaTypes.GAME.value in active_media_types:
        Game = apps.get_model("app", "Game")
        game_rows = list(
            Game.objects.filter(user=user)
            .filter(_overlap_day_filter(range_start, range_end))
            .values(
                "id",
                "item_id",
                "end_date",
                "start_date",
                "created_at",
                "status",
                "score",
                "progress",
                "item__genres",
                "item__release_datetime",
            )
            .iterator(chunk_size=2000),
        )
        prefetch["game"] = _bucket_overlap_rows(game_rows, day_list_set)

    if MediaTypes.BOARDGAME.value in active_media_types:
        BoardGame = apps.get_model("app", "BoardGame")
        boardgame_rows = list(
            BoardGame.objects.filter(user=user)
            .filter(_overlap_day_filter(range_start, range_end))
            .values(
                "id",
                "item_id",
                "end_date",
                "start_date",
                "created_at",
                "status",
                "score",
                "progress",
            )
            .iterator(chunk_size=2000),
        )
        prefetch["boardgame"] = _bucket_overlap_rows(boardgame_rows, day_list_set)

    if MediaTypes.MUSIC.value in active_media_types:
        HistoricalMusic = apps.get_model("app", "HistoricalMusic")
        music_history_rows = list(
            HistoricalMusic.objects.filter(
                Q(history_user=user) | Q(history_user__isnull=True),
                end_date__gte=range_start,
                end_date__lt=range_end,
            )
            .values("id", "end_date", "history_date")
            .iterator(chunk_size=2000),
        )
        prefetch["music_history"] = _bucket_single_field_rows(
            music_history_rows, "end_date", day_list_set
        )

        music_ids = {row["id"] for row in music_history_rows if row.get("id")}
        if music_ids:
            Music = apps.get_model("app", "Music")
            prefetch["music_map"] = {
                entry.id: entry
                for entry in Music.objects.filter(id__in=music_ids).select_related(
                    "item",
                    "artist",
                    "album",
                    "track",
                )
            }
        else:
            prefetch["music_map"] = {}

        track_duration_cache = {}
        album_ids = {
            music.album_id
            for music in prefetch["music_map"].values()
            if music and music.album_id
        }
        if album_ids:
            Track = apps.get_model("app", "Track")
            track_rows = Track.objects.filter(
                album_id__in=album_ids,
                duration_ms__isnull=False,
            ).values("album_id", "title", "duration_ms", "musicbrainz_recording_id")
            for track_data in track_rows:
                title_key = (track_data["album_id"], track_data["title"])
                track_duration_cache[title_key] = track_data["duration_ms"]
                recording_id = track_data.get("musicbrainz_recording_id")
                if recording_id:
                    track_duration_cache[("recording", recording_id)] = track_data[
                        "duration_ms"
                    ]
        prefetch["track_duration_cache"] = track_duration_cache

    if MediaTypes.PODCAST.value in active_media_types:
        PodcastPlay = apps.get_model("app", "PodcastPlay")
        podcast_history_rows = list(
            PodcastPlay.objects.filter(
                podcast__user=user,
                end_date__gte=range_start,
                end_date__lt=range_end,
            )
            .values("podcast_id", "end_date", "progress")
            .iterator(chunk_size=2000),
        )
        prefetch["podcast_history"] = _bucket_single_field_rows(
            podcast_history_rows, "end_date", day_list_set
        )

        podcast_ids = {row["podcast_id"] for row in podcast_history_rows}
        if podcast_ids:
            Podcast = apps.get_model("app", "Podcast")
            prefetch["podcast_map"] = {
                podcast.id: podcast
                for podcast in Podcast.objects.filter(
                    id__in=podcast_ids, user=user
                ).select_related(
                    "item",
                    "show",
                    "episode",
                    "episode__show",
                )
            }
        else:
            prefetch["podcast_map"] = {}

    if MediaTypes.VIDEO.value in active_media_types:
        VideoPlay = apps.get_model("app", "VideoPlay")
        prefetch["video_plays"] = _bucket_single_field_rows(
            list(
                VideoPlay.objects.filter(
                    video__user=user,
                    end_date__gte=range_start,
                    end_date__lt=range_end,
                )
                .values(
                    "id",
                    "end_date",
                    "progress",
                    "video__id",
                    "video__item_id",
                    "video__status",
                    "video__score",
                    "video__length_seconds",
                )
                .iterator(chunk_size=2000),
            ),
            "end_date",
            day_list_set,
        )

    reading_prefetch = {}
    for media_type in (
        MediaTypes.MANGA.value,
        MediaTypes.BOOK.value,
        MediaTypes.COMIC.value,
    ):
        if media_type not in active_media_types:
            continue
        model = apps.get_model("app", media_type)
        rows = list(
            model.objects.filter(user=user)
            .filter(_overlap_day_filter(range_start, range_end))
            .values(
                "id",
                "item_id",
                "end_date",
                "start_date",
                "created_at",
                "status",
                "score",
                "progress",
                "item__genres",
                "item__format",
            )
            .iterator(chunk_size=2000),
        )
        reading_prefetch[media_type] = _bucket_overlap_rows(rows, day_list_set)
    prefetch["reading"] = reading_prefetch

    if MediaTypes.BOOK.value in active_media_types:
        # Same window and local-day bucketing as the per-day SUM below.
        BookListeningSession = apps.get_model("app", "BookListeningSession")
        listening_minutes = defaultdict(int)
        for row in BookListeningSession.objects.filter(
            book__user=user,
            started_at__gte=range_start,
            started_at__lt=range_end,
        ).values("started_at", "minutes"):
            day = _normalize_day_value(row["started_at"])
            if day in day_list_set:
                listening_minutes[day] += row["minutes"] or 0
        prefetch["listening_minutes"] = listening_minutes

    # Resolve which TMDB candidates still need credits backfill once for the
    # whole range; per-day builds intersect against this instead of issuing
    # the same ~5 resolver queries on every day with a TMDB play.
    prefetch["missing_credit_ids"] = set(
        _resolve_missing_credit_item_ids(credit_candidates),
    )

    return prefetch


def _add_boardgame_rows(
    rows,
    day,
    day_start,
    day_end,
    *,
    minutes_by_type,
    daily_minutes_by_type,
    update_item_meta,
    update_top_played,
):
    """Add one day's board game rows to the day builder's accumulators.

    A row's progress is minutes played. A play counts on the day it ended; the
    minutes are spread evenly over the local days between start and end.
    """
    key = MediaTypes.BOARDGAME.value
    for row in rows:
        activity_dt = (
            row.get("end_date") or row.get("start_date") or row.get("created_at")
        )
        update_item_meta(
            key,
            row.get("item_id"),
            row.get("id"),
            row.get("status"),
            row.get("score"),
            activity_dt,
        )

        total_minutes = row.get("progress") or 0
        if total_minutes <= 0:
            continue

        play_dt = row.get("end_date") or row.get("start_date")
        if play_dt and day_start <= play_dt < day_end:
            minutes_by_type[key] += total_minutes
            update_top_played(
                key,
                row.get("item_id"),
                row.get("id"),
                minutes=total_minutes,
                plays=1,
                episode_count=0,
                activity_dt=play_dt,
            )

        start_dt = row.get("start_date")
        end_dt = row.get("end_date")
        start_local = _localize_datetime(start_dt).date() if start_dt else None
        end_local = _localize_datetime(end_dt).date() if end_dt else None
        if start_local and end_local and start_local <= day <= end_local:
            total_days = (end_local - start_local).days + 1
            per_day = total_minutes / total_days if total_days else total_minutes
            daily_minutes_by_type[key] += per_day
        else:
            activity_local = _localize_datetime(activity_dt)
            if activity_local and activity_local.date() == day:
                daily_minutes_by_type[key] += total_minutes


def _add_music_plays(
    user,
    day,
    day_start,
    day_end,
    prefetch,
    *,
    plays_by_type,
    minutes_by_type,
    daily_minutes_by_type,
    music_rollups,
    add_hour,
    update_item_meta,
    update_top_played,
):
    """Add one day's music plays to the day builder's accumulators.

    A play is one (music id, end date) pair, deduplicated across history rows.
    Returns how many plays were added and how many lacked a runtime or genres.
    """
    missing_runtime = 0
    missing_genres = 0
    plays = 0
    if prefetch is not None:
        music_history = prefetch.get("music_history", {}).get(day, [])
        music_map = prefetch.get("music_map", {})
        track_duration_cache = prefetch.get("track_duration_cache", {})
    else:
        HistoricalMusic = apps.get_model("app", "HistoricalMusic")
        music_history = (
            HistoricalMusic.objects.filter(
                Q(history_user=user) | Q(history_user__isnull=True),
                end_date__gte=day_start,
                end_date__lt=day_end,
            )
            .values("id", "end_date", "history_date")
            .iterator(chunk_size=1000)
        )

    plays_by_key = {}
    for record in music_history:
        music_id = record.get("id")
        play_end = record.get("end_date")
        hist_date = record.get("history_date")
        if not music_id or not play_end or not hist_date:
            continue
        key = (music_id, play_end)
        existing = plays_by_key.get(key)
        if not existing:
            plays_by_key[key] = hist_date
            continue
        existing_diff = abs((existing - play_end).total_seconds())
        current_diff = abs((hist_date - play_end).total_seconds())
        if current_diff < existing_diff and current_diff < SECONDS_PER_DAY:
            plays_by_key[key] = hist_date

    if prefetch is None:
        music_ids = {key[0] for key in plays_by_key}
        if music_ids:
            Music = apps.get_model("app", "Music")
            music_map = {
                entry.id: entry
                for entry in Music.objects.filter(id__in=music_ids).select_related(
                    "item", "artist", "album", "track"
                )
            }
        else:
            music_map = {}

        track_duration_cache = {}
        if music_map:
            album_ids = {
                music.album_id
                for music in music_map.values()
                if music and music.album_id
            }
            if album_ids:
                Track = apps.get_model("app", "Track")
                track_rows = Track.objects.filter(
                    album_id__in=album_ids,
                    duration_ms__isnull=False,
                ).values(
                    "album_id", "title", "duration_ms", "musicbrainz_recording_id"
                )
                for track_data in track_rows:
                    title_key = (track_data["album_id"], track_data["title"])
                    track_duration_cache[title_key] = track_data["duration_ms"]
                    recording_id = track_data.get("musicbrainz_recording_id")
                    if recording_id:
                        recording_key = ("recording", recording_id)
                        track_duration_cache[recording_key] = track_data[
                            "duration_ms"
                        ]

    for music_id, play_end in plays_by_key:
        music = music_map.get(music_id)
        if not music:
            continue
        runtime_minutes = _get_music_runtime_minutes(
            music,
            track_duration_cache=track_duration_cache,
        )
        if runtime_minutes <= 0:
            missing_runtime += 1
            runtime_minutes = 0
        localized = _localize_datetime(play_end)
        plays_by_type[MediaTypes.MUSIC.value] += 1
        plays += 1
        add_hour(MediaTypes.MUSIC.value, localized, runtime_minutes)
        if runtime_minutes:
            minutes_by_type[MediaTypes.MUSIC.value] += runtime_minutes
            daily_minutes_by_type[MediaTypes.MUSIC.value] += runtime_minutes

        update_item_meta(
            MediaTypes.MUSIC.value,
            music.item_id if getattr(music, "item_id", None) else music.id,
            music.id,
            getattr(music, "status", None),
            getattr(music, "score", None),
            play_end,
        )
        if runtime_minutes:
            update_top_played(
                MediaTypes.MUSIC.value,
                music.item_id if getattr(music, "item_id", None) else music.id,
                music.id,
                minutes=runtime_minutes,
                plays=1,
                episode_count=0,
                activity_dt=play_end,
            )

        track_key = music.id
        track_stats = music_rollups["tracks"][track_key]
        track_stats["minutes"] += runtime_minutes
        track_stats["plays"] += 1
        track_stats["title"] = music.item.title if music.item else "Unknown"
        track_stats["id"] = music.id

        album = getattr(music, "album", None)
        artist = getattr(music, "artist", None) or getattr(album, "artist", None)
        if artist:
            track_stats["artist"] = artist.name
            artist_stats = music_rollups["artists"][artist.id]
            artist_stats["minutes"] += runtime_minutes
            artist_stats["plays"] += 1
            artist_stats["name"] = artist.name
            artist_stats["image"] = artist.image or ""
            artist_stats["id"] = artist.id

        if album:
            track_stats["album"] = album.title
            track_stats["album_image"] = (
                album.image or track_stats.get("album_image") or ""
            )
            track_stats["album_id"] = album.id
            track_stats["album_artist_id"] = artist.id if artist else None
            track_stats["album_artist_name"] = artist.name if artist else ""
            album_stats = music_rollups["albums"][album.id]
            album_stats["minutes"] += runtime_minutes
            album_stats["plays"] += 1
            album_stats["title"] = album.title
            album_stats["artist"] = artist.name if artist else "Unknown"
            album_stats["artist_id"] = artist.id if artist else None
            album_stats["artist_name"] = artist.name if artist else ""
            album_stats["image"] = album.image or ""
            album_stats["id"] = album.id

        genres = []
        if album and album.genres:
            genres = _coerce_genre_list(album.genres)
        elif artist and artist.genres:
            genres = _coerce_genre_list(artist.genres)
        elif getattr(music, "track", None) and music.track.genres:
            genres = _coerce_genre_list(music.track.genres)

        if runtime_minutes > 0 and not genres:
            missing_genres += 1

        for genre in genres:
            key = str(genre).title()
            genre_stats = music_rollups["genres"][key]
            genre_stats["minutes"] += runtime_minutes
            genre_stats["plays"] += 1
            genre_stats["name"] = key

        release_date = getattr(album, "release_date", None) if album else None
        if release_date and release_date.year:
            decade_label = f"{(release_date.year // 10) * 10}s"
            decade_stats = music_rollups["decades"][decade_label]
            decade_stats["minutes"] += runtime_minutes
            decade_stats["plays"] += 1
            decade_stats["label"] = decade_label

        country_code = getattr(artist, "country", None) if artist else None
        if country_code:
            code_upper = str(country_code).upper()
            country_stats = music_rollups["countries"][code_upper]
            country_stats["minutes"] += runtime_minutes
            country_stats["plays"] += 1
            country_stats["code"] = code_upper
            country_stats["name"] = _country_name_from_code(code_upper)
    return plays, missing_runtime, missing_genres


def _add_podcast_plays(
    user,
    day,
    day_start,
    day_end,
    prefetch,
    *,
    plays_by_type,
    minutes_by_type,
    daily_minutes_by_type,
    podcast_rollups,
    add_hour,
    update_item_meta,
):
    """Add one day's podcast plays to the day builder's accumulators.

    A play is one PodcastPlay row.
    The episode runtime counts; the play's progress stands in when it is
    unknown, and a play with neither is skipped. Returns how many plays were
    added and how many lacked a runtime.
    """
    missing_runtime = 0
    plays = 0
    if prefetch is not None:
        podcast_history = prefetch.get("podcast_history", {}).get(day, [])
    else:
        PodcastPlay = apps.get_model("app", "PodcastPlay")
        podcast_history = (
            PodcastPlay.objects.filter(
                podcast__user=user,
                end_date__gte=day_start,
                end_date__lt=day_end,
            )
            .values("podcast_id", "end_date", "progress")
            .iterator(chunk_size=1000)
        )
    podcast_plays = defaultdict(list)
    for record in podcast_history:
        if record.get("end_date"):
            podcast_plays[record["podcast_id"]].append(
                (record["end_date"], record.get("progress")),
            )

    if prefetch is not None:
        podcast_map = prefetch.get("podcast_map", {})
    else:
        podcast_ids = set(podcast_plays.keys())
        if podcast_ids:
            Podcast = apps.get_model("app", "Podcast")
            podcast_map = {
                podcast.id: podcast
                for podcast in Podcast.objects.filter(
                    id__in=podcast_ids, user=user
                ).select_related("item", "show", "episode", "episode__show")
            }
        else:
            podcast_map = {}

    for podcast_id, plays_for_podcast in podcast_plays.items():
        podcast = podcast_map.get(podcast_id)
        if not podcast:
            continue
        for play_end, history_progress in plays_for_podcast:
            runtime_minutes = _get_podcast_runtime_minutes(podcast)
            if runtime_minutes <= 0 and history_progress and history_progress > 0:
                runtime_minutes = history_progress
            if runtime_minutes <= 0:
                missing_runtime += 1
                continue
            localized = _localize_datetime(play_end)
            plays_by_type[MediaTypes.PODCAST.value] += 1
            plays += 1
            add_hour(MediaTypes.PODCAST.value, localized, runtime_minutes)
            minutes_by_type[MediaTypes.PODCAST.value] += runtime_minutes
            daily_minutes_by_type[MediaTypes.PODCAST.value] += runtime_minutes

            update_item_meta(
                MediaTypes.PODCAST.value,
                podcast.item_id
                if getattr(podcast, "item_id", None)
                else podcast.id,
                podcast.id,
                getattr(podcast, "status", None),
                getattr(podcast, "score", None),
                play_end,
            )

            show = getattr(podcast, "show", None)
            if show:
                show_stats = podcast_rollups["shows"][show.id]
                show_stats["minutes"] += runtime_minutes
                show_stats["plays"] += 1
                show_stats["title"] = show.title
                show_stats["show"] = show.title
                show_stats["show_id"] = show.id
                show_stats["podcast_uuid"] = show.podcast_uuid or show_stats.get(
                    "podcast_uuid"
                )
                show_stats["slug"] = show.slug or ""
                show_stats["image"] = show.image or ""
            else:
                show_stats = podcast_rollups["shows"][podcast.id]
                show_stats["minutes"] += runtime_minutes
                show_stats["plays"] += 1
                show_stats["title"] = (
                    podcast.item.title if podcast.item else "Unknown Show"
                )
                show_stats["show"] = show_stats["title"]
                show_stats["image"] = podcast.item.image if podcast.item else ""

            episode = getattr(podcast, "episode", None)
            episode_key = episode.id if episode else podcast.id
            episode_stats = podcast_rollups["episodes"][episode_key]
            if episode:
                episode_stats["title"] = episode.title
                episode_stats["episode_id"] = episode.id
                episode_stats["duration_seconds"] = (
                    episode.duration or episode_stats.get("duration_seconds") or 0
                )
                episode_stats["show"] = (
                    episode.show.title
                    if getattr(episode, "show", None)
                    else episode_stats.get("show")
                )
                episode_stats["show_id"] = (
                    episode.show.id
                    if getattr(episode, "show", None)
                    else episode_stats.get("show_id")
                )
            else:
                episode_stats["title"] = (
                    podcast.item.title if podcast.item else "Unknown Episode"
                )
                episode_stats["episode_id"] = episode_key
                if podcast.item and podcast.item.runtime_minutes:
                    episode_stats["duration_seconds"] = (
                        podcast.item.runtime_minutes * 60
                    )
            if show:
                episode_stats["podcast_uuid"] = (
                    show.podcast_uuid or episode_stats.get("podcast_uuid")
                )
                episode_stats["slug"] = show.slug or ""
                episode_stats["image"] = show.image or ""
            elif podcast.item:
                episode_stats["image"] = podcast.item.image or ""
    return plays, missing_runtime


def build_stats_for_day(
    user_id: int,
    day_value,
    user=None,
    prefetch=None,
    history_version=None,
    defer_cache_write=False,
    backfill_collector=None,
):
    """Build a per-day statistics payload for a single user.

    Pass an already-fetched `user` when building many days back-to-back
    (e.g. a full-history rebuild) to avoid re-querying the same row per day.
    Pass `prefetch` (from `_build_prefetch_for_range`) to source this day's
    rows from an already-bucketed, range-wide fetch instead of issuing this
    day's own set of per-model queries.

    Bulk callers can also pass `history_version` (one cache GET per rebuild
    instead of one per day), `defer_cache_write=True` (caller persists the
    returned payloads via `cache.set_many`), and `backfill_collector` (a dict
    of sets — `runtime_item_ids`, `genre_item_ids`, `episode_runtime_keys`,
    `credit_item_ids` — accumulated across days and enqueued once instead of
    per day).

    Cache contract consumed by `statistics_aggregator`:
    - `day` and all activity/score timestamps are ISO strings in the current
      timezone; item mapping keys and hour keys are strings at the cache boundary.
    - `items` keeps the latest status/activity and latest scored activity per
      catalog item and media bucket; `top_played` adds plays, minutes and
      episode counts. These are distinct from tracker-row counts.
    - `totals`, `daily_minutes_by_type`, `hour_counts` and `hour_minutes` are
      additive across days. Exact-midnight date placeholders omit hour buckets.
    - `genres`/`decades` and `music`/`podcast`/`game` contain family-specific
      rollups. Reading genres use units/title counts, not elapsed minutes;
      game genre/decade IDs remain lists so aggregation can deduplicate them.
    - `activity.count` drives the activity calendar, while `backfill` carries
      credit-repair hints. `history_version` fences stale cached projections.

    Empty families remain present. This is a cached projection, not the public
    statistics response; keep its shape compatible with the range aggregator.
    """
    if user is None:
        user_model = get_user_model()
        try:
            user = user_model.objects.get(id=user_id)
        except user_model.DoesNotExist:
            return None

    day = _normalize_day_value(day_value)
    if not day:
        return None

    day_start, day_end = _day_bounds(day)
    if not day_start or not day_end:
        return None

    active_media_types = set(getattr(user, "get_active_media_types", list)())
    if not active_media_types:
        active_media_types = set(MediaTypes.values)
    split_tv_anime = not getattr(user, "anime_enabled", True) and getattr(
        user, "stats_split_tv_anime", False
    )

    items_by_type: dict[str, dict[int, dict]] = defaultdict(dict)
    top_played_by_type: dict[str, dict[int, dict]] = defaultdict(dict)
    minutes_by_type: dict[str, float] = defaultdict(float)
    plays_by_type: dict[str, int] = defaultdict(int)
    hour_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    hour_minutes: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    daily_minutes_by_type: dict[str, float] = defaultdict(float)
    movie_genres = defaultdict(lambda: {"minutes": 0, "plays": 0})
    tv_genres = defaultdict(lambda: {"minutes": 0, "plays": 0})
    anime_genres = defaultdict(lambda: {"minutes": 0, "plays": 0})
    game_genres = defaultdict(
        lambda: {"minutes": 0, "plays": 0, "name": "", "game_ids": set()}
    )
    movie_decades = defaultdict(lambda: {"minutes": 0, "plays": 0, "label": ""})
    tv_decades = defaultdict(lambda: {"minutes": 0, "plays": 0, "label": ""})
    anime_decades = defaultdict(lambda: {"minutes": 0, "plays": 0, "label": ""})
    game_decades = defaultdict(lambda: {"minutes": 0, "label": "", "game_ids": set()})
    reading_genres = {
        MediaTypes.BOOK.value: defaultdict(
            lambda: {"units": 0, "titles": set(), "name": ""}
        ),
        MediaTypes.COMIC.value: defaultdict(
            lambda: {"units": 0, "titles": set(), "name": ""}
        ),
        MediaTypes.MANGA.value: defaultdict(
            lambda: {"units": 0, "titles": set(), "name": ""}
        ),
    }
    music_rollups = {
        "artists": defaultdict(
            lambda: {"minutes": 0, "plays": 0, "name": "", "image": "", "id": None}
        ),
        "albums": defaultdict(
            lambda: {
                "minutes": 0,
                "plays": 0,
                "title": "",
                "artist": "",
                "artist_id": None,
                "artist_name": "",
                "image": "",
                "id": None,
            },
        ),
        "tracks": defaultdict(
            lambda: {
                "minutes": 0,
                "plays": 0,
                "title": "",
                "artist": "",
                "album": "",
                "album_image": "",
                "album_id": None,
                "album_artist_id": None,
                "album_artist_name": "",
                "id": None,
            },
        ),
        "genres": defaultdict(lambda: {"minutes": 0, "plays": 0, "name": ""}),
        "decades": defaultdict(lambda: {"minutes": 0, "plays": 0, "label": ""}),
        "countries": defaultdict(
            lambda: {"minutes": 0, "plays": 0, "code": "", "name": ""}
        ),
    }
    podcast_rollups = {
        "shows": defaultdict(
            lambda: {
                "minutes": 0,
                "plays": 0,
                "title": "",
                "show": "",
                "show_id": None,
                "podcast_uuid": None,
                "slug": "",
                "image": "",
            }
        ),
        "episodes": defaultdict(
            lambda: {
                "title": "",
                "show": "",
                "show_id": None,
                "episode_id": None,
                "podcast_uuid": None,
                "slug": "",
                "image": "",
                "duration_seconds": 0,
            }
        ),
    }
    game_rollups: dict[int, dict] = {}
    missing_runtime = 0
    missing_genres = 0
    play_count = 0
    missing_runtime_item_ids = set()
    missing_genre_item_ids = set()
    missing_episode_runtime_keys = set()
    missing_credit_candidate_item_ids = set()

    def _update_item_meta(
        media_type: str,
        item_id: int,
        media_id: int | None,
        status,
        score,
        activity_dt,
        stats_media_type: str | None = None,
    ):
        if not item_id:
            return
        activity_dt = _localize_datetime(activity_dt) if activity_dt else None
        score_dt = activity_dt if score is not None else None
        stats_media_type = stats_media_type or media_type
        existing = items_by_type[media_type].get(item_id)
        if not existing:
            items_by_type[media_type][item_id] = {
                "item_id": item_id,
                "media_id": media_id,
                "media_type": media_type,
                "stats_media_type": stats_media_type,
                "status": status,
                "score": float(score) if score is not None else None,
                "score_dt": score_dt,
                "activity_dt": activity_dt,
            }
            return

        existing["stats_media_type"] = stats_media_type
        existing_activity = existing.get("activity_dt")
        if activity_dt and (not existing_activity or activity_dt > existing_activity):
            existing["media_id"] = media_id or existing.get("media_id")
            existing["status"] = status
            existing["activity_dt"] = activity_dt

        if score is not None:
            existing_score_dt = existing.get("score_dt")
            if existing_score_dt is None or (score_dt and score_dt > existing_score_dt):
                existing["score"] = float(score)
                existing["score_dt"] = score_dt

        items_by_type[media_type][item_id] = existing

    def _update_top_played(
        media_type: str,
        item_id: int,
        media_id: int | None,
        minutes=0,
        plays=0,
        episode_count=0,
        activity_dt=None,
    ):
        if not item_id:
            return
        entry = top_played_by_type[media_type].get(item_id)
        if not entry:
            entry = {
                "item_id": item_id,
                "media_id": media_id,
                "minutes": 0.0,
                "plays": 0,
                "episode_count": 0,
                "activity_dt": None,
            }
            top_played_by_type[media_type][item_id] = entry
        entry["minutes"] += minutes or 0
        entry["plays"] += plays or 0
        entry["episode_count"] += episode_count or 0
        activity_dt = _localize_datetime(activity_dt) if activity_dt else None
        if activity_dt and (
            entry["activity_dt"] is None or activity_dt > entry["activity_dt"]
        ):
            entry["activity_dt"] = activity_dt
            if media_id:
                entry["media_id"] = media_id

    def _add_hour(media_type: str, activity_dt, minutes: float = 0):
        if not activity_dt:
            return
        localized = _localize_datetime(activity_dt)
        if not localized:
            return
        # Exact midnight is the date-only placeholder used when no time is known.
        if localized.time() == datetime.min.time():
            return
        hour_counts[media_type][localized.hour] += 1
        hour_minutes[media_type][localized.hour] += minutes or 0

    def _add_genres(genre_map, genres, minutes):
        if not genres or not minutes:
            return False
        added = False
        for genre in _coerce_genre_list(genres):
            key = str(genre).title()
            genre_map[key]["minutes"] += minutes
            genre_map[key]["plays"] += 1
            genre_map[key]["name"] = key
            added = True
        return added

    def _add_decade(decade_map, release_datetime, minutes):
        if not release_datetime or not minutes:
            return
        decade_label = f"{(release_datetime.year // 10) * 10}s"
        decade_map[decade_label]["minutes"] += minutes
        decade_map[decade_label]["plays"] += 1
        decade_map[decade_label]["label"] = decade_label

    def _add_game_decade(decade_map, release_datetime, minutes, game_id):
        if not release_datetime or not minutes:
            return
        decade_label = f"{(release_datetime.year // 10) * 10}s"
        decade_map[decade_label]["minutes"] += minutes
        decade_map[decade_label]["label"] = decade_label
        if game_id:
            decade_map[decade_label]["game_ids"].add(game_id)

    if (
        MediaTypes.TV.value in active_media_types
        or MediaTypes.SEASON.value in active_media_types
    ):
        if prefetch is not None:
            episodes = prefetch.get("episode", {}).get(day, [])
        else:
            Episode = apps.get_model("app", "Episode")
            episodes = (
                Episode.all_objects.filter(
                    Episode.watch_q(),
                    related_season__user=user,
                    end_date__gte=day_start,
                    end_date__lt=day_end,
                )
                .values(
                    "item_id",
                    "end_date",
                    "item__runtime_minutes",
                    "item__media_id",
                    "item__source",
                    "item__season_number",
                    "related_season_id",
                    "related_season__item_id",
                    "related_season__status",
                    "related_season__score",
                    "related_season__created_at",
                    "related_season__related_tv_id",
                    "related_season__related_tv__item_id",
                    "related_season__related_tv__status",
                    "related_season__related_tv__score",
                    "related_season__related_tv__created_at",
                    "related_season__related_tv__item__genres",
                    "related_season__related_tv__item__library_media_type",
                    "related_season__related_tv__item__release_datetime",
                )
                .iterator(chunk_size=1000)
            )
        for row in episodes:
            play_dt = row.get("end_date")
            ep_lib_type = row.get(
                "related_season__related_tv__item__library_media_type"
            )
            tv_item_genres = row.get("related_season__related_tv__item__genres")
            ep_type = (
                MediaTypes.ANIME.value
                if ep_lib_type == MediaTypes.ANIME.value
                or (
                    split_tv_anime
                    and genre_list_has_name(tv_item_genres, ANIME_SUPPLEMENT_GENRE)
                )
                else MediaTypes.TV.value
            )
            plays_by_type[ep_type] += 1
            play_count += 1
            runtime_minutes = _safe_runtime_minutes(row.get("item__runtime_minutes"))
            _add_hour(ep_type, play_dt, runtime_minutes)

            if runtime_minutes <= 0:
                missing_runtime += 1
                media_id = row.get("item__media_id")
                source = row.get("item__source")
                season_number = row.get("item__season_number")
                if media_id and source and season_number is not None:
                    missing_episode_runtime_keys.add((media_id, source, season_number))
            else:
                minutes_by_type[ep_type] += runtime_minutes
                daily_minutes_by_type[ep_type] += runtime_minutes

            tv_item_id = row.get("related_season__related_tv__item_id")
            tv_media_id = row.get("related_season__related_tv_id")
            season_item_id = row.get("related_season__item_id")
            tv_activity = play_dt or row.get("related_season__related_tv__created_at")
            genre_map = anime_genres if ep_type == MediaTypes.ANIME.value else tv_genres
            if tv_item_id:
                _update_item_meta(
                    ep_type,
                    tv_item_id,
                    tv_media_id,
                    row.get("related_season__related_tv__status"),
                    row.get("related_season__related_tv__score"),
                    tv_activity,
                )
                _update_top_played(
                    ep_type,
                    tv_item_id,
                    tv_media_id,
                    minutes=runtime_minutes,
                    plays=1,
                    episode_count=1,
                    activity_dt=play_dt or tv_activity,
                )
                if runtime_minutes > 0 and not _add_genres(
                    genre_map,
                    tv_item_genres,
                    runtime_minutes,
                ):
                    missing_genres += 1
                    if tv_item_id:
                        missing_genre_item_ids.add(tv_item_id)
                if runtime_minutes > 0:
                    decade_map = (
                        anime_decades
                        if ep_type == MediaTypes.ANIME.value
                        else tv_decades
                    )
                    _add_decade(
                        decade_map,
                        row.get("related_season__related_tv__item__release_datetime"),
                        runtime_minutes,
                    )
            if row.get("item__source") == Sources.TMDB.value:
                episode_item_id = row.get("item_id")
                if episode_item_id:
                    missing_credit_candidate_item_ids.add(episode_item_id)
                if tv_item_id:
                    missing_credit_candidate_item_ids.add(tv_item_id)
                if season_item_id:
                    missing_credit_candidate_item_ids.add(season_item_id)

            if season_item_id:
                season_activity = play_dt or row.get("related_season__created_at")
                _update_item_meta(
                    MediaTypes.SEASON.value,
                    season_item_id,
                    row.get("related_season_id"),
                    row.get("related_season__status"),
                    row.get("related_season__score"),
                    season_activity,
                    stats_media_type=ep_type,
                )

    if MediaTypes.MOVIE.value in active_media_types:
        Movie = apps.get_model("app", "Movie")
        movie_rows = _fetch_overlap_rows(
            Movie,
            user,
            day,
            day_start,
            day_end,
            (
                "id",
                "end_date",
                "start_date",
                "created_at",
                "status",
                "score",
                "item_id",
                "item__source",
                "item__runtime_minutes",
                "item__genres",
                "item__release_datetime",
            ),
            1000,
            "movie",
            prefetch,
        )
        for row in movie_rows:
            activity_dt = (
                row.get("end_date") or row.get("start_date") or row.get("created_at")
            )
            _update_item_meta(
                MediaTypes.MOVIE.value,
                row.get("item_id"),
                row.get("id"),
                row.get("status"),
                row.get("score"),
                activity_dt,
            )

            activity_dt = (
                row.get("end_date") or row.get("start_date") or row.get("created_at")
            )
            if activity_dt and day_start <= activity_dt < day_end:
                plays_by_type[MediaTypes.MOVIE.value] += 1
                play_count += 1
                runtime_minutes = _safe_runtime_minutes(
                    row.get("item__runtime_minutes")
                )
                _add_hour(MediaTypes.MOVIE.value, activity_dt, runtime_minutes)
                if runtime_minutes > 0:
                    daily_minutes_by_type[MediaTypes.MOVIE.value] += runtime_minutes

                if runtime_minutes <= 0:
                    missing_runtime += 1
                    item_id = row.get("item_id")
                    if item_id:
                        missing_runtime_item_ids.add(item_id)
                if row.get("item__source") == Sources.TMDB.value:
                    item_id = row.get("item_id")
                    if item_id:
                        missing_credit_candidate_item_ids.add(item_id)

            play_end = row.get("end_date")
            if play_end and day_start <= play_end < day_end:
                runtime_minutes = _safe_runtime_minutes(
                    row.get("item__runtime_minutes")
                )
                if runtime_minutes > 0:
                    minutes_by_type[MediaTypes.MOVIE.value] += runtime_minutes
                    if not _add_genres(
                        movie_genres, row.get("item__genres"), runtime_minutes
                    ):
                        missing_genres += 1
                        item_id = row.get("item_id")
                        if item_id:
                            missing_genre_item_ids.add(item_id)
                    _add_decade(
                        movie_decades,
                        row.get("item__release_datetime"),
                        runtime_minutes,
                    )
                    _update_top_played(
                        MediaTypes.MOVIE.value,
                        row.get("item_id"),
                        row.get("id"),
                        minutes=runtime_minutes,
                        plays=1,
                        episode_count=0,
                        activity_dt=play_end,
                    )

    if MediaTypes.ANIME.value in active_media_types:
        Anime = apps.get_model("app", "Anime")
        anime_rows = _fetch_overlap_rows(
            Anime,
            user,
            day,
            day_start,
            day_end,
            (
                "id",
                "item_id",
                "end_date",
                "start_date",
                "created_at",
                "status",
                "score",
                "progress",
                "item__runtime_minutes",
                "item__genres",
            ),
            500,
            "anime",
            prefetch,
        )
        for row in anime_rows:
            activity_dt = (
                row.get("end_date") or row.get("start_date") or row.get("created_at")
            )
            _update_item_meta(
                MediaTypes.ANIME.value,
                row.get("item_id"),
                row.get("id"),
                row.get("status"),
                row.get("score"),
                activity_dt,
            )

            runtime_minutes = _safe_runtime_minutes(row.get("item__runtime_minutes"))
            progress = row.get("progress") or 0
            total_minutes = (
                runtime_minutes * progress if runtime_minutes and progress else 0
            )
            if runtime_minutes <= 0 and progress:
                missing_runtime += 1
                item_id = row.get("item_id")
                if item_id:
                    missing_runtime_item_ids.add(item_id)

            end_date = row.get("end_date")
            if end_date and day_start <= end_date < day_end:
                minutes_by_type[MediaTypes.ANIME.value] += total_minutes
                _update_top_played(
                    MediaTypes.ANIME.value,
                    row.get("item_id"),
                    row.get("id"),
                    minutes=total_minutes,
                    plays=1,
                    episode_count=progress,
                    activity_dt=end_date,
                )

            if total_minutes > 0:
                start_dt = row.get("start_date")
                end_dt = row.get("end_date")
                if start_dt and end_dt:
                    start_local = _localize_datetime(start_dt).date()
                    end_local = _localize_datetime(end_dt).date()
                    if start_local <= day <= end_local:
                        days = (end_local - start_local).days + 1
                        per_day = total_minutes / days if days else total_minutes
                        daily_minutes_by_type[MediaTypes.ANIME.value] += per_day
                else:
                    activity_local = _localize_datetime(activity_dt)
                    if activity_local and activity_local.date() == day:
                        daily_minutes_by_type[MediaTypes.ANIME.value] += total_minutes

            if total_minutes > 0 and not _coerce_genre_list(row.get("item__genres")):
                missing_genres += 1
                item_id = row.get("item_id")
                if item_id:
                    missing_genre_item_ids.add(item_id)

    if MediaTypes.GAME.value in active_media_types:
        Game = apps.get_model("app", "Game")
        game_rollup_days_counted = set()
        game_rows = _fetch_overlap_rows(
            Game,
            user,
            day,
            day_start,
            day_end,
            (
                "id",
                "item_id",
                "end_date",
                "start_date",
                "created_at",
                "status",
                "score",
                "progress",
                "item__genres",
                "item__release_datetime",
            ),
            500,
            "game",
            prefetch,
        )
        for row in game_rows:
            activity_dt = (
                row.get("end_date") or row.get("start_date") or row.get("created_at")
            )
            _update_item_meta(
                MediaTypes.GAME.value,
                row.get("item_id"),
                row.get("id"),
                row.get("status"),
                row.get("score"),
                activity_dt,
            )

            total_minutes = row.get("progress") or 0
            if total_minutes <= 0:
                continue

            end_dt = row.get("end_date")
            if end_dt and day_start <= end_dt < day_end:
                minutes_by_type[MediaTypes.GAME.value] += total_minutes

            start_dt = row.get("start_date")
            start_local = _localize_datetime(start_dt).date() if start_dt else None
            end_local = _localize_datetime(end_dt).date() if end_dt else None
            entry_days = _get_entry_play_dates(
                SimpleNamespace(
                    start_date=start_dt,
                    end_date=end_dt,
                    created_at=row.get("created_at"),
                )
            )
            if (row.get("item_id") and day in entry_days) and row.get(
                "item_id"
            ) not in game_rollup_days_counted:
                rollup = game_rollups.setdefault(
                    row.get("item_id"),
                    {
                        "minutes_total": 0,
                        "days": 0,
                        "activity_dt": None,
                        "media_id": row.get("id"),
                    },
                )
                rollup["days"] += 1
                game_rollup_days_counted.add(row.get("item_id"))
            if (start_local and end_local) and start_local <= day <= end_local:
                total_days = (end_local - start_local).days + 1
                per_day = total_minutes / total_days if total_days else total_minutes
                daily_minutes_by_type[MediaTypes.GAME.value] += per_day
                _update_top_played(
                    MediaTypes.GAME.value,
                    row.get("item_id"),
                    row.get("id"),
                    minutes=per_day,
                    plays=1
                    if activity_dt and _localize_datetime(activity_dt).date() == day
                    else 0,
                    episode_count=0,
                    activity_dt=activity_dt,
                )
                if activity_dt and _localize_datetime(activity_dt).date() == day:
                    rollup = game_rollups.setdefault(
                        row.get("item_id"),
                        {
                            "minutes_total": 0,
                            "days": 0,
                            "activity_dt": None,
                            "media_id": row.get("id"),
                        },
                    )
                    rollup["minutes_total"] += total_minutes
                    rollup["activity_dt"] = activity_dt
                    rollup["media_id"] = row.get("id")
                    if not _add_genres(
                        game_genres, row.get("item__genres"), total_minutes
                    ):
                        missing_genres += 1
                        item_id = row.get("item_id")
                        if item_id:
                            missing_genre_item_ids.add(item_id)
                    game_id = row.get("item_id")
                    if game_id:
                        for genre in _coerce_genre_list(row.get("item__genres")):
                            key = str(genre).title()
                            game_genres[key]["game_ids"].add(game_id)
                    _add_game_decade(
                        game_decades,
                        row.get("item__release_datetime"),
                        total_minutes,
                        game_id,
                    )
                continue

            activity_local = _localize_datetime(activity_dt)
            if activity_local and activity_local.date() == day:
                daily_minutes_by_type[MediaTypes.GAME.value] += total_minutes
                _update_top_played(
                    MediaTypes.GAME.value,
                    row.get("item_id"),
                    row.get("id"),
                    minutes=total_minutes,
                    plays=1,
                    episode_count=0,
                    activity_dt=activity_dt,
                )
                rollup = game_rollups.setdefault(
                    row.get("item_id"),
                    {
                        "minutes_total": 0,
                        "days": 0,
                        "activity_dt": None,
                        "media_id": row.get("id"),
                    },
                )
                rollup["minutes_total"] += total_minutes
                rollup["activity_dt"] = activity_dt
                rollup["media_id"] = row.get("id")
                if not _add_genres(game_genres, row.get("item__genres"), total_minutes):
                    missing_genres += 1
                    item_id = row.get("item_id")
                    if item_id:
                        missing_genre_item_ids.add(item_id)
                game_id = row.get("item_id")
                if game_id:
                    for genre in _coerce_genre_list(row.get("item__genres")):
                        key = str(genre).title()
                        game_genres[key]["game_ids"].add(game_id)
                _add_game_decade(
                    game_decades,
                    row.get("item__release_datetime"),
                    total_minutes,
                    game_id,
                )

    if MediaTypes.BOARDGAME.value in active_media_types:
        BoardGame = apps.get_model("app", "BoardGame")
        boardgame_rows = _fetch_overlap_rows(
            BoardGame,
            user,
            day,
            day_start,
            day_end,
            (
                "id",
                "item_id",
                "end_date",
                "start_date",
                "created_at",
                "status",
                "score",
                "progress",
            ),
            500,
            "boardgame",
            prefetch,
        )
        _add_boardgame_rows(
            boardgame_rows,
            day,
            day_start,
            day_end,
            minutes_by_type=minutes_by_type,
            daily_minutes_by_type=daily_minutes_by_type,
            update_item_meta=_update_item_meta,
            update_top_played=_update_top_played,
        )

    if MediaTypes.MUSIC.value in active_media_types:
        music_plays, music_missing_runtime, music_missing_genres = _add_music_plays(
            user,
            day,
            day_start,
            day_end,
            prefetch,
            plays_by_type=plays_by_type,
            minutes_by_type=minutes_by_type,
            daily_minutes_by_type=daily_minutes_by_type,
            music_rollups=music_rollups,
            add_hour=_add_hour,
            update_item_meta=_update_item_meta,
            update_top_played=_update_top_played,
        )
        play_count += music_plays
        missing_runtime += music_missing_runtime
        missing_genres += music_missing_genres

    if MediaTypes.PODCAST.value in active_media_types:
        podcast_plays, podcast_missing_runtime = _add_podcast_plays(
            user,
            day,
            day_start,
            day_end,
            prefetch,
            plays_by_type=plays_by_type,
            minutes_by_type=minutes_by_type,
            daily_minutes_by_type=daily_minutes_by_type,
            podcast_rollups=podcast_rollups,
            add_hour=_add_hour,
            update_item_meta=_update_item_meta,
        )
        play_count += podcast_plays
        missing_runtime += podcast_missing_runtime

    if MediaTypes.VIDEO.value in active_media_types:
        # One play per watched day. Like History, a play counts the video's
        # length; the position reached stands in when the length is unknown.
        if prefetch is not None:
            video_plays = prefetch.get("video_plays", {}).get(day, [])
        else:
            VideoPlay = apps.get_model("app", "VideoPlay")
            video_plays = (
                VideoPlay.objects.filter(
                    video__user=user,
                    end_date__gte=day_start,
                    end_date__lt=day_end,
                )
                .values(
                    "id",
                    "end_date",
                    "progress",
                    "video__id",
                    "video__item_id",
                    "video__status",
                    "video__score",
                    "video__length_seconds",
                )
                .iterator(chunk_size=1000)
            )
        for play in video_plays:
            seconds = play.get("video__length_seconds") or play.get("progress") or 0
            # Fractional minutes, so a 45 second video still counts and long
            # ones do not lose up to a minute per play.
            runtime_minutes = seconds / 60
            if runtime_minutes <= 0:
                missing_runtime += 1
            localized = _localize_datetime(play["end_date"])
            plays_by_type[MediaTypes.VIDEO.value] += 1
            play_count += 1
            _add_hour(MediaTypes.VIDEO.value, localized, runtime_minutes)
            minutes_by_type[MediaTypes.VIDEO.value] += runtime_minutes
            daily_minutes_by_type[MediaTypes.VIDEO.value] += runtime_minutes
            _update_item_meta(
                MediaTypes.VIDEO.value,
                play.get("video__item_id"),
                play.get("video__id"),
                play.get("video__status"),
                play.get("video__score"),
                play["end_date"],
            )

    for media_type in (
        MediaTypes.MANGA.value,
        MediaTypes.BOOK.value,
        MediaTypes.COMIC.value,
    ):
        if media_type not in active_media_types:
            continue
        if prefetch is not None:
            rows = prefetch.get("reading", {}).get(media_type, {}).get(day, [])
        else:
            model = apps.get_model("app", media_type)
            rows = (
                model.objects.filter(user=user)
                .filter(_overlap_day_filter(day_start, day_end))
                .values(
                    "id",
                    "item_id",
                    "end_date",
                    "start_date",
                    "created_at",
                    "status",
                    "score",
                    "progress",
                    "item__genres",
                    "item__format",
                )
                .iterator(chunk_size=500)
            )
        for row in rows:
            activity_dt = (
                row.get("end_date") or row.get("start_date") or row.get("created_at")
            )
            _update_item_meta(
                media_type,
                row.get("item_id"),
                row.get("id"),
                row.get("status"),
                row.get("score"),
                activity_dt,
            )

            play_dt = row.get("end_date") or row.get("start_date")
            if play_dt and day_start <= play_dt < day_end:
                minutes_by_type[media_type] += 60
                _add_hour(media_type, play_dt)

            if media_type == MediaTypes.BOOK.value and (
                row.get("item__format") == "audiobook"
            ):
                # An audiobook's progress is minutes, not pages. Its listening
                # time is counted from dated sessions below, so pages and
                # minutes never share a total.
                continue
            total_minutes = row.get("progress") or 0
            if total_minutes <= 0:
                continue
            genres = _coerce_genre_list(row.get("item__genres"))
            if not genres:
                missing_genres += 1
                item_id = row.get("item_id")
                if item_id:
                    missing_genre_item_ids.add(item_id)
            start_dt = row.get("start_date")
            end_dt = row.get("end_date")
            start_local = _localize_datetime(start_dt).date() if start_dt else None
            end_local = _localize_datetime(end_dt).date() if end_dt else None
            if start_local and end_local and start_local <= day <= end_local:
                total_days = (end_local - start_local).days + 1
                per_day = total_minutes / total_days if total_days else total_minutes
                daily_minutes_by_type[media_type] += per_day
                _update_top_played(
                    media_type,
                    row.get("item_id"),
                    row.get("id"),
                    minutes=per_day,
                    plays=1 if play_dt and day_start <= play_dt < day_end else 0,
                    activity_dt=activity_dt,
                )
                for genre in genres:
                    key = str(genre).title()
                    reading_genres[media_type][key]["units"] += per_day
                    reading_genres[media_type][key]["name"] = key
                    if row.get("item_id"):
                        reading_genres[media_type][key]["titles"].add(
                            row.get("item_id")
                        )
            else:
                activity_local = _localize_datetime(activity_dt)
                if activity_local and activity_local.date() == day:
                    daily_minutes_by_type[media_type] += total_minutes
                    _update_top_played(
                        media_type,
                        row.get("item_id"),
                        row.get("id"),
                        minutes=total_minutes,
                        plays=1 if play_dt and day_start <= play_dt < day_end else 0,
                        activity_dt=activity_dt,
                    )
                    for genre in genres:
                        key = str(genre).title()
                        reading_genres[media_type][key]["units"] += total_minutes
                        reading_genres[media_type][key]["name"] = key
                        if row.get("item_id"):
                            reading_genres[media_type][key]["titles"].add(
                                row.get("item_id")
                            )

    listening_minutes = 0
    if (
        MediaTypes.BOOK.value in active_media_types
        and prefetch is not None
        and "listening_minutes" in prefetch
    ):
        listening_minutes = prefetch["listening_minutes"].get(day, 0)
    elif MediaTypes.BOOK.value in active_media_types:
        BookListeningSession = apps.get_model("app", "BookListeningSession")
        listening_minutes = (
            BookListeningSession.objects.filter(
                book__user=user,
                started_at__gte=day_start,
                started_at__lt=day_end,
            ).aggregate(total=models.Sum("minutes"))["total"]
            or 0
        )

    activity_count = play_count
    if listening_minutes > 0:
        activity_count += 1
    non_play_types = {
        MediaTypes.ANIME.value,
        MediaTypes.GAME.value,
        MediaTypes.BOARDGAME.value,
        MediaTypes.MANGA.value,
        MediaTypes.BOOK.value,
        MediaTypes.COMIC.value,
    }
    for media_type, minutes in daily_minutes_by_type.items():
        if media_type in non_play_types and minutes and minutes > 0:
            activity_count += 1
    if activity_count == 0 and sum(daily_minutes_by_type.values()) > 0:
        activity_count = 1

    if history_version is None:
        history_version = _get_history_version(user_id)
    day_stats = {
        "computed_at": timezone.now().isoformat(),
        "history_version": history_version,
        "day": day.isoformat(),
        "items": {},
        "top_played": {},
        "totals": {
            "minutes_by_type": dict(minutes_by_type),
            "plays_by_type": dict(plays_by_type),
            "listening_minutes": listening_minutes,
        },
        "hour_counts": {},
        "hour_minutes": {},
        "genres": {
            "movie": dict(movie_genres),
            "tv": dict(tv_genres),
            "anime": dict(anime_genres),
            "game": {},
            MediaTypes.BOOK.value: {},
            MediaTypes.COMIC.value: {},
            MediaTypes.MANGA.value: {},
        },
        "decades": {
            "movie": dict(movie_decades),
            "tv": dict(tv_decades),
            "anime": dict(anime_decades),
            "game": {},
        },
        "music": {},
        "podcast": {},
        "game": {},
        "daily_minutes_by_type": dict(daily_minutes_by_type),
        "activity": {"count": activity_count},
    }

    for media_type, items in items_by_type.items():
        day_stats["items"][media_type] = {}
        for item_id, meta in items.items():
            day_stats["items"][media_type][str(item_id)] = {
                **meta,
                "activity_dt": meta["activity_dt"].isoformat()
                if meta.get("activity_dt")
                else None,
                "score_dt": meta["score_dt"].isoformat()
                if meta.get("score_dt")
                else None,
            }

    for media_type, items in top_played_by_type.items():
        day_stats["top_played"][media_type] = {}
        for item_id, entry in items.items():
            day_stats["top_played"][media_type][str(item_id)] = {
                **entry,
                "activity_dt": entry["activity_dt"].isoformat()
                if entry.get("activity_dt")
                else None,
            }

    for media_type, hours in hour_counts.items():
        day_stats["hour_counts"][media_type] = {
            str(hour): count for hour, count in hours.items()
        }

    for media_type, hours in hour_minutes.items():
        day_stats["hour_minutes"][media_type] = {
            str(hour): minutes for hour, minutes in hours.items()
        }

    game_genre_payload = {}
    for genre, payload in game_genres.items():
        game_genre_payload[genre] = {
            "minutes": payload["minutes"],
            "plays": payload["plays"],
            "game_ids": sorted({str(game_id) for game_id in payload["game_ids"]}),
            "name": genre,
        }
    day_stats["genres"]["game"] = game_genre_payload

    game_decade_payload = {}
    for label, payload in game_decades.items():
        game_decade_payload[label] = {
            "minutes": payload["minutes"],
            "game_ids": sorted({str(game_id) for game_id in payload["game_ids"]}),
            "label": label,
        }
    day_stats["decades"]["game"] = game_decade_payload

    for reading_type in (
        MediaTypes.BOOK.value,
        MediaTypes.COMIC.value,
        MediaTypes.MANGA.value,
    ):
        reading_payload = {}
        for genre, payload in reading_genres[reading_type].items():
            reading_payload[genre] = {
                "units": payload["units"],
                "titles": len(payload["titles"]),
                "name": payload["name"],
            }
        day_stats["genres"][reading_type] = reading_payload

    for key, value in music_rollups.items():
        day_stats["music"][key] = {
            str(item_id): payload for item_id, payload in value.items()
        }

    for key, value in podcast_rollups.items():
        day_stats["podcast"][key] = {
            str(item_id): payload for item_id, payload in value.items()
        }

    game_payload = {}
    for item_id, payload in game_rollups.items():
        game_payload[str(item_id)] = {
            **payload,
            "activity_dt": payload["activity_dt"].isoformat()
            if payload.get("activity_dt")
            else None,
        }
    day_stats["game"]["by_game"] = game_payload
    if prefetch is not None and "missing_credit_ids" in prefetch:
        missing_credit_item_ids = sorted(
            missing_credit_candidate_item_ids & prefetch["missing_credit_ids"],
        )
    else:
        missing_credit_item_ids = _resolve_missing_credit_item_ids(
            missing_credit_candidate_item_ids,
        )
    missing_credits = len(missing_credit_item_ids)
    scheduled_credit_backfills = 0

    if backfill_collector is not None:
        backfill_collector["runtime_item_ids"].update(missing_runtime_item_ids)
        backfill_collector["genre_item_ids"].update(missing_genre_item_ids)
        backfill_collector["episode_runtime_keys"].update(missing_episode_runtime_keys)
        backfill_collector["credit_item_ids"].update(missing_credit_item_ids)
    elif (
        missing_runtime_item_ids
        or missing_genre_item_ids
        or missing_episode_runtime_keys
        or missing_credit_item_ids
    ):
        try:
            from app.tasks import (
                enqueue_credits_backfill_items,
                enqueue_episode_runtime_backfill,
                enqueue_genre_backfill_items,
                enqueue_runtime_backfill_items,
            )

            if missing_runtime_item_ids:
                enqueue_runtime_backfill_items(sorted(missing_runtime_item_ids))
            if missing_genre_item_ids:
                enqueue_genre_backfill_items(sorted(missing_genre_item_ids))
            if missing_episode_runtime_keys:
                enqueue_episode_runtime_backfill(sorted(missing_episode_runtime_keys))
            if missing_credit_item_ids:
                queued_credits = enqueue_credits_backfill_items(
                    missing_credit_item_ids,
                    countdown=3,
                )
                if isinstance(queued_credits, int) and queued_credits > 0:
                    scheduled_credit_backfills = queued_credits
        except Exception as exc:  # pragma: no cover - best-effort scheduling
            logger.debug(
                "stats_backfill_schedule_failed user_id=%s day=%s error=%s",
                user_id,
                day.isoformat(),
                exc,
            )
    day_stats["backfill"] = {
        "missing_credits": missing_credits,
        "scheduled_credits": scheduled_credit_backfills,
    }

    if not defer_cache_write:
        cache.set(
            _day_cache_key(user_id, day),
            day_stats,
            timeout=STATISTICS_DAY_CACHE_TIMEOUT,
        )
    # A sweep (defer_cache_write) builds thousands of days and already reports
    # stats_range_summary and stats_sync, so its per-day lines stay at DEBUG.
    # Production logged 21,574 of them, about a fifth of the file.
    notable = bool(play_count or missing_runtime or missing_credits)
    logger.log(
        logging.INFO if notable and not defer_cache_write else logging.DEBUG,
        (
            "stats_day_summary user_id=%s day=%s plays=%s missing_runtime=%s "
            "missing_genres=%s missing_credits=%s scheduled_credits=%s"
        ),
        user_id,
        day.isoformat(),
        play_count,
        missing_runtime,
        missing_genres,
        missing_credits,
        scheduled_credit_backfills,
    )
    return day_stats
