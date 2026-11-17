"""A read page must not need SQLite's single write lock.

SQLite has one writer. With ``transaction_mode=IMMEDIATE`` any statement that
is not a read, and any ``transaction.atomic()`` block even if it only reads,
queues behind whichever import or background job holds that lock. A page load
that does so can wait out the whole busy timeout.

Each test requests one page and compares the application code that issued a
non-read statement against ``ALLOWED_WRITES``. A new write on a read page
fails here. Fix the page (move the write to the action that causes it, or to
a task) before reaching for the allowlist; an entry needs a reason a reviewer
would accept.

This runs on the suite's SQLite database, which shows which statements a page
issues. It does not show waiting: see ``test_page_loads_with_writer_busy``.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

import requests
from django.core.cache import cache
from django.db import connection
from django.test import TestCase

from app.benchmark_data import EPOCH, build_dataset
from app.models import (
    Album,
    AlbumArtist,
    ApplicationSettings,
    Artist,
    DiscoverRowCache,
    DiscoverTasteProfile,
    Item,
    MediaTypes,
    Track,
)
from app.templatetags.app_tags import (
    music_album_url,
    music_artist_url,
    music_track_url,
)
from app.tests.sqlite_writes import record_writes
from integrations.models import IntegrationToken
from users.home_screen import ensure_home_screen_rows
from users.models import HomeScreenRow, User

# Page -> application code that may issue a non-read statement there, each
# with its reason. A key is "file:function", or "file:*" for a whole module.
_DISCOVER_SAVES = {
    "app/discover/cache_repo.py:set_taste_profile": (
        "Saves the taste profile the page just computed. Does not wait for a "
        "busy writer on a page load."
    ),
    "app/discover/cache_repo.py:set_row_cache": (
        "Saves a row the page just built. Does not wait for a busy writer on "
        "a page load."
    ),
}
ALLOWED_WRITES: dict[str, dict[str, str]] = {
    "discover": _DISCOVER_SAVES,
    "statistics": {
        "app/statistics_sync.py:*": (
            "A cold Statistics page builds its missing days inline under a "
            "lease, by design: docs/architecture/statistics-sync.md."
        ),
    },
    "home_first_visit": {
        "users/home_screen.py:ensure_home_screen_rows": (
            "Creates a person's default Home rows once, on their first visit."
        ),
    },
    "account_settings": {
        "users/models.py:get_or_create_authenticator_secret": (
            "Creates the two-factor secret once, the first time the account "
            "page offers to set it up."
        ),
    },
    "saved_choice": {
        "users/models.py:update_preference": (
            "The request carries a new sort, layout or tab and the page "
            "remembers it. Writes only when the choice changed."
        ),
    },
    "api_token": {
        "api/authentication.py:_touch_last_used": (
            "Records token use at most once per five minutes."
        ),
    },
}


def _allowed(source: str, allowed: dict[str, str]) -> bool:
    return source in allowed or f"{source.split(':', maxsplit=1)[0]}:*" in allowed


@skipUnless(
    connection.vendor == "sqlite",
    "Counts SQLite write-lock statements; PostgreSQL has no single writer.",
)
class ReadPagesDoNotWriteTests(TestCase):
    """Request read pages in cold and warm states and list what they write."""

    @classmethod
    def setUpClass(cls):
        """Freeze time at the fixture's epoch and keep providers and tasks out."""
        cls.provider_request = patch(
            "requests.sessions.Session.request",
            side_effect=requests.ConnectionError("offline in this test"),
        )
        for patcher in (
            patch("django.utils.timezone.now", return_value=EPOCH),
            # No provider is reachable, as on an offline host.
            cls.provider_request,
            # MusicBrainz's module-level requests.get (cover art, biographies).
            patch(
                "app.providers.musicbrainz.requests.get",
                side_effect=requests.ConnectionError("offline in this test"),
            ),
            # Tests run tasks inline; a queued task is not part of the page.
            patch("celery.app.task.Task.apply_async", return_value=None),
        ):
            mock = patcher.start()
            if patcher is cls.provider_request:
                cls.provider_request = mock
            cls.addClassCleanup(patcher.stop)
        super().setUpClass()

    @classmethod
    def setUpTestData(cls):
        """Build a small mixed library for one user."""
        build_dataset(100, 1729)
        # Every install has this row; the image cache creates it on first use.
        ApplicationSettings.objects.get_or_create(pk=1)
        cls.user = User.objects.get(username="bench-user-1")
        ensure_home_screen_rows(cls.user)
        cls.row_ids = list(
            HomeScreenRow.objects.filter(user=cls.user).values_list("id", flat=True)
        )
        cls.movie = Item.objects.filter(media_type=MediaTypes.MOVIE.value).first()

    def setUp(self):
        """Sign in and start from an empty cache."""
        cache.clear()
        self.client.force_login(self.user)

    def _writes(self, path, **headers):
        with record_writes() as writes:
            response = self.client.get(path, headers=headers or None)
        return response, writes

    def _check(
        self,
        page,
        path,
        *,
        states=("cold", "warm"),
        no_provider_calls=False,
        **headers,
    ):
        allowed = ALLOWED_WRITES.get(page, {})
        for state in states:
            if state == "cold":
                cache.clear()
            self.provider_request.reset_mock()
            response, writes = self._writes(path, **headers)
            if no_provider_calls:
                self.assertEqual(
                    self.provider_request.call_count,
                    0,
                    f"{page} ({state}) {path} called a provider while rendering.",
                )
            self.assertLess(
                response.status_code, 400, f"{page} ({state}) {path} did not load"
            )
            unexpected = sorted(
                {
                    f"{write.source}: {write.keyword}"
                    for write in writes
                    if not _allowed(write.source, allowed)
                }
            )
            _report(page, state, path, response.status_code, writes)
            self.assertEqual(
                unexpected,
                [],
                f"{page} ({state}) {path} issued statements that take SQLite's "
                "write lock. See this module's docstring.",
            )

    def test_home(self):
        self._check("home", "/")

    def test_home_rows(self):
        for row_id in self.row_ids:
            with self.subTest(row=row_id):
                self._check("home_row", f"/home/row/{row_id}/")
                self._check("api_home_row", f"/api/v1/home/row/{row_id}/")

    def test_home_for_a_new_user(self):
        user = User.objects.create_user(username="first-visit", password="x")
        self.client.force_login(user)
        self._check("home_first_visit", "/", states=("cold",))

    def test_home_without_rows(self):
        HomeScreenRow.objects.filter(user=self.user).delete()
        self._check("home_first_visit", "/", states=("cold",))
        self._check("api_home_first_visit", "/api/v1/home/", states=("cold",))

    def test_media_lists(self):
        for media_type in MediaTypes.values:
            if not hasattr(self.user, f"{media_type}_sort"):
                continue  # not a list page of its own (episodes, comic issues)
            with self.subTest(media_type=media_type):
                self._check("media_list", f"/medialist/{media_type}")

    def test_history(self):
        self._check("history", "/history")

    def test_calendar(self):
        self._check("calendar", "/calendar")

    def test_collection(self):
        self._check("collection", "/collection/")

    def test_lists(self):
        self._check("lists", "/lists")

    def test_search(self):
        self._check("search", "/search?q=a")

    def test_detail(self):
        item = self.movie
        self._check("detail", f"/details/{item.source}/movie/{item.media_id}/x")

    def _music_pages(self, artist, album, track):
        """Check the web and API pages for one artist, album and track."""
        for page, path in (
            ("music", music_artist_url(artist)),
            ("music", music_album_url(album)),
            ("music", music_track_url(track)),
            ("music", f"/api/v1/music/artists/{artist.id}/"),
            ("music", f"/api/v1/music/albums/{album.id}/"),
            ("music", f"/api/v1/music/albums/{album.id}/tracks/"),
            ("music", f"/api/v1/music/tracks/{track.id}/"),
        ):
            with self.subTest(path=path):
                self._check(page, path, no_provider_calls=True)

    def test_music_pages_healthy(self):
        """A synced artist, album and track page: stored rows only."""
        artist, album, track = _music_library("healthy")
        self._music_pages(artist, album, track)

    def test_music_album_without_tracks(self):
        """A new album with no tracks yet draws an empty list and queues them."""
        artist, album, track = _music_library("no-tracks")
        Album.objects.filter(pk=album.pk).update(tracks_populated=False)
        self._music_pages(artist, album, track)

    def test_music_artist_with_duplicate_albums(self):
        """Duplicate albums are merged by the repair task, not the page."""
        artist, album, track = _music_library("duplicates")
        Album.objects.create(title=album.title.upper(), artist=artist)
        self._music_pages(artist, album, track)
        self.assertEqual(Album.objects.filter(artist=artist).count(), 2)

    def test_health(self):
        self._check("health", "/health/")

    def test_statistics(self):
        self._check("statistics", "/statistics")
        self._check("no_writes", "/api/v1/statistics/preferences/")

    def test_statistics_api(self):
        self._check("statistics", "/api/v1/statistics/overview/")

    def test_discover(self):
        self._check("discover", "/discover")

    def test_discover_with_expired_rows(self):
        self.client.get("/discover")
        DiscoverRowCache.objects.update(expires_at=EPOCH - timedelta(days=1))
        DiscoverTasteProfile.objects.update(expires_at=EPOCH - timedelta(days=1))
        self._check("discover", "/discover", states=("cold",))

    def test_discover_api(self):
        self._check("discover", "/api/v1/discover/")
        self._check("no_writes", "/api/v1/discover/status/")
        self._check("no_writes", "/api/v1/discover/hidden/")

    def test_a_choice_carried_by_the_request_is_saved(self):
        for path in (
            "/medialist/movie?sort=title&layout=list",
            "/discover/rows?media_type=movie",
            "/search?q=a&media_type=tv",
            "/calendar?view=list",
            "/lists?sort=name",
        ):
            with self.subTest(path=path):
                self._check("saved_choice", path, states=("cold",))
                # Asking for the same choice again saves nothing more.
                again = "discover" if path.startswith("/discover") else "no_writes"
                self._check(again, path, states=("warm",))
                self.assertNotIn(
                    "users/models.py:update_preference",
                    {write.source for write in self._writes(path)[1]},
                )

    def test_token_authenticated_request(self):
        _, raw_token = IntegrationToken.generate(self.user, "guard")
        self.client.logout()
        headers = {"authorization": f"Bearer {raw_token}"}
        self._check("api_token", "/api/v1/home/", states=("cold",), **headers)
        # Within five minutes the token is not touched again.
        self._check("no_writes", "/api/v1/home/", states=("warm",), **headers)

    def test_settings_pages(self):
        for name in (
            "providers",
            "activity",
            "data",
            "access",
            "notifications",
            "appearance",
            "cards/movie",
            "sidebar",
            "home-screen",
            "preferences",
            "rss",
            "advanced",
            "about",
        ):
            with self.subTest(page=name):
                self._check("settings", f"/settings/{name}")

    def test_account_settings(self):
        self._check("account_settings", "/settings/account", states=("cold",))
        self._check("no_writes", "/settings/account", states=("warm",))
        self._check("no_writes", "/api/v1/user/account/")

    def test_api_read_endpoints(self):
        for path in API_READ_PATHS:
            with self.subTest(path=path):
                self._check("api", path)


