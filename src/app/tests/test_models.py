from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

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

mock_path = Path(__file__).resolve().parent / "mock_data"


class ItemModel(TestCase):
    """Test case for the Item model."""

    def setUp(self):
        """Set up test data for Item model."""
        self.item = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )

    def test_item_creation(self):
        """Test the creation of an Item instance."""
        self.assertEqual(self.item.media_id, "1")
        self.assertEqual(self.item.media_type, MediaTypes.MOVIE.value)
        self.assertEqual(self.item.title, "Test Movie")
        self.assertEqual(self.item.image, "http://example.com/image.jpg")

    def test_item_str_representation(self):
        """Test the string representation of an Item."""
        self.assertEqual(str(self.item), "Test Movie")

    def test_item_with_season_and_episode(self):
        """Test the string representation of an Item with season and episode."""
        item = Item.objects.create(
            media_id="2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test Show",
            image="http://example.com/image2.jpg",
            season_number=1,
            episode_number=2,
        )
        self.assertEqual(str(item), "Test Show S1E2")


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

        # Create seasons and episodes for TV
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

        # Create episodes for season 1
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

        # Create events for upcoming episodes
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

        # Test with specific status filter
        media_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.ANIME.value,
            status_filter=Status.IN_PROGRESS.value,
            sort_filter="score",
        )

        self.assertEqual(len(media_list), 1)
        self.assertEqual(media_list[0], self.anime)

        # Test with ALL status filter
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

        # Test with search term that matches
        media_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.ANIME.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
            search="Cowboy",
        )

        self.assertEqual(len(media_list), 1)

        # Test with search term that doesn't match
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

        # Test with TV media type
        queryset = TV.objects.filter(user=self.user.id)
        prefetched_queryset = manager._apply_prefetch_related(
            queryset,
            MediaTypes.TV.value,
        )

        # Verify prefetch_related was applied
        self.assertTrue(hasattr(prefetched_queryset, "_prefetch_related_lookups"))
        prefetch_lookups = prefetched_queryset._prefetch_related_lookups
        self.assertEqual(len(prefetch_lookups), 2)

        # Test with Season media type
        queryset = Season.objects.filter(user=self.user.id)
        prefetched_queryset = manager._apply_prefetch_related(
            queryset,
            MediaTypes.SEASON.value,
        )

        # Verify prefetch_related was applied
        self.assertTrue(hasattr(prefetched_queryset, "_prefetch_related_lookups"))
        prefetch_lookups = prefetched_queryset._prefetch_related_lookups
        self.assertEqual(len(prefetch_lookups), 2)

        # Test with other media type
        queryset = Movie.objects.filter(user=self.user.id)
        prefetched_queryset = manager._apply_prefetch_related(
            queryset,
            MediaTypes.MOVIE.value,
        )

        # Verify prefetch_related was applied for events
        self.assertTrue(hasattr(prefetched_queryset, "_prefetch_related_lookups"))
        prefetch_lookups = prefetched_queryset._prefetch_related_lookups
        self.assertEqual(len(prefetch_lookups), 1)

    def test_get_media_list_with_prefetch_related(self):
        """Test the get_media_list method with prefetch_related for TV and Season."""
        manager = MediaManager()

        # Test with TV media type
        tv_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.TV.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
        )

        # Force evaluation of the queryset and prefetch the related objects
        tv_list = list(tv_list)

        # Pre-load all the related seasons and episodes outside the assertion block
        for tv in tv_list:
            seasons = list(tv.seasons.all())
            for season in seasons:
                list(season.episodes.all())

        # Now verify no additional queries are made when accessing the prefetched data
        with self.assertNumQueries(0):  # No additional queries should be made
            for tv in tv_list:
                seasons = list(tv.seasons.all())
                for season in seasons:
                    list(season.episodes.all())

        # Test with Season media type
        season_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.SEASON.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
        )

        # Force evaluation of the queryset and prefetch the related objects
        season_list = list(season_list)

        # Pre-load all the related episodes outside the assertion block
        for season in season_list:
            list(season.episodes.all())

        # Verify prefetch_related was applied (check if episodes are prefetched)
        with self.assertNumQueries(0):  # No additional queries should be made
            for season in season_list:
                list(season.episodes.all())

    def test_sort_media_list(self):
        """Test the _sort_media_list method."""
        manager = MediaManager()

        # Create seasons with different dates
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

        # Create episodes for season 2 with later dates
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

        # Create a season with no episodes (no dates)
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

        # Get all seasons with prefetch_related applied
        queryset = Season.objects.filter(user=self.user).select_related("item")
        queryset = manager._apply_prefetch_related(queryset, MediaTypes.SEASON.value)

        # Test sorting by start_date
        sorted_queryset = manager._sort_media_list(
            queryset,
            "start_date",
            MediaTypes.SEASON.value,
        )
        seasons = list(sorted_queryset)

        # Verify seasons with episodes come first (have start_date)
        self.assertEqual(seasons[0].item.title, "Friends")
        self.assertEqual(seasons[1].item.title, "Friends Season 2")
        # Season with no episodes should come last
        self.assertEqual(seasons[2].item.title, "Friends Season 3")

        # Test sorting by end_date
        sorted_queryset = manager._sort_media_list(
            queryset,
            "end_date",
            MediaTypes.SEASON.value,
        )
        seasons = list(sorted_queryset)

        # Season 2 has later dates so should come first
        self.assertEqual(seasons[0].item.title, "Friends Season 2")
        self.assertEqual(seasons[1].item.title, "Friends")
        # Season with no episodes should come last
        self.assertEqual(seasons[2].item.title, "Friends Season 3")

        # Test sorting by score (media field)
        sorted_queryset = manager._sort_media_list(
            queryset,
            "score",
            MediaTypes.SEASON.value,
        )
        seasons = list(sorted_queryset)

        # Should be ordered by score descending
        self.assertEqual(seasons[0].score, 8)  # Season 1
        self.assertEqual(seasons[1].score, 7)  # Season 2
        self.assertEqual(seasons[2].score, 0)  # Season 3

        # Test sorting for TV shows
        tv_queryset = TV.objects.filter(user=self.user).select_related("item")
        tv_queryset = manager._apply_prefetch_related(tv_queryset, MediaTypes.TV.value)

        # Test TV sorting by progress
        sorted_tv = manager._sort_media_list(
            tv_queryset,
            "progress",
            MediaTypes.TV.value,
        )
        tv_shows = list(sorted_tv)

        # Should have our test TV show
        self.assertEqual(tv_shows[0].item.title, "Friends")

        # Test TV sorting by start_date
        sorted_tv = manager._sort_media_list(
            tv_queryset,
            "start_date",
            MediaTypes.TV.value,
        )
        tv_shows = list(sorted_tv)

        # Should have our test TV show
        self.assertEqual(tv_shows[0].item.title, "Friends")

        # Test generic media sorting (e.g., for movies)
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

        # Test sorting by title (ascending)
        media_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.MOVIE.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="title",
        )

        # Should be sorted alphabetically
        self.assertEqual(media_list[0], self.movie)

    def test_get_media_list_sort_by_regular_field(self):
        """Test the get_media_list method with sorting by regular field."""
        manager = MediaManager()

        # Create another anime with different score
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

        # Test sorting by score (descending)
        media_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.ANIME.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="score",
        )

        # Higher score should come first
        self.assertEqual(media_list.first(), self.anime)
        self.assertEqual(media_list.last(), anime2)

    def test_get_media_types_to_process(self):
        """Test the _get_media_types_to_process method."""
        manager = MediaManager()

        # Test with specific media type
        media_types = manager._get_media_types_to_process(
            self.user,
            MediaTypes.ANIME.value,
        )
        self.assertEqual(media_types, [MediaTypes.ANIME.value])

        # Test with no specific media type (all enabled)
        media_types = manager._get_media_types_to_process(self.user, None)

        # Should include all enabled media types except TV
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

        # Test again with some types disabled
        media_types = manager._get_media_types_to_process(self.user, None)
        self.assertNotIn(MediaTypes.ANIME.value, media_types)
        self.assertNotIn(MediaTypes.MANGA.value, media_types)
        self.assertIn(MediaTypes.MOVIE.value, media_types)

    def test_annotate_next_event(self):
        """Test the _annotate_next_event method."""
        manager = MediaManager()

        # Get anime queryset
        queryset = Anime.objects.filter(user=self.user.id).select_related("item")
        anime_list = list(queryset)

        # Prefetch events
        for anime in anime_list:
            anime.item.prefetched_events = list(Event.objects.filter(item=anime.item))

        # Annotate next_event
        manager._annotate_next_event(anime_list)

        # Verify next_event is set correctly
        self.assertIsNotNone(anime_list[0].next_event)
        self.assertEqual(anime_list[0].next_event.item, self.anime_item)

        # Create anime with no future events
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

        # Only past events
        Event.objects.create(
            item=anime_item2,
            content_number=1,
            datetime=timezone.now() - timedelta(days=1),
            notification_sent=True,
        )

        # Get updated queryset
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

        # Verify next_event is None for anime with no future events
        self.assertIsNone(anime_list[0].next_event)

    def test_sort_in_progress_media(self):
        """Test the _sort_in_progress_media method."""
        manager = MediaManager()

        # Create test media items with different properties
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

        # Test sort by upcoming
        sorted_list = manager._sort_in_progress_media(anime_list, "upcoming")
        # Items with next_event should come first, sorted by datetime
        self.assertEqual(sorted_list, [anime1, anime3, anime2])

        # Test sort by title
        sorted_list = manager._sort_in_progress_media(anime_list, "title")
        # Should be sorted alphabetically
        self.assertEqual(
            sorted_list,
            sorted(anime_list, key=lambda x: x.item.title.lower()),
        )

        # Test sort by completion
        sorted_list = manager._sort_in_progress_media(anime_list, "completion")
        # Higher completion percentage first
        self.assertEqual(sorted_list, [anime1, anime3, anime2])

        # Test sort by episodes_left
        sorted_list = manager._sort_in_progress_media(anime_list, "episodes_left")
        # Fewer episodes left first
        self.assertEqual(sorted_list, [anime1, anime3, anime2])

    def test_annotate_max_progress(self):
        """Test the annotate_max_progress method."""
        manager = MediaManager()

        # Test for Movie (should always be 1)
        movie_list = list(Movie.objects.filter(user=self.user.id))
        manager.annotate_max_progress(movie_list, MediaTypes.MOVIE.value)
        self.assertEqual(movie_list[0].max_progress, 1)

        # Test for Anime with events
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

        # Test for TV shows
        tv_list = TV.objects.filter(user=self.user.id)

        # Create seasons and episodes for TV
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
        # Should count episodes from all seasons except season 0
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

        # Test with specific media type
        in_progress = manager.get_in_progress(
            user=self.user,
            sort_by="title",
            items_limit=10,
        )

        self.assertIn(MediaTypes.ANIME.value, in_progress)
        self.assertEqual(len(in_progress[MediaTypes.ANIME.value]["items"]), 1)
        self.assertEqual(in_progress[MediaTypes.ANIME.value]["total"], 1)

        # Test with all media types
        in_progress = manager.get_in_progress(
            user=self.user,
            sort_by="title",
            items_limit=5,
        )

        # Should include anime, game, and manga (in progress)
        self.assertIn(MediaTypes.ANIME.value, in_progress)
        self.assertIn(MediaTypes.GAME.value, in_progress)
        self.assertIn(MediaTypes.MANGA.value, in_progress)
        self.assertNotIn(MediaTypes.MOVIE.value, in_progress)  # Completed
        self.assertNotIn(MediaTypes.BOOK.value, in_progress)  # Planned

        # Test pagination
        # Create more anime items to test pagination
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

        # Test with limit
        in_progress = manager.get_in_progress(
            user=self.user,
            sort_by="title",
            items_limit=5,
        )

        # Should only return 5 items per media type
        self.assertEqual(len(in_progress[MediaTypes.ANIME.value]["items"]), 5)
        self.assertEqual(
            in_progress[MediaTypes.ANIME.value]["total"],
            11,
        )  # 1 original + 10 new

        # Test with specific media type and offset
        in_progress = manager.get_in_progress(
            user=self.user,
            sort_by="title",
            items_limit=5,
            specific_media_type=MediaTypes.ANIME.value,
        )

        # Should return items after the first 5
        self.assertEqual(
            len(in_progress[MediaTypes.ANIME.value]["items"]),
            6,
        )  # 11 total - 5 offset
        self.assertEqual(in_progress[MediaTypes.ANIME.value]["total"], 11)

    def test_get_media(self):
        """Test the get_media method."""
        manager = MediaManager()

        # Test getting a TV show
        tv = manager.get_media(
            user=self.user,
            media_type=MediaTypes.TV.value,
            instance_id=self.tv.id,
        )

        self.assertEqual(tv, self.tv)

        # Test getting a season
        season = manager.get_media(
            user=self.user,
            media_type=MediaTypes.SEASON.value,
            instance_id=self.season1.id,
        )

        self.assertEqual(season, self.season1)

        # Test getting an episode
        episode = manager.get_media(
            user=self.user,
            media_type=MediaTypes.EPISODE.value,
            instance_id=self.season1.episodes.first().id,
        )

        self.assertIsNotNone(episode)
        self.assertEqual(episode.item.episode_number, 1)

        # Test getting a non-existent media
        non_existent = manager.get_media(
            user=self.user,
            media_type=MediaTypes.MOVIE.value,
            instance_id=9999,  # Non-existent ID
        )

        self.assertIsNone(non_existent)

    def test_filter_media(self):
        """Test the filter_media method."""
        manager = MediaManager()

        # Test getting a TV show
        tv = manager.filter_media(
            user=self.user,
            media_id="1668",
            media_type=MediaTypes.TV.value,
            source=Sources.TMDB.value,
        ).first()

        self.assertEqual(tv, self.tv)

        # Test getting a season
        season = manager.filter_media(
            user=self.user,
            media_id="1668",
            media_type=MediaTypes.SEASON.value,
            source=Sources.TMDB.value,
            season_number=1,
        ).first()

        self.assertEqual(season, self.season1)

        # Test getting an episode
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

        # Test getting a non-existent media
        non_existent = manager.filter_media(
            user=self.user,
            media_id="9999",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
        ).first()

        self.assertIsNone(non_existent)


