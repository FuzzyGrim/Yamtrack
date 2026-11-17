# FORK: first-class music endpoints (artists, albums, songs, plays) that
# mirror the web music views. Trackers are per-user; Artist/Album/Track are
# global catalog rows. Scores use the raw 0-10 storage scale like the rest
# of this API. URL wiring lives in fork_urls.py.
import logging
from decimal import Decimal, InvalidOperation
from http import HTTPStatus as HTTP  # noqa: N814

from django.conf import settings
from django.db import models
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import views as drf_views
from rest_framework.response import Response

from app import (
    fork_services_history,
    fork_services_music,
    history_cache,
    music_page_repair,
)
from app.forms import AlbumTrackerForm, ArtistTrackerForm
from app.models import (
    Album,
    AlbumTracker,
    Artist,
    ArtistTracker,
    Music,
    MusicReleasePreference,
    Status,
    Track,
)
from app.providers import musicbrainz
from app.score_views import (
    _collect_music_history_day_keys_for_album_ids,
    _collect_music_history_day_keys_for_artist,
)
from app.services.music import (
    sync_album_metadata,
    sync_album_release_tracks,
    sync_artist_discography,
    sync_artist_members,
)
from app.services.music_scrobble import dedupe_artist_albums
from app.tasks_bulk_plays import bulk_music_plays_task

from .errors import failure_response
from .helpers import paginate_data, parse_limit_offset, try_parse_datetime_input
from .serializers import serialize_data

_OBJ_READ = extend_schema(responses={200: OpenApiTypes.OBJECT})
_OBJ_WRITE = extend_schema(
    request=OpenApiTypes.OBJECT,
    responses={200: OpenApiTypes.OBJECT, 201: OpenApiTypes.OBJECT, 204: None},
)
_OBJ_DELETE = extend_schema(responses={204: None})

logger = logging.getLogger(__name__)

_TRACKER_FIELDS = ("score", "status", "start_date", "end_date", "notes")
_MUSIC_LIBRARY_FILTER_PARAMS = [
    OpenApiParameter("search", OpenApiTypes.STR, description="Filter tracked names."),
    OpenApiParameter("status", OpenApiTypes.STR, many=True, description="Filter tracker status."),
    OpenApiParameter("rating", OpenApiTypes.STR, enum=["all", "rated", "unrated"]),
    OpenApiParameter("rating_min", OpenApiTypes.NUMBER),
    OpenApiParameter("rating_max", OpenApiTypes.NUMBER),
    OpenApiParameter("sort", OpenApiTypes.STR, enum=["title", "score", "date_added", "start_date", "end_date", "release_date"]),
    OpenApiParameter("direction", OpenApiTypes.STR, enum=["asc", "desc"]),
]


def _merged_survivor(model, kind, object_id):
    """Return the artist or album a merged one now lives in, or None."""
    target_id = music_page_repair.merged_into(kind, object_id)
    if not target_id:
        return None
    queryset = model.objects.all()
    if model is Album:
        queryset = queryset.select_related("artist")
    return queryset.filter(id=target_id).first()


def _serialize_tracker(tracker):
    """Return the per-user tracker fields as a plain payload."""
    if tracker is None:
        return None
    return {
        "status": tracker.status,
        "score": str(tracker.score) if tracker.score is not None else None,
        "start_date": tracker.start_date,
        "end_date": tracker.end_date,
        "notes": tracker.notes,
        "created_at": tracker.created_at,
        "updated_at": tracker.updated_at,
    }


def _filtered_music_trackers(request, queryset, *, title_field, release_field=None):
    """Apply the shared library search, status, rating and sort controls."""
    query = request.query_params.get("search", "").strip()
    if query:
        queryset = queryset.filter(**{f"{title_field}__icontains": query})
    statuses = request.query_params.getlist("status")
    if statuses and "no_status" not in statuses:
        queryset = queryset.filter(status__in=statuses)
    elif "no_status" in statuses and len(statuses) == 1:
        queryset = queryset.none()
    rating = request.query_params.get("rating")
    if rating == "rated":
        queryset = queryset.filter(score__isnull=False)
    elif rating == "unrated":
        queryset = queryset.filter(score__isnull=True)
    minimum = request.query_params.get("rating_min")
    maximum = request.query_params.get("rating_max")
    if minimum:
        queryset = queryset.filter(score__gte=minimum)
    if maximum:
        queryset = queryset.filter(score__lte=maximum)
    sort_fields = {
        "title": title_field,
        "score": "score",
        "date_added": "created_at",
        "start_date": "start_date",
        "end_date": "end_date",
    }
    if release_field:
        sort_fields["release_date"] = release_field
    sort = request.query_params.get("sort", "date_added")
    sort_field = sort_fields.get(sort, "created_at")
    direction = "-" if request.query_params.get("direction") == "desc" else ""
    return queryset.order_by(f"{direction}{sort_field}", "pk")


