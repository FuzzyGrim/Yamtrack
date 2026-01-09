from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase

from app.models import MediaTypes, Sources
from app.providers import (
    igdb,
    mal,
    services,
    tmdb,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"


class ServicesTests(TestCase):
    """Test the services module functions."""

    @patch("app.providers.services.session.get")
    def test_api_request_get(self, mock_get):
        """Test the api_request function with GET method."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": "test"}
        mock_get.return_value = mock_response

        result = services.api_request(
            "TEST",
            "GET",
            "https://example.com/api",
            params={"param": "value"},
        )

        self.assertEqual(result, {"data": "test"})

        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["url"], "https://example.com/api")
        self.assertEqual(kwargs["params"], {"param": "value"})
        self.assertIn("timeout", kwargs)

    @patch("app.providers.services.session.post")
    def test_api_request_post(self, mock_post):
        """Test the api_request function with POST method."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": "test"}
        mock_post.return_value = mock_response

        result = services.api_request(
            "TEST",
            "POST",
            "https://example.com/api",
            params={"json_param": "value"},
            data={"form_data": "value"},
        )

        self.assertEqual(result, {"data": "test"})

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["url"], "https://example.com/api")
        self.assertEqual(kwargs["json"], {"json_param": "value"})
        self.assertEqual(kwargs["data"], {"form_data": "value"})
        self.assertIn("timeout", kwargs)

    @patch("app.providers.services.api_request")
    def test_request_error_handling_rate_limit(self, mock_api_request):
        """Test the request_error_handling function with rate limiting."""
        mock_response = MagicMock()
        mock_response.status_code = 429  # Too many requests
        mock_response.headers = {"Retry-After": "5"}

        error = requests.exceptions.HTTPError("429 Too Many Requests")
        error.response = mock_response

        mock_api_request.return_value = {"data": "retry_success"}

        result = services.api_request(
            error,
            "TEST",
            "GET",
            "https://example.com/api",
            {"param": "value"},
            None,
            None,
        )

        mock_api_request.assert_called_once()

        self.assertEqual(result, {"data": "retry_success"})

    @patch("app.providers.igdb.cache.delete")
    def test_handle_error_igdb_unauthorized(
        self,
        mock_cache_delete,
    ):
        """Test the handle_error function with IGDB unauthorized error."""
        mock_response = MagicMock()
        mock_response.status_code = 401  # Unauthorized

        error = requests.exceptions.HTTPError("401 Unauthorized")
        error.response = mock_response

        result = igdb.handle_error(error)

        mock_cache_delete.assert_called_once_with("igdb_access_token")

        self.assertEqual(result, {"retry": True})

    def test_handle_error_igdb_bad_request(self):
        """Test the handle_error function with IGDB bad request error."""
        mock_response = MagicMock()
        mock_response.status_code = 400  # Bad Request
        mock_response.json.return_value = {"message": "Invalid query"}

        error = requests.exceptions.HTTPError("400 Bad Request")
        error.response = mock_response

        with self.assertRaises(services.ProviderAPIError) as cm:
            igdb.handle_error(error)

        self.assertEqual(cm.exception.provider, Sources.IGDB.value)

    def test_handle_error_tmdb_unauthorized(self):
        """Test the handle_error function with TMDB unauthorized error."""
        mock_response = MagicMock()
        mock_response.status_code = 401  # Unauthorized
        mock_response.json.return_value = {"status_message": "Invalid API key"}

        error = requests.exceptions.HTTPError("401 Unauthorized")
        error.response = mock_response

        with self.assertRaises(services.ProviderAPIError) as cm:
            tmdb.handle_error(error)

        self.assertEqual(cm.exception.provider, Sources.TMDB.value)

    def test_handle_error_mal_forbidden(self):
        """Test the handle_error function with MAL forbidden error."""
        mock_response = MagicMock()
        mock_response.status_code = 403  # Forbidden
        mock_response.json.return_value = {"message": "Forbidden"}

        error = requests.exceptions.HTTPError("403 Forbidden")
        error.response = mock_response

        with self.assertRaises(services.ProviderAPIError) as cm:
            mal.handle_error(error)

        self.assertEqual(cm.exception.provider, Sources.MAL.value)

    @patch("app.providers.mal.anime")
    def test_get_media_metadata_anime(self, mock_anime):
        """Test the get_media_metadata function for anime."""
        mock_anime.return_value = {"title": "Test Anime"}

        result = services.get_media_metadata(
            MediaTypes.ANIME.value,
            "1",
            Sources.MAL.value,
        )

        self.assertEqual(result, {"title": "Test Anime"})

        mock_anime.assert_called_once_with("1")

    @patch("app.providers.mangaupdates.manga")
    def test_get_media_metadata_manga_mangaupdates(self, mock_manga):
        """Test the get_media_metadata function for manga from MangaUpdates."""
        mock_manga.return_value = {"title": "Test Manga"}

        result = services.get_media_metadata(
            MediaTypes.MANGA.value,
            "1",
            Sources.MANGAUPDATES.value,
        )

        self.assertEqual(result, {"title": "Test Manga"})

        mock_manga.assert_called_once_with("1")

    @patch("app.providers.mal.manga")
    def test_get_media_metadata_manga_mal(self, mock_manga):
        """Test the get_media_metadata function for manga from MAL."""
        mock_manga.return_value = {"title": "Test Manga"}

        result = services.get_media_metadata(
            MediaTypes.MANGA.value,
            "1",
            Sources.MAL.value,
        )

        self.assertEqual(result, {"title": "Test Manga"})

        mock_manga.assert_called_once_with("1")

    @patch("app.providers.tmdb.tv")
    def test_get_media_metadata_tv(self, mock_tv):
        """Test the get_media_metadata function for TV shows."""
        mock_tv.return_value = {"title": "Test TV"}

        result = services.get_media_metadata(
            MediaTypes.TV.value,
            "1",
            Sources.TMDB.value,
        )

        self.assertEqual(result, {"title": "Test TV"})

        mock_tv.assert_called_once_with("1")

    @patch("app.providers.tmdb.tv_with_seasons")
    def test_get_media_metadata_tv_with_seasons(self, mock_tv_with_seasons):
        """Test the get_media_metadata function for TV shows with seasons."""
        mock_tv_with_seasons.return_value = {"title": "Test TV with Seasons"}

        result = services.get_media_metadata(
            "tv_with_seasons",
            "1",
            Sources.TMDB.value,
            season_numbers=[1, 2],
        )

        self.assertEqual(result, {"title": "Test TV with Seasons"})

        mock_tv_with_seasons.assert_called_once_with("1", [1, 2])

    @patch("app.providers.tmdb.tv_with_seasons")
    def test_get_media_metadata_season(self, mock_tv_with_seasons):
        """Test the get_media_metadata function for TV seasons."""
        mock_tv_with_seasons.return_value = {
            "season/1": {"title": "Test Season"},
        }

        result = services.get_media_metadata(
            MediaTypes.SEASON.value,
            "1",
            Sources.TMDB.value,
            season_numbers=[1],
        )

        self.assertEqual(result, {"title": "Test Season"})

        mock_tv_with_seasons.assert_called_once_with("1", [1])

    @patch("app.providers.tmdb.episode")
    def test_get_media_metadata_episode(self, mock_episode):
        """Test the get_media_metadata function for TV episodes."""
        mock_episode.return_value = {"title": "Test Episode"}

        result = services.get_media_metadata(
            MediaTypes.EPISODE.value,
            "1",
            Sources.TMDB.value,
            season_numbers=[1],
            episode_number="2",
        )

        self.assertEqual(result, {"title": "Test Episode"})

        mock_episode.assert_called_once_with("1", 1, "2")

    @patch("app.providers.tmdb.movie")
    def test_get_media_metadata_movie(self, mock_movie):
        """Test the get_media_metadata function for movies."""
        mock_movie.return_value = {"title": "Test Movie"}

        result = services.get_media_metadata(
            MediaTypes.MOVIE.value,
            "1",
            Sources.TMDB.value,
        )

        self.assertEqual(result, {"title": "Test Movie"})

        mock_movie.assert_called_once_with("1")

    @patch("app.providers.igdb.game")
    def test_get_media_metadata_game(self, mock_game):
        """Test the get_media_metadata function for games."""
        mock_game.return_value = {"title": "Test Game"}

        result = services.get_media_metadata(
            MediaTypes.GAME.value,
            "1",
            Sources.IGDB.value,
        )

        self.assertEqual(result, {"title": "Test Game"})

        mock_game.assert_called_once_with("1")

    @patch("app.providers.comicvine.comic")
    def test_get_media_metadata_comic(self, mock_comic):
        """Test the get_media_metadata function for comics."""
        mock_comic.return_value = {"title": "Test Comic"}

        result = services.get_media_metadata(
            MediaTypes.COMIC.value,
            "1",
            Sources.COMICVINE.value,
        )

        self.assertEqual(result, {"title": "Test Comic"})

        mock_comic.assert_called_once_with("1")

    @patch("app.providers.openlibrary.book")
    def test_get_media_metadata_book(self, mock_book):
        """Test the get_media_metadata function for books."""
        mock_book.return_value = {"title": "Test Book"}

        result = services.get_media_metadata(
            MediaTypes.BOOK.value,
            "1",
            Sources.OPENLIBRARY.value,
        )

        self.assertEqual(result, {"title": "Test Book"})

        mock_book.assert_called_once_with("1")

    @patch("app.providers.manual.metadata")
    def test_get_media_metadata_manual(self, mock_metadata):
        """Test the get_media_metadata function for manual media."""
        mock_metadata.return_value = {"title": "Test Manual"}

        result = services.get_media_metadata(
            MediaTypes.MOVIE.value,
            "1",
            Sources.MANUAL.value,
        )

        self.assertEqual(result, {"title": "Test Manual"})

        mock_metadata.assert_called_once_with("1", MediaTypes.MOVIE.value)

    @patch("app.providers.manual.season")
    def test_get_media_metadata_manual_season(self, mock_season):
        """Test the get_media_metadata function for manual seasons."""
        mock_season.return_value = {"title": "Test Manual Season"}

        result = services.get_media_metadata(
            MediaTypes.SEASON.value,
            "1",
            Sources.MANUAL.value,
            season_numbers=[1],
        )

        self.assertEqual(result, {"title": "Test Manual Season"})

        mock_season.assert_called_once_with("1", 1)

    @patch("app.providers.manual.episode")
    def test_get_media_metadata_manual_episode(self, mock_episode):
        """Test the get_media_metadata function for manual episodes."""
        mock_episode.return_value = {"title": "Test Manual Episode"}

        result = services.get_media_metadata(
            MediaTypes.EPISODE.value,
            "1",
            Sources.MANUAL.value,
            season_numbers=[1],
            episode_number="2",
        )

        self.assertEqual(result, {"title": "Test Manual Episode"})

        mock_episode.assert_called_once_with("1", 1, "2")

    @patch("app.providers.tmdb.episode")
    def test_get_media_metadata_tmdb_episode_not_found(self, mock_episode):
        """Test the get_media_metadata function for TMDB episodes that don't exist."""
        mock_response = type(
            "Response", (), {"status_code": 404, "text": "Episode not found"},
        )()
        mock_error = type("Error", (), {"response": mock_response})()
        mock_episode.side_effect = services.ProviderAPIError(
            Sources.TMDB.value,
            mock_error,
        )

        with self.assertRaises(services.ProviderAPIError) as cm:
            services.get_media_metadata(
                MediaTypes.EPISODE.value,
                "1396",
                Sources.TMDB.value,
                season_numbers=[1],
                episode_number="3",
            )

        self.assertEqual(cm.exception.provider, Sources.TMDB.value)

        mock_episode.assert_called_once_with("1396", 1, "3")


    @patch("app.providers.hardcover.book")
    def test_get_media_metadata_hardcover_book(self, mock_book):
        """Test the get_media_metadata function for books from Hardcover."""
        mock_book.return_value = {"title": "Test Hardcover Book"}

        result = services.get_media_metadata(
            MediaTypes.BOOK.value,
            "1",
            Sources.HARDCOVER.value,
        )

        self.assertEqual(result, {"title": "Test Hardcover Book"})

        mock_book.assert_called_once_with("1")

    @patch("app.providers.mal.search")
    def test_search_anime(self, mock_search):
        """Test the search function for anime."""
        mock_search.return_value = [{"title": "Test Anime"}]

        result = services.search(MediaTypes.ANIME.value, "test", 1)

        self.assertEqual(result, [{"title": "Test Anime"}])

        mock_search.assert_called_once_with(MediaTypes.ANIME.value, "test", 1)

    @patch("app.providers.mangaupdates.search")
    def test_search_manga_mangaupdates(self, mock_search):
        """Test the search function for manga from MangaUpdates."""
        mock_search.return_value = [{"title": "Test Manga"}]

        result = services.search(
            MediaTypes.MANGA.value,
            "test",
            1,
            source=Sources.MANGAUPDATES.value,
        )

        self.assertEqual(result, [{"title": "Test Manga"}])

        mock_search.assert_called_once_with("test", 1)

    @patch("app.providers.mal.search")
    def test_search_manga_mal(self, mock_search):
        """Test the search function for manga from MAL."""
        mock_search.return_value = [{"title": "Test Manga"}]

        result = services.search(MediaTypes.MANGA.value, "test", 1)

        self.assertEqual(result, [{"title": "Test Manga"}])

        mock_search.assert_called_once_with(MediaTypes.MANGA.value, "test", 1)

    @patch("app.providers.tmdb.search")
    def test_search_tv(self, mock_search):
        """Test the search function for TV shows."""
        mock_search.return_value = [{"title": "Test TV"}]

        result = services.search(MediaTypes.TV.value, "test", 1)

        self.assertEqual(result, [{"title": "Test TV"}])

        mock_search.assert_called_once_with(MediaTypes.TV.value, "test", 1)

    @patch("app.providers.tmdb.search")
    def test_search_movie(self, mock_search):
        """Test the search function for movies."""
        mock_search.return_value = [{"title": "Test Movie"}]

        result = services.search(MediaTypes.MOVIE.value, "test", 1)

        self.assertEqual(result, [{"title": "Test Movie"}])

        mock_search.assert_called_once_with(MediaTypes.MOVIE.value, "test", 1)

    @patch("app.providers.igdb.search")
    def test_search_game(self, mock_search):
        """Test the search function for games."""
        mock_search.return_value = [{"title": "Test Game"}]

        result = services.search(MediaTypes.GAME.value, "test", 1)

        self.assertEqual(result, [{"title": "Test Game"}])

        mock_search.assert_called_once_with("test", 1)

    @patch("app.providers.hardcover.search")
    def test_search_hardcover_book(self, mock_search):
        """Test the search function for books from Hardcover."""
        mock_search.return_value = [{"title": "Test Hardcover Book"}]

        result = services.search(
            MediaTypes.BOOK.value,
            "test",
            1,
            source=Sources.HARDCOVER.value,
        )

        self.assertEqual(result, [{"title": "Test Hardcover Book"}])

        mock_search.assert_called_once_with("test", 1)

    @patch("app.providers.openlibrary.search")
    def test_search_openlibrary_book(self, mock_search):
        """Test the search function for books."""
        mock_search.return_value = [{"title": "Test Book"}]

        result = services.search(
            MediaTypes.BOOK.value,
            "test",
            1,
            source=Sources.OPENLIBRARY.value,
        )

        self.assertEqual(result, [{"title": "Test Book"}])

        mock_search.assert_called_once_with("test", 1)

    @patch("app.providers.comicvine.search")
    def test_search_comic(self, mock_search):
        """Test the search function for comics."""
        mock_search.return_value = [{"title": "Test Comic"}]

        result = services.search(MediaTypes.COMIC.value, "test", 1)

        self.assertEqual(result, [{"title": "Test Comic"}])

        mock_search.assert_called_once_with("test", 1)
