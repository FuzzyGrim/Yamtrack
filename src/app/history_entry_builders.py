"""Functions that build per-entry history dicts for each media type."""

import logging
from collections import defaultdict

from django.apps import apps
from django.conf import settings
from django.utils import formats
from django.utils.translation import ngettext

from app import helpers
from app.history_cache_utils import (
    _coerce_genre_list,
    _localize_datetime,
    _resolve_genres,
    _resolve_implied_genres,
    _resolve_music_genres,
)
from app.models import MediaTypes

logger = logging.getLogger(__name__)

RUNTIME_UNKNOWN_AIRED = 999998  # aired but runtime unknown
MINUTES_PER_HOUR = 60


# ── Model serializers ─────────────────────────────────────────────────────────


def _entry_dates(record, *, added_from=None):
    """Return the tracking dates a History card can show as card properties.

    ``added_from`` supplies created_at when ``record`` is a history row, which
    does not store it.
    """
    return {
        "created_at": getattr(added_from or record, "created_at", None),
        "start_date": getattr(record, "start_date", None),
        "end_date": getattr(record, "end_date", None),
    }


def _serialize_item(item):
    if not item:
        return None
    if isinstance(item, dict):
        data = dict(item)
        if data.get("season_number") is None:
            data.pop("season_number", None)
        if data.get("episode_number") is None:
            data.pop("episode_number", None)
        return data
    if not hasattr(item, "media_type"):
        return None
    data = {
        "id": getattr(item, "id", None),
        "media_type": item.media_type,
        "media_id": str(getattr(item, "media_id", ""))
        if getattr(item, "media_id", None) is not None
        else None,
        "source": getattr(item, "source", None),
        "title": getattr(item, "title", "") or "",
        "original_title": getattr(item, "original_title", None),
        "localized_title": getattr(item, "localized_title", None),
    }
    # Which library bucket the row belongs to (e.g. anime episodes grouped
    # under TV), so consumers need no second lookup to route it (#1873).
    library_media_type = getattr(item, "library_media_type", None)
    if library_media_type:
        data["library_media_type"] = library_media_type
    season_number = getattr(item, "season_number", None)
    if season_number is not None:
        data["season_number"] = season_number
    episode_number = getattr(item, "episode_number", None)
    if episode_number is not None:
        data["episode_number"] = episode_number
    genres = _coerce_genre_list(getattr(item, "genres", None))
    if genres:
        data["genres"] = genres
    implied_genres = _coerce_genre_list(getattr(item, "implied_genres", None))
    if implied_genres:
        data["implied_genres"] = implied_genres
    provider_external_ids = getattr(item, "provider_external_ids", None)
    if provider_external_ids:
        data["provider_external_ids"] = dict(provider_external_ids)
    return data


def _serialize_album(album):
    if not album:
        return None
    if isinstance(album, dict):
        return album
    return {
        "id": getattr(album, "id", None),
        "title": getattr(album, "title", "") or "",
        "image": getattr(album, "image", "") or "",
        "artist_name": getattr(getattr(album, "artist", None), "name", "") or "",
    }


def _attach_entry_score(entry, media):
    """Attach a media score to a history card entry when available."""
    if not entry or not media or entry.get("score") is not None:
        return entry
    score = getattr(media, "score", None)
    if score is not None:
        entry["score"] = score
    return entry


def flat_anime_episode_watches(records):
    """Return ``{anime_id: [(episode_number, watched_at), ...]}`` for flat anime.

    A flat entry stores progress, not episodes, so each rise in its change log
    dates the episodes it covered. A rise saved after the entry's own end date
    (an import of an old watch) is dated on that end date. Each episode keeps
    its latest date, and only episodes up to the current progress are kept, so
    a lowered progress drops the ones above it. An entry with no change log
    dates every episode on its end or start date.
    """
    records = [record for record in records if (record.progress or 0) > 0]
    if not records:
        return {}
    HistoricalAnime = apps.get_model("app", "HistoricalAnime")
    rows = (
        HistoricalAnime.objects.filter(id__in=[record.id for record in records])
        .order_by("history_date", "history_id")
        .values_list("id", "progress", "end_date", "history_date")
    )
    dated_by_id = defaultdict(dict)
    previous_progress = {}
    for anime_id, row_progress, row_end_date, history_date in rows:
        progress = row_progress or 0
        before = previous_progress.get(anime_id, 0)
        previous_progress[anime_id] = progress
        if progress <= before:
            continue
        watched_at = min(history_date, row_end_date) if row_end_date else history_date
        for episode_number in range(before + 1, progress + 1):
            dated_by_id[anime_id][episode_number] = watched_at

    watches = {}
    for record in records:
        dated = dated_by_id.get(record.id)
        if not dated:
            fallback = record.end_date or record.start_date
            if not fallback:
                continue
            dated = dict.fromkeys(range(1, record.progress + 1), fallback)
        watches[record.id] = sorted(
            (number, watched_at)
            for number, watched_at in dated.items()
            if number <= record.progress
        )
    return watches


