"""What one range aggregate may cost, and that sharing work changes no number.

A sync pass rebuilds several ranges over the same days. These tests pin the
work a range must not repeat, and that a range built inside a pass publishes
exactly what it publishes when built alone.
"""

import random
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models.signals import post_init
from django.test import TestCase
from django.utils import timezone

from app import statistics as stats
from app import statistics_aggregator, statistics_highlights, statistics_sync
from app.models import TV, Anime, Book, Game, Item, MediaTypes, Movie, Sources, Status
from app.statistics_aggregator import _aggregate_statistics_from_days
from app.statistics_refresh import _get_predefined_range_dates, _resolve_day_list
from app.statistics_talent import STATISTICS_TOP_N

ALL_RANGES = (*statistics_sync.HOT_RANGES, *statistics_sync.HEAVY_RANGES)
COMPLETED_BOOKS = STATISTICS_TOP_N + 10


class RangeAggregateTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="range-aggregate")
        now = timezone.now()

        def items(media_type, count, **extra):
            return Item.objects.bulk_create(
                [
                    Item(
                        media_id=f"range-{media_type}-{index}",
                        source=Sources.MANUAL.value,
                        media_type=media_type,
                        library_media_type=media_type,
                        title=f"Range {media_type} {index}",
                        runtime_minutes=100,
                        **extra,
                    )
                    for index in range(count)
                ]
            )

        movie_offsets = (0, 1, 3, 6, 20, 45, 100, 200, 300, 500)
        Movie.objects.bulk_create(
            [
                Movie(
                    user=cls.user,
                    item=item,
                    status=Status.COMPLETED.value,
                    score=5 + index % 5,
                    start_date=now - timedelta(days=offset, hours=2),
                    end_date=now - timedelta(days=offset),
                )
                for index, (item, offset) in enumerate(
                    zip(
                        items(MediaTypes.MOVIE.value, len(movie_offsets)),
                        movie_offsets,
                        strict=True,
                    )
                )
            ]
        )
        books = items(MediaTypes.BOOK.value, COMPLETED_BOOKS + 2, number_of_pages=320)
        Book.objects.bulk_create(
            [
                Book(
                    user=cls.user,
                    item=item,
                    status=Status.COMPLETED.value,
                    progress=300,
                    start_date=now - timedelta(days=index * 7 + 2),
                    end_date=now - timedelta(days=index * 7),
                )
                for index, item in enumerate(books[:COMPLETED_BOOKS])
            ]
            + [
                # Completed with no dates, and a second entry for one title.
                Book(
                    user=cls.user,
                    item=books[COMPLETED_BOOKS],
                    status=Status.COMPLETED.value,
                    progress=0,
                ),
                Book(
                    user=cls.user,
                    item=books[0],
                    status=Status.COMPLETED.value,
                    progress=120,
                    start_date=now - timedelta(days=12),
                    end_date=now - timedelta(days=10),
                ),
            ]
        )
        Game.objects.create(
            user=cls.user,
            item=items(MediaTypes.GAME.value, 1)[0],
            status=Status.IN_PROGRESS.value,
            progress=600,
            start_date=now - timedelta(days=9),
            end_date=now - timedelta(days=4),
        )

    def setUp(self):
        cache.clear()

    def build_alone(self, range_name):
        start_date, end_date = _get_predefined_range_dates(range_name)
        random.seed(range_name)
        data = _aggregate_statistics_from_days(
            self.user,
            _resolve_day_list(self.user, start_date, end_date),
            start_date,
            end_date,
            build_missing=True,
        )
        statistics_highlights.normalize_highlight_images(data)
        return statistics_sync.dehydrate_payload(data)

    def warm(self, range_name="All Time"):
        statistics_sync._aggregate_range(self.user, range_name, 0)


