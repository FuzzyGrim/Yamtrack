from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    TV,
    Anime,
    Book,
    BookProgressUnits,
    Experience,
    Item,
    MediaTypes,
    Season,
    Sources,
    Status,
)
from events.models import Event


class MediaDetailsViewTests(TestCase):
    """Test the media details views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_view(self, mock_get_metadata):
        """Test the media details view."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "test-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_details.html")

        self.assertIn("media", response.context)
        self.assertEqual(response.context["media"]["title"], "Test Movie")

        mock_get_metadata.assert_called_once_with(
            MediaTypes.MOVIE.value,
            "238",
            Sources.TMDB.value,
        )

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_view(self, mock_process_episodes, mock_get_metadata):
        """Test the season details view."""
        mock_get_metadata.return_value = {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }

        mock_process_episodes.return_value = [
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 1,
                "name": "Episode 1",
                "air_date": "2023-01-01",
                "watched": False,
            },
        ]

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_details.html")

        self.assertIn("media", response.context)
        self.assertEqual(response.context["media"]["title"], "Season 1")
        self.assertEqual(len(response.context["media"]["episodes"]), 1)

        mock_get_metadata.assert_called_once_with(
            "tv_with_seasons",
            "1668",
            Sources.TMDB.value,
            [1],
        )