def flat_anime_episode_entries(entry, watches, final_episode):
    """Split a flat anime card into one card per dated episode.

    The entry's score stays on the card of its last watched episode only.
    """
    cards = []
    for episode_number, watched_at in watches:
        card = {
            **entry,
            "episode_label": f"E{episode_number:02d}",
            "episode_code": f"E{episode_number:02d}",
            "episode_number": episode_number,
            "played_at_local": _localize_datetime(watched_at),
            "entry_key": f"{entry['entry_key']}-e{episode_number}",
        }
        if episode_number != final_episode:
            card.pop("score", None)
        cards.append(card)
    return cards


def _serialize_show(show):
    if not show:
        return None
    if isinstance(show, dict):
        return show
    return {
        "id": getattr(show, "id", None),
        "title": getattr(show, "title", "") or "",
        "slug": getattr(show, "slug", "") or "",
        "podcast_uuid": getattr(show, "podcast_uuid", None),
        "image": getattr(show, "image", "") or "",
        "website_url": getattr(show, "website_url", "") or "",
    }


# ── Runtime / formatting helpers ──────────────────────────────────────────────


def _resolve_runtime_minutes(*items):
    """Pick the first usable runtime value from the provided items."""
    for item in items:
        if not item:
            continue
        runtime = getattr(item, "runtime_minutes", None)
        # Exclude fallback values: 999998 (aired but runtime unknown) and 999999 (unknown runtime)
        if runtime and runtime < RUNTIME_UNKNOWN_AIRED:
            return runtime
    return 0


def _format_game_hours(minutes: int) -> str:
    """Show hours only if at least 1h, otherwise keep minutes."""
    minutes = minutes or 0
    if minutes >= MINUTES_PER_HOUR:
        return f"{minutes // MINUTES_PER_HOUR}h"
    return f"{minutes}min"


def _format_boardgame_plays(plays: int) -> str:
    """Return a play-count label."""
    plays = plays or 0
    return f"{plays} play{'s' if plays != 1 else ''}"


# ── Episode / movie builders ──────────────────────────────────────────────────


def _get_episode_poster(episode):
    """Prefer show/season posters over episodic stills for consistent cards."""
    season_item = getattr(episode.related_season, "item", None)
    getattr(episode, "item", None)
    tv_item = getattr(getattr(episode.related_season, "related_tv", None), "item", None)

    return (
        getattr(tv_item, "image", None)
        or getattr(season_item, "image", None)
        or getattr(episode.item, "image", None)
        or settings.IMG_NONE
    )


def _get_episode_display_title(episode, episode_title_map=None):
    """Derive a best-effort episode title from local data only."""
    episode_item = getattr(episode, "item", None)
    season_item = getattr(episode.related_season, "item", None)

    key = None
    if episode_item:
        key = (
            getattr(episode_item, "media_id", None),
            getattr(episode_item, "source", None),
            getattr(episode_item, "season_number", None),
            getattr(episode_item, "episode_number", None),
            getattr(episode_item, "library_media_type", None),
        )

    if episode_title_map and key in episode_title_map:
        title_candidate = episode_title_map.get(key)
        if title_candidate:
            return title_candidate
    # Prefer the stored episode title if present.
    if episode_item and episode_item.title:
        return episode_item.title
    if season_item and season_item.title:
        return season_item.title
    return ""