class MediaModel(TestCase):
    """Test the custom save of the Media model."""

    def setUp(self):
        """Create a user."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_anime = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="http://example.com/image.jpg",
        )

        self.anime = Anime.objects.create(
            item=item_anime,
            user=self.user,
            status=Status.PLANNING.value,
        )

    def test_completed_progress(self):
        """When completed, the progress should be the total number of episodes."""
        self.anime.status = Status.COMPLETED.value
        self.anime.save()
        self.assertEqual(
            Anime.objects.get(item__media_id="1", user=self.user).progress,
            26,
        )

    def test_progress_is_max(self):
        """When progress is maximum number of episodes.

        Status should be completed and end_date the current date if not specified.
        """
        self.anime.status = Status.IN_PROGRESS.value
        self.anime.progress = 26
        self.anime.save()

        self.assertEqual(
            Anime.objects.get(item__media_id="1", user=self.user).status,
            Status.COMPLETED.value,
        )
        self.assertIsNotNone(
            Anime.objects.get(item__media_id="1", user=self.user).end_date,
        )

    def test_progress_bigger_than_max(self):
        """When progress is bigger than max, it should be set to max."""
        self.anime.status = Status.IN_PROGRESS.value
        self.anime.progress = 30
        self.anime.save()
        self.assertEqual(
            Anime.objects.get(item__media_id="1", user=self.user).progress,
            26,
        )


class TVModel(TestCase):
    """Test the @properties and custom save of the TV model."""

    def setUp(self):
        """Create a user and a season with episodes."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_season1 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        # create first season
        season1 = Season.objects.create(
            item=item_season1,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        self.tv = TV.objects.get(user=self.user)

        item_ep1 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_ep1,
            related_season=season1,
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_ep2 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=2,
        )
        Episode.objects.create(
            item=item_ep2,
            related_season=season1,
            end_date=datetime(2023, 6, 2, 0, 0, tzinfo=UTC),
        )

        item_season2 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
        )

        # create second season
        season2 = Season.objects.create(
            item=item_season2,
            related_tv=self.tv,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        item_ep3 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_ep3,
            related_season=season2,
            end_date=datetime(2023, 6, 4, 0, 0, tzinfo=UTC),
        )

        item_ep4 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
            episode_number=2,
        )
        Episode.objects.create(
            item=item_ep4,
            related_season=season2,
            end_date=datetime(2023, 6, 5, 0, 0, tzinfo=UTC),
        )

    def test_tv_progress(self):
        """Test the progress property of the Season model."""
        self.assertEqual(self.tv.progress, 4)

    def test_tv_start_date(self):
        """Test the start_date property of the Season model."""
        self.assertEqual(
            self.tv.start_date,
            datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

    def test_tv_end_date(self):
        """Test the end_date property of the Season model."""
        self.assertEqual(
            self.tv.end_date,
            datetime(2023, 6, 5, 0, 0, tzinfo=UTC),
        )

    def test_tv_save(self):
        """Test the custom save method of the TV model."""
        self.tv.status = Status.COMPLETED.value
        self.tv.save(update_fields=["status"])

        # check if all seasons are created with the status Status.COMPLETED.value
        self.assertEqual(
            self.tv.seasons.filter(status=Status.COMPLETED.value).count(),
            10,
        )


class TVStatusTests(TestCase):
    """Test TV model status change behaviors."""

    def setUp(self):
        """Create test data."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        # Create TV show item
        self.tv_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test Show",
            image="http://example.com/image.jpg",
        )

        # Create TV instance
        self.tv = TV.objects.create(
            item=self.tv_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        # Create some seasons
        self.season1_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        self.season1 = Season.objects.create(
            item=self.season1_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.IN_PROGRESS.value,
        )

        self.season2_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Show",
            image="http://example.com/image.jpg",
            season_number=2,
        )

        self.season2 = Season.objects.create(
            item=self.season2_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.PLANNING.value,
        )

    @patch("app.models.providers.services.get_media_metadata")
    def test_completed_status_creates_all_seasons(self, mock_get_metadata):
        """Test setting status to COMPLETED creates all seasons."""
        mock_metadata = {
            "max_progress": 10,
            "related": {
                "seasons": [
                    {"season_number": 1, "image": "img1.jpg"},
                    {"season_number": 2, "image": "img2.jpg"},
                    {"season_number": 3, "image": "img3.jpg"},
                ],
            },
            "season/1": {
                "image": "http://example.com/image.jpg",
                "season_number": 1,
                "episodes": [{"episode_number": 1}] * 10,
            },
            "season/2": {
                "image": "http://example.com/image.jpg",
                "season_number": 2,
                "episodes": [{"episode_number": 1}] * 10,
            },
            "season/3": {
                "image": "http://example.com/image.jpg",
                "season_number": 3,
                "episodes": [{"episode_number": 1}] * 10,
            },
        }
        mock_get_metadata.return_value = mock_metadata

        self.tv.status = Status.COMPLETED.value
        self.tv.save()

        # Verify all seasons were created and marked completed
        self.assertEqual(self.tv.seasons.count(), 3)
        self.assertEqual(
            self.tv.seasons.filter(status=Status.COMPLETED.value).count(),
            3,
        )

        # Verify episodes were created for each season
        for season in self.tv.seasons.all():
            self.assertTrue(season.episodes.exists())

    def test_dropped_status_marks_in_progress_seasons_dropped(self):
        """Test setting status to DROPPED marks in-progress seasons as dropped."""
        # Create another in-progress season
        season3_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Show",
            image="http://example.com/image.jpg",
            season_number=3,
        )

        Season.objects.create(
            item=season3_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.IN_PROGRESS.value,
        )

        self.tv.status = Status.DROPPED.value
        self.tv.save()

        # Verify all in-progress seasons were marked dropped
        self.assertEqual(
            self.tv.seasons.filter(status=Status.DROPPED.value).count(),
            2,  # season1 and season3
        )
        # season2 should remain in planning
        self.assertEqual(
            self.tv.seasons.filter(status=Status.PLANNING.value).count(),
            1,
        )

    @patch("app.models.providers.services.get_media_metadata")
    def test_in_progress_status_activates_next_season(self, _):
        """Test setting status to IN_PROGRESS activates next available season."""
        # Complete season1
        self.season1.status = Status.COMPLETED.value
        self.season1.save()

        self.tv.status = Status.IN_PROGRESS.value
        self.tv.save()

        # Should activate season2 (was in planning)
        season2 = Season.objects.get(pk=self.season2.pk)
        self.assertEqual(season2.status, Status.IN_PROGRESS.value)

    @patch("app.models.providers.services.get_media_metadata")
    def test_in_progress_status_creates_new_season_if_needed(self, mock_get_metadata):
        """Test setting status to IN_PROGRESS creates new season if needed."""
        # Complete all existing seasons
        self.season1.status = Status.COMPLETED.value
        self.season1.save()
        self.season2.status = Status.COMPLETED.value
        self.season2.save()

        # Mock metadata to return a new season
        mock_metadata = {
            "related": {
                "seasons": [
                    {"season_number": 1, "image": "img1.jpg"},
                    {"season_number": 2, "image": "img2.jpg"},
                    {"season_number": 3, "image": "img3.jpg"},
                ],
            },
        }
        mock_get_metadata.return_value = mock_metadata

        self.tv.status = Status.IN_PROGRESS.value
        self.tv.save()

        # Should create and activate season3
        season3 = self.tv.seasons.get(item__season_number=3)
        self.assertEqual(season3.status, Status.IN_PROGRESS.value)

    def test_in_progress_status_noop_if_already_has_in_progress_season(self):
        """Test IN_PROGRESS status change does nothing if season already in progress."""
        # season1 is already in progress from setUp
        original_season1_status = self.season1.status

        self.tv.status = Status.IN_PROGRESS.value
        self.tv.save()

        # Verify no changes were made
        season1 = Season.objects.get(pk=self.season1.pk)
        self.assertEqual(season1.status, original_season1_status)


class SeasonModel(TestCase):
    """Test the @properties and custom save of the Season model."""

    def setUp(self):
        """Create a user and a season with episodes."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        self.season = Season.objects.create(
            item=item_season,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        item_ep1 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_ep1,
            related_season=self.season,
            end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

        item_ep2 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=2,
        )
        Episode.objects.create(
            item=item_ep2,
            related_season=self.season,
            end_date=datetime(2023, 6, 2, 0, 0, tzinfo=UTC),
        )

    def test_season_progress(self):
        """Test the progress property of the Season model."""
        self.assertEqual(self.season.progress, 2)

    def test_season_start_date(self):
        """Test the start_date property of the Season model."""
        self.assertEqual(
            self.season.start_date,
            datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

    def test_season_end_date(self):
        """Test the end_date property of the Season model."""
        self.assertEqual(
            self.season.end_date,
            datetime(2023, 6, 2, 0, 0, tzinfo=UTC),
        )

    def test_season_save(self):
        """Test the custom save method of the Season model."""
        self.season.status = Status.COMPLETED.value
        self.season.save(update_fields=["status"])

        # check if all episodes are created
        self.assertEqual(self.season.episodes.count(), 24)

    @patch("app.models.Season.get_episode_item")
    def test_watch_method(self, mock_get_episode_item):
        """Test the watch method of the Season model."""
        # Mock the get_episode_item method
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
        )
        mock_get_episode_item.return_value = episode_item

        # Test watching a new episode
        self.season.watch(3, datetime(2023, 6, 3, 0, 0, tzinfo=UTC))

        # Check if the episode was created
        episode = Episode.objects.get(
            related_season=self.season,
            item=episode_item,
        )
        self.assertEqual(episode.end_date, datetime(2023, 6, 3, 0, 0, tzinfo=UTC))

        # Test rewatching the same episode
        self.season.watch(3, datetime(2023, 6, 4, 0, 0, tzinfo=UTC))

        # Check if the episode was updated
        episodes = Episode.objects.filter(
            related_season=self.season,
            item=episode_item,
        )
        self.assertEqual(
            episodes.first().end_date,
            datetime(2023, 6, 4, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(episodes.count(), 2)

    @patch("app.models.Season.get_episode_item")
    def test_watch_with_none_date(self, mock_get_episode_item):
        """Test the watch method with None date."""
        # Mock the get_episode_item method
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
        )
        mock_get_episode_item.return_value = episode_item

        # Test watching with None date
        self.season.watch(3, None)

        # Check if the episode was created with None date
        episode = Episode.objects.get(
            related_season=self.season,
            item=episode_item,
        )
        self.assertIsNone(episode.end_date)

    @patch("app.models.Season.get_episode_item")
    def test_unwatch_method(self, mock_get_episode_item):
        """Test the unwatch method of the Season model."""
        # Mock the get_episode_item method
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
        )
        mock_get_episode_item.return_value = episode_item

        # Create an episode first
        Episode.objects.create(
            related_season=self.season,
            item=episode_item,
            end_date=datetime(2023, 6, 3, 0, 0, tzinfo=UTC),
        )

        # Test unwatching the episode
        self.season.unwatch(3)

        # Check if the episode was deleted
        with self.assertRaises(Episode.DoesNotExist):
            Episode.objects.get(
                related_season=self.season,
                item=episode_item,
            )

    @patch("app.models.Season.get_episode_item")
    def test_unwatch_with_repeats(self, mock_get_episode_item):
        """Test the unwatch method with an episode that has repeats."""
        # Mock the get_episode_item method
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
        )
        mock_get_episode_item.return_value = episode_item

        # Create an episode with repeats
        Episode.objects.create(
            related_season=self.season,
            item=episode_item,
            end_date=datetime(2023, 6, 3, 0, 0, tzinfo=UTC),
        )
        Episode.objects.create(
            related_season=self.season,
            item=episode_item,
            end_date=datetime(2024, 6, 3, 0, 0, tzinfo=UTC),
        )

        # Test unwatching the episode
        self.season.unwatch(3)

        # Check if the episode's repeats were decreased
        episodes = Episode.objects.filter(
            related_season=self.season,
            item=episode_item,
        )
        self.assertEqual(episodes.count(), 1)

    @patch("app.models.Season.get_episode_item")
    def test_unwatch_nonexistent_episode(self, mock_get_episode_item):
        """Test unwatching a non-existent episode."""
        # Mock the get_episode_item method
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
        )
        mock_get_episode_item.return_value = episode_item

        # Test unwatching a non-existent episode
        self.season.unwatch(3)

        # No exception should be raised, and no episode should be created
        with self.assertRaises(Episode.DoesNotExist):
            Episode.objects.get(
                related_season=self.season,
                item=episode_item,
            )