def _serialize_relation(relation):
    """Return one artist membership relation with the caller's tracker."""
    artist = relation["artist"]
    return {
        "id": artist.id,
        "name": artist.name,
        "image": artist.image,
        "role": relation["role"],
        "current": relation["is_current"],
        "tracker": _serialize_tracker(relation["tracker"]),
    }


def _serialize_artist(artist, tracker=None, *, include_albums=False):
    """Return an artist payload with optional tracker and album summaries."""
    payload = {
        "id": artist.id,
        "name": artist.name,
        "musicbrainz_id": artist.musicbrainz_id,
        "image": artist.image,
        "country": artist.country,
        "genres": artist.genres,
        "artist_type": artist.artist_type,
        "discography_synced_at": artist.discography_synced_at,
        "tracker": _serialize_tracker(tracker),
    }
    if include_albums:
        payload["albums"] = [_serialize_album(album) for album in artist.albums.all()]
    return payload


def _serialize_album(album, tracker=None):
    """Return an album payload with optional tracker."""
    return {
        "id": album.id,
        "title": album.title,
        "musicbrainz_release_id": album.musicbrainz_release_id,
        "musicbrainz_release_group_id": album.musicbrainz_release_group_id,
        "artist_id": album.artist_id,
        "artist_name": album.artist.name if album.artist else None,
        "release_date": album.release_date,
        "release_type": album.release_type,
        "image": album.image,
        "genres": album.genres,
        "tracker": _serialize_tracker(tracker),
    }


def _artist_activity(user, artist):
    """Return the caller's plays, date range and collection counts for an artist."""
    from app.helpers import get_artist_collection_stats
    from app.music_views import _music_activity_date_range, _play_counts_by_music_id

    entries = list(
        Music.objects.filter(
            models.Q(album__artist=artist)
            | models.Q(album__artist_credits__artist=artist),
            user=user,
        )
        .select_related("album")
        .distinct(),
    )
    counts = _play_counts_by_music_id(entries)
    first, last, _ = _music_activity_date_range(entries)
    stats = get_artist_collection_stats(user, artist)
    album_plays = {}
    for entry in entries:
        album_plays[entry.album_id] = album_plays.get(entry.album_id, 0) + counts.get(
            entry.id,
            0,
        )
    return {
        "album_plays": album_plays,
        "total_plays": sum(counts.get(entry.id, 0) for entry in entries),
        "first_listened": first,
        "last_listened": last,
        "collection": {
            "albums": stats["collected_albums"],
            "total_albums": stats["total_albums"],
            "tracks": stats["collected_tracks"],
            "total_tracks": stats["total_tracks"],
        },
    }


def _album_activity(user, album):
    """Return the caller's plays, date range and track progress for an album."""
    from app.music_views import _music_activity_date_range, _play_counts_by_music_id

    entries = list(
        Music.objects.filter(user=user, album=album, track__isnull=False),
    )
    counts = _play_counts_by_music_id(entries)
    first, last, _ = _music_activity_date_range(entries)
    return {
        "play_count": sum(counts.get(entry.id, 0) for entry in entries),
        "first_listened": first,
        "last_listened": last,
        "library_track_count": len(entries),
        "total_tracks": Track.objects.filter(album=album).count(),
    }


def _bind_tracker_form(form_class, data, instance, id_field, id_value):
    """Bind a tracker form with partial-update semantics.

    Missing fields fall back to the current instance values so PATCH can
    send only the fields being changed. Forms are bound without `user` so
    scores stay on the raw 0-10 storage scale.
    """
    merged = {id_field: id_value}
    for field in _TRACKER_FIELDS:
        current = getattr(instance, field, None) if instance else None
        merged[field] = current if current is not None else ""
    for field in _TRACKER_FIELDS:
        if field in data:
            merged[field] = data[field] if data[field] is not None else ""
    if not merged.get("status"):
        merged["status"] = Status.IN_PROGRESS.value  # model default
    return form_class(merged, instance=instance)


def _invalidate_music_score_days(user, day_keys, reason):
    """Invalidate music history days after a score change (web parity)."""
    if day_keys:
        history_cache.invalidate_history_days(
            user.id,
            day_keys=day_keys,
            logging_styles=("sessions", "repeats"),
            reason=reason,
        )


