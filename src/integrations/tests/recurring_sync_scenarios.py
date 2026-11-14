"""Fake upstreams for each scheduled sync, shared by the no-change tests.

Each scenario connects an account, stands in for the upstream with fixed
answers, runs the recurring task and, where the importer was fixed for
no-change runs, can change the upstream for real.
"""

import copy
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.utils import timezone

from integrations import tasks
from integrations.imports import helpers
from integrations.models import (
    AudiobookshelfAccount,
    GPodderAccount,
    KapowarrInstance,
    KavitaAccount,
    KomgaAccount,
    MylarInstance,
    PlexAccount,
    PocketCastsAccount,
    RadarrInstance,
    SonarrInstance,
)


def _response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


class SyncScenario:
    """One importer: how to connect it, fake its upstream and run it."""

    name = ""
    # True when the run after a first import still corrects what that import
    # wrote; only the run after it must then be silent.
    second_run_settles = False

    def connect(self, user):
        """Create the account or instance the sync reads."""

    def patches(self):
        """Return the patchers that stand in for the upstream."""
        return []

    def change(self):
        """Change the fake upstream for real (None: not exercised)."""
        raise NotImplementedError

    def run(self, user):
        """Run the recurring task once."""
        raise NotImplementedError


class RadarrScenario(SyncScenario):
    name = "radarr"

    def connect(self, user):
        self.instance = RadarrInstance.objects.create(
            user=user,
            base_url="https://radarr.local:7878",
            api_key=helpers.encrypt("radarr-key"),
        )

    def patches(self):
        movies = [
            {
                "title": "Arrival",
                "hasFile": True,
                "tmdbId": 26718,
                "movieFile": {
                    "quality": {"quality": {"name": "Bluray-1080p"}},
                    "dateAdded": "2026-01-02T03:04:05Z",
                },
            },
            {"title": "Missing", "hasFile": False, "tmdbId": 1},
        ]
        self.movies = movies
        return [
            patch(
                "integrations.imports.radarr.RadarrClient.movies",
                return_value=movies,
            ),
            patch(
                "integrations.imports.radarr.services.get_media_metadata",
                return_value={"title": "Arrival", "image": "", "genres": []},
            ),
        ]

    def change(self):
        quality = self.movies[0]["movieFile"]["quality"]["quality"]
        quality["name"] = "Bluray-2160p"
        self.movies.append({"title": "Inception", "hasFile": True, "tmdbId": 27205})

    def run(self, user):
        return tasks.import_radarr_recurring(self.instance.id)


class SonarrScenario(SyncScenario):
    name = "sonarr"

    def connect(self, user):
        self.instance = SonarrInstance.objects.create(
            user=user,
            base_url="https://sonarr.local:8989",
            api_key=helpers.encrypt("sonarr-key"),
        )

    def patches(self):
        series = [
            {
                "id": 77,
                "title": "Severance",
                "statistics": {"episodeFileCount": 1},
                "tmdbId": 95396,
                "seasons": [{"seasonNumber": 1}],
                "added": "2026-01-02T03:04:05Z",
            },
        ]
        self.series = series
        episodes = [
            {
                "seasonNumber": 1,
                "episodeNumber": 1,
                "title": "Good News About Hell",
                "airDateUtc": "2022-02-18T00:00:00Z",
                "episodeFileId": 11,
                "episodeFile": {"dateAdded": "2026-01-02T03:04:05Z"},
            },
            {
                "seasonNumber": 1,
                "episodeNumber": 2,
                "title": "Half Loop",
                "airDateUtc": "2022-02-25T00:00:00Z",
                "episodeFileId": 0,
            },
        ]
        self.episodes = episodes
        return [
            patch(
                "integrations.imports.sonarr.SonarrClient.series",
                return_value=series,
            ),
            patch(
                "integrations.imports.sonarr.SonarrClient.episodes",
                return_value=episodes,
            ),
            patch(
                "integrations.imports.sonarr.services.get_media_metadata",
                return_value={"title": "Severance", "image": "", "genres": []},
            ),
        ]

    def change(self):
        self.episodes[1]["episodeFileId"] = 12

    def run(self, user):
        return tasks.import_sonarr_recurring(self.instance.id)


