from datetime import date, timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from app.models import Status
from app.release import (
    is_released,
    is_released_episode,
    is_visible_media,
    is_visible_tv,
    released_episodes,
    released_seasons,
)


class IsReleasedTests(SimpleTestCase):
    """Date comparisons for the shared release rule."""

    def test_missing_and_invalid_dates_are_unreleased(self):
        """None, blank, and unparseable values are not released."""
        self.assertFalse(is_released(None))
        self.assertFalse(is_released(""))
        self.assertFalse(is_released("not-a-date"))

    def test_future_today_and_past_dates(self):
        """Today and earlier dates are released. Later dates are not."""
        today = date(2026, 11, 4)
        self.assertTrue(is_released(today, today))
        self.assertTrue(is_released(today - timedelta(days=1), today))
        self.assertFalse(is_released(today + timedelta(days=1), today))

    def test_partial_dates(self):
        """Year-only dates are treated as January 1 of that year."""
        self.assertTrue(is_released("2020", date(2026, 11, 4)))
        self.assertFalse(is_released("2027-12", date(2026, 11, 4)))


class ReleasedEpisodeTests(SimpleTestCase):
    """Episode visibility when air dates are missing or in the future."""

    def setUp(self):
        """Fix today so episode dates do not depend on the clock."""
        self.today = date(2026, 11, 4)
        self.past = "2026-10-01"
        self.future = "2026-12-01"

    def test_future_dated_episode_is_hidden(self):
        """An episode dated after today is not released."""
        episode = {"episode_number": 2, "air_date": self.future}
        self.assertFalse(is_released_episode(episode, [], self.today))

    def test_trailing_episode_without_a_date_is_hidden(self):
        """A future episode with no air date is hidden."""
        episodes = [
            {"episode_number": 1, "air_date": self.past, "title": "Aired"},
            {"episode_number": 2, "air_date": None, "title": "No date"},
        ]
        visible = released_episodes(episodes, self.today)
        self.assertEqual([episode["title"] for episode in visible], ["Aired"])

    def test_undated_episode_before_an_aired_later_episode_stays(self):
        """A gap with no date stays when a later episode has already aired."""
        episodes = [
            {"episode_number": 1, "air_date": None, "title": "Gap"},
            {"episode_number": 2, "air_date": self.past, "title": "Aired"},
            {"episode_number": 3, "air_date": self.future, "title": "Future"},
            {"episode_number": 4, "air_date": "", "title": "Trailing"},
        ]
        visible = released_episodes(episodes, self.today)
        self.assertEqual(
            [episode["title"] for episode in visible],
            ["Gap", "Aired"],
        )


def _season(number, status, *, next_event, progress, max_progress):
    return SimpleNamespace(
        item=SimpleNamespace(season_number=number),
        status=status,
        next_event=next_event,
        progress=progress,
        max_progress=max_progress,
    )


def _show(*seasons):
    return SimpleNamespace(seasons=SimpleNamespace(all=lambda: seasons))


class VisibleMediaTests(SimpleTestCase):
    """Card visibility from the next future release."""

    def test_in_progress_stays_while_released_backlog_remains(self):
        """Caught-up titles with a future episode are hidden."""
        upcoming = SimpleNamespace(next_event=object(), progress=1, max_progress=5)
        caught_up = SimpleNamespace(next_event=object(), progress=5, max_progress=5)
        self.assertTrue(is_visible_media(upcoming, Status.IN_PROGRESS.value))
        self.assertFalse(is_visible_media(caught_up, Status.IN_PROGRESS.value))

    def test_planning_with_a_future_release_is_hidden(self):
        """Planning titles stay only when nothing is scheduled ahead."""
        upcoming = SimpleNamespace(next_event=object(), progress=0, max_progress=None)
        quiet = SimpleNamespace(next_event=None, progress=0, max_progress=None)
        self.assertFalse(is_visible_media(upcoming, Status.PLANNING.value))
        self.assertTrue(is_visible_media(quiet, Status.PLANNING.value))

    def test_show_follows_the_last_season_only(self):
        """Older aired seasons do not keep a show whose latest season is unreleased."""
        aired = _season(
            1,
            Status.COMPLETED.value,
            next_event=None,
            progress=10,
            max_progress=10,
        )
        unavailable = _season(
            2,
            Status.PLANNING.value,
            next_event=object(),
            progress=0,
            max_progress=None,
        )
        caught_up = _season(
            2,
            Status.IN_PROGRESS.value,
            next_event=object(),
            progress=7,
            max_progress=7,
        )
        still_airing = _season(
            2,
            Status.IN_PROGRESS.value,
            next_event=object(),
            progress=1,
            max_progress=7,
        )

        self.assertFalse(is_visible_tv(_show(aired, unavailable)))
        self.assertFalse(is_visible_tv(_show(aired, caught_up)))
        self.assertTrue(is_visible_tv(_show(aired, still_airing)))


class ReleasedSeasonTests(SimpleTestCase):
    """Season lists drop seasons that have not premiered."""

    def test_keeps_the_open_season_and_released_seasons(self):
        """Missing and future premieres are dropped, except the open season."""
        today = timezone.localdate()
        seasons = [
            {"season_number": 1, "first_air_date": "2020-01-01"},
            {"season_number": 2, "first_air_date": None},
            {
                "season_number": 3,
                "first_air_date": (today + timedelta(days=10)).isoformat(),
            },
        ]
        visible = released_seasons(seasons, keep_season_number=3)
        self.assertEqual(
            [season["season_number"] for season in visible],
            [1, 3],
        )
