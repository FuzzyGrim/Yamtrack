from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import override_settings

from app.models import Item, MediaTypes, Sources
from app.providers import mangadex

from .base import YamtrackApiTestCase

TMDB_IMAGES = {
    "posters": [
        {
            "image": "https://image.tmdb.org/t/p/w500/tmdb.jpg",
            "original": "https://image.tmdb.org/t/p/original/tmdb.jpg",
        },
    ],
    "backdrops": [],
    "logos": [],
}

FANART_MOVIE_RESPONSE = {
    "movieposter": [
        {
            "id": "1",
            "url": "https://assets.fanart.tv/fanart/movies/603/movieposter/a.jpg",
            "lang": "en",
            "likes": "3",
        },
        {
            "id": "2",
            "url": "https://assets.fanart.tv/fanart/movies/603/movieposter/b.jpg",
            "lang": "00",
            "likes": "9",
        },
    ],
    "moviebackground": [
        {
            "id": "3",
            "url": "https://assets.fanart.tv/fanart/movies/603/moviebackground/c.jpg",
            "lang": "",
            "likes": "1",
        },
    ],
    "hdmovielogo": [
        {
            "id": "4",
            "url": "https://assets.fanart.tv/fanart/movies/603/hdmovielogo/d.png",
            "lang": "en",
            "likes": "2",
        },
    ],
}

FANART_TV_RESPONSE = {
    "tvposter": [
        {
            "id": "5",
            "url": "https://assets.fanart.tv/fanart/tv/81189/tvposter/e.jpg",
            "lang": "en",
            "likes": "4",
        },
    ],
    "seasonposter": [
        {
            "id": "6",
            "url": "https://assets.fanart.tv/fanart/tv/81189/seasonposter/s1.jpg",
            "lang": "en",
            "likes": "1",
            "season": "1",
        },
        {
            "id": "7",
            "url": "https://assets.fanart.tv/fanart/tv/81189/seasonposter/s2.jpg",
            "lang": "en",
            "likes": "1",
            "season": "2",
        },
    ],
}


def not_found_error():
    """Return an HTTP 404 error like the one raised by requests."""
    response = requests.Response()
    response.status_code = 404
    return requests.exceptions.HTTPError(response=response)