# /api/v1/music/artists/
class MusicArtistsView(drf_views.APIView):
    """List tracked artists or start tracking one."""

    @extend_schema(parameters=_MUSIC_LIBRARY_FILTER_PARAMS)
    def get(self, request):
        """Return the caller's artist trackers."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err
        trackers = _filtered_music_trackers(
            request,
            ArtistTracker.objects.filter(user=request.user).select_related("artist"),
            title_field="artist__name",
        )
        results = [_serialize_artist(tracker.artist, tracker) for tracker in trackers]
        return Response(
            paginate_data(request, results, limit, offset),
            status=HTTP.OK,
        )

    def post(self, request):
        """Track an artist by artist_id or musicbrainz_id (mirrors artist_save)."""
        artist = None
        artist_id = request.data.get("artist_id")
        musicbrainz_id = request.data.get("musicbrainz_id")
        if artist_id:
            artist = Artist.objects.filter(id=artist_id).first()
        elif musicbrainz_id:
            try:
                artist = fork_services_music.get_or_create_artist_from_musicbrainz(
                    musicbrainz_id,
                )
            except Exception as e:
                return failure_response(
                    "Could not fetch artist.", e, status=HTTP.BAD_GATEWAY
                )
        else:
            return Response(
                {"detail": "'artist_id' or 'musicbrainz_id' is required."},
                status=HTTP.BAD_REQUEST,
            )
        if artist is None:
            return Response({"detail": "Artist not found."}, status=HTTP.NOT_FOUND)

        tracker = ArtistTracker.objects.filter(
            user=request.user,
            artist=artist,
        ).first()
        form = _bind_tracker_form(
            ArtistTrackerForm,
            request.data,
            tracker,
            "artist_id",
            artist.id,
        )
        if not form.is_valid():
            return Response(
                {"detail": "Invalid tracker data.", "errors": form.errors},
                status=HTTP.BAD_REQUEST,
            )
        tracker = form.save(commit=False)
        tracker.user = request.user
        tracker.artist = artist
        tracker.save()
        return Response(_serialize_artist(artist, tracker), status=HTTP.CREATED)


# /api/v1/music/artists/[artist_id]/
class MusicArtistDetailView(drf_views.APIView):
    """Artist detail with the caller's tracker; PATCH/DELETE act on the tracker."""

    def _get_artist(self, artist_id):
        return Artist.objects.filter(id=artist_id).first()

    def get(self, request, artist_id):
        """Return the artist, tracker, and album summaries.

        Reads what is stored and queues the same background repair as the web
        page; ``repair_pending`` asks the app to load again shortly.
        """
        artist = self._get_artist(artist_id) or _merged_survivor(
            Artist,
            "artist",
            artist_id,
        )
        if artist is None:
            return Response({"detail": "Artist not found."}, status=HTTP.NOT_FOUND)
        repair_pending = music_page_repair.queue_artist_repair(
            artist,
            music_page_repair.artist_repair_reasons(artist),
        )
        tracker = ArtistTracker.objects.filter(
            user=request.user,
            artist=artist,
        ).first()
        payload = _serialize_artist(artist, tracker, include_albums=True)
        activity = _artist_activity(request.user, artist)
        album_plays = activity.pop("album_plays")
        for album in payload["albums"]:
            album["play_count"] = album_plays.get(album["id"], 0)
        payload.update(activity)
        payload["repair_pending"] = repair_pending
        return Response(payload, status=HTTP.OK)

    def patch(self, request, artist_id):
        """Update the caller's tracker (creates one if missing)."""
        artist = self._get_artist(artist_id)
        if artist is None:
            return Response({"detail": "Artist not found."}, status=HTTP.NOT_FOUND)
        tracker = ArtistTracker.objects.filter(
            user=request.user,
            artist=artist,
        ).first()
        old_score = tracker.score if tracker else None
        form = _bind_tracker_form(
            ArtistTrackerForm,
            request.data,
            tracker,
            "artist_id",
            artist.id,
        )
        if not form.is_valid():
            return Response(
                {"detail": "Invalid tracker data.", "errors": form.errors},
                status=HTTP.BAD_REQUEST,
            )
        tracker = form.save(commit=False)
        tracker.user = request.user
        tracker.artist = artist
        tracker.save()
        if tracker.score != old_score:
            _invalidate_music_score_days(
                request.user,
                _collect_music_history_day_keys_for_artist(request.user, artist),
                "artist_score_change",
            )
        return Response(_serialize_artist(artist, tracker), status=HTTP.OK)

    def delete(self, request, artist_id):
        """Remove the caller's tracker (mirrors artist_delete)."""
        artist = self._get_artist(artist_id)
        if artist is None:
            return Response({"detail": "Artist not found."}, status=HTTP.NOT_FOUND)
        tracker = ArtistTracker.objects.filter(
            user=request.user,
            artist=artist,
        ).first()
        if tracker is None:
            return Response(
                {"detail": "Artist is not tracked."},
                status=HTTP.NOT_FOUND,
            )
        tracker.delete()
        return Response(status=HTTP.NO_CONTENT)


