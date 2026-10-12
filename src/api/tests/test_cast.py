from unittest.mock import patch

from django.core.cache import cache

from app.models import MediaTypes

from .base import YamtrackApiTestCase
from .helpers import check_pagination_structure

MOVIE_CREDITS_RESPONSE = {
    "cast": [
        {
            "id": 6384,
            "name": "Keanu Reeves",
            "character": "Neo",
            "profile_path": "/keanu.jpg",
            "order": 0,
        },
        {
            "id": 2975,
            "name": "Laurence Fishburne",
            "character": "",
            "profile_path": None,
            "order": 1,
        },
    ],
}

TV_AGGREGATE_CREDITS_RESPONSE = {
    "cast": [
        {
            "id": 17419,
            "name": "Bryan Cranston",
            "profile_path": "/bryan.jpg",
            "roles": [
                {"character": "Walter White", "episode_count": 62},
                {"character": "Heisenberg", "episode_count": 1},
            ],
            "total_episode_count": 62,
            "order": 0,
        },
    ],
}

PERSON_COMBINED_CREDITS_RESPONSE = {
    "cast": [
        {
            "id": 603,
            "media_type": "movie",
            "title": "The Matrix",
            "poster_path": "/matrix.jpg",
            "character": "Neo",
            "release_date": "1999-03-31",
            "genre_ids": [28, 878],
            "vote_count": 25000,
        },
        {
            "id": 1399,
            "media_type": "tv",
            "name": "Swedish Dicks",
            "poster_path": "/dicks.jpg",
            "character": "Tex",
            "first_air_date": "2016-08-09",
            "genre_ids": [35],
            "episode_count": 2,
            "vote_count": 50,
        },
        {
            "id": 1399,
            "media_type": "tv",
            "name": "Swedish Dicks",
            "poster_path": "/dicks.jpg",
            "character": "Himself",
            "first_air_date": "2016-08-09",
            "genre_ids": [35],
            "episode_count": 1,
            "vote_count": 50,
        },
        {
            "id": 22980,
            "media_type": "tv",
            "name": "The Tonight Show",
            "poster_path": "/tonight.jpg",
            "character": "Self - Guest",
            "first_air_date": "2014-02-17",
            "genre_ids": [10767, 35],
            "vote_count": 900,
        },
        {
            "id": 245891,
            "media_type": "movie",
            "title": "John Wick",
            "poster_path": None,
            "character": "John Wick",
            "release_date": "",
            "genre_ids": [28],
            "vote_count": 19000,
        },
    ],
}