# The GET endpoints the Expo app's transports call (native-proof/ and the
# shared frontend/ transports), without the ones that need an object id.
API_READ_PATHS = (
    "/api/v1/info/",
    "/api/v1/home/",
    "/api/v1/home/artwork/",
    "/api/v1/media/",
    "/api/v1/media/movie/",
    "/api/v1/media/tv/",
    "/api/v1/history/",
    "/api/v1/history/genres/",
    "/api/v1/calendar/",
    "/api/v1/calendar/statuses/",
    "/api/v1/collection/",
    "/api/v1/lists/",
    "/api/v1/tags/",
    "/api/v1/search/results/?q=a",
    "/api/v1/search/sources/",
    "/api/v1/library/saved-views/?media_type=movie",
    "/api/v1/playback/now-playing/",
    "/api/v1/playback/home-cards/",
    "/api/v1/podcasts/shows/",
    "/api/v1/music/artists/",
    "/api/v1/music/albums/",
    "/api/v1/connections/",
    "/api/v1/providers/",
    "/api/v1/imports/activity/",
    "/api/v1/metadata-review/",
    "/api/v1/user/navigation/",
    "/api/v1/user/sidebar/",
    "/api/v1/user/preferences/",
    "/api/v1/user/home-screen/",
    "/api/v1/user/appearance/",
    "/api/v1/user/cards/",
    "/api/v1/user/notifications/",
    "/api/v1/user/onboarding/",
    "/api/v1/user/about/",
)