# /api/v1/music/artists/[artist_id]/sync-discography/
class MusicArtistSyncView(drf_views.APIView):
    """Force a discography sync for an artist (mirrors the web sync view)."""

    def post(self, _request, artist_id):
        """Sync albums from MusicBrainz; returns the synced album count."""
        artist = Artist.objects.filter(id=artist_id).first()
        if artist is None:
            return Response({"detail": "Artist not found."}, status=HTTP.NOT_FOUND)
        try:
            count = sync_artist_discography(artist, force=True)
        except Exception as e:
            return failure_response(
                "Discography sync failed.", e, status=HTTP.BAD_GATEWAY
            )
        if count:
            dedupe_artist_albums(artist)
        return Response({"synced_albums": count or 0}, status=HTTP.OK)


# /api/v1/music/artists/[artist_id]/plays/
class MusicArtistPlaysView(drf_views.APIView):
    """Delete all of the caller's plays for an artist."""

    def delete(self, request, artist_id):
        """Remove every Music play for the artist's albums."""
        artist = Artist.objects.filter(id=artist_id).first()
        if artist is None:
            return Response({"detail": "Artist not found."}, status=HTTP.NOT_FOUND)
        entries = Music.objects.filter(user=request.user, album__artist=artist)
        count = entries.count()
        entries.delete()
        return Response({"deleted_plays": count}, status=HTTP.OK)


# /api/v1/music/artists/[artist_id]/relations/
class MusicArtistRelationsView(drf_views.APIView):
    """Return the artist's band members and the bands they belong to."""

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    def get(self, request, artist_id):
        """Return normalized membership relations with the caller's trackers."""
        from app.music_views import _build_artist_relations

        artist = Artist.objects.filter(id=artist_id).first()
        if artist is None:
            return Response({"detail": "Artist not found."}, status=HTTP.NOT_FOUND)

        members, also_in, _missing = _build_artist_relations(request.user, artist)
        return Response(
            {
                "artist_id": artist.id,
                "members": [_serialize_relation(relation) for relation in members],
                "also_in": [_serialize_relation(relation) for relation in also_in],
            },
            status=HTTP.OK,
        )


# /api/v1/music/artists/[artist_id]/sync-members/
class MusicArtistMembersSyncView(drf_views.APIView):
    """Force a band-member sync for an artist (mirrors the web lazy sync)."""

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
    def post(self, _request, artist_id):
        """Sync members from MusicBrainz; returns the synced member count."""
        artist = Artist.objects.filter(id=artist_id).first()
        if artist is None:
            return Response({"detail": "Artist not found."}, status=HTTP.NOT_FOUND)
        try:
            count = sync_artist_members(artist, force=True)
        except Exception as e:
            return failure_response(
                "Artist member sync failed.", e, status=HTTP.BAD_GATEWAY
            )
        return Response({"synced_members": count or 0}, status=HTTP.OK)


