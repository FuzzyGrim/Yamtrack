from datetime import timedelta
from unittest.mock import call, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from app import history_cache
from app.discover import tab_cache as discover_tab_cache
from app.models import (
    TV,
    DiscoverFeedback,
    DiscoverFeedbackType,
    Item,
    ItemPersonCredit,
    ItemTag,
    MediaTypes,
    Movie,
    Person,
    Season,
    Sources,
    Status,
    Tag,
)


class DiscoverInvalidationSignalTests(TestCase):
    """Tests for Discover invalidation and priority refresh signals."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="discover-signal-user",
            password="secret123",
        )
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _item(self, media_type, media_id, *, season_number=None, episode_number=None):
        return Item.objects.create(
            media_id=str(media_id),
            source=Sources.TMDB.value,
            media_type=media_type,
            title=f"{media_type}-{media_id}",
            image="https://example.com/poster.jpg",
            season_number=season_number,
            episode_number=episode_number,
        )

    def _movie_metadata(self):
        return {"max_progress": 120}

    @patch("app.signals._schedule_credits_backfill_if_needed")
    @patch("app.signals._sync_owner_smart_lists_for_items")
    @patch("app.signals.statistics_cache.schedule_all_ranges_refresh")
    @patch("app.signals.statistics_cache.invalidate_statistics_days")
    @patch("app.signals.history_cache.invalidate_history_days")
    @patch("app.signals.discover_tab_cache.schedule_tab_refresh", return_value=True)
    @patch("app.models.Item.fetch_releases")
    @patch("app.models.providers.services.get_media_metadata")
    def test_movie_save_schedules_movie_and_all_discover_refreshes(
        self,
        mock_get_media_metadata,
        _mock_fetch_releases,
        mock_schedule_tab_refresh,
        _mock_invalidate_history_days,
        _mock_invalidate_statistics_days,
        _mock_schedule_all_ranges_refresh,
        _mock_sync_owner_smart_lists,
        _mock_schedule_credits_backfill,
    ):
        mock_get_media_metadata.return_value = self._movie_metadata()
        item = self._item(MediaTypes.MOVIE.value, "movie-1")
        # Someone who opened Discover lately: their tabs rebuild in the background.
        discover_tab_cache.mark_recently_used(self.user.id)

        Movie.objects.create(
            user=self.user,
            item=item,
            end_date=timezone.now(),
        )

        mock_schedule_tab_refresh.assert_has_calls(
            [
                call(
                    self.user.id,
                    MediaTypes.MOVIE.value,
                    show_more=False,
                    debounce_seconds=discover_tab_cache.DISCOVER_DEFAULT_REFRESH_DEBOUNCE_SECONDS,
                    countdown=discover_tab_cache.DISCOVER_DEFAULT_REFRESH_COUNTDOWN,
                    priority=discover_tab_cache.DISCOVER_TASK_PRIORITY_FOLLOWUP,
                ),
                call(
                    self.user.id,
                    "all",
                    show_more=False,
                    debounce_seconds=discover_tab_cache.DISCOVER_DEFAULT_REFRESH_DEBOUNCE_SECONDS,
                    countdown=discover_tab_cache.DISCOVER_DEFAULT_REFRESH_COUNTDOWN,
                    priority=discover_tab_cache.DISCOVER_TASK_PRIORITY_FOLLOWUP,
                ),
            ],
            any_order=False,
        )

    @patch("app.signals._sync_owner_smart_lists_for_items")
    @patch("app.signals.statistics_cache.schedule_all_ranges_refresh")
    @patch("app.signals.discover_tab_cache.schedule_tab_refresh", return_value=True)
    def test_season_save_schedules_tv_and_all_discover_refreshes(
        self,
        mock_schedule_tab_refresh,
        _mock_schedule_all_ranges_refresh,
        _mock_sync_owner_smart_lists,
    ):
        tv_item = self._item(MediaTypes.TV.value, "tv-1")
        discover_tab_cache.mark_recently_used(self.user.id)
        tv = TV.objects.create(user=self.user, item=tv_item)

        mock_schedule_tab_refresh.reset_mock()

        season_item = self._item(MediaTypes.SEASON.value, "tv-1", season_number=1)
        Season.objects.create(
            user=self.user,
            item=season_item,
            related_tv=tv,
        )

        mock_schedule_tab_refresh.assert_has_calls(
            [
                call(
                    self.user.id,
                    MediaTypes.TV.value,
                    show_more=False,
                    debounce_seconds=discover_tab_cache.DISCOVER_DEFAULT_REFRESH_DEBOUNCE_SECONDS,
                    countdown=discover_tab_cache.DISCOVER_DEFAULT_REFRESH_COUNTDOWN,
                    priority=discover_tab_cache.DISCOVER_TASK_PRIORITY_FOLLOWUP,
                ),
                call(
                    self.user.id,
                    "all",
                    show_more=False,
                    debounce_seconds=discover_tab_cache.DISCOVER_DEFAULT_REFRESH_DEBOUNCE_SECONDS,
                    countdown=discover_tab_cache.DISCOVER_DEFAULT_REFRESH_COUNTDOWN,
                    priority=discover_tab_cache.DISCOVER_TASK_PRIORITY_FOLLOWUP,
                ),
            ],
            any_order=False,
        )

    @patch("app.signals._sync_owner_smart_lists_for_items")
    @patch("app.signals.discover_tab_cache.invalidate_for_media_change")
    def test_item_tag_save_and_delete_invalidate_discover(
        self,
        mock_invalidate_for_media_change,
        _mock_sync_owner_smart_lists,
    ):
        item = self._item(MediaTypes.MOVIE.value, "movie-tag")
        tag = Tag.objects.create(user=self.user, name="Cozy")

        item_tag = ItemTag.objects.create(tag=tag, item=item)
        item_tag.delete()

        self.assertEqual(mock_invalidate_for_media_change.call_count, 2)
        mock_invalidate_for_media_change.assert_has_calls(
            [
                call(self.user.id, MediaTypes.MOVIE.value),
                call(self.user.id, MediaTypes.MOVIE.value),
            ],
        )

    @patch("app.signals.discover_tab_cache.invalidate_for_media_change")
    @patch("app.models.Item.fetch_releases")
    @patch("app.models.providers.services.get_media_metadata")
    def test_item_person_credit_save_and_delete_invalidate_discover(
        self,
        mock_get_media_metadata,
        _mock_fetch_releases,
        mock_invalidate_for_media_change,
    ):
        mock_get_media_metadata.return_value = self._movie_metadata()
        item = self._item(MediaTypes.MOVIE.value, "movie-credit")
        Movie.objects.create(user=self.user, item=item)
        mock_invalidate_for_media_change.reset_mock()

        person = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="person-1",
            name="Test Person",
        )

        credit = ItemPersonCredit.objects.create(
            item=item,
            person=person,
            role_type="cast",
            role="Lead",
        )
        credit.delete()

        self.assertEqual(mock_invalidate_for_media_change.call_count, 2)
        mock_invalidate_for_media_change.assert_has_calls(
            [
                call(self.user.id, MediaTypes.MOVIE.value),
                call(self.user.id, MediaTypes.MOVIE.value),
            ],
        )

    @patch("app.signals._schedule_credits_backfill_if_needed")
    @patch("app.signals._sync_owner_smart_lists_for_items")
    @patch("app.signals.statistics_cache.schedule_all_ranges_refresh")
    @patch("app.signals.statistics_cache.invalidate_statistics_days")
    @patch("app.signals.history_cache.schedule_history_refresh")
    @patch("app.signals.history_cache.invalidate_history_days")
    @patch("app.signals.discover_tab_cache.invalidate_for_media_change")
    @patch("app.models.Item.fetch_releases")
    @patch("app.models.providers.services.get_media_metadata")
    def test_active_discover_priority_delays_history_and_marks_statistics_days(
        self,
        mock_get_media_metadata,
        _mock_fetch_releases,
        mock_invalidate_for_media_change,
        mock_invalidate_history_days,
        mock_schedule_history_refresh,
        mock_invalidate_statistics_days,
        mock_schedule_all_ranges_refresh,
        _mock_sync_owner_smart_lists,
        _mock_schedule_credits_backfill,
    ):
        mock_get_media_metadata.return_value = self._movie_metadata()
        # Invalidation names the styles it rebuilds (the user's own); only
        # those get the delayed priority refresh.
        mock_invalidate_history_days.return_value = ("repeats",)
        discover_tab_cache.mark_active(self.user.id, MediaTypes.MOVIE.value)
        item = self._item(MediaTypes.MOVIE.value, "movie-priority")
        end_date = timezone.now() - timedelta(minutes=5)
        expected_day_key = history_cache.history_day_key(end_date)

        Movie.objects.create(
            user=self.user,
            item=item,
            end_date=end_date,
        )

        mock_invalidate_for_media_change.assert_called_once_with(
            self.user.id,
            MediaTypes.MOVIE.value,
        )
        mock_invalidate_history_days.assert_called_once_with(
            self.user.id,
            day_keys=[expected_day_key],
            logging_styles=("sessions", "repeats"),
            reason="movie_change",
            refresh_index=False,
        )
        mock_schedule_history_refresh.assert_called_once_with(
            self.user.id,
            "repeats",
            debounce_seconds=15,
            countdown=15,
            warm_days=0,
            day_keys=[expected_day_key],
            allow_inline=False,
        )
        mock_invalidate_statistics_days.assert_called_once_with(
            self.user.id,
            day_values=[expected_day_key],
            reason="movie_change",
        )
        # Marking the day is what queues the Statistics sync; there is no
        # separate range refresh to delay.
        mock_schedule_all_ranges_refresh.assert_not_called()

    @patch("app.signals._handle_media_cache_change")
    @patch("app.models.Item.fetch_releases")
    def test_media_save_clears_prior_dismiss_feedback(
        self,
        _mock_fetch_releases,
        _mock_handle_media_cache_change,
    ):
        item = self._item(MediaTypes.MOVIE.value, "movie-feedback-clear")
        DiscoverFeedback.objects.create(
            user=self.user,
            item=item,
            feedback_type=DiscoverFeedbackType.NOT_INTERESTED.value,
        )

        Movie.objects.create(
            user=self.user,
            item=item,
            status=Status.PLANNING.value,
        )

        self.assertFalse(
            DiscoverFeedback.objects.filter(
                user=self.user,
                item=item,
                feedback_type=DiscoverFeedbackType.NOT_INTERESTED.value,
            ).exists(),
        )