class DetailProgressControlTests(TestCase):
    """Test progress controls on tracked media detail pages."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_tracked_season_details_show_progress_control(self):
        """Tracked season details should render the HTMX progress changer."""
        tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Friends",
            image="http://example.com/tv.jpg",
        )
        tv = TV(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        TV.save_base(tv)
        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/season.jpg",
            season_number=1,
        )
        season = Season(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        Season.save_base(season)
        Event.objects.create(
            item=season_item,
            content_number=29,
            datetime=timezone.now() - timezone.timedelta(days=1),
        )

        with patch("app.providers.services.get_media_metadata") as mock_get_metadata:
            mock_get_metadata.return_value = {
                "title": "Friends",
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.TV.value,
                "image": "http://example.com/tv.jpg",
                "related": {
                    "seasons": [
                        {
                            "season_number": 1,
                            "season_title": "Season 1",
                            "max_progress": 29,
                            "first_air_date": "2023-01-01",
                        },
                    ],
                },
                "season/1": {
                    "title": "Friends",
                    "season_title": "Season 1",
                    "media_id": "1668",
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.SEASON.value,
                    "image": "http://example.com/season.jpg",
                    "genres": [],
                    "overview": "",
                    "details": {},
                    "episodes": [],
                    "related": {},
                    "providers": None,
                },
            }

            response = self.client.get(
                reverse(
                    "season_details",
                    kwargs={
                        "source": Sources.TMDB.value,
                        "media_id": "1668",
                        "title": "friends",
                        "season_number": 1,
                    },
                ),
            )

        self.assertEqual(response.status_code, 200)
        progress_target = f'id="progress-season-{season.id}"'
        self.assertContains(response, progress_target, count=1)
        self.assertContains(
            response,
            reverse(
                "progress_edit",
                kwargs={
                    "media_type": MediaTypes.SEASON.value,
                    "instance_id": season.id,
                },
            ),
            count=2,
        )
        self.assertContains(response, "0")
        self.assertContains(response, "/ 29 Episodes")

    def test_untracked_details_do_not_show_progress_control(self):
        """Untracked media details should not render progress editing controls."""
        with patch("app.providers.services.get_media_metadata") as mock_get_metadata:
            mock_get_metadata.return_value = {
                "media_id": "1",
                "title": "Cowboy Bebop",
                "media_type": MediaTypes.ANIME.value,
                "source": Sources.MAL.value,
                "image": "http://example.com/image.jpg",
                "genres": [],
                "overview": "",
                "details": {},
            }

            response = self.client.get(
                reverse(
                    "media_details",
                    kwargs={
                        "source": Sources.MAL.value,
                        "media_type": MediaTypes.ANIME.value,
                        "media_id": "1",
                        "title": "cowboy-bebop",
                    },
                ),
            )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "progress-anime-")
        self.assertNotContains(response, reverse("progress_edit", args=["anime", 1]))

    def test_tracked_anime_details_show_progress_control(self):
        """Tracked anime details should render the reusable progress changer."""
        item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="http://example.com/image.jpg",
        )
        anime = Anime(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=7,
        )
        Anime.save_base(anime)
        Event.objects.create(
            item=item,
            content_number=12,
            datetime=timezone.now() - timezone.timedelta(days=1),
        )

        with patch("app.providers.services.get_media_metadata") as mock_get_metadata:
            mock_get_metadata.return_value = {
                "media_id": "1",
                "title": "Cowboy Bebop",
                "media_type": MediaTypes.ANIME.value,
                "source": Sources.MAL.value,
                "image": "http://example.com/image.jpg",
                "genres": [],
                "overview": "",
                "details": {},
            }

            response = self.client.get(
                reverse(
                    "media_details",
                    kwargs={
                        "source": Sources.MAL.value,
                        "media_type": MediaTypes.ANIME.value,
                        "media_id": "1",
                        "title": "cowboy-bebop",
                    },
                ),
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'id="progress-anime-{anime.id}"', count=1)
        self.assertContains(response, "7")
        self.assertContains(response, "/ 12 Episodes")
        self.assertContains(
            response,
            reverse(
                "progress_edit",
                kwargs={
                    "media_type": MediaTypes.ANIME.value,
                    "instance_id": anime.id,
                },
            ),
            count=2,
        )


    def test_tracked_book_details_show_selected_progress_unit(self):
        """Tracked Book details display progress with the selected unit."""
        item = Item.objects.create(
            media_id="book-1",
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            title="Book One",
            image="http://example.com/book.jpg",
        )
        book = Book.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=7,
            progress_unit=BookProgressUnits.CHAPTERS.value,
        )

        with patch("app.providers.services.get_media_metadata") as mock_get_metadata:
            mock_get_metadata.return_value = {
                "media_id": "book-1",
                "title": "Book One",
                "media_type": MediaTypes.BOOK.value,
                "source": Sources.HARDCOVER.value,
                "image": "http://example.com/book.jpg",
                "genres": [],
                "overview": "",
                "details": {},
                "max_progress": 350,
            }

            response = self.client.get(
                reverse(
                    "media_details",
                    kwargs={
                        "source": Sources.HARDCOVER.value,
                        "media_type": MediaTypes.BOOK.value,
                        "media_id": "book-1",
                        "title": "book-one",
                    },
                ),
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'id="progress-book-{book.id}"', count=1)
        self.assertContains(response, "7 Chapters")
        self.assertNotContains(response, "7 / 350 Chapters")

    def test_unsupported_tracked_tv_and_experience_hide_progress_control(self):
        """Read-only and unsupported progress media should not render controls."""
        tv_item = Item.objects.create(
            media_id="2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV",
            image="http://example.com/tv.jpg",
        )
        tv = TV(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        TV.save_base(tv)
        experience_item = Item.objects.create(
            media_id="3",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.EXPERIENCE.value,
            title="Museum",
            image="http://example.com/museum.jpg",
        )
        experience = Experience(
            item=experience_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        Experience.save_base(experience)

        metadata_by_type = {
            MediaTypes.TV.value: {
                "media_id": "2",
                "title": "Test TV",
                "media_type": MediaTypes.TV.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/tv.jpg",
                "genres": [],
                "overview": "",
                "details": {},
            },
            MediaTypes.EXPERIENCE.value: {
                "media_id": "3",
                "title": "Museum",
                "media_type": MediaTypes.EXPERIENCE.value,
                "source": Sources.MANUAL.value,
                "image": "http://example.com/museum.jpg",
                "genres": [],
                "overview": "",
                "details": {},
            },
        }

        with patch("app.providers.services.get_media_metadata") as mock_get_metadata:
            mock_get_metadata.side_effect = (
                lambda media_type, _media_id, _source: metadata_by_type[media_type]
            )
            for media_type, source, media_id in [
                (MediaTypes.TV.value, Sources.TMDB.value, "2"),
                (MediaTypes.EXPERIENCE.value, Sources.MANUAL.value, "3"),
            ]:
                response = self.client.get(
                    reverse(
                        "media_details",
                        kwargs={
                            "source": source,
                            "media_type": media_type,
                            "media_id": media_id,
                            "title": "test",
                        },
                    ),
                )

                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, f"progress-{media_type}-")
                self.assertNotContains(response, f"/progress_edit/{media_type}/")