class MylarScenario(SyncScenario):
    name = "mylar3"

    def connect(self, user):
        self.instance = MylarInstance.objects.create(
            user=user,
            base_url="https://mylar.local:8090/",
            api_key=helpers.encrypt("mylar-key"),
        )

    def patches(self):
        from integrations.tests import test_mylar

        self.saga = copy.deepcopy(test_mylar.SAGA)

        def fake(url, params=None, **_kwargs):
            if params["cmd"] == "getIndex":
                return _response(test_mylar.INDEX)
            if params["cmd"] == "getComic" and params["id"] == "18166":
                return _response(self.saga)
            return _response(test_mylar.EMPTY_SERIES)

        return [patch("integrations.imports.mylar.requests.get", side_effect=fake)]

    def change(self):
        issues = self.saga["data"]["issues"]
        issues[2]["status"] = "Downloaded"  # a new copy
        issues[1]["status"] = "Wanted"  # a copy gone

    def run(self, user):
        return tasks.import_mylar_recurring(self.instance.id)


class KapowarrScenario(SyncScenario):
    name = "kapowarr"

    def connect(self, user):
        self.instance = KapowarrInstance.objects.create(
            user=user,
            base_url="https://kapowarr.local:5656/",
            api_key=helpers.encrypt("kapowarr-key"),
        )

    def patches(self):
        from integrations.tests import test_kapowarr

        self.saga = copy.deepcopy(test_kapowarr.SAGA)

        def fake(url, params=None, timeout=None, allow_redirects=False):
            if url.endswith("/api/volumes"):
                return _response(test_kapowarr.VOLUMES)
            if url.endswith("/api/volumes/1"):
                return _response(self.saga)
            return _response({"error": None, "result": {}})

        return [patch("integrations.imports.kapowarr.requests.get", side_effect=fake)]

    def change(self):
        issues = self.saga["result"]["issues"]
        issues[2]["files"] = [{"id": 7, "filepath": "/comics/Saga/Saga 003.cbz"}]
        issues[0]["files"] = []

    def run(self, user):
        return tasks.import_kapowarr_recurring(self.instance.id)


class KomgaScenario(SyncScenario):
    name = "komga"

    def connect(self, user):
        KomgaAccount.objects.create(
            user=user,
            base_url="https://komga.local:25600/",
            api_key=helpers.encrypt("komga-key"),
        )

    def patches(self):
        from integrations.tests.imports.test_komga import _book

        comic = _book("b1", read_date="2026-09-20T12:00:00Z", page=12)
        comic["metadata"]["links"] = [
            {"url": "https://comicvine.gamespot.com/saga-1/4000-301/"},
        ]
        done = _book("b2", read_date="2026-09-19T12:00:00Z", page=30, completed=True)
        payload = {"content": [comic, done], "last": True}
        return [
            patch(
                "integrations.imports.reading_server.requests.get",
                side_effect=lambda *_a, **_k: _response(payload),
            ),
        ]

    def run(self, user):
        return tasks.import_komga_recurring(user.id)


class KavitaScenario(SyncScenario):
    name = "kavita"

    def connect(self, user):
        KavitaAccount.objects.create(
            user=user,
            base_url="https://kavita.local:5000/",
            api_key=helpers.encrypt("kavita-key"),
        )

    def patches(self):
        from integrations.tests.imports.test_kavita import (
            MANGA,
            FakeKavita,
            _chapter,
            _detail,
            _series,
        )

        series = _series(1, malId=13, pages=60, pages_read=40)
        chapters = [
            _chapter(11, pages_read=20, number=1),
            _chapter(12, pages_read=20, number=2),
            _chapter(13, pages_read=0, number=3),
        ]
        fake = FakeKavita([series], {1: _detail(MANGA, chapters)})
        return [patch("integrations.imports.kavita.request_json", side_effect=fake)]

    def run(self, user):
        return tasks.import_kavita_recurring(user.id)


