"""Person pages never wait for the all-time talent scan (performance prompt 55).

Production logged person pages at 32 to 221 s with 1 to 6 s of CPU. Every
stall stack was inside ``get_person_talent_totals``: the page built a context
from every play in the library while the viewer waited. These tests hold that
build for 30 s and check the page still answers at once with stored data.
"""

import threading
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from app import request_timing, statistics_cache
from app.models import (
    CreditRoleType,
    Item,
    ItemPersonCredit,
    MediaTypes,
    Movie,
    Person,
    PersonGender,
    Sources,
    Status,
)
from app.statistics_day_cache import _set_history_version
from app.tasks_interactive import build_person_talent_context_task

HELD_SECONDS = 30
PERSON_ID = "4242"


def _person_payload():
    return {
        "person_id": PERSON_ID,
        "source": Sources.TMDB.value,
        "name": "Jane Star",
        "image": "http://example.com/jane.jpg",
        "biography": "",
        "known_for_department": "Acting",
        "gender": "female",
        "birth_date": None,
        "death_date": None,
        "place_of_birth": "",
        "filmography": [
            {
                "media_id": "501",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "title": "Tracked Movie",
                "image": "http://example.com/tracked.jpg",
                "year": 2024,
                "credit_type": "cast",
                "role": "Lead",
                "department": "Acting",
            },
            {
                "media_id": "777",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "title": "Other Movie",
                "image": "http://example.com/other.jpg",
                "year": 2021,
                "credit_type": "cast",
                "role": "Guest",
                "department": "Acting",
            },
        ],
    }


class PersonPageTalentWaitTests(TestCase):
    """With a worker deployment, the page reads stored totals only.

    Fixtures are created in eager mode; worker mode is switched on afterwards,
    with every other task publish stubbed so no test needs a real broker.
    """

    def setUp(self):
        cache.clear()
        credentials = {"username": "viewer", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)
        self.client.login(**credentials)
        person = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id=PERSON_ID,
            name="Jane Star",
            gender=PersonGender.FEMALE.value,
        )
        item = Item.objects.create(
            media_id="501",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Tracked Movie",
            image="http://example.com/tracked.jpg",
            runtime_minutes=120,
        )
        ItemPersonCredit.objects.create(
            item=item,
            person=person,
            role_type=CreditRoleType.CAST.value,
            role="Lead",
            department="Acting",
        )
        Movie.objects.create(
            item=item, user=self.user, status=Status.COMPLETED.value
        )
        provider = patch("app.providers.tmdb.person", return_value=_person_payload())
        self.provider = provider.start()
        self.addCleanup(provider.stop)
        delay = patch.object(build_person_talent_context_task, "delay")
        self.delay = delay.start()
        self.addCleanup(delay.stop)
        publish = patch("celery.app.task.Task.apply_async")
        publish.start()
        self.addCleanup(publish.stop)
        worker_mode = override_settings(CELERY_TASK_ALWAYS_EAGER=False, TESTING=False)
        worker_mode.enable()
        self.addCleanup(worker_mode.disable)

    def _url(self):
        return reverse(
            "person_detail",
            kwargs={
                "source": Sources.TMDB.value,
                "person_id": PERSON_ID,
                "name": "jane-star",
            },
        )

    def _held_build(self):
        """Patch the library scan so it takes HELD_SECONDS, like production."""
        release = threading.Event()
        from app import statistics_talent

        original = statistics_talent._build_person_talent_context

        def slow_build(*args, **kwargs):
            release.wait(HELD_SECONDS)
            return original(*args, **kwargs)

        return patch(
            "app.statistics_cache._build_person_talent_context",
            side_effect=slow_build,
        )

    def _run_build(self):
        """What the worker does: build and store the context."""
        build_person_talent_context_task.run(self.user.id)

    def test_first_visit_answers_fast_while_the_build_is_held(self):
        """Regression: the old view sat in the scan for the whole hold."""
        with self._held_build() as build:
            started = time.perf_counter()
            response = self.client.get(self._url())
            elapsed = time.perf_counter() - started

        self.assertEqual(response.status_code, 200)
        self.assertLess(elapsed, 2.0)
        build.assert_not_called()
        self.delay.assert_called_once_with(self.user.id)
        self.assertTrue(response.context["talent_pending"])
        # Credits and "in your library" marks render from stored data.
        self.assertEqual(len(response.context["filmography"]), 2)
        self.assertEqual(
            [entry["title"] for entry in response.context["watched_filmography"]],
            ["Tracked Movie"],
        )
        self.assertContains(response, "Counting plays")
        self.assertContains(response, 'id="person-talent-poller"')
        self.assertNotContains(response, "tracked play")

    def test_repeat_visits_queue_the_build_once(self):
        """The same person loaded three times in a row queues one build."""
        for _ in range(3):
            self.client.get(self._url())
        self.assertEqual(self.delay.call_count, 1)

    def test_poll_while_building_reads_the_cache_only(self):
        self.client.get(self._url())
        self.provider.reset_mock()

        with self._held_build() as build:
            response = self.client.get(
                self._url(),
                {"talent": "1", "attempt": "4"},
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 200)
        build.assert_not_called()
        self.provider.assert_not_called()
        self.assertTemplateUsed(response, "app/components/person_talent_poller.html")
        self.assertContains(response, '"attempt": "5"')

    def test_poll_stops_at_the_limit(self):
        from app.people_views import PERSON_TALENT_POLL_LIMIT

        response = self.client.get(
            self._url(),
            {"talent": "1", "attempt": str(PERSON_TALENT_POLL_LIMIT)},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")

    def test_first_visit_fills_in_without_a_reload(self):
        """Once the worker has built it, the poll swaps in totals and credits."""
        self.client.get(self._url())
        self._run_build()

        response = self.client.get(
            self._url(),
            {"talent": "1", "attempt": "2"},
            HTTP_HX_REQUEST="true",
        )

        self.assertTemplateUsed(response, "app/components/person_talent_ready.html")
        content = response.content.decode()
        self.assertIn('id="person-hero" hx-swap-oob="true"', content)
        self.assertIn('id="filmography-content" hx-swap-oob="true"', content)
        self.assertIn("1 tracked play", content)
        self.assertNotIn("Counting plays", content)
        self.assertEqual(response.context["tracked_plays_count"], 1)
        self.assertTrue(response.context["tracked_hours_count"].startswith("2h"))
        watched = response.context["watched_filmography"]
        self.assertTrue(watched[0]["watched_person_runtime_display"].startswith("2h"))

    def test_new_play_serves_last_totals_and_queues_a_rebuild(self):
        self.client.get(self._url())
        self._run_build()
        self.delay.reset_mock()
        _set_history_version(self.user.id)

        with self._held_build() as build:
            response = self.client.get(self._url())

        build.assert_not_called()
        self.delay.assert_called_once_with(self.user.id)
        self.assertFalse(response.context["talent_pending"])
        self.assertEqual(response.context["tracked_plays_count"], 1)
        self.assertNotContains(response, 'id="person-talent-poller"')

    def test_empty_library_is_remembered(self):
        Movie.objects.filter(user=self.user).delete()
        _set_history_version(self.user.id)
        self.client.get(self._url())
        self._run_build()

        response = self.client.get(self._url())

        self.assertFalse(response.context["talent_pending"])
        self.assertEqual(response.context["tracked_plays_count"], 0)
        self.assertEqual(self.delay.call_count, 1)

    def test_json_contract_reports_pending_for_expo(self):
        response = self.client.get(self._url(), {"format": "json"})
        payload = response.json()
        self.assertTrue(payload["talentPending"])
        self.assertIn({"label": "Counting plays"}, payload["counts"])

        self._run_build()
        payload = self.client.get(self._url(), {"format": "json"}).json()
        self.assertFalse(payload["talentPending"])
        self.assertIn({"label": "1 tracked play"}, payload["counts"])

    def test_enqueue_failure_releases_the_guard(self):
        self.delay.side_effect = OSError("broker down")
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["talent_pending"])
        self.delay.side_effect = None
        self.client.get(self._url())
        self.assertEqual(self.delay.call_count, 2)