class SeasonStatusTests(TestCase):
    """Test Season model status change behaviors."""

    def setUp(self):
        """Create test data."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        # Create TV show item
        self.tv_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test Show",
            image="http://example.com/image.jpg",
        )

        # Create TV instance
        self.tv = TV.objects.create(
            item=self.tv_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        # Create season item
        self.season_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        # Create season instance
        self.season = Season.objects.create(
            item=self.season_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.PLANNING.value,
        )

    @patch("app.models.providers.services.get_media_metadata")
    def test_completed_status_creates_remaining_episodes(self, mock_get_metadata):
        """Test setting status to COMPLETED creates remaining episodes."""
        mock_metadata = {
            "episodes": [
                {"episode_number": 1, "image": "img1.jpg"},
                {"episode_number": 2, "image": "img2.jpg"},
                {"episode_number": 3, "image": "img3.jpg"},
            ],
            "image": "season_img.jpg",
        }
        mock_get_metadata.return_value = mock_metadata

        self.season.status = Status.COMPLETED.value
        self.season.save()

        # Verify all episodes were created
        self.assertEqual(self.season.episodes.count(), 3)
        # Should have episodes 1, 2, and 3
        episode_numbers = set(
            self.season.episodes.values_list("item__episode_number", flat=True),
        )
        self.assertEqual(episode_numbers, {1, 2, 3})

    def test_dropped_status_updates_tv_status(self):
        """Test setting status to DROPPED updates TV status."""
        self.season.status = Status.DROPPED.value
        self.season.save()

        # Verify TV show was also marked dropped
        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.DROPPED.value)

    def test_in_progress_status_updates_tv_status(self):
        """Test setting status to IN_PROGRESS updates TV status."""
        self.season.status = Status.IN_PROGRESS.value
        self.season.save()

        # Verify TV show was also marked in progress
        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)

    def test_status_change_does_not_affect_tv_if_already_same_status(self):
        """Test status change doesn't update TV if already same status."""
        # Set TV to IN_PROGRESS first
        self.tv.status = Status.IN_PROGRESS.value
        self.tv.save()

        # Track if save was called
        with patch.object(TV, "save") as mock_tv_save:
            self.season.status = Status.IN_PROGRESS.value
            self.season.save()

            # TV save shouldn't have been called
            mock_tv_save.assert_not_called()

    @patch("app.models.providers.services.get_media_metadata")
    def test_completed_status_noop_if_no_remaining_episodes(self, mock_get_metadata):
        """Test COMPLETED status does nothing if no remaining episodes."""
        mock_metadata = {
            "episodes": [
                {"episode_number": 1, "image": "img1.jpg"},
            ],
            "image": "season_img.jpg",
        }
        mock_get_metadata.return_value = mock_metadata

        # Create all episodes already
        ep_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test Episode",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.bulk_create(
            [
                Episode(
                    item=ep_item,
                    related_season=self.season,
                    end_date=timezone.now(),
                ),
            ],
        )

        # Track if bulk_create was called
        with patch("app.models.bulk_create_with_history") as mock_bulk_create:
            self.season.status = Status.COMPLETED.value
            self.season.save()

            # bulk_create shouldn't have been called
            mock_bulk_create.assert_not_called()

    def test_get_tv_creates_tv_if_not_exists(self):
        """Test get_tv creates TV instance if it doesn't exist."""
        # Delete existing TV
        self.tv.delete()

        # Mock metadata
        with patch(
            "app.models.providers.services.get_media_metadata",
        ) as mock_get_metadata:
            mock_metadata = {
                "title": "Test Show",
                "image": "tv_img.jpg",
                "details": {"seasons": 1},
            }
            mock_get_metadata.return_value = mock_metadata

            # Call get_tv
            tv = self.season.get_tv()

            # Verify TV was created
            self.assertIsNotNone(tv)
            self.assertEqual(tv.item.title, "Test Show")
            self.assertEqual(tv.status, Status.PLANNING.value)