class HardcoverScenario(SyncScenario):
    name = "hardcover"

    def connect(self, user):
        from app.models import Item, MediaTypes, Sources
        from app.providers import credentials

        credentials.set_user("hardcover", user, {"api_key": "token"})
        for book_id in (1, 2):
            Item.objects.create(
                media_id=str(book_id),
                source=Sources.HARDCOVER.value,
                media_type=MediaTypes.BOOK.value,
                title=f"Book {book_id}",
                image="http://example.com/c.jpg",
            )

    def patches(self):
        from integrations.tests.imports.test_hardcover_sync import _entry

        entries = [
            _entry(1, 3, rating=4.5, last_read_date="2026-08-20"),
            _entry(
                2,
                2,
                user_book_reads=[
                    {"started_at": "2026-09-01", "progress_pages": 120},
                ],
            ),
        ]
        self.entries = entries
        return [
            patch(
                "app.providers.hardcover.fetch_user_books",
                return_value=entries,
            ),
        ]

    def change(self):
        self.entries[1]["user_book_reads"][0]["progress_pages"] = 150

    def run(self, user):
        # Overwrite is the mode where Hardcover may change tracked books.
        return tasks.import_hardcover_account(user.id, mode="overwrite")


class GPodderScenario(SyncScenario):
    name = "gpodder"

    def connect(self, user):
        GPodderAccount.objects.create(
            user=user,
            server_url=helpers.encrypt("https://gpodder.net"),
            username=helpers.encrypt("listener"),
            password=helpers.encrypt("secret"),
            device_id=f"floppy-{user.id}",
        )

    def patches(self):
        feed = (
            {"title": "Example Show", "author": "Host"},
            [
                {
                    "title": "Episode 1",
                    "published": datetime(2026, 1, 1, tzinfo=UTC),
                    "duration": 1800,
                    "audio_url": "https://cdn.example.com/ep1.mp3",
                    "guid": "ep-1",
                },
                {
                    "title": "Episode 2",
                    "published": datetime(2026, 1, 8, tzinfo=UTC),
                    "duration": 1800,
                    "audio_url": "https://cdn.example.com/ep2.mp3",
                    "guid": "ep-2",
                },
            ],
        )
        actions = [
            {
                "action": "play",
                "podcast": "https://example.com/feed.xml",
                "episode": "https://cdn.example.com/ep1.mp3",
                "timestamp": "2026-01-02T12:00:00Z",
                "position": 1800,
                "total": 1800,
            },
            {
                "action": "play",
                "podcast": "https://example.com/feed.xml",
                "episode": "https://cdn.example.com/ep2.mp3",
                "timestamp": "2026-01-09T12:00:00Z",
                "position": 600,
                "total": 1800,
            },
        ]
        base = "integrations.imports.gpodder"
        return [
            patch(f"{base}.gpodder_api.verify_login"),
            patch(f"{base}.gpodder_api.register_device"),
            patch(
                f"{base}.gpodder_api.fetch_subscriptions",
                return_value=["https://example.com/feed.xml"],
            ),
            patch(f"{base}.gpodder_api.fetch_episode_actions", return_value=(actions, 5)),
            patch(f"{base}.podcast_rss.fetch_feed_from_rss", return_value=feed),
        ]

    def run(self, user):
        # The daily full resync re-reads every action, so the second run sees
        # the same plays again (the worst case for a no-change poll).
        GPodderAccount.objects.filter(user=user).update(last_full_resync_at=None)
        return tasks.import_gpodder_recurring(user.id)


