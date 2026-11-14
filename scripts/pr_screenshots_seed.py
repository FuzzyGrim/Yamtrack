"""Seed a throwaway Floppy database for scripts/pr_screenshots.py.

Runs inside ``manage.py shell`` on whichever branch is being captured, so every
model access tolerates fields and routes that branch does not have yet. Prints
one JSON object mapping a page name to its path; pages a branch lacks are left
out.
"""

# ruff: noqa: T201
import json
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import NoReverseMatch, reverse
from django.utils.text import slugify

from app.models import (
    Album,
    Artist,
    ArtistMember,
    Item,
    MediaTypes,
    Movie,
    Music,
    Podcast,
    PodcastShow,
    PodcastShowTracker,
    Sources,
    Status,
    Track,
)
from lists.models import CustomList, CustomListItem

USERNAME = "shots"
PASSWORD = "shots-pass-12345"  # noqa: S105 - throwaway local database

user, _ = get_user_model().objects.get_or_create(username=USERNAME)
user.set_password(PASSWORD)
user.save()

podcast_list = CustomList.objects.create(name="Podcast status review", owner=user)
podcast_show = PodcastShow.objects.create(podcast_uuid="shots-podcast-show", title="Example Podcast")
podcast_show_item = Item.objects.create(
    media_id=podcast_show.podcast_uuid, source=podcast_show.source,
    media_type=MediaTypes.PODCAST.value, title=podcast_show.title, image=settings.IMG_NONE,
    release_datetime=datetime(2025, 1, 1, tzinfo=UTC),
)
PodcastShowTracker.objects.create(
    user=user, show=podcast_show, status=Status.IN_PROGRESS.value, score=8,
)
CustomListItem.objects.create(custom_list=podcast_list, item=podcast_show_item)
for identity, title, status in (
    ("shots-podcast-playing", "Episode in progress", Status.IN_PROGRESS.value),
    ("shots-podcast-played", "Completed episode", Status.COMPLETED.value),
    ("shots-podcast-untracked", "Untracked episode", None),
):
    item = Item.objects.create(
        media_id=identity, source=podcast_show.source,
        media_type=MediaTypes.PODCAST.value, title=title, image=settings.IMG_NONE,
        release_datetime=datetime(2025, 1, 1, tzinfo=UTC),
    )
    CustomListItem.objects.create(custom_list=podcast_list, item=item)
    if status:
        Podcast.objects.create(user=user, item=item, show=podcast_show, status=status)

now = datetime.now(UTC)
for index, title in enumerate(
    ["The Long Title That Wraps Across Several Lines", "Short", "Third Film"],
):
    item = Item.objects.create(
        media_id=f"shots-movie-{index}",
        source=Sources.MANUAL.value,
        media_type=MediaTypes.MOVIE.value,
        title=title,
        genres=["Drama", "Sci-Fi"],
    )
    Movie.objects.create(
        item=item,
        user=user,
        status=Status.COMPLETED.value,
        score=8,
        end_date=now - timedelta(days=index),
    )

band = Artist.objects.create(name="Shot Band", genres=["Rock"])
for name, role, current in [("Ada", "guitar", True), ("Bo", "drums", False)]:
    member = Artist.objects.create(name=name)
    ArtistMember.objects.create(
        band=band,
        member=member,
        role=role,
        is_current=current,
    )
album = Album.objects.create(title="Shot Album", artist=band, genres=["Rock"])
origins = ["https://soundcloud.com/shot/one", "https://open.spotify.com/track/two", ""]
first_track = None
for number, origin in enumerate(origins, start=1):
    track = Track.objects.create(
        album=album,
        title=f"Track {number}",
        track_number=number,
    )
    first_track = first_track or track
    item = Item.objects.create(
        media_id=f"shots-track-{number}",
        source=Sources.MUSICBRAINZ.value,
        media_type=MediaTypes.MUSIC.value,
        title=track.title,
    )
    extra = {"origin_url": origin} if hasattr(Music, "origin_url") else {}
    Music.objects.create(
        item=item,
        user=user,
        artist=band,
        album=album,
        track=track,
        status=Status.COMPLETED.value,
        end_date=now - timedelta(hours=number),
        **extra,
    )


def resolve(name, **kwargs):
    """Return the path for a route, or None when this branch lacks it."""
    try:
        return reverse(name, kwargs=kwargs)
    except NoReverseMatch:
        return None


artist_kwargs = {"artist_id": band.id, "artist_slug": slugify(band.name)}
album_kwargs = {
    **artist_kwargs,
    "album_id": album.id,
    "album_slug": slugify(album.title),
}
paths = {
    "home": resolve("home"),
    "library": resolve("medialist", media_type="movie"),
    "history": resolve("history"),
    "podcast-list": resolve("list_detail", list_reference=podcast_list.pk),
    "appearance": resolve("appearance"),
    "home-screen-settings": resolve("home_screen"),
    "tile-settings": resolve("tiles"),
    "music-artist": resolve("music_artist_details", **artist_kwargs),
    "music-album": resolve("music_album_details", **album_kwargs),
    "music-track": resolve(
        "music_track_details",
        **album_kwargs,
        track_id=first_track.id,
        track_slug=slugify(first_track.title),
    ),
}
print("SHOTS_PATHS=" + json.dumps({k: v for k, v in paths.items() if v}))
