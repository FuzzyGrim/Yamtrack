"""Calendar links answer an unchanged poll cheaply and a change at once."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.models import Item, MediaTypes, Movie, Sources, Status
from events import release_marker
from events.calendar.main import save_events
from events.models import Event


class CalendarFeedValidationTests(TestCase):
    """ETag, 304, invalidation and token isolation for both calendar links."""

    def setUp(self):
        cache.clear()
        self.enterContext(patch("app.models.Item.fetch_releases"))
        self.user = get_user_model().objects.create_user(
            username="feeduser", password="pw"
        )
        self.url = reverse("download_calendar", kwargs={"token": self.user.token})
        self.history_url = reverse(
            "download_history_calendar", kwargs={"token": self.user.token}
        )
        self.item = self._track("Tracked Movie")
        Event.objects.create(
            item=self.item, datetime=timezone.now() + timedelta(days=2)
        )

    def _track(self, title, *, status=Status.PLANNING.value, user=None, **fields):
        item = Item.objects.create(
            media_id=title,
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title=title,
        )
        Movie.objects.create(item=item, user=user or self.user, status=status, **fields)
        return item

    def test_unchanged_poll_is_304_with_two_queries(self):
        first = self.client.get(self.url)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first["ETag"].startswith('W/"'))
        self.assertIn("max-age=900", first["Cache-Control"])
        self.assertIn("private", first["Cache-Control"])

        with CaptureQueriesContext(connection) as queries:
            again = self.client.get(self.url, HTTP_IF_NONE_MATCH=first["ETag"])
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.content, b"")
        self.assertLessEqual(len(queries), 2)

    def test_poll_without_validator_reads_the_kept_body(self):
        first = self.client.get(self.url)
        with patch("events.models.Event.objects.get_user_events") as build:
            with CaptureQueriesContext(connection) as queries:
                again = self.client.get(self.url)
        build.assert_not_called()
        self.assertEqual(again.content, first.content)
        self.assertLessEqual(len(queries), 2)

    def test_new_release_date_appears_on_next_poll(self):
        first = self.client.get(self.url)
        other = self._track("Other Movie")
        # The calendar reload writes release dates through save_events.
        save_events([Event(item=other, datetime=timezone.now() + timedelta(days=5))])

        again = self.client.get(self.url, HTTP_IF_NONE_MATCH=first["ETag"])
        self.assertEqual(again.status_code, 200)
        self.assertIn(b"Other Movie", again.content)

    def test_moved_release_date_appears_on_next_poll(self):
        first = self.client.get(self.url)
        moved = timezone.now() + timedelta(days=9)
        save_events([Event(item=self.item, datetime=moved)])

        again = self.client.get(self.url, HTTP_IF_NONE_MATCH=first["ETag"])
        self.assertEqual(again.status_code, 200)
        self.assertIn(f"{moved:%Y%m%d}".encode(), again.content)

    def test_unchanged_reload_keeps_the_validator(self):
        event = Event.objects.get(item=self.item)
        before = release_marker.current()
        save_events([Event(item=self.item, datetime=event.datetime)])
        self.assertEqual(release_marker.current(), before)

    def test_tracking_change_appears_on_next_poll(self):
        first = self.client.get(self.url)
        movie = Movie.objects.get(item=self.item, user=self.user)
        movie.status = Status.DROPPED.value
        movie.save()

        again = self.client.get(self.url, HTTP_IF_NONE_MATCH=first["ETag"])
        self.assertEqual(again.status_code, 200)
        self.assertNotIn(b"Tracked Movie", again.content)

    def test_filters_have_their_own_validator(self):
        plain = self.client.get(self.url)
        filtered = self.client.get(
            self.url,
            {"media_types": [MediaTypes.TV.value]},
            HTTP_IF_NONE_MATCH=plain["ETag"],
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertNotIn(b"Tracked Movie", filtered.content)

    def test_body_is_never_served_across_users(self):
        other_user = get_user_model().objects.create_user(
            username="someone", password="pw"
        )
        mine = self.client.get(self.url)
        other_url = reverse("download_calendar", kwargs={"token": other_user.token})

        theirs = self.client.get(other_url, HTTP_IF_NONE_MATCH=mine["ETag"])
        self.assertEqual(theirs.status_code, 200)
        self.assertNotIn(b"Tracked Movie", theirs.content)
        self.assertNotEqual(theirs["ETag"], mine["ETag"])

    def test_rotated_token_stops_working_at_once(self):
        first = self.client.get(self.url)
        self.user.token = "rotated-token"
        self.user.save(update_fields=["token"])

        old = self.client.get(self.url, HTTP_IF_NONE_MATCH=first["ETag"])
        self.assertEqual(old.status_code, 401)
        old_plain = self.client.get(self.url)
        self.assertEqual(old_plain.status_code, 401)

        new_url = reverse("download_calendar", kwargs={"token": "rotated-token"})
        fresh = self.client.get(new_url, HTTP_IF_NONE_MATCH=first["ETag"])
        self.assertEqual(fresh.status_code, 200)

    def test_history_feed_304_and_new_play(self):
        self._track(
            "Finished Movie",
            status=Status.COMPLETED.value,
            end_date=timezone.now() - timedelta(days=1),
        )
        first = self.client.get(self.history_url)
        self.assertIn(b"Finished Movie", first.content)
        unchanged = self.client.get(self.history_url, HTTP_IF_NONE_MATCH=first["ETag"])
        self.assertEqual(unchanged.status_code, 304)

        self._track(
            "Second Movie",
            status=Status.COMPLETED.value,
            end_date=timezone.now() - timedelta(days=2),
        )
        again = self.client.get(self.history_url, HTTP_IF_NONE_MATCH=first["ETag"])
        self.assertEqual(again.status_code, 200)
        self.assertIn(b"Second Movie", again.content)

    def test_lost_release_marker_never_matches_an_old_validator(self):
        first = self.client.get(self.url)
        cache.clear()
        again = self.client.get(self.url, HTTP_IF_NONE_MATCH=first["ETag"])
        self.assertEqual(again.status_code, 200)
