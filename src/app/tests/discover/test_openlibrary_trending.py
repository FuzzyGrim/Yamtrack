"""Open Library trending rows: edition lookups are cached, bounded and overlap.

A trending "work" without an edition id costs one /editions.json request.
These tests pin how many requests one row build makes, that the candidates
(ids, titles, covers, order) are unchanged, and that a slow or failing lookup
cannot hold up or break the row.
"""

import threading
import time
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase

from app.discover import provider_candidates
from app.models import DiscoverApiCache
from app.providers import services

WORKS = [
    # Edition id already in the trending response: no lookup.
    {
        "key": "/works/OL1W",
        "title": "Direct",
        "cover_edition_key": "OL10M",
        "cover_i": 1,
    },
    {"key": "/works/OL2W", "title": "Needs lookup A", "cover_i": 2},
    {"key": "/works/OL3W", "title": "Listed keys", "edition_key": ["OL30M", "OL31M"]},
    {"key": "/works/OL4W", "title": "Needs lookup B"},
    {"key": "/works/OL5W", "title": "Needs lookup C", "cover_i": 5},
    # Same work twice: one lookup.
    {"key": "/works/OL2W", "title": "Needs lookup A again"},
]
LOOKUP_WORKS = {"/works/OL2W", "/works/OL4W", "/works/OL5W"}
EXPECTED = [
    ("OL10M", "Direct", "https://covers.openlibrary.org/b/id/1-L.jpg"),
    ("OL2W-first", "Needs lookup A", "https://covers.openlibrary.org/b/id/2-L.jpg"),
    ("OL30M", "Listed keys", None),
    ("OL4W-first", "Needs lookup B", None),
    ("OL5W-first", "Needs lookup C", "https://covers.openlibrary.org/b/id/5-L.jpg"),
    ("OL2W-first", "Needs lookup A again", None),
]


class FakeOpenLibrary:
    """Stands in for services.api_request; records every call."""

    def __init__(self, *, fail=(), block=None, delay=0.0):
        """Fail, block on an event, or sleep for the given work keys."""
        self.calls = []
        self.fail = set(fail)
        self.block = block or {}
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def __call__(self, _provider, _method, url, params=None, **_kwargs):
        with self.lock:
            self.calls.append(url)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if "/trending/" in url:
                return {"works": [dict(work) for work in WORKS]}
            work_key = url.removeprefix("https://openlibrary.org").removesuffix(
                "/editions.json",
            )
            if work_key in self.block:
                self.block[work_key].wait(5)
            if self.delay:
                time.sleep(self.delay)
            if work_key in self.fail:
                raise services.ProviderAPIError("openlibrary", Exception("boom"))
            work_id = work_key.removeprefix("/works/")
            return {"entries": [{"key": f"/books/{work_id}-first"}]}
        finally:
            with self.lock:
                self.active -= 1

    def lookups(self):
        return [url for url in self.calls if url.endswith("/editions.json")]

    def trending_calls(self):
        return [url for url in self.calls if "/trending/" in url]


def build_row():
    return provider_candidates._openlibrary_trending_candidates(
        period="daily",
        row_key="trending",
        source_reason="Open Library trending",
    )


def row_shape(candidates):
    return [(c.media_id, c.title, c.image) for c in candidates]


def expected_shape():
    return [
        (media_id, title, image or settings.IMG_NONE)
        for media_id, title, image in EXPECTED
    ]