# /api/v1/music/albums/
class MusicAlbumsView(drf_views.APIView):
    """List tracked albums or start tracking one."""

    @extend_schema(parameters=_MUSIC_LIBRARY_FILTER_PARAMS)
    def get(self, request):
        """Return the caller's album trackers."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err
        trackers = _filtered_music_trackers(
            request,
            AlbumTracker.objects.filter(user=request.user).select_related("album", "album__artist"),
            title_field="album__title",
            release_field="album__release_date",
        )
        results = [_serialize_album(tracker.album, tracker) for tracker in trackers]
        return Response(
            paginate_data(request, results, limit, offset),
            status=HTTP.OK,
        )

    def post(self, request):
        """Track an album by album_id (mirrors album_save)."""
        album_id = request.data.get("album_id")
        if not album_id:
            return Response(
                {"detail": "'album_id' is required."},
                status=HTTP.BAD_REQUEST,
            )
        album = Album.objects.filter(id=album_id).select_related("artist").first()
        if album is None:
            return Response({"detail": "Album not found."}, status=HTTP.NOT_FOUND)

        tracker = AlbumTracker.objects.filter(user=request.user, album=album).first()
        form = _bind_tracker_form(
            AlbumTrackerForm,
            request.data,
            tracker,
            "album_id",
            album.id,
        )
        if not form.is_valid():
            return Response(
                {"detail": "Invalid tracker data.", "errors": form.errors},
                status=HTTP.BAD_REQUEST,
            )
        tracker = form.save(commit=False)
        tracker.user = request.user
        tracker.album = album
        tracker.save()
        return Response(_serialize_album(album, tracker), status=HTTP.CREATED)


# /api/v1/music/albums/[album_id]/
class MusicAlbumDetailView(drf_views.APIView):
    """Album detail with the caller's tracker; PATCH/DELETE act on the tracker."""

    def _get_album(self, album_id):
        return Album.objects.filter(id=album_id).select_related("artist").first()

    def get(self, request, album_id):
        """Return the album and the caller's tracker."""
        album = self._get_album(album_id) or _merged_survivor(Album, "album", album_id)
        if album is None:
            return Response({"detail": "Album not found."}, status=HTTP.NOT_FOUND)
        tracker = AlbumTracker.objects.filter(user=request.user, album=album).first()
        payload = _serialize_album(album, tracker)
        payload.update(_album_activity(request.user, album))
        return Response(payload, status=HTTP.OK)

    def patch(self, request, album_id):
        """Update the caller's tracker (creates one if missing)."""
        album = self._get_album(album_id)
        if album is None:
            return Response({"detail": "Album not found."}, status=HTTP.NOT_FOUND)
        tracker = AlbumTracker.objects.filter(user=request.user, album=album).first()
        old_score = tracker.score if tracker else None
        form = _bind_tracker_form(
            AlbumTrackerForm,
            request.data,
            tracker,
            "album_id",
            album.id,
        )
        if not form.is_valid():
            return Response(
                {"detail": "Invalid tracker data.", "errors": form.errors},
                status=HTTP.BAD_REQUEST,
            )
        tracker = form.save(commit=False)
        tracker.user = request.user
        tracker.album = album
        tracker.save()
        if tracker.score != old_score:
            _invalidate_music_score_days(
                request.user,
                _collect_music_history_day_keys_for_album_ids(
                    request.user,
                    [album.id],
                ),
                "album_score_change",
            )
        return Response(_serialize_album(album, tracker), status=HTTP.OK)

    def delete(self, request, album_id):
        """Remove the caller's tracker (mirrors album_delete)."""
        album = self._get_album(album_id)
        if album is None:
            return Response({"detail": "Album not found."}, status=HTTP.NOT_FOUND)
        tracker = AlbumTracker.objects.filter(user=request.user, album=album).first()
        if tracker is None:
            return Response(
                {"detail": "Album is not tracked."},
                status=HTTP.NOT_FOUND,
            )
        tracker.delete()
        return Response(status=HTTP.NO_CONTENT)


# /api/v1/music/albums/[album_id]/tracks/
class MusicAlbumTracksView(drf_views.APIView):
    """Tracklist for an album with the caller's play state."""

    def get(self, request, album_id):
        """Return catalog tracks plus the user's Music entry per track.

        Reads what is stored and queues the same background repair as the web
        album page; ``pending`` asks the app to load again shortly.
        """
        from app.music_views import _play_counts_by_music_id

        album = Album.objects.filter(id=album_id).first() or _merged_survivor(
            Album,
            "album",
            album_id,
        )
        if album is None:
            return Response({"detail": "Album not found."}, status=HTTP.NOT_FOUND)
        pending = music_page_repair.queue_album_repair(
            album,
            music_page_repair.album_repair_reasons(album, request.user),
            request.user,
        )

        music_by_track = {
            music.track_id: music
            for music in Music.objects.filter(
                user=request.user,
                album=album,
                track__isnull=False,
            )
        }
        # One grouped history query for every played track, not one each.
        play_counts = _play_counts_by_music_id(music_by_track.values())
        results = []
        for track in Track.objects.filter(album=album):
            music = music_by_track.get(track.id)
            results.append(
                {
                    "id": track.id,
                    "title": track.title,
                    "track_number": track.track_number,
                    "disc_number": track.disc_number,
                    "duration_ms": track.duration_ms,
                    "musicbrainz_recording_id": track.musicbrainz_recording_id,
                    "music_id": music.id if music else None,
                    "last_played": music.end_date if music else None,
                    "play_count": play_counts.get(music.id, 0) if music else 0,
                    "score": str(music.score)
                    if music and music.score is not None
                    else None,
                },
            )
        return Response(
            {"results": results, "pending": pending and not results},
            status=HTTP.OK,
        )