def _build_episode_entry(episode, episode_title_map=None):
    played_at_local = _localize_datetime(
        episode.end_date or episode.start_date or episode.created_at
    )
    if not played_at_local:
        return None

    episode_item = getattr(episode, "item", None)
    season_item = getattr(episode.related_season, "item", None)
    tv_item = getattr(getattr(episode.related_season, "related_tv", None), "item", None)
    entry_item = episode_item or season_item or tv_item
    if not entry_item:
        return None
    runtime_minutes = _resolve_runtime_minutes(
        episode.item,
        season_item,
        tv_item,
    )
    genres = _resolve_genres(episode_item, season_item, tv_item)
    implied_genres = _resolve_implied_genres(episode_item, season_item, tv_item)

    display_title = _get_episode_display_title(episode, episode_title_map)
    title = ""
    if episode_item and episode_item.title:
        title = episode_item.title
    elif season_item and season_item.title:
        title = season_item.title
    elif tv_item and tv_item.title:
        title = tv_item.title

    episode_label = None
    episode_code = None
    if (
        episode_item
        and episode_item.season_number is not None
        and episode_item.episode_number is not None
    ):
        episode_code = (
            f"S{episode_item.season_number:02d}E{episode_item.episode_number:02d}"
        )
        episode_label = episode_code

    entry = {
        "media_type": MediaTypes.EPISODE.value,
        "item": _serialize_item(entry_item),
        "show": _serialize_show(tv_item),
        "poster": _get_episode_poster(episode),
        "title": title,
        "display_title": display_title,
        "episode_label": episode_label,
        "episode_code": episode_code,
        # Tiebreaker for entries.sort()'s played_at_local key: plays logged
        # in one bulk action often share an exact timestamp, so the season
        # order they actually play in decides ties rather than whatever
        # arbitrary order the query happened to return them in.
        "season_number": episode_item.season_number if episode_item else None,
        "episode_number": episode_item.episode_number if episode_item else None,
        "status": episode.status,
        "played_at_local": played_at_local,
        **_entry_dates(episode),
        "runtime_minutes": runtime_minutes,
        "runtime_display": helpers.minutes_to_hhmm(runtime_minutes)
        if runtime_minutes
        else None,
        "instance_id": episode.id,
        "entry_key": str(episode.id),
        "entry_source": episode.entry_source,
    }
    _attach_entry_score(entry, episode)
    if genres:
        entry["genres"] = genres
    if implied_genres:
        entry["implied_genres"] = implied_genres
    return entry


def _build_movie_entry(movie):
    played_at_local = _localize_datetime(
        movie.end_date or movie.start_date or movie.created_at
    )
    if not played_at_local:
        return None

    runtime_minutes = _resolve_runtime_minutes(movie.item)
    genres = _resolve_genres(movie.item)
    implied_genres = _resolve_implied_genres(movie.item)

    entry = {
        "media_type": MediaTypes.MOVIE.value,
        "item": _serialize_item(movie.item),
        "poster": movie.item.image or settings.IMG_NONE,
        "title": movie.item.title,
        "display_title": movie.item.title,
        "status": movie.status,
        "play_count": 1,
        "episode_label": None,
        "episode_code": None,
        "played_at_local": played_at_local,
        **_entry_dates(movie),
        "runtime_minutes": runtime_minutes,
        "runtime_display": helpers.minutes_to_hhmm(runtime_minutes)
        if runtime_minutes
        else None,
        "instance_id": movie.id,
        "entry_key": str(movie.id),
        "entry_source": movie.entry_source,
        # A grouped movie row stands for the Movie tracker, so instance_id is a
        # tracker id. A single watch is addressed by its play id on the
        # per-media history endpoints, never by this value (#1873).
        "record_type": "movie_tracker",
    }
    _attach_entry_score(entry, movie)
    if genres:
        entry["genres"] = genres
    if implied_genres:
        entry["implied_genres"] = implied_genres
    return entry


def _build_video_play_entry(play):
    """One history row per video play.

    @param play - VideoPlay with video and item selected.
    @returns Entry dict, or None when the play has no timestamp.
    """
    video = play.video
    played_at_local = _localize_datetime(play.end_date or video.created_at)
    if not played_at_local:
        return None
    runtime_minutes = video.length_seconds // 60 if video.length_seconds else None
    return {
        "media_type": MediaTypes.VIDEO.value,
        "item": _serialize_item(video.item),
        "poster": video.item.image or settings.IMG_NONE,
        "title": video.item.title,
        "display_title": video.item.title,
        "status": video.status,
        "play_count": 1,
        "episode_label": video.channel or None,
        "episode_code": None,
        "played_at_local": played_at_local,
        **_entry_dates(play, added_from=video),
        "runtime_minutes": runtime_minutes,
        "runtime_display": helpers.minutes_to_hhmm(runtime_minutes) if runtime_minutes else None,
        "instance_id": play.id,
        "entry_key": f"video:{play.id}",
        "entry_source": "youtube",
    }


