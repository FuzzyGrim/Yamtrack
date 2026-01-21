from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.db.models import Prefetch
from django.test import TestCase
from django.utils import timezone

from app.models import (
    TV,
    Anime,
    Book,
    Episode,
    Game,
    Item,
    Manga,
    MediaManager,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from events.models import Event
from users.models import MediaStatusChoices

mock_path = Path(__file__).resolve().parent.parent / "mock_data"


class MediaManagerTests(TestCase):
    """Test case for the MediaManager class."""

    def setUp(self):
        """Set up test data for MediaManager tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        # Enable all media types for the user
        for media_type in MediaTypes.values:
            setattr(self.user, f"{media_type.lower()}_enabled", True)
        self.user.save()

        self.movie_item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/fightclub.jpg",
        )

        self.anime_item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="http://example.com/bebop.jpg",
        )

        self.game_item = Item.objects.create(
            media_id="1234",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="The Last of Us",
            image="http://example.com/tlou.jpg",
        )

        self.book_item = Item.objects.create(
            media_id="OL21733390M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="1984",
            image="http://example.com/1984.jpg",
        )

        self.manga_item = Item.objects.create(
            media_id="2",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Berserk",
            image="http://example.com/berserk.jpg",
        )

        self.movie = Movie.objects.create(
            item=self.movie_item,
            user=self.user,
            status=Status.COMPLETED.value,
            score=9,
        )

        self.anime = Anime.objects.create(
            item=self.anime_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            score=10,
            progress=13,
        )

        self.game = Game.objects.create(
            item=self.game_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            score=7,
            progress=120,
        )

        self.book = Book.objects.create(
            item=self.book_item,
            user=self.user,
            status=Status.PLANNING.value,
            score=0,
        )

        self.manga = Manga.objects.create(
            item=self.manga_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            score=10,
            progress=100,
        )

        self.season1_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        self.season1 = Season.objects.create(
            item=self.season1_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            score=8,
        )

        self.tv = TV.objects.get(user=self.user)

        for i in range(1, 5):
            episode_item = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Friends S1E{i}",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=i,
            )

            watched_episodes = 3
            if i <= watched_episodes:
                Episode.objects.create(
                    item=episode_item,
                    related_season=self.season1,
                    end_date=datetime(2023, 6, i, 0, 0, tzinfo=UTC),
                )

        for i in range(4, 7):
            Event.objects.create(
                item=self.anime_item,
                content_number=i + 13,
                datetime=timezone.now() + timedelta(days=i),
                notification_sent=False,
            )

    def test_get_historical_models(self):
        """Test the get_historical_models method."""
        manager = MediaManager()
        historical_models = manager.get_historical_models()

        expected_models = [
            f"historical{media_type}" for media_type in MediaTypes.values
        ]
        self.assertEqual(historical_models, expected_models)

    def test_get_media_list_with_status_filter(self):
        """Test the get_media_list method with status filter."""
        manager = MediaManager()

        media_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.ANIME.value,
            status_filter=Status.IN_PROGRESS.value,
            sort_filter="score",
        )

        self.assertEqual(len(media_list), 1)
        self.assertEqual(media_list[0], self.anime)

        media_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.ANIME.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
        )

        self.assertEqual(len(media_list), 1)

    def test_get_media_list_with_search(self):
        """Test the get_media_list method with search parameter."""
        manager = MediaManager()

        media_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.ANIME.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
            search="Cowboy",
        )

        self.assertEqual(len(media_list), 1)

        media_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.ANIME.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
            search="Naruto",
        )

        self.assertEqual(len(media_list), 0)

    def test_apply_prefetch_related(self):
        """Test the _apply_prefetch_related method."""
        manager = MediaManager()

        queryset = TV.objects.filter(user=self.user.id)
        prefetched_queryset = manager._apply_prefetch_related(
            queryset,
            MediaTypes.TV.value,
        )

        self.assertTrue(hasattr(prefetched_queryset, "_prefetch_related_lookups"))
        prefetch_lookups = prefetched_queryset._prefetch_related_lookups
        self.assertEqual(len(prefetch_lookups), 2)

        queryset = Season.objects.filter(user=self.user.id)
        prefetched_queryset = manager._apply_prefetch_related(
            queryset,
            MediaTypes.SEASON.value,
        )

        self.assertTrue(hasattr(prefetched_queryset, "_prefetch_related_lookups"))
        prefetch_lookups = prefetched_queryset._prefetch_related_lookups
        self.assertEqual(len(prefetch_lookups), 2)

        queryset = Movie.objects.filter(user=self.user.id)
        prefetched_queryset = manager._apply_prefetch_related(
            queryset,
            MediaTypes.MOVIE.value,
        )

        self.assertTrue(hasattr(prefetched_queryset, "_prefetch_related_lookups"))
        prefetch_lookups = prefetched_queryset._prefetch_related_lookups
        self.assertEqual(len(prefetch_lookups), 1)

    def test_get_media_list_with_prefetch_related(self):
        """Test the get_media_list method with prefetch_related for TV and Season."""
        manager = MediaManager()

        tv_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.TV.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
        )

        tv_list = list(tv_list)

        for tv in tv_list:
            seasons = list(tv.seasons.all())
            for season in seasons:
                list(season.episodes.all())

        with self.assertNumQueries(0):  # No additional queries should be made
            for tv in tv_list:
                seasons = list(tv.seasons.all())
                for season in seasons:
                    list(season.episodes.all())

        season_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.SEASON.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
        )

        season_list = list(season_list)

        for season in season_list:
            list(season.episodes.all())

        with self.assertNumQueries(0):  # No additional queries should be made
            for season in season_list:
                list(season.episodes.all())

    def test_sort_media_list(self):
        """Test the _sort_media_list method."""
        manager = MediaManager()

        season2_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends Season 2",
            image="http://example.com/image.jpg",
            season_number=2,
        )

        season2 = Season.objects.create(
            item=season2_item,
            related_tv=self.tv,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            score=7,
        )

        for i in range(1, 3):
            episode_item = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Friends S2E{i}",
                image="http://example.com/image.jpg",
                season_number=2,
                episode_number=i,
            )

            Episode.objects.create(
                item=episode_item,
                related_season=season2,
                end_date=datetime(2023, 7, i, 0, 0, tzinfo=UTC),
            )

        season3_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends Season 3",
            image="http://example.com/image.jpg",
            season_number=3,
        )

        Season.objects.create(
            item=season3_item,
            related_tv=self.tv,
            user=self.user,
            status=Status.PLANNING.value,
            score=0,
        )

        queryset = Season.objects.filter(user=self.user).select_related("item")
        queryset = manager._apply_prefetch_related(queryset, MediaTypes.SEASON.value)

        sorted_queryset = manager._sort_media_list(
            queryset,
            "start_date",
            MediaTypes.SEASON.value,
        )
        seasons = list(sorted_queryset)

        self.assertEqual(seasons[0].item.title, "Friends")
        self.assertEqual(seasons[1].item.title, "Friends Season 2")
        self.assertEqual(seasons[2].item.title, "Friends Season 3")

        sorted_queryset = manager._sort_media_list(
            queryset,
            "end_date",
            MediaTypes.SEASON.value,
        )
        seasons = list(sorted_queryset)

        self.assertEqual(seasons[0].item.title, "Friends Season 2")
        self.assertEqual(seasons[1].item.title, "Friends")
        self.assertEqual(seasons[2].item.title, "Friends Season 3")

        sorted_queryset = manager._sort_media_list(
            queryset,
            "score",
            MediaTypes.SEASON.value,
        )
        seasons = list(sorted_queryset)

        self.assertEqual(seasons[0].score, 8)  # Season 1
        self.assertEqual(seasons[1].score, 7)  # Season 2
        self.assertEqual(seasons[2].score, 0)  # Season 3

        tv_queryset = TV.objects.filter(user=self.user).select_related("item")
        tv_queryset = manager._apply_prefetch_related(tv_queryset, MediaTypes.TV.value)

        sorted_tv = manager._sort_media_list(
            tv_queryset,
            "progress",
            MediaTypes.TV.value,
        )
        tv_shows = list(sorted_tv)

        self.assertEqual(tv_shows[0].item.title, "Friends")

        sorted_tv = manager._sort_media_list(
            tv_queryset,
            "start_date",
            MediaTypes.TV.value,
        )
        tv_shows = list(sorted_tv)

        self.assertEqual(tv_shows[0].item.title, "Friends")

        movie_queryset = Movie.objects.filter(user=self.user).select_related("item")
        sorted_movies = manager._sort_media_list(
            movie_queryset,
            "title",
            MediaTypes.MOVIE.value,
        )
        movies = list(sorted_movies)

        self.assertEqual(movies[0].item.title, "Fight Club")

    def test_get_media_list_sort_by_item_field(self):
        """Test the get_media_list method with sorting by item field."""
        manager = MediaManager()

        media_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.MOVIE.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="title",
        )

        self.assertEqual(media_list[0], self.movie)

    def test_get_media_list_sort_by_regular_field(self):
        """Test the get_media_list method with sorting by regular field."""
        manager = MediaManager()

        anime_item2 = Item.objects.create(
            media_id="5",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Naruto",
            image="http://example.com/naruto.jpg",
        )

        anime2 = Anime.objects.create(
            item=anime_item2,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            score=6,
        )

        media_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.ANIME.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
        )

        self.assertEqual(media_list.first(), self.anime)
        self.assertEqual(media_list.last(), anime2)

    def test_get_media_types_to_process(self):
        """Test the _get_media_types_to_process method."""
        manager = MediaManager()

        media_types = manager._get_media_types_to_process(
            self.user,
            MediaTypes.ANIME.value,
        )
        self.assertEqual(media_types, [MediaTypes.ANIME.value])

        media_types = manager._get_media_types_to_process(self.user, None)

        self.assertNotIn(MediaTypes.TV.value, media_types)
        self.assertIn(MediaTypes.ANIME.value, media_types)
        self.assertIn(MediaTypes.MOVIE.value, media_types)
        self.assertIn(MediaTypes.GAME.value, media_types)
        self.assertIn(MediaTypes.BOOK.value, media_types)
        self.assertIn(MediaTypes.MANGA.value, media_types)

        # Disable some media types
        self.user.anime_enabled = False
        self.user.manga_enabled = False
        self.user.save()

        media_types = manager._get_media_types_to_process(self.user, None)
        self.assertNotIn(MediaTypes.ANIME.value, media_types)
        self.assertNotIn(MediaTypes.MANGA.value, media_types)
        self.assertIn(MediaTypes.MOVIE.value, media_types)

    def test_annotate_next_event(self):
        """Test the _annotate_next_event method."""
        manager = MediaManager()

        queryset = Anime.objects.filter(user=self.user.id).select_related("item")
        anime_list = list(queryset)

        # Prefetch events
        for anime in anime_list:
            anime.item.prefetched_events = list(Event.objects.filter(item=anime.item))

        # Annotate next_event
        manager._annotate_next_event(anime_list)

        self.assertIsNotNone(anime_list[0].next_event)
        self.assertEqual(anime_list[0].next_event.item, self.anime_item)

        anime_item2 = Item.objects.create(
            media_id="5",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Naruto",
            image="http://example.com/naruto.jpg",
        )

        Anime.objects.create(
            item=anime_item2,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            score=6,
        )

        Event.objects.create(
            item=anime_item2,
            content_number=1,
            datetime=timezone.now() - timedelta(days=1),
            notification_sent=True,
        )

        queryset = Anime.objects.filter(
            user=self.user.id,
            item=anime_item2,
        ).select_related("item")
        anime_list = list(queryset)

        # Prefetch events
        for anime in anime_list:
            anime.item.prefetched_events = list(Event.objects.filter(item=anime.item))

        # Annotate next_event
        manager._annotate_next_event(anime_list)

        self.assertIsNone(anime_list[0].next_event)

    def test_sort_in_progress_media(self):
        """Test the _sort_in_progress_media method."""
        manager = MediaManager()

        anime_list = []

        # Anime with next event and high completion
        anime1 = self.anime
        anime1.max_progress = 20
        anime1.progress = 13
        anime1.next_event = Event.objects.filter(item=self.anime_item).first()
        anime_list.append(anime1)

        # Anime with no next event and low completion
        anime_item2 = Item.objects.create(
            media_id="5",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Naruto",
            image="http://example.com/naruto.jpg",
        )

        anime2 = Anime.objects.create(
            item=anime_item2,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            score=6,
            progress=5,
        )
        anime2.max_progress = 100
        anime2.next_event = None
        anime_list.append(anime2)

        # Anime with next event and medium completion
        anime_item3 = Item.objects.create(
            media_id="6",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Attack on Titan",
            image="http://example.com/aot.jpg",
        )

        anime3 = Anime.objects.create(
            item=anime_item3,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            score=9,
            progress=30,
        )
        anime3.max_progress = 50
        anime3.next_event = Event.objects.create(
            item=anime_item3,
            content_number=31,
            datetime=timezone.now() + timedelta(days=10),  # Further in the future
            notification_sent=False,
        )
        anime_list.append(anime3)

        sorted_list = manager._sort_in_progress_media(anime_list, "upcoming")
        # Items with next_event should come first, sorted by datetime
        self.assertEqual(sorted_list, [anime1, anime3, anime2])

        sorted_list = manager._sort_in_progress_media(anime_list, "title")
        self.assertEqual(
            sorted_list,
            sorted(anime_list, key=lambda x: x.item.title.lower()),
        )

        sorted_list = manager._sort_in_progress_media(anime_list, "completion")
        self.assertEqual(sorted_list, [anime1, anime3, anime2])

        sorted_list = manager._sort_in_progress_media(anime_list, "episodes_left")
        self.assertEqual(sorted_list, [anime1, anime3, anime2])

        sorted_list = manager._sort_in_progress_media(anime_list, sort_by="recent")
        self.assertEqual(sorted_list, [anime3, anime2, anime1])

    def test_annotate_max_progress(self):
        """Test the annotate_max_progress method."""
        manager = MediaManager()

        movie_list = list(Movie.objects.filter(user=self.user.id))
        manager.annotate_max_progress(movie_list, MediaTypes.MOVIE.value)
        self.assertEqual(movie_list[0].max_progress, 1)

        anime_list = list(
            Anime.objects.filter(user=self.user.id).select_related("item"),
        )

        Event.objects.create(
            item=self.anime_item,
            content_number=20,
            datetime=timezone.now() - timedelta(days=20),
            notification_sent=True,
        )

        # Prefetch events
        for anime in anime_list:
            anime.item.prefetched_events = list(Event.objects.filter(item=anime.item))

        manager.annotate_max_progress(anime_list, MediaTypes.ANIME.value)
        self.assertEqual(anime_list[0].max_progress, 20)

        tv_list = TV.objects.filter(user=self.user.id)

        Event.objects.create(
            item=self.season1_item,
            content_number=10,
            datetime=timezone.now() - timedelta(days=10),
        )

        # Prefetch events
        tv_list = tv_list.prefetch_related(
            Prefetch(
                "seasons__item__event_set",
                queryset=Event.objects.all(),
                to_attr="prefetched_events",
            ),
        )

        manager._annotate_tv_released_episodes(tv_list, timezone.now())
        self.assertEqual(tv_list[0].max_progress, 10)

    def test_get_in_progress(self):
        """Test the get_in_progress method."""
        manager = MediaManager()

        Event.objects.create(
            item=self.anime_item,
            content_number=20,
            datetime=timezone.now() - timedelta(days=20),
            notification_sent=True,
        )

        in_progress = manager.get_in_progress(
            user=self.user,
            sort_by="title",
            items_limit=10,
        )

        self.assertIn(MediaTypes.ANIME.value, in_progress)
        self.assertEqual(len(in_progress[MediaTypes.ANIME.value]["items"]), 1)
        self.assertEqual(in_progress[MediaTypes.ANIME.value]["total"], 1)

        in_progress = manager.get_in_progress(
            user=self.user,
            sort_by="title",
            items_limit=5,
        )

        self.assertIn(MediaTypes.ANIME.value, in_progress)
        self.assertIn(MediaTypes.GAME.value, in_progress)
        self.assertIn(MediaTypes.MANGA.value, in_progress)
        self.assertNotIn(MediaTypes.MOVIE.value, in_progress)  # Completed
        self.assertNotIn(MediaTypes.BOOK.value, in_progress)  # Planned

        for i in range(10):
            anime_item = Item.objects.create(
                media_id=f"100{i}",
                source=Sources.MAL.value,
                media_type=MediaTypes.ANIME.value,
                title=f"Test Anime {i}",
                image=f"http://example.com/anime{i}.jpg",
            )

            Anime.objects.create(
                item=anime_item,
                user=self.user,
                status=Status.IN_PROGRESS.value,
            )

        in_progress = manager.get_in_progress(
            user=self.user,
            sort_by="title",
            items_limit=5,
        )

        self.assertEqual(len(in_progress[MediaTypes.ANIME.value]["items"]), 5)
        self.assertEqual(
            in_progress[MediaTypes.ANIME.value]["total"],
            11,
        )  # 1 original + 10 new

        in_progress = manager.get_in_progress(
            user=self.user,
            sort_by="title",
            items_limit=5,
            specific_media_type=MediaTypes.ANIME.value,
        )

        self.assertEqual(
            len(in_progress[MediaTypes.ANIME.value]["items"]),
            6,
        )  # 11 total - 5 offset
        self.assertEqual(in_progress[MediaTypes.ANIME.value]["total"], 11)

    def test_get_media(self):
        """Test the get_media method."""
        manager = MediaManager()

        tv = manager.get_media(
            user=self.user,
            media_type=MediaTypes.TV.value,
            instance_id=self.tv.id,
        )

        self.assertEqual(tv, self.tv)

        season = manager.get_media(
            user=self.user,
            media_type=MediaTypes.SEASON.value,
            instance_id=self.season1.id,
        )

        self.assertEqual(season, self.season1)

        episode = manager.get_media(
            user=self.user,
            media_type=MediaTypes.EPISODE.value,
            instance_id=self.season1.episodes.first().id,
        )

        self.assertIsNotNone(episode)
        self.assertEqual(episode.item.episode_number, 1)

        with self.assertRaises(Movie.DoesNotExist):
            manager.get_media(
                user=self.user,
                media_type=MediaTypes.MOVIE.value,
                instance_id=9999,  # Non-existent ID
            )

    def test_filter_media(self):
        """Test the filter_media method."""
        manager = MediaManager()

        tv = manager.filter_media(
            user=self.user,
            media_id="1668",
            media_type=MediaTypes.TV.value,
            source=Sources.TMDB.value,
        ).first()

        self.assertEqual(tv, self.tv)

        season = manager.filter_media(
            user=self.user,
            media_id="1668",
            media_type=MediaTypes.SEASON.value,
            source=Sources.TMDB.value,
            season_number=1,
        ).first()

        self.assertEqual(season, self.season1)

        episode = manager.filter_media(
            user=self.user,
            media_id="1668",
            media_type=MediaTypes.EPISODE.value,
            source=Sources.TMDB.value,
            season_number=1,
            episode_number=1,
        ).first()

        self.assertIsNotNone(episode)
        self.assertEqual(episode.item.episode_number, 1)

        non_existent = manager.filter_media(
            user=self.user,
            media_id="9999",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
        ).first()

        self.assertIsNone(non_existent)


