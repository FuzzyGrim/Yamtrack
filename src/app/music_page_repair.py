"""Music pages render what is stored; repairs run in a background task.

Opening an artist, album or track page used to merge duplicate albums, sync a
discography, fetch MusicBrainz metadata and write tracks, credits and genres
before any HTML was produced. A busy SQLite writer or a slow MusicBrainz then
held the page. Now a page:

1. reads what is stored, and MusicBrainz answers only from the cache
   (``musicbrainz.cached_*``);
2. asks the read-only ``*_repair_reasons`` functions here what is missing or
   due;
3. queues one repair task for that artist, album or track, at most once per
   cool-down (``queue_*``); and
4. when something the reader can see is missing (no tracks, no bio), shows a
   short poller that reloads the page once the task finishes
   (``music_repair_status``).

The repair bodies (``run_*``) are the code the views used to run inline, in the
same order. Rule and tests: docs/architecture/sqlite-runtime-safety.md,
``app.tests.test_read_pages_do_not_write``.
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.db import IntegrityError, models, transaction

from app.log_safety import exception_summary
from app.models import Album, Artist, Track

logger = logging.getLogger(__name__)

# How often viewing an artist may lead to MusicBrainz work: an MBID search when
# the artist has none, and a discography sync.
ARTIST_VIEW_MBID_RESOLVE_SECONDS = 24 * 60 * 60
ARTIST_VIEW_SYNC_SECONDS = 60 * 60
# Credits and genres look the same before and after a refresh, so a page cannot
# tell they are current; it asks for a refresh at most this often.
MAINTENANCE_SECONDS = 7 * 24 * 60 * 60
# A repair that keeps failing (MusicBrainz down, no match) is not queued again
# by every reload within this window.
REPAIR_COOLDOWN_SECONDS = 10 * 60
# The pending marker outlives a stuck worker by this much at most.
REPAIR_PENDING_SECONDS = 10 * 60
# Where a merged or replaced artist or album now lives.
MERGE_POINTER_SECONDS = 30 * 24 * 60 * 60
# The page poller asks every 2 seconds; this bounds it to about two minutes.
REPAIR_POLL_MAX_ATTEMPTS = 60

KINDS = ("artist", "album", "track")

# Reasons a reader can see on the page (no tracks, no bio, a placeholder
# album). Only these show the poller; the rest refresh quietly.
VISIBLE_REASONS = frozenset(
    {
        "mbid",
        "metadata",
        "no_albums",
        "duplicates",
        "incomplete",
        "release_id",
        "tracks",
        "selected_release",
        "recording",
    },
)


def _pending_key(kind, object_id):
    return f"music:repair-pending:{kind}:{object_id}"


def _cooldown_key(kind, object_id):
    return f"music:repair-queued:{kind}:{object_id}"


def _merged_key(kind, object_id):
    return f"music:merged-into:{kind}:{object_id}"


def _maintenance_key(kind, object_id):
    return f"music:maintenance:{kind}:{object_id}"


def _mbid_resolve_key(artist_id):
    return f"music:artist-mbid-resolve:{artist_id}"


def _view_sync_key(artist_id):
    return f"music:artist-view-sync:{artist_id}"


def _album_heal_key(album_id):
    return f"music:album-heal:{album_id}"


def _cache_get(key, default=None):
    try:
        return cache.get(key, default)
    except Exception:  # pragma: no cover - a cache outage must not fail a page
        return default


def repair_pending(kind, object_id) -> bool:
    """Return True while a queued repair for this page has not finished."""
    return bool(_cache_get(_pending_key(kind, object_id)))


def merged_into(kind, object_id):
    """Return the id an artist or album was merged into, or None."""
    return _cache_get(_merged_key(kind, object_id))


def remember_merge(kind, old_id, new_id):
    """Point links to a merged or replaced artist or album at the survivor."""
    if old_id == new_id:
        return
    try:
        cache.set(_merged_key(kind, old_id), new_id, MERGE_POINTER_SECONDS)
    except Exception:  # pragma: no cover - defensive
        logger.debug("Could not remember %s %s -> %s", kind, old_id, new_id)


def _normalized_title(title):
    from app.services.music_scrobble import _normalize

    return _normalize(title or "")


def has_duplicate_album_titles(artist) -> bool:
    """Return True when ``dedupe_artist_albums`` would find a group to merge.

    One read of the artist's album titles; the merge itself runs in the task.
    """
    if artist is None:
        return False
    seen = set()
    for title in Album.objects.filter(artist=artist).values_list("title", flat=True):
        norm = _normalized_title(title)
        if norm in seen:
            return True
        seen.add(norm)
    return False


# --- What a page needs (read only) ------------------------------------------


def artist_repair_reasons(artist) -> list[str]:
    """Return what the artist page is missing or due, without writing."""
    from app.providers import musicbrainz
    from app.services.music import artist_members_need_sync, needs_discography_sync

    reasons = []
    if not artist.musicbrainz_id:
        if _cache_get(_mbid_resolve_key(artist.id)) is None:
            reasons.append("mbid")
        if has_duplicate_album_titles(artist):
            reasons.append("duplicates")
        return reasons

    if has_duplicate_album_titles(artist):
        reasons.append("duplicates")
    albums = Album.objects.filter(
        models.Q(artist=artist) | models.Q(artist_credits__artist=artist),
    )
    has_albums = albums.exists()
    missing_mbids = albums.filter(
        musicbrainz_release_id__isnull=True,
        musicbrainz_release_group_id__isnull=True,
    ).exists()
    if _cache_get(_view_sync_key(artist.id)) is None:
        if not has_albums:
            reasons.append("no_albums")
        elif missing_mbids or needs_discography_sync(artist, max_age_days=1):
            reasons.append("discography")
    if musicbrainz.cached_artist(artist.musicbrainz_id) is None:
        reasons.append("metadata")
    if artist_members_need_sync(artist):
        reasons.append("members")
    return reasons


def album_repair_reasons(album, user=None) -> list[str]:
    """Return what the album page is missing or due, without writing."""
    from app.models import MusicReleasePreference
    from app.providers import musicbrainz
    from app.services import music as music_services
    from app.services.music_scrobble import is_incomplete_album

    reasons = []
    artist = album.artist
    if artist is not None and has_duplicate_album_titles(artist):
        reasons.append("duplicates")
    if (
        artist is not None
        and artist.musicbrainz_id
        and _cache_get(_album_heal_key(album.id)) is None
        and is_incomplete_album(album)
    ):
        reasons.append("incomplete")
    if not album.musicbrainz_release_id and album.musicbrainz_release_group_id:
        reasons.append("release_id")
    has_identity = music_services.album_has_musicbrainz_id(album)
    if not album.tracks_populated and has_identity:
        reasons.append("tracks")
    if has_identity and _cache_get(_maintenance_key("album", album.id)) is None:
        reasons.append("refresh")
    if user is not None and album.musicbrainz_release_group_id:
        preference = MusicReleasePreference.objects.filter(
            user=user,
            album=album,
        ).first()
        if preference and musicbrainz.cached_release(preference.release_id) is None:
            reasons.append("selected_release")
    return reasons


def track_recording_lookup(track):
    """Return ``(recording, missing)`` for an unlistened track, from the cache.

    ``missing`` is True when the cache has no answer yet, so the page queues a
    lookup. A cached search that found nothing is an answer.
    """
    from app.providers import musicbrainz

    recording_id = (track.musicbrainz_recording_id or "").strip()
    if not recording_id:
        query = _track_search_query(track)
        if not query:
            return None, False
        results = musicbrainz.cached_search(query, page=1, skip_cover_art=True)
        if results is None:
            return None, True
        recording_id = _matching_recording_id(track, results)
        if not recording_id:
            return None, False
    recording = musicbrainz.cached_recording(recording_id)
    return recording, recording is None


def _lookup_text(value):
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _track_search_query(track):
    artist = track.album.artist if track.album_id else None
    artist_name = artist.name if artist else ""
    return " ".join(part for part in (artist_name, track.title) if part).strip()


def _matching_recording_id(track, results):
    expected_title = _lookup_text(track.title)
    for result in (results or {}).get("results") or []:
        if expected_title and expected_title in _lookup_text(result.get("title")):
            return result.get("media_id") or ""
    return ""


# --- Queueing (no database writes) ------------------------------------------


def _queue(kind, object_id, reasons, task, *args) -> bool:
    """Queue ``task`` once per cool-down; return True while a visible repair runs."""
    if not reasons:
        return False
    visible = bool(VISIBLE_REASONS.intersection(reasons))
    try:
        if cache.add(_cooldown_key(kind, object_id), True, REPAIR_COOLDOWN_SECONDS):
            if visible:
                cache.set(_pending_key(kind, object_id), True, REPAIR_PENDING_SECONDS)
            try:
                task.delay(*args)
            except Exception:
                cache.delete(_cooldown_key(kind, object_id))
                cache.delete(_pending_key(kind, object_id))
                raise
    except Exception as exc:  # pragma: no cover - a page still renders
        logger.warning(
            "Music %s %s repair was not queued: %s",
            kind,
            object_id,
            exception_summary(exc),
        )
        return False
    return visible and repair_pending(kind, object_id)


def queue_artist_repair(artist, reasons) -> bool:
    """Queue the artist repair; True when the page should poll for it."""
    from app.tasks_music import repair_music_artist_page

    return _queue("artist", artist.id, reasons, repair_music_artist_page, artist.id)


def queue_album_repair(album, reasons, user=None) -> bool:
    """Queue the album repair; True when the page should poll for it."""
    from app.tasks_music import repair_music_album_page

    user_id = user.id if user is not None else None
    return _queue(
        "album",
        album.id,
        reasons,
        repair_music_album_page,
        album.id,
        user_id,
    )


def queue_track_lookup(track) -> bool:
    """Queue the recording lookup for an unlistened track; True to poll."""
    from app.tasks_music import repair_music_track_page

    return _queue("track", track.id, ["recording"], repair_music_track_page, track.id)


def finish_repair(kind, object_id):
    """Clear the pending marker so the page's poller reloads."""
    try:
        cache.delete(_pending_key(kind, object_id))
    except Exception:  # pragma: no cover - defensive
        logger.debug("Could not clear the %s %s repair marker", kind, object_id)