class EpisodeModel(TestCase):
    """Test the custom save of the Episode model."""

    def setUp(self):
        """Create a user and a season."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        self.season = Season.objects.create(
            item=item_season,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            notes="",
        )

    def test_episode_save(self):
        """Test the custom save method of the Episode model."""
        for i in range(1, 25):
            item_episode = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Friends",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=i,
            )
            Episode.objects.create(
                item=item_episode,
                related_season=self.season,
                end_date=datetime(2023, 6, i, 0, 0, tzinfo=UTC),
            )

        # when all episodes are created, the season status should be COMPLETED
        self.assertEqual(self.season.status, Status.COMPLETED.value)


class EpisodeStatusTests(TestCase):
    """Test how Episode model affects Season and TV statuses."""

    def setUp(self):
        """Create test data."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.tv_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test Show",
            image="http://example.com/image.jpg",
        )

        self.tv = TV.objects.create(
            item=self.tv_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        self.season_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        self.season = Season.objects.create(
            item=self.season_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.PLANNING.value,
        )

        self.episode_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test Episode",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )

    @patch("app.models.providers.services.get_media_metadata")
    def test_first_episode_sets_season_in_progress(self, mock_get_metadata):
        """Test first episode sets season to IN_PROGRESS."""
        mock_metadata = {
            "season/1": {
                "episodes": [{"episode_number": 1}, {"episode_number": 2}],
            },
            "related": {
                "seasons": [{"season_number": 1}],
            },
        }
        mock_get_metadata.return_value = mock_metadata

        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=timezone.now(),
        )

        # Verify season status updated
        self.season.refresh_from_db()
        self.assertEqual(self.season.status, Status.IN_PROGRESS.value)

        # Verify TV status updated
        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)

    @patch("app.models.providers.services.get_media_metadata")
    def test_last_episode_sets_season_completed(self, mock_get_metadata):
        """Test last episode sets season to COMPLETED."""
        mock_metadata = {
            "season/1": {
                "episodes": [{"episode_number": 1}],
            },
            "related": {
                "seasons": [{"season_number": 1}],
            },
        }
        mock_get_metadata.return_value = mock_metadata

        # Create episode (will be the only/last one)
        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=timezone.now(),
        )

        # Verify season status updated
        self.season.refresh_from_db()
        self.assertEqual(self.season.status, Status.COMPLETED.value)

        # Verify TV status updated (since it's the last season)
        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.COMPLETED.value)

    @patch("app.models.providers.services.get_media_metadata")
    def test_middle_episode_does_not_change_status(self, mock_get_metadata):
        """Test middle episode doesn't change season/TV status."""
        mock_metadata = {
            "season/1": {
                "episodes": [
                    {"episode_number": 1},
                    {"episode_number": 2},
                    {"episode_number": 3},
                ],
            },
            "related": {
                "seasons": [{"season_number": 1}, {"season_number": 2}],
            },
        }
        mock_get_metadata.return_value = mock_metadata

        # Create first episode to set in progress
        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=timezone.now(),
        )

        # Create second episode item
        ep_item2 = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test Episode 2",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=2,
        )

        # Track bulk updates
        with patch("app.models.bulk_update_with_history") as mock_bulk_update:
            # Create second episode (middle of season)
            Episode.objects.create(
                item=ep_item2,
                related_season=self.season,
                end_date=timezone.now(),
            )

            # No status changes should occur
            mock_bulk_update.assert_not_called()

    @patch("app.models.providers.services.get_media_metadata")
    def test_last_season_completes_tv_show(self, mock_get_metadata):
        """Test last season completion also completes TV show."""
        mock_metadata = {
            "season/1": {
                "episodes": [{"episode_number": 1}],
            },
            "related": {
                "seasons": [{"season_number": 1}],  # Only one season
            },
        }
        mock_get_metadata.return_value = mock_metadata

        # Create episode (will complete season and TV)
        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=timezone.now(),
        )

        # Verify TV status updated
        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.COMPLETED.value)

    @patch("app.models.providers.services.get_media_metadata")
    def test_non_last_season_does_not_complete_tv_show(self, mock_get_metadata):
        """Test non-last season completion doesn't complete TV show."""
        mock_metadata = {
            "season/1": {
                "episodes": [{"episode_number": 1}],
            },
            "related": {
                "seasons": [{"season_number": 1}, {"season_number": 2}],  # Two seasons
            },
        }
        mock_get_metadata.return_value = mock_metadata

        # Create episode (will complete season but not TV)
        Episode.objects.create(
            item=self.episode_item,
            related_season=self.season,
            end_date=timezone.now(),
        )

        # Verify TV status remains unchanged
        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.PLANNING.value)


