"""Canonical watched state must agree with the legacy stores it projects from.

Completion state lives in six places today, so every assertion here pins the
projection against the store that actually owns the fact: MoviePlay rows and
duplicate Movie rows for movies, one row per watch for episodes, duplicate rows
for the flat types.
"""

import datetime
import logging

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase

from app.models import (
    TV,
    Book,
    Episode,
    Item,
    MediaTypes,
    Movie,
    MoviePlay,
    Season,
    Sources,
    Status,
    WatchState,
    calculate_state_digest,
)
from app.services.watch_state import (
    allocate_sequence,
    effective_state,
    effective_states,
    project_watch_state,
    suspend_projection,
)


def setUpModule():
    """Silence log noise for this module only."""
    logging.disable(logging.DEBUG)


def tearDownModule():
    """Restore logging so other modules' assertLogs still see records."""
    logging.disable(logging.NOTSET)


def _dt(day, hour=12):
    return datetime.datetime(2026, 1, day, hour, tzinfo=datetime.UTC)


class WatchStateDigestTests(TestCase):
    """The digest is what two sides compare, so its edges matter."""

    def test_sub_second_difference_is_not_a_disagreement(self):
        base = _dt(1)
        self.assertEqual(
            calculate_state_digest(True, 1, base),
            calculate_state_digest(True, 1, base.replace(microsecond=500000)),
        )

    def test_state_differences_change_the_digest(self):
        base = calculate_state_digest(True, 1, _dt(1))
        self.assertNotEqual(base, calculate_state_digest(False, 1, _dt(1)))
        self.assertNotEqual(base, calculate_state_digest(True, 2, _dt(1)))
        self.assertNotEqual(base, calculate_state_digest(True, 1, _dt(2)))
        self.assertNotEqual(base, calculate_state_digest(True, 1, None))