# /api/v1/music/albums/[album_id]/plays/
class MusicAlbumPlaysView(drf_views.APIView):
    """Delete all of the caller's plays for an album."""

    def delete(self, request, album_id):
        """Remove every Music play for the album."""
        album = Album.objects.filter(id=album_id).first()
        if album is None:
            return Response({"detail": "Album not found."}, status=HTTP.NOT_FOUND)
        entries = Music.objects.filter(user=request.user, album=album)
        count = entries.count()
        entries.delete()
        return Response({"deleted_plays": count}, status=HTTP.OK)


# /api/v1/music/albums/[album_id]/sync-metadata/
class MusicAlbumSyncView(drf_views.APIView):
    """Force album metadata and tracklist sync (mirrors the web action)."""

    @extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
    def post(self, _request, album_id):
        """Rebuild the album's metadata and tracks from MusicBrainz."""
        album = Album.objects.filter(id=album_id).first()
        if album is None:
            return Response({"detail": "Album not found."}, status=HTTP.NOT_FOUND)

        result = sync_album_metadata(album)
        if result["error"]:
            return Response(
                {"detail": "Album metadata sync failed."},
                status=HTTP.BAD_GATEWAY,
            )
        return Response(
            {
                "album_id": result["album"].id,
                "merged_into": result["merged_into"].id
                if result["merged_into"] is not None
                else None,
                "synced_tracks": result["synced_tracks"],
                "missing_release": result["missing_release"],
            },
            status=HTTP.OK,
        )


# /api/v1/music/songs/plays/
class MusicSongPlayView(drf_views.APIView):
    """Record a listen for a song (mirrors web song_save)."""

    def post(self, request):
        """Add a play for a track on an album."""
        album_id = request.data.get("album_id")
        if not album_id:
            return Response(
                {"detail": "'album_id' is required."},
                status=HTTP.BAD_REQUEST,
            )
        album = Album.objects.filter(id=album_id).select_related("artist").first()
        if album is None:
            return Response({"detail": "Album not found."}, status=HTTP.NOT_FOUND)

        track = None
        track_id = request.data.get("track_id")
        if track_id:
            track = Track.objects.filter(id=track_id, album=album).first()
            if track is None:
                return Response(
                    {"detail": "Track not found on this album."},
                    status=HTTP.NOT_FOUND,
                )

        end_date = None
        raw_end_date = request.data.get("end_date")
        if raw_end_date not in (None, ""):
            try:
                end_date = try_parse_datetime_input(raw_end_date)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Invalid end_date format."},
                    status=HTTP.BAD_REQUEST,
                )

        music = fork_services_music.record_song_play(
            request.user,
            album,
            track=track,
            recording_id=request.data.get("recording_id"),
            end_date=end_date,
        )
        return Response(serialize_data(music), status=HTTP.CREATED)


# /api/v1/music/tracks/[music_id]/score/
class MusicTrackScoreView(drf_views.APIView):
    """Set or clear the score on a Music play row."""

    def patch(self, request, music_id):
        """Update the score (raw 0-10 scale; null clears)."""
        music = Music.objects.filter(id=music_id, user=request.user).first()
        if music is None:
            return Response({"detail": "Music entry not found."}, status=HTTP.NOT_FOUND)

        if "score" not in request.data:
            return Response(
                {"detail": "'score' is required (number or null)."},
                status=HTTP.BAD_REQUEST,
            )
        raw_score = request.data.get("score")
        score = None
        if raw_score is not None:
            try:
                score = Decimal(str(raw_score))
            except (InvalidOperation, TypeError, ValueError):
                return Response({"detail": "Invalid score."}, status=HTTP.BAD_REQUEST)
            if not (Decimal(0) <= score <= Decimal(10)):
                return Response(
                    {"detail": "Score must be between 0 and 10."},
                    status=HTTP.BAD_REQUEST,
                )
        music.score = score
        music.save()
        return Response(
            {"score": str(score) if score is not None else None},
            status=HTTP.OK,
        )


# /api/v1/music/tracks/[track_id]/notes/
class MusicTrackNotesView(drf_views.APIView):
    """Edit private track notes without recording a listen."""

    @extend_schema(request=OpenApiTypes.OBJECT, responses={200: OpenApiTypes.OBJECT})
    def patch(self, request, track_id):
        """Save notes on the caller's Music row for the catalog track."""
        track = (
            Track.objects.select_related("album__artist")
            .filter(id=track_id)
            .first()
        )
        if track is None:
            return Response({"detail": "Track not found."}, status=HTTP.NOT_FOUND)
        if "notes" not in request.data:
            return Response(
                {"detail": "'notes' is required."},
                status=HTTP.BAD_REQUEST,
            )
        notes = request.data.get("notes")
        if notes is not None and not isinstance(notes, str):
            return Response(
                {"detail": "'notes' must be a string or null."},
                status=HTTP.BAD_REQUEST,
            )
        music = fork_services_music.set_track_notes(
            request.user,
            track,
            notes or "",
        )
        return Response(
            {
                "track_id": track.id,
                "notes": music.notes if music is not None else "",
            },
            status=HTTP.OK,
        )