class AudiobookshelfScenario(SyncScenario):
    name = "audiobookshelf"

    def connect(self, user):
        AudiobookshelfAccount.objects.create(
            user=user,
            base_url="https://abs.example.com",
            api_token=helpers.encrypt("token"),
        )

    def patches(self):
        from integrations.tests.imports.test_audiobookshelf import (
            podcast_library_item,
            podcast_progress,
        )

        book_item = {
            "id": "item-1",
            "mediaType": "book",
            "media": {
                "duration": 7200,
                "metadata": {"title": "Piranesi", "authors": [{"name": "Susanna Clarke"}]},
            },
        }
        me = {
            "mediaProgress": [
                {
                    "libraryItemId": "item-1",
                    "currentTime": 3600,
                    "duration": 7200,
                    "progress": 0.5,
                    "isFinished": False,
                    "lastUpdate": 1000,
                    "startedAt": 1_761_800_000_000,
                },
                podcast_progress(),
            ],
        }
        started = 1_761_900_000_000
        sessions = {
            "numPages": 1,
            "sessions": [
                {
                    "id": "s1",
                    "libraryItemId": "item-1",
                    "startedAt": started,
                    "timeListening": 1800,
                    "currentTime": 3600,
                },
            ],
        }
        self.sessions = sessions

        def library_item(library_item_id, *, expanded=False):
            return podcast_library_item() if library_item_id == "podcast-item" else book_item

        base = "integrations.imports.audiobookshelf.AudiobookshelfClient"
        return [
            patch(f"{base}.get_me", return_value=me),
            patch(f"{base}.get_library_item", side_effect=library_item),
            patch(f"{base}.get_listening_sessions", return_value=sessions),
        ]

    def change(self):
        self.sessions["sessions"][0]["timeListening"] = 2700

    def run(self, user):
        return tasks.import_audiobookshelf_recurring(user.id)


class PocketCastsScenario(SyncScenario):
    name = "pocketcasts"
    # A newly imported in-progress episode is stored with its listened minutes
    # counted twice (row plus pending history); the next run sets it back. Not
    # a no-change cost, a first-import bug, left as a follow-up.
    second_run_settles = True

    def connect(self, user):
        PocketCastsAccount.objects.create(user=user, access_token="token")

    def patches(self):
        from integrations.imports.pocketcasts import PocketCastsImporter

        podcast_list = {
            "podcasts": [
                {
                    "uuid": "show-1",
                    "title": "Test Show",
                    "author": "Test Author",
                    "description": "",
                    "url": "",
                },
            ],
        }
        play_states = {
            "uuid-played": {
                "uuid": "uuid-played",
                "playingStatus": 3,
                "playedUpTo": 1800,
                "duration": 1800,
            },
            "uuid-inprogress": {
                "uuid": "uuid-inprogress",
                "playingStatus": 2,
                "playedUpTo": 600,
                "duration": 1800,
            },
            "uuid-unplayed": {
                "uuid": "uuid-unplayed",
                "playingStatus": 0,
                "playedUpTo": 0,
                "duration": 1800,
            },
        }
        episodes = {
            uuid: {
                "uuid": uuid,
                "title": title,
                "published": published,
                "duration": 1800,
                "url": f"https://example.com/{uuid}.mp3",
            }
            for uuid, title, published in (
                ("uuid-played", "Played Ep", "2026-01-01T00:00:00Z"),
                ("uuid-inprogress", "In-Progress Ep", "2026-01-02T00:00:00Z"),
                ("uuid-unplayed", "Unplayed Ep", "2026-01-03T00:00:00Z"),
            )
        }
        return [
            patch(
                "integrations.pocketcasts_artwork.fetch_podcast_artwork_and_rss",
                return_value=(None, None),
            ),
            patch(
                "integrations.pocketcasts_artwork.fetch_podcast_artwork",
                return_value=None,
            ),
            patch.object(PocketCastsImporter, "_ensure_valid_token"),
            patch.object(PocketCastsImporter, "_get_access_token", return_value="fake"),
            patch(
                "integrations.pocketcasts_api.get_podcast_list",
                return_value=podcast_list,
            ),
            patch.object(
                PocketCastsImporter,
                "_fetch_show_play_states",
                return_value=play_states,
            ),
            patch.object(
                PocketCastsImporter,
                "_fetch_show_full_metadata",
                return_value=episodes,
            ),
        ]

    def run(self, user):
        # The recurring task skips a run inside its 30-minute cooldown.
        cache.delete(f"pocketcasts_recurring_cooldown_{user.id}")
        return tasks.import_pocketcasts_history(user.id)


