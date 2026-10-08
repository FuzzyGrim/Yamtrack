from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    TV,
    BasicMedia,
    Episode,
    Item,
    MediaTypes,
    Season,
    Sources,
    Status,
)
from events.models import Event

SEASON_LENGTH = 10
SEASON_COUNT = 3


class RewatchTests(TestCase):
    """Test progress while a show is being rewatched."""

    def setUp(self):
        """Create a fully watched show with three seasons of ten episodes."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.episodes_metadata = [
            {
                "episode_number": episode_number,
                "image": f"img{episode_number}.jpg",
                "air_date": datetime(2019, 1, 1, tzinfo=UTC),
            }
            for episode_number in range(1, SEASON_LENGTH + 1)
        ]

        def mock_metadata(
            media_type,
            _media_id,
            _source,
            season_numbers=None,
            **_kwargs,
        ):
            season_numbers = season_numbers or [1]
            if media_type == "tv_with_seasons":
                return {
                    f"season/{season}": {"episodes": self.episodes_metadata}
                    for season in season_numbers
                } | {
                    "related": {
                        "seasons": [
                            {"season_number": season} for season in season_numbers
                        ],
                    },
                }
            return {
                "title": "Test Show",
                "image": "http://example.com/image.jpg",
                "episodes": self.episodes_metadata,
                "max_progress": SEASON_LENGTH * SEASON_COUNT,
                "details": {"seasons": SEASON_COUNT},
                "related": {"seasons": []},
            }

        self.metadata_patcher = patch(
            "app.models.providers.services.get_media_metadata",
        )
        self.mock_get_metadata = self.metadata_patcher.start()
        self.mock_get_metadata.side_effect = mock_metadata
        self.addCleanup(self.metadata_patcher.stop)

        tv_item = Item.objects.create(
            media_id="456",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test Show",
            image="http://example.com/image.jpg",
        )
        self.tv = TV(
            item=tv_item,
            user=self.user,
            status=Status.COMPLETED.value,
        )
        TV.save_base(self.tv)

        self.first_run = datetime(2019, 6, 1, 12, tzinfo=UTC)
        self.seasons = {}
        for season_number in range(1, SEASON_COUNT + 1):
            self.seasons[season_number] = self._create_season(season_number)

    def _create_season(self, season_number):
        """Create a completed season with every episode watched in 2019."""
        season_item = Item.objects.create(
            media_id="456",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Show",
            image="http://example.com/image.jpg",
            season_number=season_number,
        )
        season = Season(
            item=season_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.COMPLETED.value,
        )
        Season.save_base(season)

        # The release calendar drives the denominator and the recomputed status
        Event.objects.create(
            item=season_item,
            content_number=SEASON_LENGTH,
            datetime=self.first_run,
        )

        for episode_number in range(1, SEASON_LENGTH + 1):
            self._watch(season, episode_number, self.first_run)

        return season

    def _watch(self, season, episode_number, end_date):
        """Record a play without running the episode side effects."""
        episode_item, _ = Item.objects.get_or_create(
            media_id="456",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=season.item.season_number,
            episode_number=episode_number,
            defaults={
                "title": "Test Show",
                "image": "http://example.com/image.jpg",
            },
        )
        Episode.save_base(
            Episode(
                item=episode_item,
                related_season=season,
                end_date=end_date,
            ),
        )

    def _reload_season(self, season_number):
        """Return a freshly loaded season with its episodes prefetched."""
        return Season.objects.select_related("related_tv").get(
            related_tv=self.tv,
            item__season_number=season_number,
        )

    def _reload_tv(self):
        """Return a freshly loaded show with its seasons and episodes."""
        return TV.objects.prefetch_related("seasons__episodes__item").get(pk=self.tv.pk)

    def test_progress_counts_every_play_without_rewatch(self):
        """Without a rewatch date the show reads as fully watched."""
        self.assertEqual(self._reload_season(1).progress, SEASON_LENGTH)
        self.assertEqual(self._reload_tv().progress, SEASON_LENGTH * SEASON_COUNT)

    def test_rewatch_resets_progress_to_the_current_run(self):
        """Only plays from the rewatch date on count (worked example)."""
        self.tv.rewatch_since = timezone.now()
        self.tv.save(update_fields=["rewatch_since"])

        now = timezone.now()
        for episode_number in range(1, 5):
            self._watch(self.seasons[1], episode_number, now)

        self.assertEqual(self._reload_season(1).progress, 4)
        self.assertEqual(self._reload_season(2).progress, 0)
        self.assertEqual(self._reload_season(3).progress, 0)
        self.assertEqual(self._reload_tv().progress, 4)

    def test_rewatch_keeps_every_play_in_history(self):
        """Plays from before the rewatch are never removed."""
        self.tv.rewatch_since = timezone.now()
        self.tv.save(update_fields=["rewatch_since"])
        self._watch(self.seasons[1], 1, timezone.now())

        self.assertEqual(
            Episode.objects.filter(related_season__related_tv=self.tv).count(),
            SEASON_LENGTH * SEASON_COUNT + 1,
        )

    def test_rewatch_recomputes_season_statuses(self):
        """Starting a rewatch clears the completed badges it invalidates."""
        self.tv.rewatch_since = timezone.now()
        self.tv.save(update_fields=["rewatch_since"])

        for season_number in range(1, SEASON_COUNT + 1):
            self.assertEqual(
                self._reload_season(season_number).status,
                Status.PLANNING.value,
            )
        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)

    def test_ending_a_rewatch_restores_progress_and_statuses(self):
        """Clearing the date widens the plays back out, losing nothing."""
        self.tv.rewatch_since = timezone.now()
        self.tv.save(update_fields=["rewatch_since"])

        self.tv.rewatch_since = None
        self.tv.save(update_fields=["rewatch_since"])

        self.assertEqual(self._reload_tv().progress, SEASON_LENGTH * SEASON_COUNT)
        for season_number in range(1, SEASON_COUNT + 1):
            self.assertEqual(
                self._reload_season(season_number).status,
                Status.COMPLETED.value,
            )
        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.COMPLETED.value)

    def test_cutoff_is_exact_to_the_second(self):
        """A play on the cutoff counts, one a second earlier does not."""
        cutoff = timezone.now()
        self.tv.rewatch_since = cutoff
        self.tv.save(update_fields=["rewatch_since"])

        self._watch(self.seasons[1], 1, cutoff)
        self._watch(self.seasons[1], 2, cutoff - timedelta(seconds=1))

        self.assertEqual(self._reload_season(1).progress, 1)

    def test_rewatch_filters_dates_and_last_watched(self):
        """Derived dates follow the current run too."""
        self.tv.rewatch_since = timezone.now()
        self.tv.save(update_fields=["rewatch_since"])

        now = timezone.now()
        self._watch(self.seasons[1], 1, now)

        season = self._reload_season(1)
        self.assertEqual(season.start_date, now)
        self.assertEqual(season.end_date, now)
        self.assertEqual(season.progressed_at, now)
        self.assertEqual(self._reload_tv().last_watched, "S01E01")

    def test_progress_sort_counts_current_run_only(self):
        """The SQL progress sort must not count plays from earlier runs."""
        self.tv.rewatch_since = timezone.now()
        self.tv.save(update_fields=["rewatch_since"])
        self._watch(self.seasons[1], 1, timezone.now())

        media_list = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.TV.value,
            status_filter="All",
            sort_filter="progress",
        )

        self.assertEqual(media_list[0].calculated_progress, 1)

    def test_season_progress_sort_counts_current_run_only(self):
        """The season list sort follows the show's rewatch date."""
        self.tv.rewatch_since = timezone.now()
        self.tv.save(update_fields=["rewatch_since"])
        self._watch(self.seasons[2], 3, timezone.now())

        media_list = BasicMedia.objects.get_media_list(
            user=self.user,
            media_type=MediaTypes.SEASON.value,
            status_filter="All",
            sort_filter="progress",
        )
        progress_by_season = {
            media.item.season_number: media.calculated_progress for media in media_list
        }

        self.assertEqual(progress_by_season[2], 3)
        self.assertIsNone(progress_by_season[1])

    def test_save_view_starts_a_rewatch_right_now(self):
        """The right now option stamps the current moment."""
        before = timezone.now()

        self.client.post(f"{self._save_url()}?next=/", {"mode": "now"})

        self.tv.refresh_from_db()
        self.assertIsNotNone(self.tv.rewatch_since)
        self.assertGreaterEqual(self.tv.rewatch_since, before)
        self.assertLessEqual(self.tv.rewatch_since, timezone.now())

    def test_save_view_starts_a_rewatch_at_a_specific_moment(self):
        """A specific date and time is stored as given."""
        started = timezone.localtime(timezone.now() - timedelta(days=3)).replace(
            hour=20,
            minute=30,
            second=0,
            microsecond=0,
        )

        self.client.post(
            f"{self._save_url()}?next=/",
            {
                "mode": "date",
                "rewatch_since": started.strftime("%Y-%m-%dT%H:%M"),
            },
        )

        self.tv.refresh_from_db()
        self.assertEqual(self.tv.rewatch_since, started)

    def test_save_view_requires_a_date_in_date_mode(self):
        """Submitting the date option without a value changes nothing."""
        self.client.post(f"{self._save_url()}?next=/", {"mode": "date"})

        self.tv.refresh_from_db()
        self.assertIsNone(self.tv.rewatch_since)

    def test_save_view_ends_a_rewatch(self):
        """The end option clears the date again."""
        self.tv.rewatch_since = timezone.now()
        self.tv.save(update_fields=["rewatch_since"])

        self.client.post(f"{self._save_url()}?next=/", {"mode": "end"})

        self.tv.refresh_from_db()
        self.assertIsNone(self.tv.rewatch_since)

    def test_modal_view_renders_the_form(self):
        """The modal offers both options for a tracked show."""
        response = self.client.get(
            reverse("rewatch_modal", kwargs={"instance_id": self.tv.pk}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_rewatch.html")
        self.assertContains(response, "Right now")
        self.assertContains(response, "Specific date")

        # The browser only prefills a datetime-local input in this exact format
        self.assertContains(
            response,
            f'value="{timezone.localtime().strftime("%Y-%m-%dT%H:%M")}"',
        )

    def test_rewatch_views_reject_other_users(self):
        """A show belonging to someone else cannot be rewatched."""
        other_credentials = {"username": "other", "password": "12345"}
        get_user_model().objects.create_user(**other_credentials)
        self.client.login(**other_credentials)

        self.assertEqual(
            self.client.post(f"{self._save_url()}?next=/", {"mode": "now"}).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("rewatch_modal", kwargs={"instance_id": self.tv.pk}),
            ).status_code,
            404,
        )

    def _save_url(self):
        """Return the rewatch save URL for the fixture show."""
        return reverse("rewatch_save", kwargs={"instance_id": self.tv.pk})