# /api/v1/music/bulk-plays/
class MusicBulkPlaysView(drf_views.APIView):
    """Dispatch a bulk music play range as a background task.

    Mirrors the web music_bulk_save. Disc/track positions map to the task's
    season/episode numbering. Returns 202 with a pollable task_id.
    """

    def post(self, request):
        """Queue bulk plays for an artist or album context."""
        context_kind = (request.data.get("context_kind") or "").strip()
        if context_kind not in ("artist", "album"):
            return Response(
                {"detail": "context_kind must be 'artist' or 'album'."},
                status=HTTP.BAD_REQUEST,
            )
        try:
            context_id = int(request.data.get("context_id"))
        except (TypeError, ValueError):
            return Response(
                {"detail": "Invalid context_id."},
                status=HTTP.BAD_REQUEST,
            )

        start_date = (str(request.data.get("start_date") or "")).strip()
        end_date = (str(request.data.get("end_date") or "")).strip()
        if not start_date or not end_date:
            return Response(
                {"detail": "start_date and end_date are required."},
                status=HTTP.BAD_REQUEST,
            )

        try:
            first_disc = int(request.data["first_disc_number"])
            first_track = int(request.data["first_track_number"])
            last_disc = int(request.data["last_disc_number"])
            last_track = int(request.data["last_track_number"])
        except (KeyError, TypeError, ValueError):
            return Response(
                {"detail": "Invalid track range."},
                status=HTTP.BAD_REQUEST,
            )

        write_mode = request.data.get("write_mode", "add")
        distribution_mode = request.data.get("distribution_mode", "even")
        if write_mode not in ("add", "replace"):
            return Response(
                {"detail": "write_mode must be 'add' or 'replace'."},
                status=HTTP.BAD_REQUEST,
            )
        if distribution_mode not in ("even", "air_date"):
            return Response(
                {"detail": "distribution_mode must be 'even' or 'air_date'."},
                status=HTTP.BAD_REQUEST,
            )

        task = bulk_music_plays_task.apply_async(
            kwargs={
                "user_id": request.user.id,
                "context_kind": context_kind,
                "context_id": context_id,
                "first_season_number": first_disc,
                "first_episode_number": first_track,
                "last_season_number": last_disc,
                "last_episode_number": last_track,
                "write_mode": write_mode,
                "distribution_mode": distribution_mode,
                "start_date_str": start_date,
                "end_date_str": end_date,
            },
            priority=settings.CELERY_TASK_PRIORITY_INTERACTIVE,
        )
        return Response({"task_id": task.id}, status=HTTP.ACCEPTED)


def _track_listens(music):
    """Return a Music row's listens, newest first, in one query.

    Every save of the row writes a history record; a record is a listen when
    its end date is set and differs from the record before it (the web history
    modal hides score and note edits the same way).
    """
    listens = []
    previous_end = None
    first = True
    for history_id, end_date, score in music.history.order_by(
        "history_date",
        "history_id",
    ).values_list("history_id", "end_date", "score"):
        if end_date is not None and (first or end_date != previous_end):
            listens.append(
                {
                    "history_id": history_id,
                    "played_at": end_date,
                    "score": str(score) if score is not None else None,
                },
            )
        previous_end = end_date
        first = False
    listens.reverse()
    return listens


# /api/v1/music/tracks/[track_id]/
class MusicTrackDetailView(drf_views.APIView):
    """A catalog track with its artist and album ids and the caller's state."""

    @_OBJ_READ
    def get(self, request, track_id):
        """Return ids, play count and the caller's score and notes."""
        track = Track.objects.select_related("album__artist").filter(id=track_id).first()
        if track is None:
            return Response({"detail": "Track not found."}, status=HTTP.NOT_FOUND)
        music = Music.objects.filter(user=request.user, track=track).first()
        return Response(
            {
                "id": track.id,
                "title": track.title,
                "track_number": track.track_number,
                "disc_number": track.disc_number,
                "duration_ms": track.duration_ms,
                "album_id": track.album_id,
                "artist_id": track.album.artist_id if track.album else None,
                "music_id": music.id if music else None,
                "score": str(music.score) if music and music.score is not None else None,
                "notes": music.notes if music else "",
                "play_count": len(_track_listens(music)) if music else 0,
            },
            status=HTTP.OK,
        )


