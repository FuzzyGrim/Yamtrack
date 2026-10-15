import datetime

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app import history_processor
from app.models import (
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)


class JournalViewTests(TestCase):
    """Test the journal (activity feed) view."""

    def setUp(self):
        """Create a user with some tracked media and history."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.movie_item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        self.movie = Movie(
            item=self.movie_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )
        # Mirror the middleware attributing history records to the request user.
        self.movie._history_user = self.user
        self.movie.save()
        self.movie.status = Status.COMPLETED.value
        self.movie.progress = 1
        self.movie.score = 8
        self.movie.save()

    def _add_episode(self):
        """Add a watched episode without triggering provider lookups."""
        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        episode = Episode(
            item=episode_item,
            related_season=season,
            end_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
        )
        episode._history_user = self.user
        Episode.save_base(episode)
        return season_item

    def test_journal_page_renders(self):
        """The journal page renders with activity entries for the user."""
        response = self.client.get(reverse("journal"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/journal.html")
        self.assertGreater(len(response.context["entries"]), 0)

    def test_journal_entry_descriptions(self):
        """Entries expose human-readable change descriptions and their item."""
        response = self.client.get(reverse("journal"))

        descriptions = [
            change["description"]
            for entry in response.context["entries"]
            for change in entry["changes"]
        ]
        self.assertIn("Finished watching", descriptions)
        self.assertIn("Rated 8.0/10", descriptions)
        self.assertIn("Marked as currently watching", descriptions)

        # Newest activity is first
        self.assertEqual(
            response.context["entries"][0]["item"],
            self.movie_item,
        )

    def test_journal_entry_accents(self):
        """Entries carry a semantic accent used for icon/colour."""
        response = self.client.get(reverse("journal"))

        accents = {
            change["description"]: entry["accent"]
            for entry in response.context["entries"]
            for change in entry["changes"]
        }
        # The movie was created in progress and then completed.
        self.assertEqual(accents["Finished watching"], Status.COMPLETED.value)
        self.assertEqual(
            accents["Marked as currently watching"],
            Status.IN_PROGRESS.value,
        )

    def test_journal_entries_expose_history_id(self):
        """Each entry carries its history record id for deletion."""
        response = self.client.get(reverse("journal"))

        for entry in response.context["entries"]:
            self.assertIn("id", entry)

    def test_journal_card_has_delete_confirmation(self):
        """The feed renders a delete control and confirmation modal."""
        response = self.client.get(reverse("journal"))

        self.assertContains(response, "Delete activity?")
        self.assertContains(response, "hx-delete")

    def test_journal_delete_history_entry(self):
        """Deleting a history entry removes the underlying record."""
        historical_movie = apps.get_model("app", "historicalmovie")
        record = historical_movie.objects.filter(history_user=self.user).first()

        response = self.client.delete(
            reverse(
                "delete_history_record",
                args=[MediaTypes.MOVIE.value, record.history_id],
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            historical_movie.objects.filter(history_id=record.history_id).exists(),
        )

    def test_journal_htmx_returns_partial(self):
        """HTMX requests receive only the items partial."""
        response = self.client.get(
            reverse("journal"),
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/journal_items.html")
        self.assertTemplateNotUsed(response, "app/journal.html")

    def test_journal_episode_entry(self):
        """Episode activity shows the season item and a watched-episode line."""
        season_item = self._add_episode()

        response = self.client.get(reverse("journal"))

        episode_entries = [
            entry
            for entry in response.context["entries"]
            if entry["media_type"] == MediaTypes.EPISODE.value
        ]
        self.assertEqual(len(episode_entries), 1)
        self.assertEqual(episode_entries[0]["item"], season_item)
        self.assertEqual(
            episode_entries[0]["changes"][0]["description"],
            "Watched episode 1",
        )

    def test_journal_excludes_other_users(self):
        """The feed only contains the requesting user's activity."""
        credentials = {"username": "other", "password": "12345"}
        other = get_user_model().objects.create_user(**credentials)
        other_item = Item.objects.create(
            media_id="278",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Other Movie",
            image="http://example.com/image.jpg",
        )
        other_movie = Movie(
            item=other_item,
            user=other,
            status=Status.COMPLETED.value,
        )
        other_movie._history_user = other
        other_movie.save()

        response = self.client.get(reverse("journal"))

        items = {entry["item"] for entry in response.context["entries"]}
        self.assertNotIn(other_item, items)

    def test_journal_context_has_activity_dashboard(self):
        """The journal exposes the activity dashboard data and date range."""
        response = self.client.get(reverse("journal"))

        self.assertIn("activity_data", response.context)
        self.assertIn("stats", response.context["activity_data"])
        self.assertGreater(response.context["activity_total"], 0)
        self.assertIn("date_format_values", response.context)

    def test_journal_date_range_excludes_activity(self):
        """A past date range with no activity yields an empty feed."""
        response = self.client.get(
            reverse("journal") + "?start-date=2000-01-01&end-date=2000-12-31",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["entries"]), 0)
        self.assertEqual(response.context["activity_total"], 0)

    def test_journal_all_time_includes_activity(self):
        """The 'all' range includes the user's activity."""
        response = self.client.get(
            reverse("journal") + "?start-date=all&end-date=all",
        )

        self.assertIsNone(response.context["start_date"])
        self.assertGreater(len(response.context["entries"]), 0)

    def test_journal_pagination_preserves_date_range(self):
        """The load-more URL carries the active date range."""
        response = self.client.get(
            reverse("journal") + "?start-date=all&end-date=all",
        )

        self.assertEqual(
            response.context["filter_query"],
            "start-date=all&end-date=all",
        )

    def test_journal_groups_entries_by_day(self):
        """Entries are grouped into day buckets labelled Today/Yesterday."""
        response = self.client.get(reverse("journal"))

        labels = [day["label"] for day in response.context["journal_days"]]
        self.assertEqual(labels, ["Today"])
        self.assertContains(response, "Today")

    def test_journal_day_header_not_repeated_across_pages(self):
        """A day continued on the next page suppresses its repeated header."""
        # Next page continuing the same day as the previous page's last entry.
        response = self.client.get(
            reverse("journal") + f"?last_day={timezone.localdate().isoformat()}",
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["prev_day"], timezone.localdate().isoformat())

    def test_journal_empty_page_forwards_prev_day(self):
        """A page that renders no days forwards the incoming day, not ''."""
        # A far-future cursor yields no rows, so last_day must fall back to the
        # incoming day rather than reset to "" and duplicate the header later.
        today = timezone.localdate().isoformat()
        # A cursor older than every row returns nothing after it, so the page
        # is empty and last_day must fall back to the incoming day.
        response = self.client.get(
            reverse("journal")
            + "?cursor_date=2000-01-01T00:00:00%2B00:00"
            + f"&cursor_type={MediaTypes.MOVIE.value}&cursor_id=1"
            + f"&last_day={today}",
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["entries"]), 0)
        self.assertEqual(response.context["last_day"], today)

    def test_journal_keyset_pagination_covers_all_rows(self):
        """Following the cursor yields every entry exactly once."""
        for i in range(25):
            item = Item.objects.create(
                media_id=f"pag-{i}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Movie {i}",
            )
            movie = Movie(item=item, user=self.user, status=Status.COMPLETED.value)
            movie._history_user = self.user
            # save_base skips the status cascade (and its provider lookup) while
            # still writing a history record.
            Movie.save_base(movie)

        seen = []
        url = reverse("journal") + "?start-date=all&end-date=all"
        for _ in range(6):  # safety bound against a runaway loop
            response = self.client.get(url, headers={"HX-Request": "true"})
            self.assertEqual(response.status_code, 200)
            seen.extend(
                (entry["media_type"], entry["id"])
                for entry in response.context["entries"]
            )
            if not response.context["has_next"]:
                break
            url = reverse("journal") + "?" + response.context["next_query"]

        # More than one page of activity, every entry unique, none dropped.
        self.assertGreater(len(seen), 20)
        self.assertEqual(len(seen), len(set(seen)))

    def test_journal_keyset_handles_identical_timestamps(self):
        """Rows sharing a history_date paginate without dropping or duplicating."""
        shared = timezone.now()
        for i in range(5):
            item = Item.objects.create(
                media_id=f"tie-{i}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Tie {i}",
            )
            movie = Movie(item=item, user=self.user, status=Status.COMPLETED.value)
            movie._history_user = self.user
            Movie.save_base(movie)

        historical_movie = apps.get_model("app", "historicalmovie")
        historical_movie.objects.filter(history_user=self.user).update(
            history_date=shared,
        )

        collected = []
        cursor = None
        for _ in range(20):  # safety bound
            rows, has_next = history_processor.get_journal_page(
                self.user,
                None,
                None,
                limit=2,
                cursor=cursor,
            )
            collected.extend(rows)
            if not has_next:
                break
            cursor = rows[-1]

        keys = [(media_type, history_id) for _d, media_type, history_id in collected]
        self.assertEqual(len(keys), len(set(keys)))

        all_rows, _ = history_processor.get_journal_page(
            self.user,
            None,
            None,
            limit=1000,
            cursor=None,
        )
        self.assertEqual(
            set(keys),
            {(media_type, history_id) for _d, media_type, history_id in all_rows},
        )

    def test_journal_empty_state(self):
        """A user without activity sees the empty-state message."""
        credentials = {"username": "empty", "password": "12345"}
        get_user_model().objects.create_user(**credentials)
        self.client.login(**credentials)

        response = self.client.get(reverse("journal"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["entries"]), 0)
        self.assertContains(response, "No tracking activity")