# --- Repairs (run by the tasks in tasks_music.py) ---------------------------


def _dedupe_and_remember(artist):
    """Run ``dedupe_artist_albums`` and point removed albums at their survivor."""
    from app.services.music_scrobble import dedupe_artist_albums

    before = dict(Album.objects.filter(artist=artist).values_list("id", "title"))
    dedupe_artist_albums(artist)
    after = dict(Album.objects.filter(artist=artist).values_list("id", "title"))
    survivors = {}
    for album_id in sorted(after):
        survivors.setdefault(_normalized_title(after[album_id]), album_id)
    for album_id, title in before.items():
        if album_id in after:
            continue
        survivor = survivors.get(_normalized_title(title))
        if survivor:
            remember_merge("album", album_id, survivor)


def _resolve_artist_mbid(artist):
    """Attach an MBID found by name; merge into an existing artist on conflict.

    Returns the artist the page should now show.
    """
    from app.services import music as sync_services

    if not cache.add(
        _mbid_resolve_key(artist.id),
        True,
        ARTIST_VIEW_MBID_RESOLVE_SECONDS,
    ):
        return artist
    mbid, cand_count, variant = sync_services.resolve_artist_mbid(
        artist.name or "",
        artist.sort_name or "",
    )
    if not mbid:
        logger.debug(
            "No MBID attached for %s after searching variants (candidates=%d)",
            artist.name,
            cand_count,
        )
        return artist
    try:
        artist.musicbrainz_id = mbid
        artist.discography_synced_at = None
        with transaction.atomic():
            artist.save(update_fields=["musicbrainz_id", "discography_synced_at"])
        logger.info(
            "Attached MBID %s to artist %s via '%s' (candidates=%d)",
            mbid,
            artist.name,
            variant,
            cand_count,
        )
    except IntegrityError:
        existing = Artist.objects.filter(musicbrainz_id=mbid).first()
        if existing:
            old_id = artist.id
            artist = sync_services.merge_artist_records(
                Artist.objects.get(pk=old_id),
                existing,
            )
            remember_merge("artist", old_id, artist.id)
            logger.info(
                "Merged artist %s into existing MBID %s via '%s'",
                artist.name,
                mbid,
                variant,
            )
    return artist


