"""Long-catalogue fixtures for the detail-page census (assignment 49).

Each ``build_*`` function creates one catalogue of a given length and, for the
``tracked`` user, a library that has watched or played every child with one
repeat. Rows are named from ``prefix`` and ``size`` so callers find them by
name, never by position. Inserts are bulk, so the extreme sizes stay quick.

``FakeTmdb`` answers ``services.api_request`` for TMDB show, season and person
URLs from a synthetic catalogue and counts every call, so pages go through the
real provider code without the network.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from simple_history.utils import bulk_create_with_history

from app.models import (
    TV,
    Album,
    Artist,
    ArtistTracker,
    CollectionEntry,
    Episode,
    Item,
    ItemTag,
    MediaTypes,
    Movie,
    Music,
    Season,
    Sources,
    Status,
    Tag,
    Track,
)
from lists.models import CustomList, CustomListItem

BASE = datetime(2020, 1, 1, 12, 0, tzinfo=UTC)
EPISODES_PER_SEASON = 10
TRACKS_PER_ALBUM = 5
# TMDB ids for synthetic shows, people and movies; far above anything the
# rest of the suite creates.
SHOW_ID_BASE = 9_100_000
PERSON_ID_BASE = 9_200_000
MOVIE_ID_BASE = 9_300_000


@dataclass
class FakeTmdb:
    """Answer TMDB requests for synthetic shows and people; count each call."""

    shows: dict[int, dict[int, int]] = field(default_factory=dict)
    people: dict[int, list[int]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    # The real ``services.api_request``: answers every other provider the way
    # an offline host does (its network layer is patched to fail).
    passthrough: object = None

    def add_show(self, show_id, episodes_by_season):
        self.shows[int(show_id)] = dict(episodes_by_season)

    def add_person(self, person_id, movie_ids):
        self.people[int(person_id)] = list(movie_ids)

    def __call__(self, provider, method, url, params=None, **kwargs):
        self.calls.append(url)
        if provider != Sources.TMDB.value:
            return self.passthrough(provider, method, url, params=params, **kwargs)
        if match := re.search(r"/tv/(\d+)$", url):
            return self._tv(int(match[1]), (params or {}).get("append_to_response", ""))
        if match := re.search(r"/person/(\d+)$", url):
            return self._person(int(match[1]))
        return self.passthrough(provider, method, url, params=params, **kwargs)

    def _tv(self, show_id, appends):
        seasons = self.shows[show_id]
        total = sum(seasons.values())
        response = {
            "id": show_id,
            "name": f"Census Show {show_id}",
            "original_name": f"Census Show {show_id}",
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "overview": "A synthetic show.",
            "genres": [{"id": 18, "name": "Drama"}],
            "vote_average": 7.5,
            "vote_count": 100,
            "first_air_date": "2000-01-01",
            "last_air_date": "2020-01-01",
            "status": "Ended",
            "number_of_seasons": len(seasons),
            "number_of_episodes": total,
            "episode_run_time": [45],
            "production_companies": [],
            "production_countries": [],
            "spoken_languages": [],
            "created_by": [],
            "aggregate_credits": {"cast": [], "crew": []},
            "external_ids": {"tvdb_id": show_id},
            "recommendations": {"results": []},
            "watch/providers": {"results": {}},
            "last_episode_to_air": None,
            "next_episode_to_air": None,
            "seasons": [
                {
                    "season_number": number,
                    "name": f"Season {number}",
                    "poster_path": None,
                    "air_date": f"{2000 + number % 20}-01-01",
                    "episode_count": count,
                    "overview": "",
                    "vote_average": 7.0,
                    "vote_count": 10,
                }
                for number, count in seasons.items()
            ],
        }
        for append in appends.split(","):
            match = re.fullmatch(r"season/(\d+)", append)
            if match and int(match[1]) in seasons:
                number = int(match[1])
                response[append] = self._season(number, seasons[number])
        return response

    @staticmethod
    def _season(number, count):
        return {
            "name": f"Season {number}",
            "poster_path": None,
            "season_number": number,
            "overview": "",
            "vote_average": 7.0,
            "air_date": "2000-01-01",
            "episodes": [
                {
                    "id": number * 100_000 + episode,
                    "episode_number": episode,
                    "season_number": number,
                    "name": f"Episode {episode}",
                    "overview": "",
                    "air_date": (
                        BASE.date() - timedelta(days=count - episode)
                    ).isoformat(),
                    "runtime": 45,
                    "vote_average": 7.0,
                    "vote_count": 5,
                    "still_path": None,
                    "crew": [],
                    "guest_stars": [],
                }
                for episode in range(1, count + 1)
            ],
        }

    def _person(self, person_id):
        return {
            "id": person_id,
            "name": f"Census Person {person_id}",
            "profile_path": None,
            "biography": "",
            "known_for_department": "Acting",
            "gender": 2,
            "birthday": "1970-01-01",
            "deathday": None,
            "place_of_birth": "",
            "external_ids": {},
            "combined_credits": {
                "cast": [
                    {
                        "id": movie_id,
                        "media_type": "movie",
                        "title": f"Census Movie {movie_id}",
                        "poster_path": "/m.jpg",
                        "release_date": "2010-01-01",
                        "character": "Lead",
                        "vote_average": 6.0,
                        "vote_count": 50,
                        "popularity": 1.0,
                    }
                    for movie_id in self.people[person_id]
                ],
                "crew": [],
            },
        }


def _plays(user, model, items, *, repeats=2, **fields):
    """Bulk-create ``repeats`` completed entries per item, with history."""
    rows = [
        model(
            item=item,
            user=user,
            status=Status.COMPLETED.value,
            progress=1,
            end_date=BASE - timedelta(days=index * repeats + repeat),
            **fields,
        )
        for index, item in enumerate(items)
        for repeat in range(repeats)
    ]
    bulk_create_with_history(rows, model, default_user=user, batch_size=1000)


def movie_items(prefix, size):
    """Return ``size`` TMDB movie items named ``{prefix} Movie NNNNN``."""
    first = MOVIE_ID_BASE + zlib.crc32(prefix.encode()) % 10_000 * 100_000
    Item.objects.bulk_create(
        Item(
            media_id=str(first + index),
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title=f"{prefix} Movie {index:05d}",
            image="https://image.tmdb.org/t/p/w500/m.jpg",
            release_datetime=BASE - timedelta(days=index),
        )
        for index in range(size)
    )
    return list(
        Item.objects.filter(title__startswith=f"{prefix} Movie ").order_by("title")
    )


def build_tv_show(fake, tracked, *, seasons, episodes=EPISODES_PER_SEASON, prefix):
    """A TMDB show with ``seasons`` seasons; ``tracked`` watched all of it twice."""
    show_id = SHOW_ID_BASE + zlib.crc32(prefix.encode()) % 10_000 * 100
    fake.add_show(show_id, dict.fromkeys(range(1, seasons + 1), episodes))
    media_id = str(show_id)
    title = f"Census Show {show_id}"
    show = Item.objects.create(
        media_id=media_id,
        source=Sources.TMDB.value,
        media_type=MediaTypes.TV.value,
        title=title,
        image="https://image.tmdb.org/t/p/w500/poster.jpg",
    )
    Item.objects.bulk_create(
        Item(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title=title,
            season_number=number,
            image="https://image.tmdb.org/t/p/w500/poster.jpg",
        )
        for number in range(1, seasons + 1)
    )
    Item.objects.bulk_create(
        Item(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title=title,
            season_number=number,
            episode_number=episode,
            runtime_minutes=45,
            image="https://image.tmdb.org/t/p/w500/still.jpg",
        )
        for number in range(1, seasons + 1)
        for episode in range(1, episodes + 1)
    )
    if tracked is not None:
        tv = TV.objects.create(item=show, user=tracked, status=Status.COMPLETED.value)
        season_items = Item.objects.filter(
            media_id=media_id, media_type=MediaTypes.SEASON.value
        ).order_by("season_number")
        # Creating the show may already add its seasons; add the rest.
        existing = set(
            Season.objects.filter(related_tv=tv).values_list("item_id", flat=True)
        )
        bulk_create_with_history(
            [
                Season(
                    item=item,
                    user=tracked,
                    related_tv=tv,
                    status=Status.COMPLETED.value,
                )
                for item in season_items
                if item.id not in existing
            ],
            Season,
            default_user=tracked,
        )
        Season.objects.filter(related_tv=tv).update(status=Status.COMPLETED.value)
        season_by_number = {
            season.item.season_number: season
            for season in Season.objects.filter(related_tv=tv).select_related("item")
        }
        episode_items = Item.objects.filter(
            media_id=media_id, media_type=MediaTypes.EPISODE.value
        ).order_by("season_number", "episode_number")
        rows = [
            Episode(
                related_season=season_by_number[item.season_number],
                item=item,
                status=Status.COMPLETED.value,
                end_date=BASE - timedelta(days=index * 2 + repeat),
            )
            for index, item in enumerate(episode_items)
            for repeat in range(2)
        ]
        bulk_create_with_history(rows, Episode, default_user=tracked, batch_size=1000)
    return show


def build_artist(tracked, *, albums, prefix):
    """An artist with ``albums`` albums of five tracks; ``tracked`` played each twice."""
    artist = Artist.objects.create(name=f"{prefix} Artist")
    Album.objects.bulk_create(
        Album(
            title=f"{prefix} Album {index:04d}",
            artist=artist,
            tracks_populated=True,
            release_type="Album",
            release_date=(BASE - timedelta(days=index * 30)).date(),
        )
        for index in range(albums)
    )
    album_rows = list(Album.objects.filter(artist=artist).order_by("title"))
    Track.objects.bulk_create(
        Track(
            album=album,
            title=f"{album.title} Track {number}",
            track_number=number,
            disc_number=1,
            duration_ms=200_000,
            musicbrainz_recording_id=f"{prefix}-{album.id}-{number}",
        )
        for album in album_rows
        for number in range(1, TRACKS_PER_ALBUM + 1)
    )
    if tracked is None:
        return artist
    ArtistTracker.objects.create(user=tracked, artist=artist)
    tracks = list(Track.objects.filter(album__artist=artist).order_by("id"))
    Item.objects.bulk_create(
        Item(
            media_id=track.musicbrainz_recording_id,
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            title=track.title,
        )
        for track in tracks
    )
    items = {
        item.media_id: item
        for item in Item.objects.filter(
            media_type=MediaTypes.MUSIC.value, media_id__startswith=f"{prefix}-"
        )
    }
    rows = [
        Music(
            item=items[track.musicbrainz_recording_id],
            user=tracked,
            artist=artist,
            album_id=track.album_id,
            track=track,
            status=Status.COMPLETED.value,
            progress=2,
            end_date=BASE - timedelta(minutes=index),
        )
        for index, track in enumerate(tracks)
    ]
    bulk_create_with_history(rows, Music, default_user=tracked, batch_size=1000)
    # The second play of each track, as an extra history row.
    entries = Music.objects.filter(user=tracked, artist=artist)
    historical = Music.history.model
    historical.objects.bulk_create(
        [
            historical(
                id=entry.id,
                artist_id=artist.id,
                album_id=entry.album_id,
                track_id=entry.track_id,
                status=entry.status,
                progress=entry.progress,
                end_date=entry.end_date + timedelta(days=1),
                history_date=entry.end_date + timedelta(days=1),
                history_type="~",
                history_user=tracked,
            )
            for entry in entries
        ],
        batch_size=1000,
    )
    return artist


def build_person(fake, tracked, *, credit_count, prefix):
    """A TMDB person with ``credit_count`` movie credits; ``tracked`` watched each twice."""
    person_id = PERSON_ID_BASE + zlib.crc32(prefix.encode()) % 10_000
    items = movie_items(prefix, credit_count)
    fake.add_person(person_id, [int(item.media_id) for item in items])
    if tracked is not None:
        _plays(tracked, Movie, items)
    return person_id


def build_list(owner, tracked, *, items, prefix):
    """A list of ``items`` movies owned by ``owner``; ``tracked`` watched each twice."""
    movies = movie_items(prefix, items)
    custom_list = CustomList.objects.create(name=f"{prefix} List", owner=owner)
    CustomListItem.objects.bulk_create(
        [
            CustomListItem(custom_list=custom_list, item=item, added_by=owner)
            for item in movies
        ],
        batch_size=1000,
    )
    if tracked is not None:
        _plays(tracked, Movie, movies)
    return custom_list


def build_collection(owner, tracked, *, entries, prefix):
    """``entries`` collected movies for ``owner``; ``tracked`` also watched each twice."""
    movies = movie_items(prefix, entries)
    CollectionEntry.objects.bulk_create(
        [
            CollectionEntry(
                user=owner,
                item=item,
                media_type="bluray",
                resolution="1080p",
            )
            for item in movies
        ],
        batch_size=1000,
    )
    if tracked is not None:
        _plays(tracked, Movie, movies)
    return movies


def build_tag(owner, tracked, *, entries, prefix):
    """One tag on ``entries`` movies for ``owner``; ``tracked`` also watched each twice."""
    movies = movie_items(prefix, entries)
    tag = Tag.objects.create(user=owner, name=f"{prefix} tag")
    ItemTag.objects.bulk_create(
        [ItemTag(tag=tag, item=item) for item in movies], batch_size=1000
    )
    if tracked is not None:
        _plays(tracked, Movie, movies)
    return tag