# /api/v1/music/tracks/[track_id]/plays/
class MusicTrackPlaysView(drf_views.APIView):
    """The caller's listens of one track."""

    @_OBJ_READ
    def get(self, request, track_id):
        """Return one page of listens newest first (history_id, played_at, score)."""
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err
        if not Track.objects.filter(id=track_id).exists():
            return Response({"detail": "Track not found."}, status=HTTP.NOT_FOUND)
        music = Music.objects.filter(user=request.user, track_id=track_id).first()
        listens = _track_listens(music) if music else []
        page = paginate_data(request, listens, limit, offset)
        return Response({**page, "count": len(listens)}, status=HTTP.OK)


# /api/v1/music/tracks/[track_id]/plays/[history_id]/
class MusicTrackPlayDetailView(drf_views.APIView):
    """Delete one listen of a track."""

    @_OBJ_DELETE
    def delete(self, request, track_id, history_id):
        """Delete the history record when it belongs to the caller's track row."""
        music = Music.objects.filter(user=request.user, track_id=track_id).first()
        if music is None or not music.history.filter(history_id=history_id).exists():
            return Response({"detail": "Listen not found."}, status=HTTP.NOT_FOUND)
        try:
            fork_services_history.delete_history_record_core(
                request.user,
                "music",
                history_id,
            )
        except fork_services_history.HistoryRecordNotFoundError:
            return Response({"detail": "Listen not found."}, status=HTTP.NOT_FOUND)
        except fork_services_history.HistoryDeletionError:
            return Response(
                {"detail": "Could not delete the listen."},
                status=HTTP.INTERNAL_SERVER_ERROR,
            )
        return Response(status=HTTP.NO_CONTENT)


# /api/v1/music/albums/[album_id]/releases/
class MusicAlbumReleasesView(drf_views.APIView):
    """MusicBrainz releases of an album and the caller's chosen release."""

    @_OBJ_READ
    def get(self, request, album_id):
        """Return the releases (optionally filtered by `q`) and the selection."""
        album = Album.objects.filter(id=album_id).first()
        if album is None:
            return Response({"detail": "Album not found."}, status=HTTP.NOT_FOUND)
        releases = []
        if album.musicbrainz_release_group_id:
            try:
                releases = musicbrainz.get_release_group_releases(
                    album.musicbrainz_release_group_id,
                )
            except Exception:
                logger.warning("Failed to list releases for album %s", album.id)
                return Response(
                    {"detail": "Could not load releases."},
                    status=HTTP.BAD_GATEWAY,
                )
        query = (request.GET.get("q") or "").strip().casefold()
        if query:
            releases = [
                release
                for release in releases
                if query
                in " ".join(str(value) for value in release.values()).casefold()
            ]
        preference = MusicReleasePreference.objects.filter(
            user=request.user,
            album=album,
        ).first()
        return Response(
            {
                "results": releases,
                "selected_release_id": preference.release_id if preference else None,
            },
            status=HTTP.OK,
        )

    @_OBJ_WRITE
    def post(self, request, album_id):
        """Choose a release and refresh the tracklist from it."""
        album = Album.objects.filter(id=album_id).first()
        if album is None:
            return Response({"detail": "Album not found."}, status=HTTP.NOT_FOUND)
        release_id = str(request.data.get("release_id") or "").strip()
        if not release_id:
            return Response(
                {"detail": "'release_id' is required."},
                status=HTTP.BAD_REQUEST,
            )
        if not album.musicbrainz_release_group_id:
            return Response(
                {"detail": "This album has no MusicBrainz release group."},
                status=HTTP.BAD_REQUEST,
            )
        try:
            releases = musicbrainz.get_release_group_releases(
                album.musicbrainz_release_group_id,
            )
        except Exception:
            return Response(
                {"detail": "Could not load releases."},
                status=HTTP.BAD_GATEWAY,
            )
        if not any(release.get("release_id") == release_id for release in releases):
            return Response(
                {"detail": "That release is not part of this album."},
                status=HTTP.BAD_REQUEST,
            )
        MusicReleasePreference.objects.update_or_create(
            user=request.user,
            album=album,
            defaults={"release_id": release_id},
        )
        try:
            track_count = sync_album_release_tracks(album, release_id)
        except Exception:
            logger.warning("Failed to sync release %s for album %s", release_id, album.id)
            return Response(
                {"selected_release_id": release_id, "synced": False},
                status=HTTP.OK,
            )
        return Response(
            {
                "selected_release_id": release_id,
                "synced": True,
                "track_count": track_count,
            },
            status=HTTP.OK,
        )