def _refresh_artist_metadata(artist):
    """Fetch the artist from MusicBrainz (warming the page's cache) and store it."""
    from django.conf import settings

    from app.providers import musicbrainz

    mb_data = musicbrainz.get_artist(artist.musicbrainz_id)
    updated_fields = []
    if mb_data.get("type") and mb_data.get("type") != artist.artist_type:
        artist.artist_type = mb_data.get("type", "")
        updated_fields.append("artist_type")
    if mb_data.get("country") and mb_data.get("country") != artist.country:
        artist.country = mb_data.get("country", "")
        updated_fields.append("country")
    if mb_data.get("genres"):
        genre_names = [g.get("name") for g in mb_data.get("genres") if g.get("name")]
        if genre_names != artist.genres:
            artist.genres = genre_names
            updated_fields.append("genres")
    wiki_image = mb_data.get("image")
    if wiki_image and (not artist.image or artist.image == settings.IMG_NONE):
        artist.image = wiki_image
        updated_fields.append("image")
    if updated_fields:
        artist.save(update_fields=updated_fields)


def run_artist_repair(artist_id) -> dict:
    """Do what viewing an artist used to do inline. Returns a short summary."""
    from app.services.music import (
        needs_discography_sync,
        sync_artist_discography,
        sync_artist_members,
    )

    summary = {"artist": artist_id, "synced": 0}
    artist = Artist.objects.filter(id=artist_id).first()
    if artist is None:
        return summary

    if not artist.musicbrainz_id:
        try:
            artist = _resolve_artist_mbid(artist)
        except Exception as exc:
            logger.debug(
                "Artist MBID attach failed for %s: %s",
                artist.name,
                exception_summary(exc),
            )
        summary["artist"] = artist.id

    _dedupe_and_remember(artist)

    albums = Album.objects.filter(
        models.Q(artist=artist) | models.Q(artist_credits__artist=artist),
    )
    existing_album_count = albums.distinct().count()
    missing_mbids = albums.filter(
        musicbrainz_release_id__isnull=True,
        musicbrainz_release_group_id__isnull=True,
    ).exists()
    should_sync = (
        needs_discography_sync(artist, max_age_days=1)
        or existing_album_count == 0
        or missing_mbids
    )
    force_sync = existing_album_count == 0 or missing_mbids
    # A forced sync re-reads the whole discography. Albums the provider cannot
    # match keep ``missing_mbids`` true forever, so it runs at most hourly; the
    # page's sync button still forces one on demand.
    if (
        should_sync
        and artist.musicbrainz_id
        and cache.add(_view_sync_key(artist.id), True, ARTIST_VIEW_SYNC_SECONDS)
    ):
        summary["synced"] = sync_artist_discography(artist, force=force_sync)
        if summary["synced"]:
            _dedupe_and_remember(artist)

    if artist.musicbrainz_id:
        try:
            _refresh_artist_metadata(artist)
        except Exception as exc:
            logger.debug("Failed to fetch artist metadata from MusicBrainz: %s", exc)
        try:
            sync_artist_members(artist)
        except Exception as exc:
            logger.debug("Failed to sync artist members from MusicBrainz: %s", exc)
    return summary