class MovieProjectionTests(TestCase):
    """Movies are the hard case: two disagreeing play stores."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="owner")
        self.item = self._movie_item("100")

    @staticmethod
    def _movie_item(media_id):
        item, _created = Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            defaults={"title": "Film"},
        )
        return item

    def test_a_watch_projects_as_watched(self):
        movie = Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=_dt(1),
        )
        movie.watch(_dt(2))

        state = effective_state(self.user, self.item)
        self.assertTrue(state.watched)
        # watch() lazily preserves the pre-existing end_date as its own play,
        # so the earlier viewing is not lost when plays start.
        self.assertEqual(state.play_count, 2)
        self.assertEqual(state.first_watched_at, _dt(1))
        self.assertEqual(state.last_watched_at, _dt(2))

    def test_importer_shaped_duplicate_rows_count_as_separate_plays(self):
        for day in (1, 5):
            Movie.objects.create(
                item=self.item,
                user=self.user,
                status=Status.COMPLETED.value,
                end_date=_dt(day),
            )

        state = effective_state(self.user, self.item)
        self.assertEqual(state.play_count, 2)
        self.assertEqual(state.first_watched_at, _dt(1))
        self.assertEqual(state.last_watched_at, _dt(5))

    def test_removing_every_play_keeps_the_completed_status_the_user_sees(self):
        """Movie.unwatch() drops the play but leaves status at Completed.

        The two stores then disagree, and the projection reports the one the
        user is actually shown: media lists filter on status, so a row that
        still says Completed is still watched as far as the product is
        concerned. Reconciling that contradiction means changing unwatch
        behaviour, which belongs with the non-destructive retraction work, not
        with a projection that is meant to observe without changing anything.
        """
        movie = Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        movie.watch(_dt(1))
        movie.unwatch()

        movie.refresh_from_db()
        self.assertEqual(movie.status, Status.COMPLETED.value)

        state = effective_state(self.user, self.item)
        self.assertTrue(state.watched)
        self.assertEqual(state.play_count, 0)
        self.assertIsNone(state.last_watched_at)

    def test_dropping_the_status_too_leaves_the_row_unwatched(self):
        movie = Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        movie.watch(_dt(1))
        movie.unwatch()
        movie.status = Status.IN_PROGRESS.value
        movie.save(update_fields=["status"])

        state = effective_state(self.user, self.item)
        self.assertFalse(state.watched)
        self.assertEqual(state.play_count, 0)

    def test_a_deleted_play_reduces_the_count(self):
        movie = Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.COMPLETED.value,
        )
        movie.watch(_dt(1))
        movie.watch(_dt(5))
        self.assertEqual(effective_state(self.user, self.item).play_count, 2)

        MoviePlay.objects.filter(movie=movie, end_date=_dt(5)).delete()
        self.assertEqual(effective_state(self.user, self.item).play_count, 1)

    def test_state_is_scoped_to_one_user(self):
        other = get_user_model().objects.create_user(username="other")
        Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=_dt(1),
        )

        self.assertTrue(effective_state(self.user, self.item).watched)
        self.assertIsNone(effective_state(other, self.item))


class EpisodeProjectionTests(TestCase):
    """Only a finished episode play is a watch; open and dropped plays are not."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="owner")
        self.tv_item, _ = Item.objects.get_or_create(
            media_id="200",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            defaults={"title": "Show"},
        )
        self.season_item, _ = Item.objects.get_or_create(
            media_id="200",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            defaults={"title": "Show"},
        )
        self.episode_item, _ = Item.objects.get_or_create(
            media_id="200",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            defaults={"title": "Show"},
        )
        self.tv = TV.objects.create(
            item=self.tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        self.season = Season.objects.create(
            item=self.season_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.IN_PROGRESS.value,
        )

    def test_each_watch_is_a_play(self):
        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=_dt(1),
        )
        state = effective_state(self.user, self.episode_item)
        self.assertTrue(state.watched)
        self.assertEqual(state.play_count, 1)

        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=_dt(5),
        )
        state = effective_state(self.user, self.episode_item)
        self.assertEqual(state.play_count, 2)
        self.assertEqual(state.first_watched_at, _dt(1))
        self.assertEqual(state.last_watched_at, _dt(5))

    def test_a_dropped_episode_is_not_a_watch(self):
        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=_dt(1),
            status=Status.DROPPED.value,
        )
        state = effective_state(self.user, self.episode_item)
        self.assertFalse(state.watched)
        self.assertEqual(state.play_count, 0)

    def test_an_open_play_is_not_a_watch_until_it_finishes(self):
        """A check-in's In Progress play counts once it completes, not before."""
        play = Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            start_date=_dt(1),
            status=Status.IN_PROGRESS.value,
        )
        state = effective_state(self.user, self.episode_item)
        self.assertFalse(state.watched)
        self.assertEqual(state.play_count, 0)

        play.status = Status.COMPLETED.value
        play.end_date = _dt(1, hour=13)
        play.save()
        state = effective_state(self.user, self.episode_item)
        self.assertTrue(state.watched)
        self.assertEqual(state.play_count, 1)

    def test_deleting_the_watch_clears_the_state(self):
        episode = Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=_dt(1),
        )
        episode.delete()

        state = effective_state(self.user, self.episode_item)
        self.assertFalse(state.watched)
        self.assertEqual(state.play_count, 0)

    def test_deleting_the_item_does_not_resurrect_state(self):
        """Item deletion cascades, and post_delete then fires with no Item left.

        Reprojecting there would both raise and recreate a row for an item that
        no longer exists.
        """
        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=_dt(1),
        )
        item_pk = self.episode_item.pk

        Item.objects.filter(pk=item_pk).delete()

        self.assertFalse(WatchState.objects.filter(item_id=item_pk).exists())

    def test_containers_get_no_row_of_their_own(self):
        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=_dt(1),
        )
        self.assertIsNone(effective_state(self.user, self.tv_item))
        self.assertIsNone(effective_state(self.user, self.season_item))