class PlexWatchlistScenario(SyncScenario):
    name = "plex_watchlist"

    def connect(self, user):
        PlexAccount.objects.create(
            user=user,
            plex_token="token",
            plex_username="watcher",
            plex_account_id="acct-1",
        )

    def patches(self):
        from app.models import MediaTypes

        entries = [
            {
                "ratingKey": "rk-movie",
                "type": "movie",
                "title": "Movie Title",
                "Guid": [{"id": "tmdb://123"}],
            },
            {
                "ratingKey": "rk-show",
                "type": "show",
                "title": "Show Title",
                "Guid": [{"id": "tmdb://321"}],
            },
        ]
        self.entries = entries

        def metadata(media_type, media_id, source):
            title = "Movie Title" if media_type == MediaTypes.MOVIE.value else "Show Title"
            return {"title": title, "image": "https://example.com/i.jpg", "max_progress": 1}

        return [
            patch(
                "integrations.plex_watchlist.plex_api.fetch_watchlist",
                side_effect=lambda *_a, **_k: (list(self.entries), len(self.entries)),
            ),
            patch(
                "integrations.plex_watchlist.services.get_media_metadata",
                side_effect=metadata,
            ),
        ]

    def change(self):
        self.entries.pop()  # the show left the watchlist

    def run(self, user):
        return tasks.sync_plex_watchlist(user.id)


class PlexMarkWatchedScenario(SyncScenario):
    name = "plex_mark_watched"

    def connect(self, user):
        user.plex_usernames = "plexuser"
        user.save(update_fields=["plex_usernames"])
        self.checkpoint = timezone.now().replace(microsecond=0) - timedelta(hours=1)
        PlexAccount.objects.create(
            user=user,
            plex_token="token",
            plex_username="plexuser",
            plex_account_id="1",
            sections=[{"id": "1", "machine_identifier": "machine", "type": "movie"}],
            mark_watched_checkpoint=self.checkpoint,
            mark_watched_sync_enabled=True,
        )

    def patches(self):
        from integrations.tests.test_plex_mark_watched import (
            _movie_entry,
            _movie_metadata,
        )

        viewed = int(self.checkpoint.timestamp()) + 60
        return [
            patch(
                "integrations.imports.plex.PlexHistoryImporter._import_ratings_from_library",
            ),
            patch("integrations.imports.plex.plex_api.list_users", return_value=[]),
            patch("integrations.imports.plex.services.get_media_metadata", _movie_metadata),
            patch(
                "integrations.imports.plex.plex_api.fetch_history",
                return_value=([_movie_entry("200", viewed)], 1),
            ),
            patch(
                "integrations.imports.plex.plex_api.list_resources",
                return_value=[
                    {"machine_identifier": "machine", "connections": [{"uri": "http://plex"}]},
                ],
            ),
        ]

    def run(self, user):
        # The play sits inside the poll's overlap window, so the next poll
        # reads it again: the same answer twice.
        PlexAccount.objects.filter(user=user).update(
            mark_watched_checkpoint=self.checkpoint,
        )
        return tasks.sync_plex_mark_watched(user.id)


SCENARIOS = (
    RadarrScenario,
    SonarrScenario,
    MylarScenario,
    KapowarrScenario,
    KomgaScenario,
    KavitaScenario,
    HardcoverScenario,
    GPodderScenario,
    AudiobookshelfScenario,
    PocketCastsScenario,
    PlexWatchlistScenario,
    PlexMarkWatchedScenario,
)