def _heal_incomplete_album(album):
    """Replace a placeholder album with the synced one of the same title."""
    from app.services.music import sync_artist_discography
    from app.services.music_scrobble import _choose_primary_album

    artist = album.artist
    if not cache.add(_album_heal_key(album.id), True, ARTIST_VIEW_SYNC_SECONDS):
        return album
    sync_artist_discography(artist, force=True)
    _dedupe_and_remember(artist)
    album = Album.objects.filter(id=album.id).first()
    if album is None:
        return None
    norm = _normalized_title(album.title)
    candidates = [
        candidate
        for candidate in Album.objects.filter(artist=artist)
        if _normalized_title(candidate.title) == norm
    ]
    if candidates:
        best = _choose_primary_album(candidates, album)
        if best.id != album.id:
            remember_merge("album", album.id, best.id)
            return None
    return album


def run_album_repair(album_id, user_id=None) -> dict:
    """Do what viewing an album used to do inline. Returns a short summary."""
    from app.models import MusicReleasePreference
    from app.providers import musicbrainz
    from app.services import music as sync_services
    from app.services.music_scrobble import is_incomplete_album

    summary = {"album": album_id, "tracks": 0}
    album = Album.objects.select_related("artist").filter(id=album_id).first()
    if album is None:
        return summary
    artist = album.artist

    if artist is not None:
        _dedupe_and_remember(artist)
        album = Album.objects.select_related("artist").filter(id=album_id).first()
        if album is None:
            return summary
        if is_incomplete_album(album) and artist.musicbrainz_id:
            try:
                album = _heal_incomplete_album(album)
            except Exception as exc:
                logger.debug(
                    "Failed to heal album %s via discography: %s",
                    album_id,
                    exception_summary(exc),
                )
                album = Album.objects.filter(id=album_id).first()
            if album is None:
                return summary

    if not album.musicbrainz_release_id and album.musicbrainz_release_group_id:
        sync_services.ensure_album_has_release_id(album)

    has_identity = sync_services.album_has_musicbrainz_id(album)
    if album.musicbrainz_release_id and sync_services.album_artist_credits_need_sync(
        album,
    ):
        try:
            release_data = musicbrainz.get_release(album.musicbrainz_release_id)
            sync_services.sync_album_artist_credits(album, release_data)
        except Exception as exc:
            logger.warning(
                "Failed to sync artist credits for album %s: %s",
                album.title,
                exc,
            )
    if has_identity:
        try:
            sync_services.populate_album_implied_genres(album)
        except Exception as exc:
            logger.warning(
                "Failed to refresh album genres for %s: %s", album.title, exc
            )
    if not album.tracks_populated and has_identity:
        try:
            summary["tracks"] = sync_services.populate_album_tracks(album)
        except Exception as exc:
            logger.warning(
                "Failed to populate tracks for album %s: %s",
                album.title,
                exc,
            )
    if has_identity:
        try:
            cache.set(_maintenance_key("album", album.id), True, MAINTENANCE_SECONDS)
        except Exception:  # pragma: no cover - defensive
            logger.debug("Could not stamp album %s refresh", album.id)

    if user_id and album.musicbrainz_release_group_id:
        preference = MusicReleasePreference.objects.filter(
            user_id=user_id,
            album=album,
        ).first()
        if preference:
            try:
                musicbrainz.get_release(preference.release_id)
            except Exception as exc:
                logger.debug(
                    "Failed to load saved release %s: %s",
                    preference.release_id,
                    exception_summary(exc),
                )
    return summary


def run_track_lookup(track_id) -> dict:
    """Fetch the MusicBrainz recording an unlistened track page shows."""
    from app.providers import musicbrainz

    track = Track.objects.select_related("album__artist").filter(id=track_id).first()
    if track is None:
        return {"track": track_id, "found": False}
    recording_id = (track.musicbrainz_recording_id or "").strip()
    try:
        if not recording_id:
            query = _track_search_query(track)
            if query:
                results = musicbrainz.search(query, page=1, skip_cover_art=True)
                recording_id = _matching_recording_id(track, results)
        if recording_id:
            musicbrainz.recording(recording_id)
    except Exception as exc:
        logger.debug(
            "Track %s recording lookup failed: %s",
            track_id,
            exception_summary(exc),
        )
    return {"track": track_id, "found": bool(recording_id)}