class PersonTalentTotalsEquivalenceTests(TestCase):
    """The per-person read through the person index matches the built totals."""

    def test_ready_totals_match_inline_totals(self):
        user = get_user_model().objects.create_user(username="u", password="p")
        person = Person.objects.create(
            source=Sources.TMDB.value, source_person_id="9", name="P"
        )
        other = Person.objects.create(
            source=Sources.TMDB.value, source_person_id="10", name="Q"
        )
        for index in range(3):
            item = Item.objects.create(
                media_id=str(900 + index),
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"M{index}",
                image="http://example.com/x.jpg",
                runtime_minutes=90,
            )
            ItemPersonCredit.objects.create(
                item=item, person=other, role_type=CreditRoleType.CAST.value
            )
            if index < 2:
                ItemPersonCredit.objects.create(
                    item=item, person=person, role_type=CreditRoleType.CAST.value
                )
                Movie.objects.create(
                    item=item, user=user, status=Status.COMPLETED.value
                )
        # A credit on an unplayed title must not count.
        unplayed = Item.objects.create(
            media_id="999",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Unplayed",
            image="http://example.com/x.jpg",
            runtime_minutes=90,
        )
        ItemPersonCredit.objects.create(
            item=unplayed, person=person, role_type=CreditRoleType.CAST.value
        )

        totals, pending = statistics_cache.get_ready_person_talent_totals(
            user, Sources.TMDB.value, "9"
        )

        self.assertFalse(pending)
        self.assertEqual(totals["plays"], 2)
        self.assertEqual(totals["unique_titles"], 2)
        self.assertEqual(totals["watched_minutes"], 180)


class DbFetchBoundaryTests(TestCase):
    """Row stepping after execute is timed, so it is not 'unclassified'."""

    def test_iterator_fetches_are_counted_as_db_fetch(self):
        request_timing.install_boundaries()
        Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="A",
            image="http://example.com/a.jpg",
        )
        tally, token = request_timing.begin()
        try:
            list(Item.objects.values_list("id", flat=True).iterator(chunk_size=10))
        finally:
            request_timing.end(token)
        self.assertGreaterEqual(tally["boundary_calls"].get("db_fetch", 0), 1)
        self.assertIn("db_fetch", tally["boundaries"])