class PassEqualityTests(RangeAggregateTestCase):
    def test_a_range_built_in_a_pass_equals_the_range_built_alone(self):
        # Warm first. Building a day draws random numbers (backfill jitter), a
        # different count inline than in a batch, and the highlight cards pick
        # at random: only warm runs draw the same sequence.
        for name in ALL_RANGES:
            self.warm(name)
        alone = {name: self.build_alone(name) for name in ALL_RANGES}

        pass_memo = {}
        for name in ALL_RANGES:
            with self.subTest(range=name):
                random.seed(name)
                data = statistics_sync._aggregate_range(
                    self.user, name, 0, pass_memo=pass_memo
                )
                self.assertEqual(statistics_sync.dehydrate_payload(data), alone[name])
        self.assertIn("history_index", pass_memo)

    def test_a_pass_reads_the_history_index_once(self):
        with patch.object(
            statistics_highlights,
            "_load_history_index_days",
            wraps=statistics_highlights._load_history_index_days,
        ) as load_index:
            outcome = statistics_sync.run_sync(
                self.user.id, only_ranges=statistics_sync.HOT_RANGES
            )

        self.assertEqual(outcome["status"], "done")
        self.assertEqual(len(outcome["published"]), len(statistics_sync.HOT_RANGES))
        self.assertEqual(load_index.call_count, 1)

    def test_a_pass_loads_and_annotates_each_card_row_once(self):
        for name in ALL_RANGES:
            self.warm(name)
        loaded = []
        annotated = []
        fetch = statistics_aggregator._fetch_media_objects
        annotate = stats._annotate_top_rated_media

        def record_fetch(refs):
            loaded.extend(refs)
            return fetch(refs)

        def record_annotate(media):
            annotated.extend((row._meta.model_name, row.pk) for row in media)
            return annotate(media)

        with (
            patch.object(statistics_aggregator, "_fetch_media_objects", record_fetch),
            patch.object(stats, "_annotate_top_rated_media", record_annotate),
        ):
            outcome = statistics_sync.run_sync(self.user.id, only_ranges=ALL_RANGES)

        self.assertEqual(outcome["status"], "done")
        self.assertTrue(loaded)
        self.assertTrue(annotated)
        self.assertEqual(len(loaded), len(set(loaded)))
        self.assertEqual(len(annotated), len(set(annotated)))

    def test_ranges_of_a_pass_get_their_own_copies_of_shared_rows(self):
        pass_memo = {}
        week = statistics_sync._aggregate_range(
            self.user, "Last 7 Days", 0, pass_memo=pass_memo
        )
        month = statistics_sync._aggregate_range(
            self.user, "Last 30 Days", 0, pass_memo=pass_memo
        )

        week_movie = week["top_rated"][0]
        month_movie = next(
            movie for movie in month["top_rated"] if movie.pk == week_movie.pk
        )
        self.assertIsNot(week_movie, month_movie)
        self.assertIsNot(week_movie.item, month_movie.item)
        # A card's own attributes stay with that range's copy.
        week_movie.aggregated_score = -1
        week_movie.item.title = "changed in one range"
        self.assertNotEqual(month_movie.aggregated_score, -1)
        self.assertNotEqual(month_movie.item.title, "changed in one range")
        # Within one range the overall and per-type lists still share a row.
        self.assertIs(week["top_rated_by_type"][MediaTypes.MOVIE.value][0], week_movie)
        # The rows the memo keeps are never the ones a range was handed.
        stored = pass_memo["top_rated_rows"][
            (MediaTypes.MOVIE.value, week_movie.pk, week_movie.item_id)
        ]
        self.assertIsNot(stored, week_movie)
        self.assertIsNot(stored, month_movie)

    def test_the_pass_memo_is_not_published(self):
        pass_memo = {}
        data = statistics_sync._aggregate_range(
            self.user, "Last 30 Days", 0, pass_memo=pass_memo
        )

        self.assertTrue(pass_memo)
        self.assertFalse(
            any(value is pass_memo for value in data.values()),
        )
        self.assertNotIn("history_index", data)
        self.assertNotIn("history_days", data)