class OpenLibraryTrendingLookupTests(TestCase):
    def setUp(self):
        cache.clear()
        DiscoverApiCache.objects.all().delete()

    def _run(self, fake):
        with patch.object(services, "api_request", fake):
            return build_row()

    def test_cold_build_resolves_each_missing_work_once_with_same_candidates(self):
        fake = FakeOpenLibrary()
        candidates = self._run(fake)

        self.assertEqual(row_shape(candidates), expected_shape())
        self.assertEqual({c.source for c in candidates}, {"openlibrary"})
        self.assertEqual({c.media_type for c in candidates}, {"book"})
        self.assertEqual(len(fake.trending_calls()), 1)
        self.assertEqual(len(fake.lookups()), len(LOOKUP_WORKS))

    def test_warm_resolution_cache_costs_only_the_trending_request(self):
        self._run(FakeOpenLibrary())
        # The trending list expires hourly; the resolutions do not.
        DiscoverApiCache.objects.all().delete()

        fake = FakeOpenLibrary()
        candidates = self._run(fake)

        self.assertEqual(row_shape(candidates), expected_shape())
        self.assertEqual(len(fake.trending_calls()), 1)
        self.assertEqual(fake.lookups(), [])

    def test_repeat_build_makes_no_requests(self):
        self._run(FakeOpenLibrary())
        fake = FakeOpenLibrary()
        candidates = self._run(fake)

        self.assertEqual(row_shape(candidates), expected_shape())
        self.assertEqual(fake.calls, [])

    def test_resolution_is_cached_for_a_month(self):
        self._run(FakeOpenLibrary(fail={"/works/OL4W"}))
        key = provider_candidates._openlibrary_first_edition_cache_key

        month = provider_candidates.OPENLIBRARY_FIRST_EDITION_TTL_SECONDS
        self.assertGreater(cache.ttl(key("/works/OL2W")), month - 60)
        failure = provider_candidates.OPENLIBRARY_FIRST_EDITION_FAILURE_TTL_SECONDS
        self.assertLessEqual(cache.ttl(key("/works/OL4W")), failure)
        self.assertGreater(cache.ttl(key("/works/OL4W")), failure - 60)

    def test_one_failing_lookup_leaves_the_rest_of_the_row(self):
        fake = FakeOpenLibrary(fail={"/works/OL4W"})
        candidates = self._run(fake)

        expected = [row for row in expected_shape() if row[0] != "OL4W-first"]
        self.assertEqual(row_shape(candidates), expected)

        # The failure is remembered for a while, as before: no retry storm.
        DiscoverApiCache.objects.all().delete()
        fake = FakeOpenLibrary()
        self._run(fake)
        self.assertEqual(fake.lookups(), [])

    def test_slow_lookup_is_left_out_and_shown_next_build(self):
        release = threading.Event()
        fake = FakeOpenLibrary(block={"/works/OL5W": release})
        with patch.object(
            provider_candidates,
            "OPENLIBRARY_EDITION_LOOKUP_BUDGET_SECONDS",
            0.3,
        ):
            started = time.monotonic()
            candidates = self._run(fake)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 3)
        expected = [row for row in expected_shape() if row[0] != "OL5W-first"]
        self.assertEqual(row_shape(candidates), expected)

        # The lookup finishes in the background and fills the cache.
        release.set()
        key = provider_candidates._openlibrary_first_edition_cache_key("/works/OL5W")
        for _ in range(50):
            if cache.get(key) is not None:
                break
            time.sleep(0.05)
        self.assertEqual(cache.get(key), "OL5W-first")

        fake = FakeOpenLibrary()
        candidates = self._run(fake)
        self.assertEqual(row_shape(candidates), expected_shape())
        self.assertEqual(fake.calls, [])

    def test_lookups_overlap_but_stay_within_the_worker_cap(self):
        many = [{"key": f"/works/OL{n}0W", "title": f"Book {n}"} for n in range(12)]
        fake = FakeOpenLibrary(delay=0.1)
        with patch.object(services, "api_request", fake):
            resolved = provider_candidates._openlibrary_resolve_first_editions(
                [work["key"] for work in many],
            )

        self.assertEqual(len(resolved), 12)
        self.assertGreater(fake.max_active, 1)
        self.assertLessEqual(
            fake.max_active,
            provider_candidates.OPENLIBRARY_EDITION_LOOKUP_WORKERS,
        )

    def test_interactive_scope_reaches_the_lookup_threads(self):
        seen = []

        def fake(_provider, _method, url, params=None, **_kwargs):
            seen.append(services._interactive_request.get())
            return {"entries": [{"key": "/books/OL1M"}]}

        with patch.object(services, "api_request", fake):
            with services.interactive_request_scope():
                provider_candidates._openlibrary_resolve_first_editions(["/works/OL9W"])

        self.assertEqual(seen, [True])