def _music_library(label):
    """Return a synced artist, one album and one track (no provider needed)."""
    artist = Artist.objects.create(
        name=f"Guard Artist {label}",
        musicbrainz_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"artist-{label}")),
        discography_synced_at=EPOCH,
        members_synced_at=EPOCH,
        genres=["Rock"],
    )
    album = Album.objects.create(
        title=f"Guard Album {label}",
        artist=artist,
        musicbrainz_release_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"rel-{label}")),
        musicbrainz_release_group_id=str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"rg-{label}"),
        ),
        tracks_populated=True,
        image="https://example.com/cover.jpg",
        genres=["Rock"],
    )
    AlbumArtist.objects.create(album=album, artist=artist)
    track = Track.objects.create(
        album=album,
        title=f"Guard Track {label}",
        track_number=1,
        musicbrainz_recording_id=str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"rec-{label}"),
        ),
    )
    return artist, album, track


def _report(page, state, path, status, writes):
    """Append one observation to a receipt file when asked to (not in CI)."""
    target = os.environ.get("FLOPPY_READ_PAGE_WRITES_REPORT")
    if not target:
        return
    sources = sorted({f"{write.source}: {write.keyword}" for write in writes})
    with open(target, "a", encoding="utf-8") as handle:  # noqa: PTH123
        handle.write(f"{page}\t{state}\t{status}\t{path}\t{' | '.join(sources)}\n")
