from unittest.mock import patch

from defusedxml import ElementTree
from django.core.cache import cache

from app.models import MediaTypes

from .base import YamtrackApiTestCase


def build_anilist_page(characters, staff, *, has_next_page=False):
    """Build a fake AniList credits response page."""
    return {
        "data": {
            "Media": {
                "characters": {
                    "pageInfo": {"hasNextPage": has_next_page},
                    "edges": characters,
                },
                "staff": {
                    "pageInfo": {"hasNextPage": False},
                    "edges": staff,
                },
            },
        },
    }


def build_anilist_node(node_id, name):
    """Build a fake AniList character or staff node."""
    return {
        "id": node_id,
        "name": {"full": name},
        "image": {"large": f"https://s4.anilist.co/{node_id}.jpg"},
        "siteUrl": f"https://anilist.co/node/{node_id}",
    }


ANILIST_FIRST_PAGE = build_anilist_page(
    [
        {
            "role": "MAIN",
            "node": build_anilist_node(1, "Spike Spiegel"),
            "voiceActors": [build_anilist_node(95011, "Kouichi Yamadera")],
        },
        {
            "role": "SUPPORTING",
            "node": build_anilist_node(2, "Ein"),
            "voiceActors": [],
        },
    ],
    [
        {"role": "Director", "node": build_anilist_node(100, "Shinichirou Watanabe")},
        {"role": "Storyboard", "node": build_anilist_node(100, "Shinichirou Watanabe")},
        {"role": "Music", "node": build_anilist_node(101, "Yoko Kanno")},
    ],
    has_next_page=True,
)

ANILIST_SECOND_PAGE = build_anilist_page(
    [
        {
            "role": "MAIN",
            "node": build_anilist_node(3, "Faye Valentine"),
            "voiceActors": [build_anilist_node(95012, "Megumi Hayashibara")],
        },
    ],
    [],
)


