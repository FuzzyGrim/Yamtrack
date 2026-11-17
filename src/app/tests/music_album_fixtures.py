"""Build long albums with played tracks for query-count tests and benchmarks.

``build_long_album(user, tracks=2000, plays_per_track=10)`` creates an album
whose tracks span several discs, a Music entry per played track, and that many
history rows per entry, all with bulk inserts so even the 2,000-track case is
quick. No provider is touched: the album has no MusicBrainz ids, so the album
page renders it without any repair or lookup step.
"""

from datetime import UTC, datetime, timedelta

from simple_history.utils import bulk_create_with_history

from app.models import Album, Artist, Item, MediaTypes, Music, Sources, Status, Track

TRACKS_PER_DISC = 100


def build_long_album(
    user,
    *,
    tracks,
    plays_per_track=0,
    title="Long Album",
    musicbrainz_release_id="",
):
    """Return ``(artist, album)`` with ``tracks`` tracks, each played N times."""
    artist = Artist.objects.create(name=f"{title} Artist")
    album = Album.objects.create(
        title=title,
        artist=artist,
        musicbrainz_release_id=musicbrainz_release_id,
        tracks_populated=True,
        release_type="Album",
    )
    track_rows = Track.objects.bulk_create(
        Track(
            album=album,
            title=f"{title} Track {index + 1:04d}",
            track_number=index % TRACKS_PER_DISC + 1,
            disc_number=index // TRACKS_PER_DISC + 1,
            duration_ms=180_000 + index,
            musicbrainz_recording_id=f"{album.id}-recording-{index + 1}",
        )
        for index in range(tracks)
    )
    if not plays_per_track:
        return artist, album

    track_rows = list(Track.objects.filter(album=album).order_by("id"))
    items = Item.objects.bulk_create(
        Item(
            media_id=track.musicbrainz_recording_id,
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            title=track.title,
        )
        for track in track_rows
    )
    items = {
        item.media_id: item
        for item in Item.objects.filter(
            media_id__in=[track.musicbrainz_recording_id for track in track_rows],
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
        )
    }
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    entries = [
        Music(
            item=items[track.musicbrainz_recording_id],
            user=user,
            artist=artist,
            album=album,
            track=track,
            status=Status.COMPLETED.value,
            progress=plays_per_track,
            start_date=base + timedelta(minutes=index),
            end_date=base + timedelta(minutes=index),
        )
        for index, track in enumerate(track_rows)
    ]
    # One history row per entry (the first play) ...
    bulk_create_with_history(entries, Music, default_user=user)
    entries = list(Music.objects.filter(user=user, album=album).order_by("id"))
    # ... and the remaining plays as extra history rows, one day apart.
    historical = Music.history.model
    extra = []
    for entry in entries:
        for play in range(1, plays_per_track):
            played = entry.end_date + timedelta(days=play)
            extra.append(
                historical(
                    id=entry.id,
                    artist_id=artist.id,
                    album_id=album.id,
                    track_id=entry.track_id,
                    status=entry.status,
                    progress=entry.progress,
                    start_date=played,
                    end_date=played,
                    history_date=played,
                    history_type="~",
                    history_user=user,
                ),
            )
    historical.objects.bulk_create(extra, batch_size=2000)
    return artist, album