class GroupedAnimeProjectionTests(TestCase):
    """Grouped anime is TV-shaped rows in the anime bucket, not a separate model.

    The same show can legitimately exist twice, differing only in
    ``Item.library_media_type``, so the two buckets must not share a state row.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="owner")

    def _episode_in_bucket(self, library_media_type):
        tv_item, _ = Item.objects.get_or_create(
            media_id="300",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=library_media_type,
            defaults={"title": "Show"},
        )
        season_item, _ = Item.objects.get_or_create(
            media_id="300",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            library_media_type=library_media_type,
            season_number=1,
            defaults={"title": "Show"},
        )
        episode_item, _ = Item.objects.get_or_create(
            media_id="300",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            library_media_type=library_media_type,
            season_number=1,
            episode_number=1,
            defaults={"title": "Show"},
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        return episode_item, season

    def test_the_two_buckets_keep_separate_state(self):
        tv_episode, tv_season = self._episode_in_bucket(MediaTypes.TV.value)
        anime_episode, _anime_season = self._episode_in_bucket(MediaTypes.ANIME.value)
        self.assertNotEqual(tv_episode.pk, anime_episode.pk)

        Episode.objects.create(
            item=tv_episode,
            related_season=tv_season,
            end_date=_dt(1),
        )

        self.assertTrue(effective_state(self.user, tv_episode).watched)
        self.assertIsNone(effective_state(self.user, anime_episode))


class FlatMediaProjectionTests(TestCase):
    """For flat types a repeat is a duplicate row, and only Completed counts."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="owner")
        self.item, _ = Item.objects.get_or_create(
            media_id="400",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            defaults={"title": "Book"},
        )

    def test_only_completed_rows_are_plays(self):
        Book.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        state = effective_state(self.user, self.item)
        self.assertFalse(state.watched)
        self.assertEqual(state.play_count, 0)

        Book.objects.create(
            item=self.item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=_dt(3),
        )
        state = effective_state(self.user, self.item)
        self.assertTrue(state.watched)
        self.assertEqual(state.play_count, 1)
        self.assertEqual(state.last_watched_at, _dt(3))


class ProjectionMechanicsTests(TestCase):
    """Recompute semantics: idempotent, suspendable, repairable."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="owner")
        self.item, _ = Item.objects.get_or_create(
            media_id="500",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            defaults={"title": "Film"},
        )

    def test_projection_is_idempotent(self):
        Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=_dt(1),
        )
        first = effective_state(self.user, self.item)
        project_watch_state(self.user, self.item)
        project_watch_state(self.user, self.item)

        self.assertEqual(WatchState.objects.filter(user=self.user).count(), 1)
        second = effective_state(self.user, self.item)
        self.assertEqual(first.state_digest, second.state_digest)
        self.assertEqual(first.revision, second.revision)

    def test_projection_emits_no_change_rows(self):
        Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=_dt(1),
        )
        # Projection records what is already true. A change records a decision,
        # and a backfill that emitted changes would push a whole library out to
        # every connected provider on upgrade.
        self.assertEqual(self.user.watch_state_changes.count(), 0)

    def test_suspension_defers_the_write_and_a_recompute_repairs_it(self):
        with suspend_projection():
            Movie.objects.create(
                item=self.item,
                user=self.user,
                status=Status.COMPLETED.value,
                end_date=_dt(1),
            )
        self.assertIsNone(effective_state(self.user, self.item))

        project_watch_state(self.user, self.item)
        self.assertTrue(effective_state(self.user, self.item).watched)

    def test_effective_states_is_keyed_by_item(self):
        Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=_dt(1),
        )
        states = effective_states(self.user, [self.item])
        self.assertEqual(list(states), [self.item.pk])
        self.assertTrue(states[self.item.pk].watched)

    def test_stored_digest_matches_the_stored_values(self):
        Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=_dt(1),
        )
        state = effective_state(self.user, self.item)
        self.assertEqual(state.state_digest, state.recalculate_digest())


class SequenceAllocationTests(TransactionTestCase):
    """Sequence order must equal commit order, so it cannot come from a PK."""

    def test_allocation_is_monotonic(self):
        user = get_user_model().objects.create_user(username="owner")
        allocated = [allocate_sequence(user) for _ in range(5)]
        self.assertEqual(allocated, [1, 2, 3, 4, 5])

    def test_allocation_is_per_user(self):
        first = get_user_model().objects.create_user(username="first")
        second = get_user_model().objects.create_user(username="second")

        self.assertEqual(allocate_sequence(first), 1)
        self.assertEqual(allocate_sequence(second), 1)
        self.assertEqual(allocate_sequence(first), 2)