class RangeWorkBoundTests(RangeAggregateTestCase):
    def test_sync_publishes_when_item_type_differs_from_row_model(self):
        movie = Movie.objects.filter(user=self.user).order_by("pk").first()
        Item.objects.filter(pk=movie.item_id).update(media_type=MediaTypes.BOOK.value)

        outcome = statistics_sync.run_sync(self.user.pk)

        self.assertEqual(outcome["status"], "done")
        cache.clear()
        snapshot = statistics_sync.load_snapshot(self.user.pk, "All Time")
        cards = snapshot["data"]["top_rated"]
        self.assertTrue(any(isinstance(card, Movie) and card.pk == movie.pk for card in cards))

    def test_deleted_row_during_annotation_does_not_shift_other_cards(self):
        self.warm()
        original = stats._annotate_top_rated_media
        deleted = []

        def delete_then_annotate(rows):
            deleted.append((rows[0]._meta.concrete_model, rows[0].pk))
            rows[0].delete()
            return original(rows)

        with patch.object(stats, "_annotate_top_rated_media", side_effect=delete_then_annotate):
            data = statistics_sync._aggregate_range(self.user, "All Time", 0)

        cards = data["top_rated"]
        self.assertTrue(cards)
        self.assertNotIn(deleted[0], [(card._meta.concrete_model, card.pk) for card in cards])
        self.assertTrue(all(hasattr(card, "aggregated_score") for card in cards))

    def test_annotation_uses_row_model_and_drops_deleted_rows(self):
        movies = list(Movie.objects.filter(user=self.user).order_by("pk")[:2])
        movies[0].item.media_type = MediaTypes.BOOK.value
        movies[0].aggregated_score = 8.5
        movies[0].route_media_type = MediaTypes.ANIME.value
        movies[1].delete()

        result = stats._annotate_top_rated_media(movies)

        self.assertEqual([row.pk for row in result], [movies[0].pk])
        self.assertEqual(result[0].aggregated_score, 8.5)
        self.assertEqual(result[0].route_media_type, MediaTypes.ANIME.value)
        self.assertEqual(result[0].max_progress, 1)

    def test_grouped_and_flat_anime_with_same_id_publish_distinct_cards(self):
        now = timezone.now()
        anime_item = Item.objects.create(
            media_id="flat-anime", source=Sources.MANUAL.value,
            media_type=MediaTypes.ANIME.value, title="Flat anime",
        )
        tv_item = Item.objects.create(
            media_id="grouped-anime", source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value, library_media_type=MediaTypes.ANIME.value,
            title="Grouped anime",
        )
        anime = Anime.objects.create(user=self.user, item=anime_item, score=7)
        tv = TV.objects.create(pk=anime.pk, user=self.user, item=tv_item, score=9)
        day = timezone.localdate()
        entries = {
            str(item.pk): {
                "media_type": MediaTypes.ANIME.value, "media_id": row.pk,
                "score": score, "activity_dt": now.isoformat(),
            }
            for item, row, score in [(anime_item, anime, 7), (tv_item, tv, 9)]
        }
        data = _aggregate_statistics_from_days(
            self.user, [day], now, now,
            prebuilt_days={day: {"items": {MediaTypes.ANIME.value: entries}}},
        )
        cards = {card.item_id: card for card in data["top_rated"]}
        self.assertIsInstance(cards[anime_item.pk], Anime)
        self.assertIsInstance(cards[tv_item.pk], TV)
        self.assertEqual(cards[anime_item.pk].aggregated_score, 7)
        self.assertEqual(cards[tv_item.pk].aggregated_score, 9)
        self.assertEqual(cards[tv_item.pk].route_media_type, MediaTypes.ANIME.value)

        statistics_sync.publish_snapshot(self.user.pk, "All Time", data, 0)
        cache.clear()
        restored = statistics_sync.load_snapshot(self.user.pk, "All Time")["data"]
        cards = {card.item_id: card for card in restored["top_rated"]}
        self.assertIsInstance(cards[anime_item.pk], Anime)
        self.assertIsInstance(cards[tv_item.pk], TV)
        self.assertEqual(cards[tv_item.pk].aggregated_score, 9)
        self.assertEqual(cards[tv_item.pk].route_media_type, MediaTypes.ANIME.value)

    def test_top_rated_rows_are_annotated_once_and_shared(self):
        self.warm()
        with patch.object(
            stats,
            "_annotate_top_rated_media",
            wraps=stats._annotate_top_rated_media,
        ) as annotate:
            data = statistics_sync._aggregate_range(self.user, "All Time", 0)

        self.assertEqual(annotate.call_count, 1)
        overall = {movie.pk: movie for movie in data["top_rated"]}
        by_type = data["top_rated_by_type"][MediaTypes.MOVIE.value]
        self.assertEqual(len(by_type), 10)
        for movie in by_type:
            self.assertIs(overall[movie.pk], movie)
            self.assertEqual(movie.max_progress, 1)
            self.assertIsNotNone(movie.aggregated_score)

    def test_reading_completions_are_scanned_without_building_models(self):
        self.warm()
        built = []

        def count(sender, **kwargs):
            built.append(sender)

        post_init.connect(count, sender=Book)
        self.addCleanup(post_init.disconnect, count, sender=Book)
        data = statistics_sync._aggregate_range(self.user, "All Time", 0)

        book = data["book_consumption"]
        # One finished title per item: the re-read shares its title's slot.
        self.assertEqual(book["completions"]["total"], COMPLETED_BOOKS + 1)
        # One length per completed entry: the undated one counts its pages.
        self.assertEqual(len(book["completed_lengths"]), COMPLETED_BOOKS + 2)
        # Only the rows the top-items card shows are loaded as models.
        self.assertLessEqual(len(built), STATISTICS_TOP_N)

    def test_a_warm_range_reads_its_days_once(self):
        self.warm("Last 7 Days")
        timings = {}
        with (
            patch.object(
                statistics_sync,
                "_missing_days",
                side_effect=AssertionError("day payloads decoded twice"),
            ),
            patch.object(statistics_sync, "_build_days") as build_days,
        ):
            statistics_sync._aggregate_range(
                self.user, "Last 7 Days", 0, timings=timings
            )

        build_days.assert_not_called()
        self.assertEqual(timings["missing_days"], 0)
        self.assertEqual(timings["days_decoded"], 7)

    def test_missing_days_are_built_in_one_batch_not_one_at_a_time(self):
        timings = {}
        with patch(
            "app.statistics_aggregator.build_stats_for_day",
            side_effect=AssertionError("a missing day was built on its own"),
        ):
            data = statistics_sync._aggregate_range(
                self.user, "Last 7 Days", 0, timings=timings
            )

        self.assertEqual(timings["missing_days"], 7)
        self.assertEqual(
            data["movie_consumption"]["plays"]["total"],
            4,
        )
        self.assertEqual(statistics_sync._missing_days(self.user.id, []), set())
        start_date, end_date = _get_predefined_range_dates("Last 7 Days")
        days = _resolve_day_list(self.user, start_date, end_date)
        self.assertEqual(statistics_sync._missing_days(self.user.id, days), set())