class GameModel(TestCase):
    """Test case for the Game model methods."""

    def setUp(self):
        """Set up test data for Game model tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.game_item = Item.objects.create(
            media_id="1234",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="The Last of Us",
            image="http://example.com/tlou.jpg",
        )

        self.game = Game.objects.create(
            item=self.game_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=60,  # 60 minutes
        )

    def test_increase_progress(self):
        """Test increasing the progress of a game."""
        initial_progress = self.game.progress
        self.game.increase_progress()

        # Progress should be increased by 30 minutes
        self.assertEqual(self.game.progress, initial_progress + 30)

    def test_decrease_progress(self):
        """Test decreasing the progress of a game."""
        initial_progress = self.game.progress
        self.game.decrease_progress()

        # Progress should be decreased by 30 minutes
        self.assertEqual(self.game.progress, initial_progress - 30)

    def test_field_tracker(self):
        """Test that the field tracker is tracking changes."""
        # Initially, there should be no changes
        self.assertFalse(self.game.tracker.changed())

        # Change the progress
        self.game.progress = 90

        # Now there should be changes
        self.assertTrue(self.game.tracker.changed())
        self.assertEqual(self.game.tracker.previous("progress"), 60)

    def test_multiple_progress_changes(self):
        """Test multiple progress changes."""
        # Increase progress twice
        self.game.increase_progress()
        self.game.increase_progress()

        # Progress should be increased by 60 minutes total
        self.assertEqual(self.game.progress, 120)

        # Decrease progress once
        self.game.decrease_progress()

        # Progress should now be 90 minutes
        self.assertEqual(self.game.progress, 90)