class MediaCastTests(YamtrackApiTestCase):
    """Validate media cast endpoint contracts."""

    def setUp(self):
        """Clear cached provider data between tests."""
        super().setUp()
        cache.clear()

    @patch("app.providers.tmdb.services.api_request")
    def test_movie_cast_returns_paginated_cast(self, mock_request):
        """Cast endpoint should return paginated movie cast."""
        mock_request.return_value = MOVIE_CREDITS_RESPONSE

        response = self.call_api(
            "get",
            "api_media_cast",
            args=(MediaTypes.MOVIE.value, "tmdb", 603),
            params={"limit": 1},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_pagination_structure(self, payload["pagination"])
        self.assertEqual(payload["pagination"]["total"], 2)
        self.assertEqual(
            payload["results"],
            [
                {
                    "person_id": 6384,
                    "name": "Keanu Reeves",
                    "character": "Neo",
                    "image": "https://image.tmdb.org/t/p/w500/keanu.jpg",
                    "order": 0,
                    "episode_count": None,
                },
            ],
        )
        self.assertTrue(mock_request.call_args.args[2].endswith("/movie/603/credits"))

    @patch("app.providers.tmdb.services.api_request")
    def test_tv_cast_uses_aggregate_credits(self, mock_request):
        """Cast endpoint should merge roles of tv cast members."""
        mock_request.return_value = TV_AGGREGATE_CREDITS_RESPONSE

        response = self.call_api(
            "get",
            "api_media_cast",
            args=(MediaTypes.TV.value, "tmdb", 1396),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        member = response.json()["results"][0]
        self.assertEqual(member["character"], "Walter White / Heisenberg")
        self.assertEqual(member["episode_count"], 62)
        self.assertTrue(
            mock_request.call_args.args[2].endswith("/tv/1396/aggregate_credits"),
        )

    def test_cast_unsupported_media_type_returns_bad_request(self):
        """Cast endpoint should reject media types without cast."""
        requests = [
            ("invalid", "tmdb", 1),
            (MediaTypes.ANIME.value, "mal", 1),
            (MediaTypes.SEASON.value, "tmdb", 1),
            (MediaTypes.MOVIE.value, "manual", 1),
        ]

        for args in requests:
            response = self.call_api(
                "get",
                "api_media_cast",
                args=args,
                headers=self.auth_headers,
            )
            self.assertEqual(response.status_code, 400)

    @patch("api.views.tmdb.media_cast", side_effect=Exception("boom"))
    def test_cast_provider_error_returns_internal_server_error(self, _mock_cast):
        """Cast endpoint should surface provider lookup failures."""
        response = self.call_api(
            "get",
            "api_media_cast",
            args=(MediaTypes.MOVIE.value, "tmdb", 999999),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 500)


class PersonKnownForTests(YamtrackApiTestCase):
    """Validate person known for endpoint contracts."""

    def setUp(self):
        """Clear cached provider data between tests."""
        super().setUp()
        cache.clear()

    @patch("app.providers.tmdb.services.api_request")
    def test_known_for_returns_movies_and_tv(self, mock_request):
        """Known for should dedupe roles, skip talk shows and sort by votes."""
        mock_request.return_value = PERSON_COMBINED_CREDITS_RESPONSE

        response = self.call_api(
            "get",
            "api_person_known_for",
            args=("tmdb", 6384),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_pagination_structure(self, payload["pagination"])
        self.assertEqual(payload["pagination"]["total"], 3)
        self.assertEqual(
            [media["title"] for media in payload["results"]],
            ["The Matrix", "John Wick", "Swedish Dicks"],
        )
        self.assertEqual(
            payload["results"][0],
            {
                "source": "tmdb",
                "media_type": "movie",
                "media_id": "603",
                "title": "The Matrix",
                "image": "https://image.tmdb.org/t/p/w500/matrix.jpg",
                "character": "Neo",
                "release_date": "1999-03-31",
                "episode_count": None,
            },
        )
        self.assertIsNone(payload["results"][1]["release_date"])
        self.assertEqual(payload["results"][2]["character"], "Tex / Himself")
        self.assertEqual(payload["results"][2]["release_date"], "2016-08-09")
        self.assertTrue(
            mock_request.call_args.args[2].endswith("/person/6384/combined_credits"),
        )

    @patch("app.providers.tmdb.services.api_request")
    def test_known_for_filters_by_media_type(self, mock_request):
        """Known for should filter by the media_type query parameter."""
        mock_request.return_value = PERSON_COMBINED_CREDITS_RESPONSE

        for media_type, titles in (
            (MediaTypes.MOVIE.value, ["The Matrix", "John Wick"]),
            (MediaTypes.TV.value, ["Swedish Dicks"]),
        ):
            response = self.call_api(
                "get",
                "api_person_known_for",
                args=("tmdb", 6384),
                params={"media_type": media_type},
                headers=self.auth_headers,
            )

            self.assertEqual(response.status_code, 200)
            results = response.json()["results"]
            self.assertEqual([media["title"] for media in results], titles)
            for media in results:
                self.assertEqual(media["media_type"], media_type)

        # Provider data should be cached across filters
        mock_request.assert_called_once()

    def test_known_for_invalid_media_type_returns_bad_request(self):
        """Known for should reject unsupported media_type filters."""
        response = self.call_api(
            "get",
            "api_person_known_for",
            args=("tmdb", 6384),
            params={"media_type": "anime"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_known_for_unsupported_source_returns_bad_request(self):
        """Known for should reject sources without people data."""
        response = self.call_api(
            "get",
            "api_person_known_for",
            args=("mal", 6384),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    @patch("api.views.tmdb.person_known_for", side_effect=Exception("boom"))
    def test_known_for_provider_error_returns_internal_server_error(
        self,
        _mock_known_for,
    ):
        """Known for should surface provider lookup failures."""
        response = self.call_api(
            "get",
            "api_person_known_for",
            args=("tmdb", 999999),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 500)


PERSON_RESPONSE = {
    "id": 6384,
    "name": "Keanu Reeves",
    "profile_path": "/keanu.jpg",
    "biography": "Keanu Charles Reeves is a Canadian actor.",
    "birthday": "1964-09-02",
    "deathday": None,
    "place_of_birth": "Beirut, Lebanon",
    "known_for_department": "Acting",
    "also_known_as": ["Keanu Charles Reeves"],
    "homepage": "",
    "external_ids": {
        "imdb_id": "nm0000206",
        "wikidata_id": "Q43416",
        "instagram_id": None,
        "twitter_id": "",
    },
}


class PersonDetailTests(YamtrackApiTestCase):
    """Validate person detail endpoint contracts."""

    def setUp(self):
        """Clear cached provider data between tests."""
        super().setUp()
        cache.clear()

    @patch("app.providers.tmdb.services.api_request")
    def test_person_detail_returns_person(self, mock_request):
        """Person detail should return normalized person data."""
        mock_request.return_value = PERSON_RESPONSE

        response = self.call_api(
            "get",
            "api_person_detail",
            args=("tmdb", 6384),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "person_id": 6384,
                "source": "tmdb",
                "source_url": "https://www.themoviedb.org/person/6384",
                "name": "Keanu Reeves",
                "image": "https://image.tmdb.org/t/p/w500/keanu.jpg",
                "biography": "Keanu Charles Reeves is a Canadian actor.",
                "birthday": "1964-09-02",
                "deathday": None,
                "place_of_birth": "Beirut, Lebanon",
                "known_for_department": "Acting",
                "also_known_as": ["Keanu Charles Reeves"],
                "homepage": None,
                "external_links": {
                    "IMDb": "https://www.imdb.com/name/nm0000206/",
                    "Wikidata": "https://www.wikidata.org/wiki/Q43416",
                },
            },
        )
        self.assertTrue(mock_request.call_args.args[2].endswith("/person/6384"))

    def test_person_detail_unsupported_source_returns_bad_request(self):
        """Person detail should reject sources without people data."""
        response = self.call_api(
            "get",
            "api_person_detail",
            args=("mal", 6384),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    @patch("api.views.tmdb.person", side_effect=Exception("boom"))
    def test_person_detail_provider_error_returns_internal_server_error(
        self,
        _mock_person,
    ):
        """Person detail should surface provider lookup failures."""
        response = self.call_api(
            "get",
            "api_person_detail",
            args=("tmdb", 999999),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 500)


def build_people_search_page(page, total_results, count):
    """Build a fake TMDB person search response page."""
    return {
        "page": page,
        "total_results": total_results,
        "results": [
            {
                "id": (page - 1) * 20 + index,
                "name": f"Person {(page - 1) * 20 + index}",
                "profile_path": None,
                "known_for_department": "Acting",
                "known_for": [
                    {
                        "id": 603,
                        "media_type": "movie",
                        "title": "The Matrix",
                        "poster_path": "/matrix.jpg",
                    },
                    {
                        "id": 1399,
                        "media_type": "tv",
                        "name": "Game of Thrones",
                        "poster_path": "/got.jpg",
                    },
                ],
            }
            for index in range(count)
        ],
    }


class PeopleSearchTests(YamtrackApiTestCase):
    """Validate people search endpoint contracts."""

    def setUp(self):
        """Clear cached provider data between tests."""
        super().setUp()
        cache.clear()

    @patch("app.providers.tmdb.services.api_request")
    def test_people_search_returns_paginated_people(self, mock_request):
        """People search should return paginated people with known for media."""
        mock_request.return_value = build_people_search_page(1, 2, 2)

        response = self.call_api(
            "get",
            "api_people_search",
            params={"search": "keanu"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_pagination_structure(self, payload["pagination"])
        self.assertEqual(payload["pagination"]["total"], 2)
        person = payload["results"][0]
        self.assertEqual(person["person_id"], 0)
        self.assertEqual(person["source"], "tmdb")
        self.assertEqual(person["name"], "Person 0")
        self.assertEqual(person["known_for_department"], "Acting")
        self.assertEqual(
            person["known_for"],
            [
                {
                    "source": "tmdb",
                    "media_type": "movie",
                    "media_id": "603",
                    "title": "The Matrix",
                    "image": "https://image.tmdb.org/t/p/w500/matrix.jpg",
                },
                {
                    "source": "tmdb",
                    "media_type": "tv",
                    "media_id": "1399",
                    "title": "Game of Thrones",
                    "image": "https://image.tmdb.org/t/p/w500/got.jpg",
                },
            ],
        )
        self.assertTrue(mock_request.call_args.args[2].endswith("/search/person"))
        self.assertEqual(mock_request.call_args.kwargs["params"]["query"], "keanu")

    @patch("app.providers.tmdb.services.api_request")
    def test_people_search_fetches_next_provider_page(self, mock_request):
        """People search should fetch provider pages until the offset is covered."""
        mock_request.side_effect = [
            build_people_search_page(1, 25, 20),
            build_people_search_page(2, 25, 5),
        ]

        response = self.call_api(
            "get",
            "api_people_search",
            params={"search": "keanu", "limit": 5, "offset": 20},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["pagination"]["total"], 25)
        self.assertEqual(
            [person["person_id"] for person in payload["results"]],
            [20, 21, 22, 23, 24],
        )
        self.assertEqual(mock_request.call_count, 2)

    def test_people_search_without_query_returns_bad_request(self):
        """People search should require a search query."""
        response = self.call_api(
            "get",
            "api_people_search",
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_people_search_unsupported_source_returns_bad_request(self):
        """People search should reject sources without people data."""
        response = self.call_api(
            "get",
            "api_people_search",
            params={"search": "keanu", "source": "mal"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    @patch("api.views.tmdb.search_people", side_effect=Exception("boom"))
    def test_people_search_provider_error_returns_internal_server_error(
        self,
        _mock_search,
    ):
        """People search should surface provider lookup failures."""
        response = self.call_api(
            "get",
            "api_people_search",
            params={"search": "keanu"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 500)