# ── Music builders ────────────────────────────────────────────────────────────


def _get_music_runtime_minutes(music_entry, track_duration_cache=None):
    """Get runtime in minutes from a Music entry, checking track and item.

    Args:
        music_entry: The Music model instance
        track_duration_cache: Optional dict with two types of keys:
            - (album_id, track_title) -> duration_ms
            - ("recording", recording_id) -> duration_ms
    """
    # First try the linked Track's duration_ms
    if music_entry.track and music_entry.track.duration_ms:
        return music_entry.track.duration_ms // 60000  # ms to minutes

    # Fall back to item runtime_minutes
    if music_entry.item and music_entry.item.runtime_minutes:
        return music_entry.item.runtime_minutes

    # Try to look up duration from cache (built from album tracklist)
    if track_duration_cache and music_entry.item:
        # Try matching by title (if we have album)
        if music_entry.album_id:
            title_key = (music_entry.album_id, music_entry.item.title)
            duration_ms = track_duration_cache.get(title_key)
            if duration_ms:
                return duration_ms // 60000

        # Try matching by recording ID (item.media_id is the MusicBrainz recording ID)
        if music_entry.item.media_id:
            recording_key = ("recording", music_entry.item.media_id)
            duration_ms = track_duration_cache.get(recording_key)
            if duration_ms:
                return duration_ms // 60000

    return 0


def _build_music_album_entries(
    music_entries_for_album,
    album,
    day_date,
    user,
    track_duration_cache=None,
    album_scores=None,
):
    """Build a single history entry for an album's plays on a given day.

    Groups all track plays for an album on a day into one card showing:
    - Album poster
    - Play count (sum of plays that day from history records)
    - Album name
    - Time range (earliest to latest play time)
    - Total runtime
    - Album rating (if available)
    """
    if not music_entries_for_album:
        return None

    # Collect all play times and runtimes for this album on this day
    # We need to count history records, not Music entries, since a track
    # played twice creates 2 history records on the same Music entry
    play_times = []
    total_runtime_minutes = 0
    play_count = 0
    latest_play_time = None
    primary_music = None

    for music in music_entries_for_album:
        runtime_for_track = _get_music_runtime_minutes(music, track_duration_cache)

        # Check history records for plays on this day
        # Each history record with an end_date on this day counts as a play
        for history_record in music.history.all():
            # Only include history records for this user (or null history_user for legacy records)
            history_user = getattr(history_record, "history_user", None)
            if history_user is not None and history_user != user:
                continue

            history_end_date = getattr(history_record, "end_date", None)
            if history_end_date:
                play_time = _localize_datetime(history_end_date)
                if play_time and play_time.date() == day_date:
                    play_times.append(play_time)
                    play_count += 1
                    total_runtime_minutes += runtime_for_track
                    if latest_play_time is None or play_time > latest_play_time:
                        latest_play_time = play_time
                        primary_music = music

    if not play_times:
        return None

    play_times.sort()
    earliest_time = play_times[0]
    latest_time = play_times[-1]

    # Format time range (just times, no date)
    if len(play_times) == 1:
        time_range_display = formats.time_format(earliest_time, "g:i A")
    else:
        time_range_display = f"{formats.time_format(earliest_time, 'g:i A')} - {formats.time_format(latest_time, 'g:i A')}"

    # Get album poster
    poster = settings.IMG_NONE
    if album and album.image:
        poster = album.image

    # Album name
    album_name = album.title if album else "Unknown Album"
    artist_name = album.artist.name if album and album.artist else "Unknown Artist"

    entry_item = primary_music.item if primary_music and primary_music.item else None
    instance_id = primary_music.id if primary_music else None
    track = getattr(primary_music, "track", None) if primary_music else None
    genres = _resolve_music_genres(
        album=album, artist=album.artist if album else None, track=track
    )
    implied_genres = _coerce_genre_list(getattr(album, "implied_genres", None))
    entry_key = f"{album.id if album else 'album'}-{day_date.strftime('%Y%m%d')}"

    # Get album score if available
    album_score = None
    if album_scores and album and album.id:
        album_score = album_scores.get(album.id)

    entry = {
        "media_type": MediaTypes.MUSIC.value,
        "item": _serialize_item(entry_item),
        "album": _serialize_album(album),
        "poster": poster,
        "title": album_name,
        "display_title": album_name,
        "artist_name": artist_name,
        "play_count": play_count,
        "time_range_display": time_range_display,
        "episode_label": None,
        "episode_code": None,
        "played_at_local": latest_time,  # Use latest play for sorting
        **_entry_dates(primary_music),
        "end_date": latest_time,
        "runtime_minutes": total_runtime_minutes,
        "runtime_display": helpers.minutes_to_hhmm(total_runtime_minutes)
        if total_runtime_minutes
        else None,
        "instance_id": instance_id,
        "entry_key": entry_key,
        "score": album_score,  # Album tracker score
    }
    if genres:
        entry["genres"] = genres
    if implied_genres:
        entry["implied_genres"] = implied_genres
    return entry


