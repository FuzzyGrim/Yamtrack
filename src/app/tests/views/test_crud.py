import datetime
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from app import history_cache
from app.models import (
    TV,
    Album,
    Anime,
    Artist,
    Book,
    Comic,
    ComicIssue,
    DeletedMedia,
    Episode,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Music,
    Season,
    Sources,
    Status,
    Track,
)
from app.providers import services


class MediaSaveProviderFailure(TestCase):
    """A provider that cannot supply a title is a failed save, not a 500."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def _save_with_provider_status(self, status_code):
        error = Exception("provider said no")
        error.response = SimpleNamespace(status_code=status_code, headers={})
        with patch(
            "app.save_views.ensure_item_metadata",
            side_effect=services.ProviderAPIError(Sources.POCKETCASTS.value, error),
        ):
            return self.client.post(
                reverse("media_save"),
                {
                    "media_id": "gone-show-uuid",
                    "source": Sources.POCKETCASTS.value,
                    "media_type": MediaTypes.PODCAST.value,
                    "status": Status.PLANNING.value,
                },
                follow=True,
            )

    def test_provider_404_shows_a_message_and_saves_nothing(self):
        response = self._save_with_provider_status(404)

        self.assertEqual(response.status_code, 200)
        page_messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("no longer has this title" in m for m in page_messages))
        self.assertFalse(Item.objects.filter(media_id="gone-show-uuid").exists())

    def test_htmx_first_save_gets_a_toast_not_a_redirect(self):
        error = Exception("provider said no")
        error.response = SimpleNamespace(status_code=404, headers={})
        with patch(
            "app.save_views.ensure_item_metadata",
            side_effect=services.ProviderAPIError(Sources.POCKETCASTS.value, error),
        ):
            response = self.client.post(
                reverse("media_save"),
                {
                    "media_id": "gone-show-uuid",
                    "source": Sources.POCKETCASTS.value,
                    "media_type": MediaTypes.PODCAST.value,
                    "status": Status.PLANNING.value,
                },
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 502)
        toast = json.loads(response["HX-Trigger"])["showToast"]
        self.assertEqual(toast["type"], "error")
        self.assertIn("no longer has this title", toast["message"])

    def test_unconfigured_provider_keeps_setup_guidance(self):
        with patch(
            "app.save_views.ensure_item_metadata",
            side_effect=services.ProviderNotConfiguredError(
                Sources.IGDB.value,
                "IGDB credentials are not set.",
            ),
        ):
            response = self.client.post(
                reverse("media_save"),
                {
                    "media_id": "1",
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "status": Status.PLANNING.value,
                },
            )

        self.assertEqual(response.status_code, 503)

    def test_provider_outage_asks_the_user_to_retry(self):
        response = self._save_with_provider_status(503)

        self.assertEqual(response.status_code, 200)
        page_messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("did not respond" in m for m in page_messages))


class CreateMedia(TestCase):
    """Test the creation of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    @tag("network")
    @override_settings(MEDIA_ROOT=("create_media"))
    def test_create_anime(self):
        """Test the creation of a TV object."""
        Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
            image="http://example.com/image.jpg",
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "1",
                "source": Sources.MAL.value,
                "media_type": MediaTypes.ANIME.value,
                "status": Status.PLANNING.value,
                "progress": 0,
                "repeats": 0,
            },
        )
        self.assertEqual(
            Anime.objects.filter(item__media_id="1", user=self.user).exists(),
            True,
        )

    @tag("network")
    @override_settings(MEDIA_ROOT=("create_media"))
    def test_create_tv(self):
        """Test the creation of a TV object through views."""
        Item.objects.create(
            media_id="5895",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Friends",
            image="http://example.com/image.jpg",
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "5895",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.TV.value,
                "status": Status.PLANNING.value,
            },
        )
        self.assertEqual(
            TV.objects.filter(item__media_id="5895", user=self.user).exists(),
            True,
        )

    @patch("app.views.services.get_media_metadata")
    def test_create_tv_with_null_runtime_metadata(self, metadata_mock):
        """Creating TV media should handle provider runtime=None values."""
        metadata_mock.return_value = {
            "title": "Clevatess",
            "original_title": "Clevatess",
            "localized_title": "Clevatess",
            "image": "http://example.com/image.jpg",
            "details": {
                "runtime": None,
            },
        }

        self.client.post(
            reverse("media_save"),
            {
                "media_id": "258348",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.TV.value,
                "status": Status.PLANNING.value,
            },
        )

        self.assertEqual(
            TV.objects.filter(item__media_id="258348", user=self.user).exists(),
            True,
        )
        self.assertEqual(
            Item.objects.get(
                media_id="258348",
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
            ).runtime,
            "",
        )

    @patch("app.models.providers.services.get_media_metadata")
    @patch("app.views.services.get_media_metadata")
    def test_create_reading_media_with_null_format_metadata(
        self, view_metadata_mock, model_metadata_mock
    ):
        """Creating reading media should coerce None format metadata to an empty string."""
        cases = [
            (
                MediaTypes.BOOK.value,
                Sources.HARDCOVER.value,
                "format-none-book",
                Book,
                100,
            ),
            (
                MediaTypes.COMIC.value,
                Sources.COMICVINE.value,
                "format-none-comic",
                Comic,
                10,
            ),
            (
                MediaTypes.MANGA.value,
                Sources.MANGAUPDATES.value,
                "format-none-manga",
                Manga,
                10,
            ),
        ]

        for media_type, source, media_id, model_class, max_progress in cases:
            metadata = {
                "title": f"Formatless {media_type.title()}",
                "original_title": f"Formatless {media_type.title()}",
                "localized_title": f"Formatless {media_type.title()}",
                "image": "http://example.com/image.jpg",
                "max_progress": max_progress,
                "details": {
                    "format": None,
                    "number_of_pages": max_progress
                    if media_type == MediaTypes.BOOK.value
                    else None,
                },
            }
            view_metadata_mock.return_value = metadata
            model_metadata_mock.return_value = metadata

            response = self.client.post(
                reverse("media_save"),
                {
                    "media_id": media_id,
                    "source": source,
                    "media_type": media_type,
                    "status": Status.PLANNING.value,
                    "progress": 0,
                },
            )

            self.assertEqual(response.status_code, 302)
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=media_type,
            )
            self.assertEqual(item.format, "")
            self.assertTrue(
                model_class.objects.filter(item=item, user=self.user).exists()
            )

    @patch("app.models.providers.services.get_media_metadata")
    @patch("app.views.services.get_media_metadata")
    def test_create_comic_persists_authors_from_people_metadata(
        self, view_metadata_mock, model_metadata_mock
    ):
        """Comic saves should persist provider people names into Item.authors."""
        metadata = {
            "title": "People Writer Comic",
            "original_title": "People Writer Comic",
            "localized_title": "People Writer Comic",
            "image": "http://example.com/comic.jpg",
            "details": {
                "people": ["Writer One", "Writer Two"],
            },
        }
        view_metadata_mock.return_value = metadata
        model_metadata_mock.return_value = metadata

        response = self.client.post(
            reverse("media_save"),
            {
                "media_id": "comic-people-authors",
                "source": Sources.COMICVINE.value,
                "media_type": MediaTypes.COMIC.value,
                "status": Status.PLANNING.value,
                "progress": 0,
            },
        )

        self.assertEqual(response.status_code, 302)
        item = Item.objects.get(
            media_id="comic-people-authors",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
        )
        self.assertEqual(item.authors, ["Writer One", "Writer Two"])
        self.assertTrue(Comic.objects.filter(item=item, user=self.user).exists())

    @patch("app.save_views.ensure_item_metadata")
    def test_create_comic_issue(self, mock_ensure_item_metadata):
        """Comic issue saves should create ComicIssue rows."""
        item = Item.objects.create(
            media_id="114214",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC_ISSUE.value,
            title="The Iron Prometheus",
            image="http://example.com/issue.jpg",
        )
        mock_ensure_item_metadata.return_value = SimpleNamespace(
            item=item,
            artist=None,
            album=None,
            track=None,
            podcast_show=None,
        )

        response = self.client.post(
            reverse("media_save"),
            {
                "media_id": "114214",
                "source": Sources.COMICVINE.value,
                "media_type": MediaTypes.COMIC_ISSUE.value,
                "status": Status.COMPLETED.value,
                "progress": 1,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(ComicIssue.objects.filter(item=item, user=self.user).exists())

    @patch("app.views.services.get_media_metadata")
    def test_create_movie_sets_release_datetime_from_metadata(self, metadata_mock):
        metadata_mock.return_value = {
            "title": "The Matrix",
            "original_title": "The Matrix",
            "localized_title": "The Matrix",
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "details": {
                "release_date": "1999-03-31",
            },
        }

        self.client.post(
            reverse("media_save"),
            {
                "media_id": "603",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "status": Status.PLANNING.value,
            },
        )

        item = Item.objects.get(
            media_id="603",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
        )
        self.assertIsNotNone(item.release_datetime)
        self.assertEqual(
            item.release_datetime.date(), timezone.datetime(1999, 3, 31).date()
        )

    @patch("app.models.providers.services.get_media_metadata")
    @patch("app.views.services.get_media_metadata")
    def test_create_reading_media_persists_metadata_genres(
        self, view_metadata_mock, model_metadata_mock
    ):
        metadata_by_media = {
            (MediaTypes.BOOK.value, "book-genre", Sources.OPENLIBRARY.value): {
                "title": "Book Genre",
                "original_title": "Book Genre",
                "localized_title": "Book Genre",
                "image": "http://example.com/book.jpg",
                "max_progress": 320,
                "genres": ["Fantasy", "Adventure"],
                "details": {"number_of_pages": 320},
            },
            (MediaTypes.COMIC.value, "comic-genre", Sources.COMICVINE.value): {
                "title": "Comic Genre",
                "original_title": "Comic Genre",
                "localized_title": "Comic Genre",
                "image": "http://example.com/comic.jpg",
                "max_progress": 50,
                "genres": ["Sci-Fi"],
                "details": {},
            },
            (MediaTypes.MANGA.value, "manga-genre", Sources.MANGAUPDATES.value): {
                "title": "Manga Genre",
                "original_title": "Manga Genre",
                "localized_title": "Manga Genre",
                "image": "http://example.com/manga.jpg",
                "max_progress": 120,
                "genres": ["Shonen"],
                "details": {},
            },
        }

        def _metadata_side_effect(media_type, media_id, source, *_args, **_kwargs):
            return metadata_by_media[(media_type, media_id, source)]

        view_metadata_mock.side_effect = _metadata_side_effect
        model_metadata_mock.side_effect = _metadata_side_effect

        cases = [
            (
                MediaTypes.BOOK.value,
                Sources.OPENLIBRARY.value,
                "book-genre",
                ["Fantasy", "Adventure"],
                Book,
            ),
            (
                MediaTypes.COMIC.value,
                Sources.COMICVINE.value,
                "comic-genre",
                ["Sci-Fi"],
                Comic,
            ),
            (
                MediaTypes.MANGA.value,
                Sources.MANGAUPDATES.value,
                "manga-genre",
                ["Shonen"],
                Manga,
            ),
        ]

        for media_type, source, media_id, expected_genres, model in cases:
            self.client.post(
                reverse("media_save"),
                {
                    "media_id": media_id,
                    "source": source,
                    "media_type": media_type,
                    "status": Status.PLANNING.value,
                    "progress": 0,
                },
            )

            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=media_type,
            )
            self.assertEqual(item.genres, expected_genres)
            self.assertTrue(model.objects.filter(item=item, user=self.user).exists())

    @tag("network")
    def test_create_season(self):
        """Test the creation of a Season through views."""
        Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.SEASON.value,
                "season_number": 1,
                "status": Status.PLANNING.value,
            },
        )
        self.assertEqual(
            Season.objects.filter(item__media_id="1668", user=self.user).exists(),
            True,
        )

    @patch("app.providers.services.get_media_metadata")
    def test_complete_season_with_blank_end_date_creates_dateless_episodes(
        self,
        metadata_mock,
    ):
        """Marking a season Completed with end_date left blank sets no watch date.

        Regression test for GitHub issue #452: bulk-completing a season used
        to always stamp remaining episodes with today's date.
        """
        metadata_mock.return_value = {
            "episodes": [
                {"episode_number": 1, "image": "img1.jpg", "air_date": None},
                {"episode_number": 2, "image": "img2.jpg", "air_date": None},
            ],
            "image": "season_img.jpg",
        }
        Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.SEASON.value,
                "season_number": 1,
                "status": Status.COMPLETED.value,
                "end_date": "",
            },
        )
        season = Season.objects.get(item__media_id="1668", user=self.user)
        episodes = Episode.objects.filter(related_season=season)
        self.assertEqual(episodes.count(), 2)
        for episode in episodes:
            self.assertIsNone(episode.end_date)

    @patch("app.providers.services.get_media_metadata")
    def test_create_tvdb_season_with_tv_identity_media_type(self, metadata_mock):
        """A TVDB season save must create a Season, not a TV, despite identity_media_type=tv.

        Regression test for GitHub issue #323: TVDB seasons always carry
        identity_media_type="tv" to describe the parent show. media_save posts
        that value straight through as a hidden field, and
        get_tracking_media_type used to collapse "season" into "tv" whenever
        identity_media_type was truthy, using the wrong form (no season_number
        field) and the wrong tracking model.
        """
        metadata_mock.return_value = {
            "media_id": "388593",
            "source": Sources.TVDB.value,
            "media_type": MediaTypes.SEASON.value,
            "title": "Horimiya",
            "season_title": "Season 1",
            "season_number": 1,
            "identity_media_type": MediaTypes.TV.value,
            "library_media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "related": {},
            "details": {},
        }
        tv_item = Item.objects.create(
            media_id="388593",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            title="Horimiya",
            image="http://example.com/image.jpg",
        )
        TV.objects.create(item=tv_item, user=self.user, status=Status.PLANNING.value)
        Item.objects.create(
            media_id="388593",
            source=Sources.TVDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Horimiya",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "388593",
                "source": Sources.TVDB.value,
                "media_type": MediaTypes.SEASON.value,
                "identity_media_type": MediaTypes.TV.value,
                "library_media_type": MediaTypes.TV.value,
                "season_number": 1,
                "status": Status.PLANNING.value,
            },
        )
        self.assertTrue(
            Season.objects.filter(
                item__media_id="388593",
                item__season_number=1,
                user=self.user,
            ).exists(),
        )
        self.assertEqual(
            TV.objects.filter(item__media_id="388593", user=self.user).count(),
            1,
        )

    def test_media_save_updates_existing_tv_identity_linked_season(self):
        """Saving from the anime-bucket season page must update, not duplicate.

        Regression test for GitHub issue #623: when a season is already
        tracked via `related_tv` pointing at the show's TV-identity `TV` row,
        clicking "Add to tracker" on the anime-identity season page (which has
        no `instance_id`, because the page failed to find the existing row)
        used to try to INSERT a second `Season` row and 500 with
        `IntegrityError: ... app_season_unique_tv_item`.
        """
        tv_item = Item.objects.create(
            media_id="79824",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            title="Naruto Shippuden",
            image="http://example.com/image.jpg",
            library_media_type=MediaTypes.TV.value,
        )
        tv_identity = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="79824",
            source=Sources.TVDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=0,
            title="Naruto Shippuden",
            image="http://example.com/season.jpg",
            library_media_type=MediaTypes.TV.value,
        )
        existing_season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv_identity,
            status=Status.IN_PROGRESS.value,
            notes="pre-existing notes",
            score=8.5,
        )

        response = self.client.post(
            reverse("media_save"),
            {
                "media_id": "79824",
                "source": Sources.TVDB.value,
                "media_type": MediaTypes.SEASON.value,
                "identity_media_type": MediaTypes.ANIME.value,
                "library_media_type": MediaTypes.ANIME.value,
                "season_number": 0,
                "status": Status.COMPLETED.value,
                # A real submission resubmits the modal's pre-filled fields
                # unchanged alongside the one the user actually edited.
                "notes": "pre-existing notes",
                "score": 8.5,
            },
        )

        self.assertNotEqual(response.status_code, 500)
        self.assertEqual(
            Season.objects.filter(
                item__media_id="79824",
                item__season_number=0,
                user=self.user,
            ).count(),
            1,
        )
        existing_season.refresh_from_db()
        self.assertEqual(existing_season.status, Status.COMPLETED.value)
        self.assertEqual(existing_season.notes, "pre-existing notes")
        self.assertEqual(existing_season.score, 8.5)

    @tag("network")
    def test_create_episodes(self):
        """Test the creation of Episode through views."""
        self.client.post(
            reverse("episode_save"),
            {
                "media_id": "1668",
                "season_number": 1,
                "episode_number": 1,
                "source": Sources.TMDB.value,
                "date": "2023-06-01T00:00",
            },
        )
        self.assertEqual(
            Episode.objects.filter(
                item__media_id="1668",
                related_season__user=self.user,
                item__episode_number=1,
            ).exists(),
            True,
        )

    @patch(
        "app.providers.services.get_media_metadata",
        return_value={"episodes": [{"episode_number": 1}]},
    )
    def test_create_episode_rejects_absent_provider_coordinate(self, _metadata_mock):
        """Episode saves must fail before creating a season or history row."""
        response = self.client.post(
            reverse("episode_save"),
            {
                "media_id": "1668",
                "season_number": 1,
                "episode_number": 99,
                "source": Sources.TMDB.value,
                "date": "2023-06-01T00:00",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            Season.objects.filter(
                user=self.user,
                item__media_id="1668",
                item__season_number=1,
            ).exists(),
        )
        self.assertFalse(
            Episode.objects.filter(
                related_season__user=self.user,
                item__media_id="1668",
                item__episode_number=99,
            ).exists(),
        )

    @patch("app.models.providers.services.get_media_metadata")
    @patch("app.views.services.get_media_metadata")
    def test_create_episode_htmx_returns_inline_detail_updates(
        self,
        view_metadata_mock,
        model_metadata_mock,
    ):
        """HTMX episode saves should refresh the season card counters in place."""
        tv_metadata = {
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "title": "Friends",
            "original_title": "Friends",
            "localized_title": "Friends",
            "image": "http://example.com/friends.jpg",
            "details": {"seasons": 1},
            "related": {
                "seasons": [
                    {
                        "season_number": 1,
                        "image": "http://example.com/friends-s1.jpg",
                    },
                ],
            },
        }
        season_metadata = {
            **tv_metadata,
            "media_type": MediaTypes.SEASON.value,
            "season_number": 1,
            "season_title": "Season 1",
            "details": {"episodes": 1},
            "episodes": [
                {
                    "episode_number": 1,
                    "air_date": "2023-06-01",
                    "image": "http://example.com/friends-s1e1.jpg",
                    "name": "The One Where Monica Gets a Roommate",
                },
            ],
        }

        def metadata_side_effect(
            media_type,
            media_id,
            source,
            season_numbers=None,
            _episode_number=None,
            **_kwargs,
        ):
            self.assertEqual(media_id, "1668")
            self.assertEqual(source, Sources.TMDB.value)
            if media_type == "tv_with_seasons":
                self.assertEqual(season_numbers, [1])
                return {
                    **tv_metadata,
                    "season/1": season_metadata,
                }
            if media_type == MediaTypes.SEASON.value:
                self.assertEqual(season_numbers, [1])
                return season_metadata
            if media_type == MediaTypes.TV.value:
                return tv_metadata
            error_message = f"Unexpected metadata request: {media_type}"
            raise AssertionError(error_message)

        view_metadata_mock.side_effect = metadata_side_effect
        model_metadata_mock.side_effect = metadata_side_effect

        create_response = self.client.post(
            f"{reverse('episode_save')}?next=/details/tmdb/tv/1668/friends/season/1",
            {
                "media_id": "1668",
                "season_number": 1,
                "episode_number": 1,
                "source": Sources.TMDB.value,
                "end_date": "2023-06-01",
            },
        )
        self.assertEqual(create_response.status_code, 302)

        watch_operation_id = uuid4()
        response = self.client.post(
            f"{reverse('episode_save')}?next=/details/tmdb/tv/1668/friends/season/1",
            {
                "media_id": "1668",
                "season_number": 1,
                "episode_number": 1,
                "source": Sources.TMDB.value,
                "end_date": "2023-06-02",
                "watch_operation_id": watch_operation_id,
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'id="episode-track-button-',
            count=2,
            html=False,
        )
        # An additional data attribute now separates the id and OOB marker.
        self.assertRegex(
            response.content.decode(),
            r'id="episode-track-button-\d+-list"[^>]*\bhx-swap-oob="true"',
        )
        latest_episode = Episode.objects.filter(
            item__media_id="1668",
            related_season__user=self.user,
            item__episode_number=1,
        ).latest("id")
        self.assertContains(
            response,
            f'"instance_id": "{latest_episode.id}"',
            count=2,
            html=False,
        )
        self.assertContains(response, 'id="episode-history-', html=False)
        self.assertContains(response, 'hx-swap-oob="true"', html=False)
        self.assertContains(response, "2\u00d7 watched")
        self.assertContains(response, "season-progress-mobile-", html=False)
        self.assertContains(response, "season-progress-desktop-", html=False)
        trigger = json.loads(response["HX-Trigger"])
        returned_token = trigger["closeModal"]["watchOperationId"]
        self.assertEqual(str(UUID(returned_token)), returned_token)
        self.assertNotEqual(returned_token, str(watch_operation_id))
        self.assertEqual(trigger["showToast"]["type"], "success")
        self.assertIn("Added watch", trigger["showToast"]["message"])
        self.assertEqual(
            Episode.objects.filter(
                item__media_id="1668",
                related_season__user=self.user,
                item__episode_number=1,
            ).count(),
            2,
        )

    def test_create_song_htmx_returns_inline_detail_updates(self):
        """HTMX song saves should refresh album track state in place."""
        artist = Artist.objects.create(name="The Futureheads")
        album = Album.objects.create(title="The Futuristics", artist=artist)
        track = Track.objects.create(
            album=album,
            title="Track 1",
            track_number=1,
            disc_number=1,
            musicbrainz_recording_id="recording-1",
            duration_ms=180000,
        )

        create_response = self.client.post(
            f"{reverse('song_save')}?next=/details/music/artist/{artist.id}/the-futureheads/album/{album.id}/the-futuristics",
            {
                "recording_id": "recording-1",
                "album_id": album.id,
                "track_id": track.id,
                "end_date": "2023-06-01T00:00",
            },
        )
        self.assertEqual(create_response.status_code, 302)

        response = self.client.post(
            f"{reverse('song_save')}?next=/details/music/artist/{artist.id}/the-futureheads/album/{album.id}/the-futuristics",
            {
                "recording_id": "recording-1",
                "album_id": album.id,
                "track_id": track.id,
                "end_date": "2023-06-02T00:00",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="music-track-button-', html=False)
        self.assertContains(response, 'id="track-history-', html=False)
        self.assertContains(response, 'hx-swap-oob="true"', html=False)
        self.assertContains(response, "Listened 2 times")
        self.assertContains(response, "music-album-activity-subtitle-", html=False)
        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(trigger["closeModal"], {})
        self.assertEqual(trigger["showToast"]["type"], "success")
        self.assertIn("Added listen", trigger["showToast"]["message"])
        self.assertEqual(
            Music.objects.filter(
                item__media_id="recording-1",
                user=self.user,
                track=track,
            ).count(),
            1,
        )
        self.assertEqual(
            Music.objects.get(
                item__media_id="recording-1",
                user=self.user,
                track=track,
            ).history.count(),
            2,
        )

    @patch("app.save_views.ensure_item_metadata")
    def test_create_game_htmx_returns_activity_subtitle_update(
        self, ensure_item_metadata_mock
    ):
        """HTMX game saves should refresh the shared activity subtitle in place."""
        item = Item.objects.create(
            media_id="123",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Tracked Game",
            image="http://example.com/game.jpg",
            provider_game_lengths={"igdb": {"summary": {"normally_seconds": 8100}}},
            provider_game_lengths_source="igdb",
        )
        ensure_item_metadata_mock.return_value = SimpleNamespace(item=item)

        response = self.client.post(
            f"{reverse('media_save')}?next=/details/igdb/game/123/tracked-game",
            {
                "track_form_id": "track-form-test",
                "media_id": "123",
                "source": Sources.IGDB.value,
                "media_type": MediaTypes.GAME.value,
                "status": Status.PLANNING.value,
                "progress": "3h25min",
                "start_date": "2026-04-13T12:00",
                "end_date": "2026-05-12T12:00",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'id="detail-activity-subtitle-slot-game-123-igdb"', html=False
        )
        self.assertContains(response, "Progress: 3h 25min")
        self.assertContains(response, "April 13, 2026 - May 12, 2026")
        self.assertContains(response, "data-track-action-root", html=False)
        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(trigger["closeModal"]["formId"], "track-form-test")
        self.assertEqual(trigger["showToast"]["type"], "success")
        self.assertIn("Added", trigger["showToast"]["message"])
        self.assertTrue(
            Game.objects.filter(
                item__media_id="123",
                user=self.user,
                progress=205,
                status=Status.PLANNING.value,
            ).exists(),
        )

    @patch("app.save_views.ensure_item_metadata")
    @patch("app.save_views._build_detail_activity_state")
    def test_create_game_htmx_recovers_from_post_save_enrichment_failure(
        self,
        activity_state_mock,
        ensure_item_metadata_mock,
    ):
        """A post-save rendering failure must not blank a first-save response.

        Regression test for #601: the record is already committed by this point,
        so a downstream enrichment error (e.g. building the activity subtitle)
        must still return a valid confirmation, not a 500 swapped into a fragment.
        """
        item = Item.objects.create(
            media_id="456",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Tracked Game",
            image="http://example.com/game.jpg",
        )
        ensure_item_metadata_mock.return_value = SimpleNamespace(item=item)
        activity_state_mock.side_effect = RuntimeError("boom")

        response = self.client.post(
            f"{reverse('media_save')}?next=/details/igdb/game/456/tracked-game",
            {
                "track_form_id": "track-form-test",
                "media_id": "456",
                "source": Sources.IGDB.value,
                "media_type": MediaTypes.GAME.value,
                "status": Status.PLANNING.value,
                "progress": "0h",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-track-action-root", html=False)
        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(trigger["closeModal"]["formId"], "track-form-test")
        self.assertEqual(trigger["showToast"]["type"], "success")
        self.assertEqual(
            Game.objects.filter(item__media_id="456", user=self.user).count(),
            1,
        )

    @patch("app.providers.services.get_media_metadata")
    def test_create_movie_htmx_hydrates_real_first_save_metadata(
        self,
        metadata_mock,
    ):
        """The genuine first-save hydration path (uncached Item) must not blank.

        Unlike other HTMX create tests, this does not mock ``ensure_item_metadata``
        itself, so the real get-or-create + provider-metadata code path runs.
        """
        metadata_mock.return_value = {
            "title": "Brand New Movie",
            "image": "http://example.com/movie.jpg",
        }

        response = self.client.post(
            f"{reverse('media_save')}?next=/details/tmdb/movie/999/brand-new-movie",
            {
                "track_form_id": "track-form-test",
                "media_id": "999",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "status": Status.PLANNING.value,
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-track-action-root", html=False)
        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(trigger["showToast"]["type"], "success")
        self.assertEqual(
            Movie.objects.filter(item__media_id="999", user=self.user).count(),
            1,
        )

    @patch("app.save_views.ensure_item_metadata")
    @override_settings(TRACK_TIME=True)
    def test_create_game_backfills_start_date_from_progress(
        self,
        ensure_item_metadata_mock,
    ):
        """New game saves should infer start_date from progress when it is missing."""
        item = Item.objects.create(
            media_id="wordle-1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Wordle",
            image="http://example.com/wordle.jpg",
        )
        ensure_item_metadata_mock.return_value = SimpleNamespace(item=item)

        response = self.client.post(
            reverse("media_save"),
            {
                "media_id": "wordle-1",
                "source": Sources.IGDB.value,
                "media_type": MediaTypes.GAME.value,
                "status": Status.PLANNING.value,
                "progress": "5min",
                "end_date": "2026-05-12T12:00",
            },
        )

        self.assertEqual(response.status_code, 302)
        game = Game.objects.get(item__media_id="wordle-1")
        self.assertEqual(game.progress, 5)
        self.assertIsNotNone(game.start_date)
        self.assertEqual(game.start_date, game.end_date - datetime.timedelta(minutes=5))

    @patch("app.save_views.ensure_item_metadata")
    @override_settings(TRACK_TIME=True)
    def test_create_game_respects_cleared_flag(self, ensure_item_metadata_mock):
        """start_date_cleared=1 on a create form must prevent server from auto-calculating."""
        item = Item.objects.create(
            media_id="wordle-4",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Wordle",
            image="http://example.com/wordle.jpg",
        )
        ensure_item_metadata_mock.return_value = SimpleNamespace(item=item)

        response = self.client.post(
            reverse("media_save"),
            {
                "media_id": "wordle-4",
                "source": Sources.IGDB.value,
                "media_type": MediaTypes.GAME.value,
                "status": Status.PLANNING.value,
                "progress": "20min",
                "end_date": "2026-05-12T12:00",
                "start_date": "",
                "start_date_cleared": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        game = Game.objects.get(item__media_id="wordle-4")
        self.assertIsNone(game.start_date)

    @patch("app.models.providers.services.get_media_metadata")
    @patch("app.views.services.get_media_metadata")
    def test_create_grouped_anime_episode_uses_provider_source(
        self,
        view_metadata_mock,
        model_metadata_mock,
    ):
        """Grouped-anime episode saves should keep provider source and anime library type."""
        tv_metadata = {
            "media_id": "76703",
            "source": Sources.TVDB.value,
            "media_type": MediaTypes.TV.value,
            "title": "Pokemon",
            "original_title": "Pokemon",
            "localized_title": "Pokemon",
            "image": "http://example.com/pokemon.jpg",
            "details": {"seasons": 2},
            "related": {
                "seasons": [
                    {
                        "season_number": 1,
                        "image": "http://example.com/indigo.jpg",
                    },
                    {
                        "season_number": 2,
                        "image": "http://example.com/orange.jpg",
                    },
                ],
            },
        }
        tv_with_seasons_metadata = {
            **tv_metadata,
            "season/2": {
                "media_id": "76703",
                "source": Sources.TVDB.value,
                "media_type": MediaTypes.SEASON.value,
                "season_number": 2,
                "season_title": "Orange Islands",
                "image": "http://example.com/orange.jpg",
                "details": {"episodes": 2},
                "episodes": [
                    {
                        "episode_number": 1,
                        "air_date": "1999-01-28",
                        "image": "http://example.com/orange-1.jpg",
                        "name": "Pallet Party Panic",
                    },
                    {
                        "episode_number": 2,
                        "air_date": "1999-02-04",
                        "image": "http://example.com/orange-2.jpg",
                        "name": "A Scare in the Air",
                    },
                ],
            },
        }

        def metadata_side_effect(
            media_type,
            media_id,
            source,
            season_numbers=None,
            **_kwargs,
        ):
            self.assertEqual(media_id, "76703")
            self.assertEqual(source, Sources.TVDB.value)
            if media_type == "tv_with_seasons":
                self.assertEqual(season_numbers, [2])
                return tv_with_seasons_metadata
            if media_type == MediaTypes.SEASON.value:
                self.assertEqual(season_numbers, [2])
                return tv_with_seasons_metadata["season/2"]
            if media_type == MediaTypes.TV.value:
                return tv_metadata
            error_message = f"Unexpected metadata request: {media_type}"
            raise AssertionError(error_message)

        view_metadata_mock.side_effect = metadata_side_effect
        model_metadata_mock.side_effect = metadata_side_effect

        response = self.client.post(
            reverse("episode_save"),
            {
                "media_id": "76703",
                "season_number": 2,
                "episode_number": 1,
                "source": Sources.TVDB.value,
                "library_media_type": MediaTypes.ANIME.value,
                "end_date": "2024-01-01",
            },
        )

        self.assertEqual(response.status_code, 302)
        created_episode = Episode.objects.get(
            item__media_id="76703",
            item__source=Sources.TVDB.value,
            item__episode_number=1,
            related_season__user=self.user,
        )
        self.assertEqual(created_episode.related_season.item.source, Sources.TVDB.value)
        self.assertEqual(
            created_episode.related_season.item.library_media_type,
            MediaTypes.ANIME.value,
        )
        self.assertEqual(
            created_episode.related_season.related_tv.item.library_media_type,
            MediaTypes.ANIME.value,
        )

    @patch("app.models.providers.services.get_media_metadata")
    @patch("app.views.services.get_media_metadata")
    def test_create_grouped_anime_episode_prefers_route_source_over_tmdb_post_value(
        self,
        view_metadata_mock,
        model_metadata_mock,
    ):
        """Grouped-anime episode saves should follow the provider encoded in the details route."""
        tv_metadata = {
            "media_id": "267440",
            "source": Sources.TVDB.value,
            "media_type": MediaTypes.TV.value,
            "title": "Attack on Titan",
            "original_title": "進撃の巨人",
            "localized_title": "Attack on Titan",
            "image": "http://example.com/aot.jpg",
            "details": {"seasons": 4},
            "related": {
                "seasons": [
                    {
                        "season_number": 1,
                        "image": "http://example.com/aot-s1.jpg",
                    },
                ],
            },
        }
        tv_with_seasons_metadata = {
            **tv_metadata,
            "season/1": {
                "media_id": "267440",
                "source": Sources.TVDB.value,
                "media_type": MediaTypes.SEASON.value,
                "season_number": 1,
                "season_title": "Season 1",
                "image": "http://example.com/aot-s1.jpg",
                "details": {"episodes": 1},
                "episodes": [
                    {
                        "episode_number": 1,
                        "air_date": "2013-04-07",
                        "image": "http://example.com/aot-e1.jpg",
                        "name": "To You, in 2000 Years",
                    },
                ],
            },
        }

        def metadata_side_effect(
            media_type,
            media_id,
            source,
            season_numbers=None,
            **_kwargs,
        ):
            self.assertEqual(media_id, "267440")
            self.assertEqual(source, Sources.TVDB.value)
            if media_type == "tv_with_seasons":
                self.assertEqual(season_numbers, [1])
                return tv_with_seasons_metadata
            if media_type == MediaTypes.SEASON.value:
                self.assertEqual(season_numbers, [1])
                return tv_with_seasons_metadata["season/1"]
            if media_type == MediaTypes.TV.value:
                return tv_metadata
            error_message = f"Unexpected metadata request: {media_type}"
            raise AssertionError(error_message)

        view_metadata_mock.side_effect = metadata_side_effect
        model_metadata_mock.side_effect = metadata_side_effect

        response = self.client.post(
            f"{reverse('episode_save')}?next=/details/tvdb/tv/267440/attack-on-titan/season/1",
            {
                "media_id": "267440",
                "season_number": 1,
                "episode_number": 1,
                "source": Sources.TMDB.value,
                "library_media_type": MediaTypes.ANIME.value,
                "end_date": "2024-01-01",
            },
        )

        self.assertEqual(response.status_code, 302)
        created_episode = Episode.objects.get(
            item__media_id="267440",
            item__source=Sources.TVDB.value,
            item__episode_number=1,
            related_season__user=self.user,
        )
        self.assertEqual(created_episode.related_season.item.source, Sources.TVDB.value)


class EditMedia(TestCase):
    """Test the editing of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_edit_movie_score(self):
        """Test the editing of a movie score."""
        item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.user,
            score=9,
            progress=1,
            status=Status.COMPLETED.value,
            notes="Nice",
            start_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
            end_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
        )

        response = self.client.post(
            reverse("media_save"),
            {
                "instance_id": movie.id,
                "media_id": "10494",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "score": 10,
                "progress": 1,
                "status": Status.COMPLETED.value,
                "notes": "Nice",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Movie.objects.get(item__media_id="10494").score, 10)

    @patch("app.models.providers.services.get_media_metadata")
    def test_edit_episode_tracking_details(self, metadata_mock):
        """Episode edits and save-as-new submissions persist the right row."""
        metadata_mock.return_value = {"season/1": {"episodes": []}}
        season_item = Item.objects.create(
            media_id="episode-edit-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Episode Edit",
            image="http://example.com/season.jpg",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        episode_item = Item.objects.create(
            media_id="episode-edit-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Episode One",
            image="http://example.com/episode.jpg",
            season_number=1,
            episode_number=1,
        )
        episode = Episode.objects.create(
            item=episode_item,
            related_season=season,
            end_date=datetime.datetime(2025, 1, 2, tzinfo=datetime.UTC),
        )

        response = self.client.post(
            reverse("episode_save"),
            {
                "instance_id": episode.id,
                "media_id": episode_item.media_id,
                "source": episode_item.source,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 1,
                "score": 8.5,
                "status": Status.PAUSED.value,
                "start_date": "2025-01-01",
                "notes": "Paused midway",
            },
        )

        self.assertEqual(response.status_code, 302)
        episode.refresh_from_db()
        self.assertEqual(episode.score, 8.5)
        self.assertEqual(episode.status, Status.PAUSED.value)
        self.assertEqual(episode.start_date.date().isoformat(), "2025-01-01")
        self.assertIsNone(episode.end_date)
        self.assertEqual(episode.notes, "Paused midway")
        self.assertFalse(episode.dropped)
        metadata_mock.return_value = {
            "max_progress": 1,
            "season/1": {
                "details": {"episodes": 1},
                "episodes": [{"episode_number": 1}],
            },
        }
        operation_id = uuid4()
        with (
            patch("app.save_views.resolve_episode_coordinate"),
            patch(
                "app.save_views.fork_services_episode.resolve_or_create_season",
                return_value=season,
            ),
        ):
            response = self.client.post(
                reverse("episode_save"),
                {
                    "instance_id": episode.id,
                    "save_as_new_entry": "1",
                    "media_id": episode_item.media_id,
                    "source": episode_item.source,
                    "media_type": MediaTypes.EPISODE.value,
                    "season_number": 1,
                    "episode_number": 1,
                    "end_date": "2025-01-03",
                    "notes": "Second watch",
                    "watch_operation_id": operation_id,
                },
            )

        self.assertEqual(response.status_code, 302)
        history = Episode.objects.filter(item=episode_item).order_by("id")
        self.assertEqual(history.count(), 2)
        episode.refresh_from_db()
        self.assertEqual(episode.notes, "Paused midway")
        new_entry = history.exclude(pk=episode.pk).get()
        self.assertEqual(new_entry.notes, "Second watch")
        self.assertEqual(new_entry.end_date.date().isoformat(), "2025-01-03")
        self.assertEqual(new_entry.watch_operation_id, operation_id)

    @patch("app.models.providers.services.get_media_metadata")
    def test_adding_watch_date_to_open_episode_play_completes_it(self, metadata_mock):
        """A paused check-in's open play edited with a watch date is a finished watch.

        The track modal edits the latest play, so marking an episode watched
        while a check-in left it open submits the prefilled In Progress status.
        """
        metadata_mock.return_value = {"season/1": {"episodes": []}}
        season_item = Item.objects.create(
            media_id="episode-open-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Open Play",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        episode_item = Item.objects.create(
            media_id="episode-open-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Episode One",
            season_number=1,
            episode_number=1,
        )
        episode = Episode.objects.create(
            item=episode_item,
            related_season=season,
            status=Status.IN_PROGRESS.value,
            start_date=datetime.datetime(2025, 1, 2, 20, 0, tzinfo=datetime.UTC),
            entry_source="check-in",
        )

        response = self.client.post(
            reverse("episode_save"),
            {
                "instance_id": episode.id,
                "media_id": episode_item.media_id,
                "source": episode_item.source,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 1,
                "status": Status.IN_PROGRESS.value,
                "start_date": "2025-01-02T20:00",
                "end_date": "2025-01-02T20:25",
                "entry_source": "check-in",
            },
        )

        self.assertEqual(response.status_code, 302)
        episode.refresh_from_db()
        self.assertEqual(episode.status, Status.COMPLETED.value)
        self.assertIsNotNone(episode.end_date)

    def test_edit_movie_htmx_returns_inline_detail_update(self):
        """HTMX saves should update the detail tracker in place."""
        item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.user,
            score=9,
            progress=1,
            status=Status.IN_PROGRESS.value,
            notes="Nice",
            start_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
            end_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
        )
        initial_status = Movie.objects.get(item__media_id="10494").status

        response = self.client.post(
            f"{reverse('media_save')}?next=/details/tmdb/movie/10494/perfect-blue",
            {
                "track_form_id": "track-form-test",
                "instance_id": movie.id,
                "media_id": "10494",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "score": 10,
                "progress": 1,
                "status": Status.COMPLETED.value,
                "notes": "Nice",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-track-action-root", html=False)
        self.assertContains(response, 'id="track-action-movie-10494"', html=False)
        # The modal is teleported to <body>, so the pill must replace itself by id.
        self.assertContains(response, 'hx-swap-oob="outerHTML"', html=False)
        self.assertContains(response, f'id="detail-score-chip-{movie.id}"', html=False)
        self.assertContains(response, "Edit rating")
        # A completed movie offers another entry beside Check-in.
        self.assertContains(response, "Check-in</span>", html=False)
        self.assertContains(response, "Add new entry")
        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(trigger["closeModal"]["formId"], "track-form-test")
        self.assertEqual(trigger["showToast"]["type"], "success")
        self.assertIn("Updated", trigger["showToast"]["message"])
        self.assertTrue(
            Movie.objects.filter(
                item__media_id="10494",
                score=10,
                status=Status.COMPLETED.value,
            ).exists(),
        )

    @override_settings(TRACK_TIME=True)
    def test_edit_game_preserves_existing_start_date(self):
        """Editing an existing game should not rewrite an already-set start date."""
        item = Item.objects.create(
            media_id="wordle-1",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Wordle",
            image="http://example.com/wordle.jpg",
        )
        start_date = datetime.datetime(2026, 5, 12, 11, 40, tzinfo=datetime.UTC)
        end_date = datetime.datetime(2026, 5, 12, 12, 0, tzinfo=datetime.UTC)
        game = Game.objects.create(
            item=item,
            user=self.user,
            status=Status.PLANNING.value,
            progress=20,
            start_date=start_date,
            end_date=end_date,
        )

        response = self.client.post(
            reverse("media_save"),
            {
                "instance_id": game.id,
                "media_id": "wordle-1",
                "source": Sources.IGDB.value,
                "media_type": MediaTypes.GAME.value,
                "status": Status.PLANNING.value,
                "progress": "10min",
                "start_date": "2026-05-12T11:40",
                "end_date": "2026-05-12T12:00",
            },
        )

        self.assertEqual(response.status_code, 302)
        game.refresh_from_db()
        self.assertEqual(game.start_date, start_date)
        self.assertEqual(game.end_date, end_date)
        self.assertEqual(game.progress, 10)

    def test_edit_game_clears_start_date_when_start_date_cleared_flag_set(self):
        """start_date_cleared=1 must prevent the server from recalculating start_date."""
        item = Item.objects.create(
            media_id="wordle-2",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Wordle",
            image="http://example.com/wordle.jpg",
        )
        start_date = datetime.datetime(2026, 5, 12, 11, 40, tzinfo=datetime.UTC)
        end_date = datetime.datetime(2026, 5, 12, 12, 0, tzinfo=datetime.UTC)
        game = Game.objects.create(
            item=item,
            user=self.user,
            status=Status.PLANNING.value,
            progress=20,
            start_date=start_date,
            end_date=end_date,
        )

        response = self.client.post(
            reverse("media_save"),
            {
                "instance_id": game.id,
                "media_id": "wordle-2",
                "source": Sources.IGDB.value,
                "media_type": MediaTypes.GAME.value,
                "status": Status.PLANNING.value,
                "progress": "20min",
                "end_date": "2026-05-12T12:00",
                "start_date": "",
                "start_date_cleared": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        game.refresh_from_db()
        self.assertIsNone(game.start_date)

    def test_edit_game_clears_start_date_when_db_had_start_date(self):
        """Clearing start_date without the sentinel flag still works when the DB had a value."""
        item = Item.objects.create(
            media_id="wordle-3",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Wordle",
            image="http://example.com/wordle.jpg",
        )
        start_date = datetime.datetime(2026, 5, 12, 11, 40, tzinfo=datetime.UTC)
        end_date = datetime.datetime(2026, 5, 12, 12, 0, tzinfo=datetime.UTC)
        game = Game.objects.create(
            item=item,
            user=self.user,
            status=Status.PLANNING.value,
            progress=20,
            start_date=start_date,
            end_date=end_date,
        )

        response = self.client.post(
            reverse("media_save"),
            {
                "instance_id": game.id,
                "media_id": "wordle-3",
                "source": Sources.IGDB.value,
                "media_type": MediaTypes.GAME.value,
                "status": Status.PLANNING.value,
                "progress": "20min",
                "end_date": "2026-05-12T12:00",
                "start_date": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        game.refresh_from_db()
        self.assertIsNone(game.start_date)

    @tag("network")
    @patch("app.views.ensure_item_metadata")
    def test_create_movie_htmx_inserts_score_chip_slot(self, ensure_item_metadata_mock):
        """HTMX tracker creation should insert the score chip into the page."""
        item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        ensure_item_metadata_mock.return_value = SimpleNamespace(item=item)

        response = self.client.post(
            f"{reverse('media_save')}?next=/details/tmdb/movie/10494/perfect-blue",
            {
                "track_form_id": "track-form-test",
                "media_id": "10494",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "score": 8.7,
                "progress": 1,
                "status": Status.COMPLETED.value,
                "notes": "Nice",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'id="detail-score-chip-slot-movie-10494-tmdb"',
            html=False,
        )
        self.assertContains(
            response,
            f'id="detail-score-chip-{Movie.objects.get(item__media_id="10494").id}"',
            html=False,
        )
        self.assertContains(response, "Edit rating")
        self.assertContains(response, "Completed")
        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(trigger["closeModal"]["formId"], "track-form-test")
        self.assertEqual(trigger["showToast"]["type"], "success")
        self.assertIn("Added", trigger["showToast"]["message"])
        self.assertTrue(
            Movie.objects.filter(
                item__media_id="10494",
                score=8.7,
                status=Status.COMPLETED.value,
            ).exists(),
        )

    def test_edit_movie_htmx_validation_error_reopens_modal(self):
        """Invalid HTMX saves should re-render the modal and reopen it."""
        item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.user,
            score=9,
            progress=1,
            status=Status.IN_PROGRESS.value,
            notes="Nice",
            start_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
            end_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
        )
        initial_status = Movie.objects.get(item__media_id="10494").status

        response = self.client.post(
            f"{reverse('media_save')}?next=/details/tmdb/movie/10494/perfect-blue",
            {
                "track_form_id": "track-form-test",
                "instance_id": movie.id,
                "media_id": "10494",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "score": 10,
                "progress": 1,
                "status": "bogus",
                "notes": "Nice",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-track-action-root", html=False)
        self.assertContains(response, "editTrackOpen: true", html=False)
        self.assertContains(response, "track-form-test")
        self.assertContains(response, "data-track-modal-root", html=False)
        self.assertContains(
            response,
            'hx-target="this"',
            html=False,
        )
        self.assertContains(response, 'hx-swap="none"', html=False)
        self.assertContains(response, 'hx-swap-oob="outerHTML"', html=False)
        self.assertEqual(
            Movie.objects.get(item__media_id="10494").status,
            initial_status,
        )

    @patch("app.save_views.ensure_item_metadata")
    def test_add_movie_entry_htmx_validation_error_reopens_create_modal(
        self, ensure_item_metadata_mock,
    ):
        """A failed Add new entry reopens its own modal on the split button."""
        item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            progress=1,
            status=Status.COMPLETED.value,
        )
        ensure_item_metadata_mock.return_value = SimpleNamespace(item=item)

        response = self.client.post(
            f"{reverse('media_save')}?next=/details/tmdb/movie/10494/perfect-blue",
            {
                "track_form_id": "track-form-test",
                "media_id": "10494",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "progress": 1,
                "status": "bogus",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check-in</span>", html=False)
        self.assertContains(response, "createTrackOpen: true", html=False)
        self.assertContains(response, "editTrackOpen: false", html=False)
        self.assertContains(response, "track-form-test")
        self.assertContains(response, "data-track-modal-root", html=False)
        self.assertContains(response, 'hx-swap-oob="outerHTML"', html=False)
        self.assertEqual(Movie.objects.filter(item=item).count(), 1)

    def test_edit_movie_image_url(self):
        """Test overriding a movie image URL from the edit form."""
        item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/original.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        self.client.post(
            reverse("media_save"),
            {
                "instance_id": movie.id,
                "media_id": "10494",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "status": Status.PLANNING.value,
                "image_url": "https://images.example.com/custom-poster.jpg",
            },
        )

        item.refresh_from_db()
        self.assertEqual(item.image, "https://images.example.com/custom-poster.jpg")


class DeleteMedia(TestCase):
    """Test the deletion of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        self.season = Season.objects.create(
            item=self.item_season,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        self.item_ep = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=99,
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.episode = Episode.objects.create(
                item=self.item_ep,
                related_season=self.season,
                end_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
            )
        cache.clear()

    def test_delete_tv(self):
        """Test the deletion of a tv through views."""
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)
        tv_obj = TV.objects.get(user=self.user)

        self.client.post(
            reverse("media_delete"),
            data={
                "instance_id": tv_obj.id,
                "media_type": MediaTypes.TV.value,
            },
        )

        self.assertEqual(Movie.objects.filter(user=self.user).count(), 0)

    def test_delete_season(self):
        """Test the deletion of a season through views."""
        self.client.post(
            reverse(
                "media_delete",
            ),
            data={"instance_id": self.season.id, "media_type": MediaTypes.SEASON.value},
        )

        self.assertEqual(Season.objects.filter(user=self.user).count(), 0)
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            0,
        )

    def test_delete_custom_tv(self):
        """Deleting a manual/custom item tombstones a UUID media_id (#877)."""
        manual_id = Item.generate_manual_id()
        item = Item.objects.create(
            media_id=manual_id,
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Custom Show",
            image="http://example.com/image.jpg",
        )
        tv_obj = TV.objects.create(item=item, user=self.user, status=Status.IN_PROGRESS.value)

        response = self.client.post(
            reverse("media_delete"),
            data={
                "instance_id": tv_obj.id,
                "media_type": MediaTypes.TV.value,
            },
        )

        self.assertNotEqual(response.status_code, 405)
        self.assertFalse(TV.objects.filter(id=tv_obj.id).exists())
        self.assertTrue(
            DeletedMedia.objects.filter(
                user=self.user,
                media_type=MediaTypes.TV.value,
                source=Sources.MANUAL.value,
                media_id=manual_id,
            ).exists(),
        )

    def test_unwatch_episode(self):
        """Test unwatching of an episode through views."""
        self.client.post(
            reverse("media_delete"),
            data={
                "instance_id": self.episode.id,
                "media_type": MediaTypes.EPISODE.value,
            },
        )

        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            0,
        )

    def test_unwatch_episode_htmx_updates_episode_controls(self):
        """HTMX episode deletes update the list without redirecting the page."""
        response = self.client.post(
            reverse("media_delete") + "?next=/details/tmdb/tv/1668/friends/season/1",
            data={
                "instance_id": self.episode.id,
                "media_type": MediaTypes.EPISODE.value,
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("HX-Redirect", response)
        self.assertContains(response, 'id="episode-track-button-', html=False)
        self.assertContains(response, 'id="episode-history-', html=False)
        self.assertContains(response, "season-progress-mobile-", html=False)
        self.assertContains(response, "season-progress-desktop-", html=False)
        trigger = json.loads(response["HX-Trigger"])
        self.assertEqual(trigger["closeModal"], {})
        self.assertEqual(trigger["showToast"]["type"], "success")
        self.assertFalse(Episode.objects.filter(pk=self.episode.id).exists())

    @patch("app.signals._handle_media_cache_change")
    def test_unwatch_episode_removes_warm_history_cache_entry(
        self,
        _handle_media_cache_change,
    ):
        """Deleting a play must remove its cached history card before redirect."""
        logging_style = "repeats"
        history_cache.refresh_history_cache(
            self.user.id,
            logging_style=logging_style,
        )
        history_day_key = history_cache.history_day_key(self.episode.end_date)
        cached_day = cache.get(
            history_cache._day_cache_key(
                self.user.id,
                logging_style,
                history_day_key,
            ),
        )
        self.assertTrue(cached_day["entries"])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("media_delete"),
                data={
                    "instance_id": self.episode.id,
                    "media_type": MediaTypes.EPISODE.value,
                },
            )

        self.assertEqual(response.status_code, 302)
        history_days, _ = history_cache.get_month_history(
            self.user,
            self.episode.end_date.year,
            self.episode.end_date.month,
            logging_style_override=logging_style,
        )
        self.assertFalse(
            any(day["entries"] for day in history_days),
        )