@patch("api.helpers.tmdb.media_images", return_value=TMDB_IMAGES)
class FanartImagesTests(YamtrackApiTestCase):
    """Validate the Fanart.tv extra artwork."""

    def setUp(self):
        """Clear cached provider data between tests."""
        super().setUp()
        cache.clear()

    def get_images(self, media_type, media_id, params=None):
        """Call the media images endpoint."""
        return self.call_api(
            "get",
            "api_media_images",
            args=(media_type, "tmdb", media_id),
            params=params,
            headers=self.auth_headers,
        )

    @override_settings(FANART_API="key")
    @patch("app.providers.fanart.services.api_request")
    def test_movie_images_include_fanart(self, mock_request, _mock_tmdb):
        """Movie images should add Fanart.tv posters, backgrounds and logos."""
        mock_request.return_value = FANART_MOVIE_RESPONSE

        response = self.get_images(MediaTypes.MOVIE.value, "603")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [(poster["source"], poster["vote_count"]) for poster in payload["posters"]],
            [("tmdb", None), ("fanart", 9), ("fanart", 3)],
        )
        fanart_poster = payload["posters"][1]
        self.assertEqual(
            fanart_poster["image"],
            "https://assets.fanart.tv/preview/movies/603/movieposter/b.jpg",
        )
        self.assertEqual(
            fanart_poster["original"],
            "https://assets.fanart.tv/fanart/movies/603/movieposter/b.jpg",
        )
        self.assertIsNone(fanart_poster["language"])
        self.assertEqual(payload["posters"][2]["language"], "en")
        self.assertEqual(len(payload["backdrops"]), 1)
        self.assertEqual(payload["logos"][0]["source"], "fanart")
        self.assertTrue(mock_request.call_args.args[2].endswith("/movies/603"))
        self.assertEqual(mock_request.call_args.kwargs["params"]["api_key"], "key")

    @override_settings(FANART_API="")
    @patch("app.providers.fanart.services.api_request")
    def test_fanart_skipped_without_api_key(self, mock_request, _mock_tmdb):
        """Without an API key, Fanart.tv should not be queried."""
        response = self.get_images(MediaTypes.MOVIE.value, "603")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [poster["source"] for poster in response.json()["posters"]],
            ["tmdb"],
        )
        mock_request.assert_not_called()

    @override_settings(FANART_API="key")
    @patch("api.helpers.tmdb.tv", return_value={"tvdb_id": 81189})
    @patch("app.providers.fanart.services.api_request")
    def test_tv_images_use_tvdb_id(self, mock_request, _mock_tv, _mock_tmdb):
        """TV images should query Fanart.tv with the TVDB ID of the show."""
        mock_request.return_value = FANART_TV_RESPONSE

        response = self.get_images(MediaTypes.TV.value, "1396", {"type": "posters"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [poster["source"] for poster in response.json()["posters"]],
            ["tmdb", "fanart"],
        )
        self.assertTrue(mock_request.call_args.args[2].endswith("/tv/81189"))

    @override_settings(FANART_API="key")
    @patch("app.providers.fanart.services.api_request", side_effect=not_found_error())
    def test_fanart_not_found_returns_provider_images(self, _mock_request, _mock):
        """Media without Fanart.tv artwork should only return the source images."""
        response = self.get_images(MediaTypes.MOVIE.value, "603")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["posters"]), 1)

    @override_settings(FANART_API="key")
    @patch("app.providers.fanart.services.api_request", side_effect=Exception("boom"))
    def test_failing_extra_source_does_not_break_images(self, _mock_request, _mock):
        """A failing extra artwork source should be skipped."""
        response = self.get_images(MediaTypes.MOVIE.value, "603")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["posters"]), 1)

    @override_settings(FANART_API="key")
    @patch("api.views.services.get_media_metadata")
    @patch("app.providers.fanart.services.api_request")
    def test_fanart_image_can_be_set_as_cover(
        self,
        mock_request,
        mock_metadata,
        _mock_tmdb,
    ):
        """A Fanart.tv poster should be accepted as a custom cover."""
        mock_request.return_value = FANART_MOVIE_RESPONSE
        mock_metadata.return_value = {
            "media_id": "701",
            "source": "tmdb",
            "media_type": "movie",
            "title": "Movie 1",
            "image": "https://image.tmdb.org/t/p/w500/tmdb.jpg",
        }
        cover = "https://assets.fanart.tv/fanart/movies/603/movieposter/a.jpg"

        response = self.call_api(
            "put",
            "api_media_cover",
            args=(MediaTypes.MOVIE.value, "tmdb", "701"),
            payload={"image": cover},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        item = Item.objects.get(
            media_id="701",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
        )
        self.assertEqual(item.custom_image, cover)


class FanartSeasonImagesTests(YamtrackApiTestCase):
    """Validate the Fanart.tv season posters."""

    def setUp(self):
        """Clear cached provider data between tests."""
        super().setUp()
        cache.clear()

    @override_settings(FANART_API="key")
    @patch("api.helpers.tmdb.tv", return_value={"tvdb_id": 81189})
    @patch("api.helpers.tmdb.media_images", return_value={"posters": []})
    @patch("app.providers.fanart.services.api_request")
    def test_season_images_include_matching_fanart_posters(
        self,
        mock_request,
        _mock_tmdb,
        _mock_tv,
    ):
        """Season images should add the Fanart.tv posters of that season only."""
        mock_request.return_value = FANART_TV_RESPONSE

        response = self.call_api(
            "get",
            "api_media_season_images",
            args=(MediaTypes.TV.value, "tmdb", "1396", 1),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [poster["original"] for poster in response.json()["posters"]],
            ["https://assets.fanart.tv/fanart/tv/81189/seasonposter/s1.jpg"],
        )


MANGADEX_SEARCH_RESPONSE = {
    "data": [
        {
            "id": "other-id",
            "attributes": {"links": {"mal": "999"}},
        },
        {
            "id": "berserk-id",
            "attributes": {"links": {"mal": "2", "mu": "pb8uwds"}},
        },
    ],
}

MANGADEX_COVERS_RESPONSE = {
    "data": [
        {"attributes": {"fileName": "v1.jpg", "volume": "1", "locale": "ja"}},
        {"attributes": {"fileName": "v2.png", "volume": "2", "locale": "en"}},
    ],
}


class AnimeMangaExtraImagesTests(YamtrackApiTestCase):
    """Validate the AniList and MangaDex extra artwork."""

    def setUp(self):
        """Clear cached provider data between tests."""
        super().setUp()
        cache.clear()

    @patch("api.helpers.mal.media_pictures", return_value={"posters": []})
    @patch("app.providers.anilist.services.api_request")
    def test_anime_images_include_anilist_cover_and_banner(
        self,
        mock_request,
        _mock_mal,
    ):
        """Anime images should add the AniList cover and banner."""
        mock_request.return_value = {
            "data": {
                "Media": {
                    "coverImage": {
                        "extraLarge": "https://s4.anilist.co/cover/xl.jpg",
                        "large": "https://s4.anilist.co/cover/l.jpg",
                    },
                    "bannerImage": "https://s4.anilist.co/banner/b.jpg",
                },
            },
        }

        response = self.call_api(
            "get",
            "api_media_images",
            args=(MediaTypes.ANIME.value, "mal", "1"),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            (payload["posters"][0]["image"], payload["posters"][0]["original"]),
            ("https://s4.anilist.co/cover/l.jpg", "https://s4.anilist.co/cover/xl.jpg"),
        )
        self.assertEqual(payload["posters"][0]["source"], "anilist")
        self.assertEqual(
            payload["backdrops"][0]["original"],
            "https://s4.anilist.co/banner/b.jpg",
        )

    @patch("api.helpers.anilist.media_images", return_value={})
    @patch("api.helpers.mal.manga", return_value={"title": "Berserk"})
    @patch("api.helpers.mal.media_pictures", return_value={"posters": []})
    @patch("app.providers.mangadex.services.api_request")
    def test_manga_images_include_mangadex_volume_covers(
        self,
        mock_request,
        _mock_mal,
        _mock_manga,
        _mock_anilist,
    ):
        """MAL manga images should add the MangaDex covers of the matching manga."""
        mock_request.side_effect = [MANGADEX_SEARCH_RESPONSE, MANGADEX_COVERS_RESPONSE]

        response = self.call_api(
            "get",
            "api_media_images",
            args=(MediaTypes.MANGA.value, "mal", "2"),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        posters = response.json()["posters"]
        self.assertEqual(
            [(poster["original"], poster["language"]) for poster in posters],
            [
                ("https://uploads.mangadex.org/covers/berserk-id/v1.jpg", "ja"),
                ("https://uploads.mangadex.org/covers/berserk-id/v2.png", "en"),
            ],
        )
        self.assertEqual(
            posters[0]["image"],
            "https://uploads.mangadex.org/covers/berserk-id/v1.jpg.512.jpg",
        )
        self.assertEqual({poster["source"] for poster in posters}, {"mangadex"})
        search_call, covers_call = mock_request.call_args_list
        self.assertEqual(search_call.kwargs["params"]["title"], "Berserk")
        self.assertEqual(covers_call.kwargs["params"]["manga[]"], ["berserk-id"])

    @patch("app.providers.mangadex.services.api_request")
    def test_mangadex_without_match_returns_no_covers(self, mock_request):
        """MangaDex covers should be empty when no manga matches the ID."""
        mock_request.return_value = MANGADEX_SEARCH_RESPONSE

        covers = mangadex.manga_covers("Unknown", Sources.MAL.value, "12345")

        self.assertEqual(covers, {"posters": []})
        mock_request.assert_called_once()

    def test_mangadex_matches_base36_mangaupdates_ids(self):
        """MangaDex MangaUpdates links should match numeric MangaUpdates IDs."""
        self.assertTrue(mangadex.is_same_mangaupdates_id("pb8uwds", 55099564912))
        self.assertTrue(mangadex.is_same_mangaupdates_id("12345", "12345"))
        self.assertFalse(mangadex.is_same_mangaupdates_id("pb8uwds", 1))
        self.assertFalse(mangadex.is_same_mangaupdates_id(None, 1))