def build_listening_session_entry(session):
    """Build the history entry for one audiobook listening session."""
    book = session.book
    item = book.item
    entry = {
        "media_type": MediaTypes.BOOK.value,
        "item": _serialize_item(item),
        "poster": item.image or settings.IMG_NONE,
        "title": item.title,
        "display_title": item.title,
        "status": book.status,
        "episode_label": None,
        "episode_code": None,
        "played_at_local": _localize_datetime(session.started_at),
        **_entry_dates(book),
        "runtime_minutes": session.minutes,
        "runtime_display": helpers.minutes_to_hhmm(session.minutes),
        "instance_id": book.id,
        "entry_key": f"book-session-{session.id}",
    }
    if session.position_minutes is not None:
        entry["position_display"] = helpers.minutes_to_hhmm(session.position_minutes)
        if item.runtime_minutes:
            entry["position_total_display"] = helpers.minutes_to_hhmm(
                item.runtime_minutes,
            )
    _attach_entry_score(entry, book)
    return entry


def build_book_page_entries(books, start=None, end=None, history_rows=None):
    """Build one entry per day a page-based book's progress rose.

    ``books`` are Book rows with ``item`` loaded. The first change-log row of a
    book is its baseline (an import or a first save), so it dates nothing: a
    book with a single row, or only a current page, yields no entries. A day
    with several saves is one entry holding the pages gained and where reading
    stopped. Audiobooks are skipped: their progress is minutes.

    ``history_rows`` are those books' ``(id, progress, history_date)`` change
    rows, in ``id, history_date, history_id`` order, when the caller has loaded
    them for a whole span of days; otherwise they are read here.
    """
    books = {
        book.id: book
        for book in books
        if book.item.format != "audiobook"
    }
    if not books:
        return []
    if history_rows is not None:
        rows = history_rows
    else:
        HistoricalBook = apps.get_model("app", "HistoricalBook")
        rows = (
            HistoricalBook.objects.filter(id__in=books, history_type__in=("+", "~"))
            .order_by("id", "history_date", "history_id")
            .values_list("id", "progress", "history_date")
        )
    previous = {}
    days = {}
    for book_id, row_progress, history_date in rows:
        progress = row_progress or 0
        before = previous.get(book_id)
        previous[book_id] = progress
        if before is None or progress <= before:
            continue
        local = _localize_datetime(history_date)
        if not local:
            continue
        day = days.setdefault((book_id, local.date()), {"pages": 0, "at": local})
        day["pages"] += progress - before
        day["position"] = progress
        day["at"] = local

    entries = []
    for (book_id, _day), day in days.items():
        if (start and day["at"] < start) or (end and day["at"] >= end):
            continue
        book = books[book_id]
        item = book.item
        pages = day["pages"]
        entry = {
            "media_type": MediaTypes.BOOK.value,
            "item": _serialize_item(item),
            "poster": item.image or settings.IMG_NONE,
            "title": item.title,
            "display_title": item.title,
            "status": book.status,
            "episode_label": None,
            "episode_code": None,
            "played_at_local": day["at"],
            **_entry_dates(book),
            "runtime_minutes": 0,
            "runtime_display": ngettext("%(count)d page", "%(count)d pages", pages)
            % {"count": pages},
            "position_unit": "page",
            "position_display": str(day["position"]),
            "instance_id": book.id,
            "entry_key": f"book-pages-{book.id}-{day['at'].date().isoformat()}",
        }
        if item.number_of_pages:
            entry["position_total_display"] = str(item.number_of_pages)
        _attach_entry_score(entry, book)
        entries.append(entry)
    return entries
