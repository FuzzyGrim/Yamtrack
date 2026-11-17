"""A long list's completed count costs the same SQL at any length (BENCH-12)."""

from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.models import Movie, Status
from app.tests.catalogue_fixtures import build_list


class LongListCompletedCountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="owner", password="x")
        cls.user.movie_enabled = True
        cls.user.save()
        cls.short = build_list(cls.user, None, items=10, prefix="short")
        cls.long = build_list(cls.user, None, items=600, prefix="long")
        # Three of the short list's movies watched, one only started.
        movies = list(cls.short.items.order_by("title"))
        for item in movies[:3]:
            Movie.objects.create(
                item=item,
                user=cls.user,
                status=Status.COMPLETED.value,
                end_date=datetime(2025, 1, 1, tzinfo=UTC),
            )
        Movie.objects.create(
            item=movies[3], user=cls.user, status=Status.IN_PROGRESS.value
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _get(self, custom_list):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(
                reverse("list_detail", args=[custom_list.public_reference])
            )
        self.assertEqual(response.status_code, 200)
        return response, queries.captured_queries

    def test_completed_count_and_percent(self):
        response, _ = self._get(self.short)
        self.assertEqual(response.context["completed_count"], 3)
        self.assertEqual(response.context["completion_percent"], 30)

    def test_completed_count_does_not_send_every_item_id(self):
        _, short_queries = self._get(self.short)
        _, long_queries = self._get(self.long)

        def completed_sql(queries):
            return [q["sql"] for q in queries if "'Completed'" in q["sql"]]

        short_sql, long_sql = completed_sql(short_queries), completed_sql(long_queries)
        self.assertEqual(len(short_sql), len(long_sql))
        # The same statements, not 600 ids longer each (old code: ~3,000
        # characters longer per statement).
        self.assertLess(max(map(len, long_sql)), max(map(len, short_sql)) + 50)