class AnimeMangaExtrasTests(YamtrackApiTestCase):
    """Validate anime and manga cast, crew, creators, characters and images."""

    def setUp(self):
        """Clear cached provider data between tests."""
        super().setUp()
        cache.clear()

    @patch("app.providers.anilist.services.api_request")
    def test_anime_cast_returns_voice_actors(self, mock_request):
        """Anime cast should list voice actors with their characters."""
        mock_request.side_effect = [ANILIST_FIRST_PAGE, ANILIST_SECOND_PAGE]

        response = self.call_api(
            "get",
            "api_media_cast",
            args=(MediaTypes.ANIME.value, "mal", "1"),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(
            results[0],
            {
                "person_id": 95011,
                "name": "Kouichi Yamadera",
                "character": "Spike Spiegel",
                "image": "https://s4.anilist.co/95011.jpg",
                "order": 0,
                "episode_count": None,
            },
        )
        self.assertEqual(
            [member["character"] for member in results],
            ["Spike Spiegel", "Faye Valentine"],
        )
        self.assertEqual(mock_request.call_count, 2)
        variables = mock_request.call_args_list[0].kwargs["params"]["variables"]
        self.assertEqual(variables, {"idMal": 1, "type": "ANIME", "page": 1})

    @patch("app.providers.anilist.services.api_request")
    def test_anime_crew_merges_staff_roles(self, mock_request):
        """Anime crew should list staff with their merged roles."""
        mock_request.side_effect = [ANILIST_FIRST_PAGE, ANILIST_SECOND_PAGE]

        response = self.call_api(
            "get",
            "api_media_crew",
            args=(MediaTypes.ANIME.value, "mal", "1"),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(results[0]["name"], "Shinichirou Watanabe")
        self.assertEqual(results[0]["jobs"], ["Director", "Storyboard"])
        self.assertIsNone(results[0]["department"])
        self.assertEqual(len(results), 2)

    @patch("app.providers.anilist.services.api_request")
    def test_anime_characters_share_cached_credits(self, mock_request):
        """Anime characters should reuse the cast provider requests."""
        mock_request.side_effect = [ANILIST_FIRST_PAGE, ANILIST_SECOND_PAGE]

        self.call_api(
            "get",
            "api_media_cast",
            args=(MediaTypes.ANIME.value, "mal", "1"),
            headers=self.auth_headers,
        )
        response = self.call_api(
            "get",
            "api_media_characters",
            args=(MediaTypes.ANIME.value, "mal", "1"),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(
            results[1],
            {
                "id": "2",
                "name": "Ein",
                "roles": ["Supporting"],
                "image": "https://s4.anilist.co/2.jpg",
                "issue_count": None,
                "source_url": "https://anilist.co/node/2",
            },
        )
        self.assertEqual(len(results), 3)
        self.assertEqual(mock_request.call_count, 2)

    @patch("app.providers.anilist.services.api_request")
    def test_manga_creators_use_anilist_staff(self, mock_request):
        """MAL manga creators should come from AniList staff."""
        mock_request.return_value = build_anilist_page(
            [],
            [{"role": "Story & Art", "node": build_anilist_node(7, "Kentarou Miura")}],
        )

        response = self.call_api(
            "get",
            "api_media_creators",
            args=(MediaTypes.MANGA.value, "mal", "2"),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["results"],
            [
                {
                    "id": "7",
                    "name": "Kentarou Miura",
                    "roles": ["Story & Art"],
                    "image": "https://s4.anilist.co/7.jpg",
                    "issue_count": None,
                    "source_url": "https://anilist.co/node/7",
                },
            ],
        )
        variables = mock_request.call_args.kwargs["params"]["variables"]
        self.assertEqual(variables["type"], "MANGA")

    @patch(
        "app.providers.anilist.services.api_request",
        return_value={"data": {"Media": None}},
    )
    def test_anilist_missing_media_returns_internal_server_error(self, _mock):
        """Credits of media missing on AniList should surface a provider error."""
        response = self.call_api(
            "get",
            "api_media_characters",
            args=(MediaTypes.ANIME.value, "mal", "999999"),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 500)

    @patch("app.providers.mal.services.api_request")
    def test_mal_images_returns_unique_pictures(self, mock_request):
        """MAL images should return the main picture and pictures once each."""
        mock_request.return_value = {
            "main_picture": {
                "medium": "https://cdn/1m.jpg",
                "large": "https://cdn/1l.jpg",
            },
            "pictures": [
                {"medium": "https://cdn/1m.jpg", "large": "https://cdn/1l.jpg"},
                {"medium": "https://cdn/2m.jpg", "large": "https://cdn/2l.jpg"},
            ],
        }

        response = self.call_api(
            "get",
            "api_media_images",
            args=(MediaTypes.ANIME.value, "mal", "1"),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        posters = response.json()["posters"]
        self.assertEqual(
            [(poster["image"], poster["original"]) for poster in posters],
            [
                ("https://cdn/1m.jpg", "https://cdn/1l.jpg"),
                ("https://cdn/2m.jpg", "https://cdn/2l.jpg"),
            ],
        )
        self.assertTrue(mock_request.call_args.args[2].endswith("/anime/1"))


MANGAUPDATES_SERIES_RESPONSE = {
    "image": {
        "url": {
            "original": "https://cdn.mangaupdates.com/image/i1.jpg",
            "thumb": "https://cdn.mangaupdates.com/image/thumb/i1.jpg",
        },
        "width": 300,
        "height": 450,
    },
    "authors": [
        {"name": "MIURA Kentarou", "author_id": 10, "type": "Author"},
        {"name": "MIURA Kentarou", "author_id": 10, "type": "Artist"},
        {"name": "Studio Gaga", "author_id": None, "type": "Artist"},
    ],
}


class MangaUpdatesExtrasTests(YamtrackApiTestCase):
    """Validate MangaUpdates images and creators."""

    def setUp(self):
        """Clear cached provider data between tests."""
        super().setUp()
        cache.clear()

    @patch("app.providers.mangaupdates.services.api_request")
    def test_mangaupdates_creators_and_images(self, mock_request):
        """MangaUpdates should merge author roles and return the cover."""
        mock_request.return_value = MANGAUPDATES_SERIES_RESPONSE

        creators = self.call_api(
            "get",
            "api_media_creators",
            args=(MediaTypes.MANGA.value, "mangaupdates", "123"),
            headers=self.auth_headers,
        )
        images = self.call_api(
            "get",
            "api_media_images",
            args=(MediaTypes.MANGA.value, "mangaupdates", "123"),
            headers=self.auth_headers,
        )

        self.assertEqual(creators.status_code, 200)
        self.assertEqual(
            creators.json()["results"][0],
            {
                "id": "10",
                "name": "MIURA Kentarou",
                "roles": ["Author", "Artist"],
                "image": None,
                "issue_count": None,
                "source_url": "https://www.mangaupdates.com/author/10",
            },
        )
        self.assertIsNone(creators.json()["results"][1]["id"])
        self.assertEqual(images.status_code, 200)
        poster = images.json()["posters"][0]
        self.assertEqual(
            poster["original"], "https://cdn.mangaupdates.com/image/i1.jpg"
        )
        self.assertEqual(poster["aspect_ratio"], 0.667)
        mock_request.assert_called_once()

    def test_mangaupdates_characters_returns_bad_request(self):
        """MangaUpdates has no characters."""
        response = self.call_api(
            "get",
            "api_media_characters",
            args=(MediaTypes.MANGA.value, "mangaupdates", "123"),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)


class BookExtrasTests(YamtrackApiTestCase):
    """Validate Open Library and Hardcover images and creators."""

    def setUp(self):
        """Clear cached provider data between tests."""
        super().setUp()
        cache.clear()

    @patch("app.providers.openlibrary.services.api_request")
    def test_openlibrary_creators_and_images(self, mock_request):
        """Open Library should return edition and work covers and the authors."""
        mock_request.side_effect = [
            {"covers": [111, -1], "works": [{"key": "/works/OL1W"}]},
            {
                "covers": [111, 222],
                "authors": [{"author": {"key": "/authors/OL9A"}}],
            },
            {"name": "Frank Herbert", "photos": [5]},
        ]

        creators = self.call_api(
            "get",
            "api_media_creators",
            args=(MediaTypes.BOOK.value, "openlibrary", "OL5M"),
            headers=self.auth_headers,
        )
        images = self.call_api(
            "get",
            "api_media_images",
            args=(MediaTypes.BOOK.value, "openlibrary", "OL5M"),
            headers=self.auth_headers,
        )

        self.assertEqual(creators.status_code, 200)
        self.assertEqual(
            creators.json()["results"],
            [
                {
                    "id": "OL9A",
                    "name": "Frank Herbert",
                    "roles": ["Author"],
                    "image": "https://covers.openlibrary.org/a/olid/OL9A-L.jpg",
                    "issue_count": None,
                    "source_url": "https://openlibrary.org/authors/OL9A",
                },
            ],
        )
        self.assertEqual(images.status_code, 200)
        self.assertEqual(
            [poster["original"] for poster in images.json()["posters"]],
            [
                "https://covers.openlibrary.org/b/id/111-L.jpg",
                "https://covers.openlibrary.org/b/id/222-L.jpg",
            ],
        )
        self.assertEqual(mock_request.call_count, 3)

    @patch("app.providers.hardcover.services.api_request")
    def test_hardcover_creators_and_images(self, mock_request):
        """Hardcover should merge contributor roles and return the cover."""
        mock_request.return_value = {
            "data": {
                "books_by_pk": {
                    "cached_image": {
                        "url": "https://assets.hardcover.app/dune.jpg",
                        "width": 400,
                        "height": 600,
                    },
                    "cached_contributors": [
                        {
                            "author": {
                                "id": 5,
                                "name": "Frank Herbert",
                                "slug": "frank-herbert",
                                "image": {"url": "https://assets.hardcover.app/fh.jpg"},
                            },
                            "contribution": None,
                        },
                        {
                            "author": {
                                "id": 6,
                                "name": "Scott Brick",
                                "slug": "scott-brick",
                            },
                            "contribution": "Narrator",
                        },
                    ],
                },
            },
        }

        creators = self.call_api(
            "get",
            "api_media_creators",
            args=(MediaTypes.BOOK.value, "hardcover", "77"),
            headers=self.auth_headers,
        )
        images = self.call_api(
            "get",
            "api_media_images",
            args=(MediaTypes.BOOK.value, "hardcover", "77"),
            headers=self.auth_headers,
        )

        self.assertEqual(creators.status_code, 200)
        results = creators.json()["results"]
        self.assertEqual(results[0]["roles"], ["Author"])
        self.assertEqual(results[0]["image"], "https://assets.hardcover.app/fh.jpg")
        self.assertEqual(
            results[0]["source_url"],
            "https://hardcover.app/authors/frank-herbert",
        )
        self.assertEqual(results[1]["roles"], ["Narrator"])
        self.assertEqual(images.status_code, 200)
        self.assertEqual(
            images.json()["posters"][0]["original"],
            "https://assets.hardcover.app/dune.jpg",
        )
        mock_request.assert_called_once()

    @patch(
        "app.providers.hardcover.services.api_request",
        return_value={"data": {"books_by_pk": None}},
    )
    def test_hardcover_missing_book_returns_internal_server_error(self, _mock):
        """Missing Hardcover books should surface a provider error."""
        response = self.call_api(
            "get",
            "api_media_creators",
            args=(MediaTypes.BOOK.value, "hardcover", "999999"),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 500)


BGG_THING_RESPONSE = """
<items>
  <item type="boardgame" id="13">
    <thumbnail>https://cf.geekdo-images.com/thumb.jpg</thumbnail>
    <image>https://cf.geekdo-images.com/original.jpg</image>
    <link type="boardgamedesigner" id="11" value="Klaus Teuber" />
    <link type="boardgameartist" id="11" value="Klaus Teuber" />
    <link type="boardgameartist" id="12" value="Michael Menzel" />
    <link type="boardgamepublisher" id="37" value="KOSMOS" />
    <link type="boardgamepublisher" id="3" value="Catan Studio" />
  </item>
</items>
"""


class BoardGameExtrasTests(YamtrackApiTestCase):
    """Validate BGG images, creators and companies."""

    def setUp(self):
        """Clear cached provider data between tests."""
        super().setUp()
        cache.clear()

    @patch("app.providers.bgg.services.api_request")
    def test_bgg_creators_companies_and_images(self, mock_request):
        """BGG should return designers and artists, publishers and the image."""
        mock_request.return_value = ElementTree.fromstring(BGG_THING_RESPONSE)

        creators = self.call_api(
            "get",
            "api_media_creators",
            args=(MediaTypes.BOARDGAME.value, "bgg", "13"),
            headers=self.auth_headers,
        )
        companies = self.call_api(
            "get",
            "api_media_companies",
            args=(MediaTypes.BOARDGAME.value, "bgg", "13"),
            params={"role": "publisher"},
            headers=self.auth_headers,
        )
        images = self.call_api(
            "get",
            "api_media_images",
            args=(MediaTypes.BOARDGAME.value, "bgg", "13"),
            headers=self.auth_headers,
        )

        self.assertEqual(creators.status_code, 200)
        self.assertEqual(
            creators.json()["results"],
            [
                {
                    "id": "11",
                    "name": "Klaus Teuber",
                    "roles": ["Designer", "Artist"],
                    "image": None,
                    "issue_count": None,
                    "source_url": "https://boardgamegeek.com/boardgamedesigner/11",
                },
                {
                    "id": "12",
                    "name": "Michael Menzel",
                    "roles": ["Artist"],
                    "image": None,
                    "issue_count": None,
                    "source_url": "https://boardgamegeek.com/boardgameartist/12",
                },
            ],
        )
        self.assertEqual(companies.status_code, 200)
        self.assertEqual(
            [company["name"] for company in companies.json()["results"]],
            ["KOSMOS", "Catan Studio"],
        )
        self.assertEqual(images.status_code, 200)
        self.assertEqual(
            images.json()["posters"][0],
            {
                "image": "https://cf.geekdo-images.com/thumb.jpg",
                "original": "https://cf.geekdo-images.com/original.jpg",
                "width": None,
                "height": None,
                "aspect_ratio": None,
                "language": None,
                "vote_average": None,
                "vote_count": None,
            },
        )
        mock_request.assert_called_once()


class UnsupportedExtrasTests(YamtrackApiTestCase):
    """Validate that unsupported media types and sources are rejected."""

    def test_unsupported_combinations_return_bad_request(self):
        """Endpoints should reject media types and sources without that data."""
        requests = [
            ("api_media_cast", (MediaTypes.MANGA.value, "mal", 1)),
            ("api_media_crew", (MediaTypes.GAME.value, "igdb", 1)),
            ("api_media_creators", (MediaTypes.ANIME.value, "mal", 1)),
            ("api_media_creators", (MediaTypes.BOOK.value, "mal", 1)),
            ("api_media_characters", (MediaTypes.BOOK.value, "openlibrary", 1)),
            ("api_media_companies", (MediaTypes.COMIC.value, "comicvine", 1)),
            ("api_media_images", (MediaTypes.ANIME.value, "manual", 1)),
        ]

        for url_name, args in requests:
            response = self.call_api(
                "get",
                url_name,
                args=args,
                headers=self.auth_headers,
            )
            self.assertEqual(response.status_code, 400, (url_name, args))
