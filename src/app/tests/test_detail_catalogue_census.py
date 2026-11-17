"""Detail pages against long catalogues (assignment 49, receipts BENCH-12).

For each detail page, build one catalogue at an ordinary, large and extreme
length and request the web page and the API endpoints the Expo app calls,
for a user who tracks nothing and one who has played everything twice.
Each request is measured cold (empty cache) and warm: queries, CPU ms,
response bytes, model instances built, provider calls (TMDB answered by
``FakeTmdb``, everything else offline), queued tasks and SQLite write
statements.

A benchmark, never in the fast suite. ``FLOPPY_CENSUS_SIZES=all`` runs the
three sizes (several minutes); the default runs the ordinary size only.
``FLOPPY_CENSUS_REPORT=path`` appends one JSON line per measurement;
``FLOPPY_CENSUS_BODIES=dir`` keeps each warm response body.

    scripts/test.sh app.tests.test_detail_catalogue_census
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

import requests
from django.core.cache import cache
from django.db import connection
from django.db.models.signals import post_init
from django.test import TestCase, tag
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.podcast_views import podcast_show_url
from app.tests import catalogue_fixtures as fx
from app.tests.music_album_fixtures import build_long_album
from app.tests.sqlite_writes import record_writes
from app.tests.views.test_podcast_show_detail import build_long_show
from users.models import User

SIZES = {
    "tv_show": (5, 20, 60),
    "season": (10, 100, 500),
    "artist": (10, 100, 600),
    "person": (20, 200, 1000),
    "list": (50, 1000, 10000),
    "collection": (50, 1000, 10000),
    "tag": (50, 1000, 10000),
    "podcast_show": (50, 500, 2000),
    "album": (10, 200, 2000),
}
LABELS = ("ordinary", "large", "extreme")


def _sizes(row):
    sizes = zip(LABELS, SIZES[row], strict=True)
    if os.environ.get("FLOPPY_CENSUS_SIZES") == "all":
        return list(sizes)
    return [next(sizes)]


def setUpModule():
    logging.disable(logging.WARNING)


def tearDownModule():
    logging.disable(logging.NOTSET)


@contextmanager
def _count_models():
    built = [0]

    def receiver(**_kwargs):
        built[0] += 1

    post_init.connect(receiver, weak=False, dispatch_uid="census-models")
    try:
        yield built
    finally:
        post_init.disconnect(dispatch_uid="census-models")


@tag("slow", "benchmark")
@skipUnless(connection.vendor == "sqlite", "Counts SQLite write statements.")
class DetailCatalogueCensus(TestCase):
    """Measure every detail page at three catalogue lengths."""

    @classmethod
    def setUpClass(cls):
        from app.providers import services

        cls.fake = fx.FakeTmdb(passthrough=services.api_request)
        cls.offline_calls = []
        cls.queued = []

        def offline(_session, method, url, *args, **kwargs):
            cls.offline_calls.append(url)
            raise requests.ConnectionError("offline in this test")

        def queue(task, *args, **kwargs):
            cls.queued.append(task.name)

        for patcher in (
            patch("app.providers.services.api_request", side_effect=cls.fake),
            patch("requests.sessions.Session.request", offline),
            patch("celery.app.task.Task.apply_async", queue),
        ):
            patcher.start()
            cls.addClassCleanup(patcher.stop)
        super().setUpClass()

    @classmethod
    def setUpTestData(cls):
        cls.untracked = User.objects.create_user(username="census-none", password="x")
        cls.tracked = User.objects.create_user(username="census-all", password="x")
        for user in (cls.untracked, cls.tracked):
            for media_type in ("tv", "movie", "music", "podcast", "anime"):
                setattr(user, f"{media_type}_enabled", True)
            user.save()

    # -- measuring ---------------------------------------------------------

    def _request(self, path):
        provider_before = len(self.fake.calls)
        network_before = len(self.offline_calls)
        queued_before = len(self.queued)
        with (
            record_writes() as writes,
            _count_models() as built,
            CaptureQueriesContext(connection) as queries,
        ):
            cpu = time.process_time()
            wall = time.perf_counter()
            response = self.client.get(
                path, headers={"hx-request": "true"} if "fragment=" in path else None
            )
            cpu_ms = (time.process_time() - cpu) * 1000
            wall_ms = (time.perf_counter() - wall) * 1000
        if response.status_code in (301, 302):
            return self._request(response["Location"])
        body = b"".join(response) if response.streaming else response.content
        self._last_body = body
        return {
            "status": response.status_code,
            "queries": len(queries.captured_queries),
            "cpu_ms": round(cpu_ms, 1),
            "wall_ms": round(wall_ms, 1),
            "bytes": len(body),
            "models": built[0],
            # Provider requests the page asked for, and how many of them got
            # as far as the (offline) network.
            "provider_calls": len(self.fake.calls) - provider_before,
            "network_attempts": len(self.offline_calls) - network_before,
            "tasks": len(self.queued) - queued_before,
            "writes": len(writes),
            "write_sources": sorted({w.source for w in writes}),
        }

    def _cell(self, row, label, size, state, user, paths):
        self.client.force_login(user)
        for platform, path in paths:
            cache.clear()
            cold = self._request(path)
            warm = self._request(path)
            record = {
                "row": row,
                "size_label": label,
                "size": size,
                "state": state,
                "platform": platform,
                "path": path,
                "cold": cold,
                "warm": warm,
            }
            self._report(record)
            self._save_body(record)
            self.assertLess(warm["status"], 400, record)

    def _save_body(self, record):
        target = os.environ.get("FLOPPY_CENSUS_BODIES")
        if target:
            name = "-".join(
                str(record[key]) for key in ("row", "size", "state", "platform")
            ).replace(" ", "_")
            (Path(target) / name).write_bytes(self._last_body)

    def _report(self, record):
        target = os.environ.get("FLOPPY_CENSUS_REPORT")
        if target:
            with Path(target).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        warm = record["warm"]
        print(
            f"{record['row']:13} {record['size']:>6} {record['state']:9} "
            f"{record['platform']:24} q={warm['queries']:<5} "
            f"cpu={warm['cpu_ms']:<8} bytes={warm['bytes']:<9} "
            f"models={warm['models']:<7} cold_prov={record['cold']['provider_calls']} "
            f"writes={warm['writes']}",
        )

    def _both_states(self, row, label, size, paths):
        self._cell(row, label, size, "none", self.untracked, paths)
        self._cell(row, label, size, "all", self.tracked, paths)

    # -- rows --------------------------------------------------------------

    def test_tv_show(self):
        for label, size in _sizes("tv_show"):
            show = fx.build_tv_show(
                self.fake, self.tracked, seasons=size, prefix=f"tv-{size}"
            )
            api = f"/api/v1/media/tv/tmdb/{show.media_id}"
            self._both_states(
                "tv_show",
                label,
                size,
                [
                    ("web", f"/details/tmdb/tv/{show.media_id}/census"),
                    # The shell loads seasons, cast and history in this second
                    # request.
                    (
                        "web secondary",
                        f"/details/tmdb/tv/{show.media_id}/census?fragment=secondary",
                    ),
                    ("api detail", f"{api}/"),
                    # The app pages each season's episodes on demand; the
                    # first season stands for one tap.
                    ("api season 1 episodes", f"{api}/1/episodes/"),
                ],
            )

    def test_season(self):
        for label, size in _sizes("season"):
            show = fx.build_tv_show(
                self.fake,
                self.tracked,
                seasons=1,
                episodes=size,
                prefix=f"season-{size}",
            )
            api = f"/api/v1/media/tv/tmdb/{show.media_id}/1"
            self._both_states(
                "season",
                label,
                size,
                [
                    ("web", f"/details/tmdb/tv/{show.media_id}/census/season/1"),
                    (
                        "web secondary",
                        f"/details/tmdb/tv/{show.media_id}/census/season/1"
                        "?fragment=secondary",
                    ),
                    ("api detail", f"{api}/"),
                    ("api episodes", f"{api}/episodes/"),
                ],
            )

    def test_artist(self):
        for label, size in _sizes("artist"):
            artist = fx.build_artist(self.tracked, albums=size, prefix=f"artist-{size}")
            self._both_states(
                "artist",
                label,
                size,
                [
                    ("web", reverse("artist_detail", args=[artist.id])),
                    ("api detail", f"/api/v1/music/artists/{artist.id}/"),
                ],
            )

    def test_person(self):
        for label, size in _sizes("person"):
            person_id = fx.build_person(
                self.fake, self.tracked, credit_count=size, prefix=f"person-{size}"
            )
            self._both_states(
                "person",
                label,
                size,
                [
                    ("web", f"/person/tmdb/{person_id}/census"),
                    ("api detail", f"/api/v1/people/tmdb/{person_id}/"),
                ],
            )

    def _owned_rows(self, row, builder):
        for label, size in _sizes(row):
            for state, user in (("none", self.untracked), ("all", self.tracked)):
                tracked = user if state == "all" else None
                built = builder(user, tracked, size, f"{row}-{state}-{size}")
                self._cell(row, label, size, state, user, built)

    def test_list(self):
        def build(owner, tracked, size, prefix):
            custom_list = fx.build_list(owner, tracked, items=size, prefix=prefix)
            return [
                ("web", reverse("list_detail", args=[custom_list.public_reference])),
                ("api detail", f"/api/v1/lists/{custom_list.id}/"),
            ]

        self._owned_rows("list", build)

    def test_collection(self):
        def build(owner, tracked, size, prefix):
            fx.build_collection(owner, tracked, entries=size, prefix=prefix)
            return [
                ("web", "/collection/"),
                ("api first page", "/api/v1/collection/"),
            ]

        self._owned_rows("collection", build)

    def test_tag(self):
        def build(owner, tracked, size, prefix):
            tag_row = fx.build_tag(owner, tracked, entries=size, prefix=prefix)
            # Neither platform has a page per tag: both list the tags, and a
            # tag opens the library filtered by it (assignment 54's page).
            return [
                ("web index", "/tags"),
                ("web library by tag", f"/medialist/movie?tag={tag_row.name}"),
                ("api tags", "/api/v1/tags/"),
                ("api library by tag", f"/api/v1/media/movie/?tag={tag_row.name}"),
            ]

        self._owned_rows("tag", build)

    def test_podcast_show(self):
        """Owned by assignment 46; measured for the table only."""
        for label, size in _sizes("podcast_show"):
            show = build_long_show(self.tracked, size, prefix=f"census-{size}")
            self._both_states(
                "podcast_show",
                label,
                size,
                [
                    ("web", podcast_show_url(show)),
                    ("web secondary", f"{podcast_show_url(show)}?fragment=secondary"),
                    ("api detail", f"/api/v1/podcasts/shows/{show.id}/"),
                ],
            )

    def test_album(self):
        """Owned by assignments 47 and 48; measured for the table only."""
        for label, size in _sizes("album"):
            _artist, album = build_long_album(
                self.tracked, tracks=size, plays_per_track=2, title=f"Census {size}"
            )
            self._both_states(
                "album",
                label,
                size,
                [
                    ("web", reverse("album_detail", args=[album.id])),
                    ("api detail", f"/api/v1/music/albums/{album.id}/"),
                    ("api tracks", f"/api/v1/music/albums/{album.id}/tracks/"),
                ],
            )
