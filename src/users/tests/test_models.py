from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from django_celery_results.models import TaskResult

from users.models import (
    HomeSortChoices,
    MediaTypes,
)


class UserUpdatePreferenceTests(TestCase):
    """Tests for the User.update_preference method."""

    def setUp(self):
        """Set up test data."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

    def test_update_preference_no_new_value(self):
        """Test update_preference when no new value is provided."""
        # Set initial value
        self.user.home_sort = HomeSortChoices.UPCOMING
        self.user.save()

        # Call update_preference with no new value
        result = self.user.update_preference("home_sort", None)

        # Should return current value
        self.assertEqual(result, HomeSortChoices.UPCOMING)
        # Should not change the value
        self.user.refresh_from_db()
        self.assertEqual(self.user.home_sort, HomeSortChoices.UPCOMING)

    def test_update_preference_same_value(self):
        """Test update_preference when the new value is the same as current."""
        # Set initial value
        self.user.home_sort = HomeSortChoices.UPCOMING
        self.user.save()

        # Call update_preference with same value
        result = self.user.update_preference("home_sort", HomeSortChoices.UPCOMING)

        # Should return current value
        self.assertEqual(result, HomeSortChoices.UPCOMING)
        # Should not change the value
        self.user.refresh_from_db()
        self.assertEqual(self.user.home_sort, HomeSortChoices.UPCOMING)

    def test_update_preference_valid_value(self):
        """Test update_preference with a valid new value."""
        # Set initial value
        self.user.home_sort = HomeSortChoices.UPCOMING
        self.user.save()

        # Call update_preference with new valid value
        result = self.user.update_preference("home_sort", HomeSortChoices.TITLE)

        # Should return new value
        self.assertEqual(result, HomeSortChoices.TITLE)
        # Should change the value
        self.user.refresh_from_db()
        self.assertEqual(self.user.home_sort, HomeSortChoices.TITLE)

    def test_update_preference_invalid_value(self):
        """Test update_preference with an invalid new value."""
        # Set initial value
        self.user.home_sort = HomeSortChoices.UPCOMING
        self.user.save()

        # Call update_preference with invalid value
        result = self.user.update_preference("home_sort", "invalid_value")

        # Should return current value
        self.assertEqual(result, HomeSortChoices.UPCOMING)
        # Should not change the value
        self.user.refresh_from_db()
        self.assertEqual(self.user.home_sort, HomeSortChoices.UPCOMING)

    def test_update_preference_boolean_field(self):
        """Test update_preference with a boolean field."""
        # Set initial value
        self.user.tv_enabled = True
        self.user.save()

        # Call update_preference with new value
        result = self.user.update_preference(field_name="tv_enabled", new_value=False)

        # Should return new value
        self.assertEqual(result, False)
        # Should change the value
        self.user.refresh_from_db()
        self.assertEqual(self.user.tv_enabled, False)

    def test_update_preference_last_search_type_valid(self):
        """Test update_preference with last_search_type and valid value."""
        # Set initial value
        self.user.last_search_type = MediaTypes.TV.value
        self.user.save()

        # Call update_preference with new valid value
        result = self.user.update_preference("last_search_type", MediaTypes.MOVIE.value)

        # Should return new value
        self.assertEqual(result, MediaTypes.MOVIE.value)
        # Should change the value
        self.user.refresh_from_db()
        self.assertEqual(self.user.last_search_type, MediaTypes.MOVIE.value)

    def test_update_preference_last_search_type_invalid(self):
        """Test update_preference with last_search_type and invalid value."""
        # Set initial value
        self.user.last_search_type = MediaTypes.TV.value
        self.user.save()

        # Call update_preference with invalid value (SEASON is in EXCLUDED_SEARCH_TYPES)
        result = self.user.update_preference(
            "last_search_type",
            MediaTypes.SEASON.value,
        )

        # Should return current value
        self.assertEqual(result, MediaTypes.TV.value)
        # Should not change the value
        self.user.refresh_from_db()
        self.assertEqual(self.user.last_search_type, MediaTypes.TV.value)

    def test_update_preference_daily_digest_enabled(self):
        """Test update_preference with daily_digest_enabled field."""
        # Set initial value
        self.user.daily_digest_enabled = True
        self.user.save()

        # Call update_preference with new value
        result = self.user.update_preference(
            field_name="daily_digest_enabled",
            new_value=False,
        )

        # Should return new value
        self.assertEqual(result, False)
        # Should change the value
        self.user.refresh_from_db()
        self.assertEqual(self.user.daily_digest_enabled, False)

    def test_update_preference_release_notifications_enabled(self):
        """Test update_preference with release_notifications_enabled field."""
        # Set initial value
        self.user.release_notifications_enabled = True
        self.user.save()

        # Call update_preference with new value
        result = self.user.update_preference(
            field_name="release_notifications_enabled",
            new_value=False,
        )

        # Should return new value
        self.assertEqual(result, False)
        # Should change the value
        self.user.refresh_from_db()
        self.assertEqual(self.user.release_notifications_enabled, False)


class UserGetImportTasksTests(TestCase):
    """Tests for the User.get_import_tasks method."""

    def setUp(self):
        """Set up test data."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.credentials_other = {"username": "otheruser", "password": "12345"}
        self.other_user = get_user_model().objects.create_user(
            **self.credentials_other,
        )

        # Create a crontab schedule for periodic tasks
        self.crontab = CrontabSchedule.objects.create(
            minute="0",
            hour="0",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
        )

    @patch("users.helpers.process_task_result")
    def test_get_import_tasks_results(self, mock_process_task_result):
        """Test get_import_tasks returns correct task results."""
        # Create mock processed task
        mock_task = MagicMock()
        mock_task.summary = "Imported 10 items"
        mock_task.errors = []
        mock_task.mode = "overwrite"
        mock_process_task_result.return_value = mock_task

        # Create task results for the user
        TaskResult.objects.create(
            task_id="task1",
            task_name="Import from Trakt",
            task_kwargs=(f"{{'user_id': {self.user.id}, 'username': 'testuser'}}"),
            status="SUCCESS",
            date_done=timezone.now() - timedelta(days=1),
            result="{}",
        )

        TaskResult.objects.create(
            task_id="task2",
            task_name="Import from MyAnimeList",
            task_kwargs=(f"{{'user_id': {self.user.id}, 'username': 'testuser'}}"),
            status="FAILURE",
            date_done=timezone.now(),
            result="{}",
        )

        # Create task result for another user (should not be included)
        TaskResult.objects.create(
            task_id="task3",
            task_name="Import from Trakt",
            task_kwargs=(
                f"{{'user_id': {self.other_user.id}, 'username': 'otheruser'}}"
            ),
            status="SUCCESS",
            date_done=timezone.now(),
            result="{}",
        )

        # Get import tasks
        import_tasks = self.user.get_import_tasks()

        # Check results
        self.assertEqual(len(import_tasks["results"]), 2)

        # Check first result (most recent)
        self.assertEqual(import_tasks["results"][1]["task"], mock_task)
        self.assertEqual(import_tasks["results"][1]["source"], "trakt")
        self.assertEqual(import_tasks["results"][1]["status"], "SUCCESS")
        self.assertEqual(import_tasks["results"][1]["summary"], "Imported 10 items")
        self.assertEqual(import_tasks["results"][1]["errors"], [])

        # Check second result
        self.assertEqual(import_tasks["results"][0]["task"], mock_task)
        self.assertEqual(import_tasks["results"][0]["source"], "myanimelist")
        self.assertEqual(import_tasks["results"][0]["status"], "FAILURE")

    @patch("users.helpers.get_next_run_info")
    def test_get_import_tasks_schedules(self, mock_get_next_run_info):
        """Test get_import_tasks returns correct scheduled tasks."""
        # Create mock next run info
        mock_get_next_run_info.return_value = {
            "next_run": timezone.now() + timedelta(days=1),
            "frequency": "Daily at midnight",
            "mode": "overwrite",
        }

        # Create periodic tasks for the user
        periodic_task1 = PeriodicTask.objects.create(
            name="Import from Trakt for testuser at daily",
            task="Import from Trakt",
            kwargs=(f'{{"user_id": {self.user.id}, "username": "testuser"}}'),
            crontab=self.crontab,
            enabled=True,
        )

        periodic_task2 = PeriodicTask.objects.create(
            name="Import from AniList for testuser at weekly",
            task="Import from AniList",
            kwargs=(f'{{"user_id": {self.user.id}, "username": "testuser"}}'),
            crontab=self.crontab,
            enabled=True,
        )

        # Create disabled periodic task (should not be included)
        PeriodicTask.objects.create(
            name="Import from SIMKL for testuser at daily",
            task="Import from SIMKL",
            kwargs=(f'{{"user_id": {self.user.id}, "username": "testuser"}}'),
            crontab=self.crontab,
            enabled=False,
        )

        # Create periodic task for another user (should not be included)
        PeriodicTask.objects.create(
            name="Import from Trakt for otheruser at daily",
            task="Import from Trakt",
            kwargs=(f'{{"user_id": {self.other_user.id}, "username": "testuser"}}'),
            crontab=self.crontab,
            enabled=True,
        )

        # Get import tasks
        import_tasks = self.user.get_import_tasks()

        # Check schedules
        self.assertEqual(len(import_tasks["schedules"]), 2)

        # Check first schedule
        self.assertEqual(import_tasks["schedules"][0]["task"], periodic_task1)
        self.assertEqual(import_tasks["schedules"][0]["source"], "trakt")
        self.assertEqual(import_tasks["schedules"][0]["username"], "testuser")
        self.assertEqual(import_tasks["schedules"][0]["schedule"], "Daily at midnight")

        # Check second schedule
        self.assertEqual(import_tasks["schedules"][1]["task"], periodic_task2)
        self.assertEqual(import_tasks["schedules"][1]["source"], "anilist")
        self.assertEqual(import_tasks["schedules"][1]["username"], "testuser")

    @patch("users.helpers.process_task_result")
    @patch("users.helpers.get_next_run_info")
    def test_get_import_tasks_empty(
        self,
        mock_get_next_run_info,
        _,
    ):
        """Test get_import_tasks when there are no tasks."""
        # Set up mocks
        mock_get_next_run_info.return_value = None

        # Get import tasks
        import_tasks = self.user.get_import_tasks()

        # Check results
        self.assertEqual(len(import_tasks["results"]), 0)
        self.assertEqual(len(import_tasks["schedules"]), 0)

    @patch("users.helpers.process_task_result")
    def test_get_import_tasks_unknown_source(self, mock_process_task_result):
        """Test get_import_tasks with an unknown task source."""
        # Create mock processed task
        mock_task = MagicMock()
        mock_task.summary = "Imported 10 items"
        mock_task.errors = []
        mock_task.mode = "overwrite"
        mock_process_task_result.return_value = mock_task

        # Create task result with unknown source
        TaskResult.objects.create(
            task_id="task1",
            task_name="Import from Unknown",
            task_kwargs=(f"{{'user_id': {self.user.id}, 'username': 'testuser'}}"),
            status="SUCCESS",
            date_done=timezone.now(),
            result="{}",
        )

        # Get import tasks
        import_tasks = self.user.get_import_tasks()

        # Check results
        self.assertEqual(len(import_tasks["results"]), 0)
