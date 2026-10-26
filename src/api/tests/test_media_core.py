from unittest.mock import patch
from uuid import UUID

from django.utils import timezone

from app.models import TV, Episode, Item, MediaTypes, Movie, Sources, Status

from .base import YamtrackApiTestCase
from .helpers import (
    WATCH_PROVIDERS_RESPONSE,
    check_changes_history_record_structure,
    check_complete_media_structure,
    check_consumption_structure,
    check_media_structure,
    check_minimized_lists_structure,
    check_pagination_structure,
)


class MediaCoreTests(YamtrackApiTestCase):
    """Validate media endpoint behavior for core media types."""

    def setUp(self):
        """Set up."""
        super().setUp()

    def test_media_list_get_returns_paginated_payload(self):
        """Media list endpoint should return standard pagination payload."""
        response = self.call_api("get", "api_media_list", headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        check_pagination_structure(
            self,
            payload["pagination"],
            total=sum(
                len(items)
                for media_type, items in self.items_by_type.items()
                if media_type not in {MediaTypes.SEASON.value, MediaTypes.EPISODE.value}
            ),
            limit=20,
            offset=0,
        )
        self.assertIn("results", payload)
        for item in payload["results"]:
            check_media_structure(self, item)

    def test_media_list_get_with_type_filter_returns_filtered_results(self):
        """Media list endpoint should filter results by media type."""
        response = self.call_api(
            "get",
            "api_media_list",
            params={"media_type": MediaTypes.TV.value},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        self.assertIn("results", payload)
        check_pagination_structure(
            self,
            payload["pagination"],
            total=len(self.tv_medias),
            limit=20,
            offset=0,
        )
        for item in payload["results"]:
            check_media_structure(self, item)
            self.assertEqual(item["item"]["media_type"], MediaTypes.TV.value)

    def test_media_list_get_with_status_filter_returns_filtered_results(self):
        """Media list endpoint should filter results by status."""
        completed_movie = self.movie_medias[0]
        completed_movie.status = Status.COMPLETED.value
        completed_movie.save(update_fields=["status"])

        response = self.call_api(
            "get",
            "api_media_list",
            params={
                "media_type": MediaTypes.MOVIE.value,
                "status": "3",
            },
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("results", payload)
        self.assertGreaterEqual(len(payload["results"]), 1)
        for item in payload["results"]:
            check_media_structure(self, item)
            self.assertEqual(item["item"]["media_type"], MediaTypes.MOVIE.value)
            self.assertEqual(item["status"], 3)

    def test_media_list_get_with_search_filter_returns_filtered_results(self):
        """Media list endpoint should filter results by search query."""
        response = self.call_api(
            "get",
            "api_media_list",
            params={
                "media_type": MediaTypes.MOVIE.value,
                "search": "Movie 1",
            },
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("results", payload)
        self.assertGreaterEqual(len(payload["results"]), 1)
        for item in payload["results"]:
            check_media_structure(self, item)
            self.assertIn("movie 1", item["item"]["title"].lower())

    def test_media_list_get_with_sort_filter_returns_sorted_results(self):
        """Media list endpoint should sort results when requested."""
        response = self.call_api(
            "get",
            "api_media_list",
            params={"media_type": MediaTypes.MOVIE.value, "sort": "title_desc"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        titles = [item["item"]["title"] for item in payload["results"]]
        self.assertEqual(titles, sorted(titles, reverse=True))

    def test_media_list_get_with_exclude_filter_excludes_type(self):
        """Media list endpoint should exclude requested media types."""
        response = self.call_api(
            "get",
            "api_media_list",
            params={"exclude": MediaTypes.MOVIE.value},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        for item in payload["results"]:
            check_media_structure(self, item)
            self.assertNotEqual(item["item"]["media_type"], MediaTypes.MOVIE.value)

    def test_media_list_get_invalid_status_returns_not_found(self):
        """Media list endpoint should reject unsupported status values."""
        response = self.call_api(
            "get",
            "api_media_list",
            params={"status": "abc"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_media_list_get_invalid_sort_returns_not_found(self):
        """Media list endpoint should reject unsupported sort values."""
        response = self.call_api(
            "get",
            "api_media_list",
            params={"sort": "unknown_desc"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_media_type_list_invalid_type_returns_bad_request(self):
        """Media-type list endpoint should reject unsupported media types."""
        response = self.call_api(
            "get",
            "api_media_type_list",
            args=("invalid",),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_media_type_list_get_returns_paginated_payload(self):
        """Media-type list endpoint should return standard pagination payload."""
        response = self.call_api(
            "get",
            "api_media_type_list",
            args=(MediaTypes.MOVIE.value,),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        check_pagination_structure(
            self,
            payload["pagination"],
            total=len(self.movie_medias),
            limit=20,
            offset=0,
        )
        self.assertIn("results", payload)
        for item in payload["results"]:
            check_media_structure(self, item)

    def test_media_type_list_get_with_search_filter_returns_filtered_results(self):
        """Media-type list endpoint should filter by search query."""
        response = self.call_api(
            "get",
            "api_media_type_list",
            args=(MediaTypes.MOVIE.value,),
            params={"search": "Movie 1"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("results", payload)
        self.assertGreaterEqual(len(payload["results"]), 1)
        for item in payload["results"]:
            check_media_structure(self, item)
            self.assertIn("movie 1", item["item"]["title"].lower())

    def test_media_type_list_get_with_sort_filter_returns_sorted_results(self):
        """Media-type list endpoint should sort by requested field."""
        response = self.call_api(
            "get",
            "api_media_type_list",
            args=(MediaTypes.MOVIE.value,),
            params={"sort": "title_desc"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        titles = [item["item"]["title"] for item in payload["results"]]
        self.assertEqual(titles, sorted(titles, reverse=True))

    def test_media_type_list_get_with_status_filter_returns_filtered_results(self):
        """Media-type list endpoint should filter by status."""
        completed_movie = self.movie_medias[0]
        completed_movie.status = Status.COMPLETED.value
        completed_movie.save(update_fields=["status"])

        response = self.call_api(
            "get",
            "api_media_type_list",
            args=(MediaTypes.MOVIE.value,),
            params={"status": "3"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("results", payload)
        self.assertGreaterEqual(len(payload["results"]), 1)
        for item in payload["results"]:
            check_media_structure(self, item)
            self.assertEqual(item["status"], 3)

    def create_unwatched_tv(self):
        """Track a new tv show without any watched episode."""
        item = Item.objects.create(
            media_id="9999",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Unwatched Show",
            image="https://example.com/unwatched.jpg",
        )
        TV.objects.create(item=item, user=self.user1, status=Status.PLANNING.value)
        return item

    def test_updated_desc_keeps_unwatched_media_last(self):
        """Media never progressed should come last when sorting by updated."""
        unwatched = self.create_unwatched_tv()
        # Watch the episodes of one tracked show, the others have none watched
        Episode.objects.filter(related_season__user=self.user1).update(
            end_date=timezone.now(),
        )

        for url_name, args in (
            ("api_media_list", ()),
            ("api_media_type_list", (MediaTypes.TV.value,)),
        ):
            for sort in ("updated_desc", "updated_asc"):
                response = self.call_api(
                    "get",
                    url_name,
                    args=args,
                    params={"sort": sort, "limit": 200},
                    headers=self.auth_headers,
                )

                self.assertEqual(response.status_code, 200, (url_name, sort))
                results = response.json()["results"]
                dates = [media["progressed_at"] for media in results]
                # Media with a date come first, in the requested order
                dated = [date for date in dates if date is not None]
                self.assertTrue(dated, (url_name, sort))
                self.assertEqual(dates[: len(dated)], dated, (url_name, sort))
                self.assertEqual(
                    dated,
                    sorted(dated, reverse=sort.endswith("desc")),
                    (url_name, sort),
                )
                media_ids = [media["item"]["media_id"] for media in results]
                self.assertGreaterEqual(
                    media_ids.index(unwatched.media_id),
                    len(dated),
                    (url_name, sort),
                )

    def test_media_type_list_post_creates_media(self):
        """Media-type create endpoint should create new media items."""
        response = self.call_api(
            "post",
            "api_media_type_list",
            args=(MediaTypes.MOVIE.value,),
            payload={
                "source": "manual",
                "title": "Manual Movie",
                "image": "https://example.com/poster.jpg",
                "status": 3,
            },
            headers=self.auth_headers,
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()

        check_media_structure(self, payload)
        manual_media_id = payload["item"]["media_id"]
        self.assertEqual(str(UUID(manual_media_id)), manual_media_id)
        self.assertEqual(payload["item"]["source"], "manual")
        self.assertEqual(payload["item"]["media_type"], MediaTypes.MOVIE.value)

    def test_media_type_list_post_saves_requested_status(self):
        """Media-type create endpoint should save the status sent in the body."""
        for status_value, expected in (
            (0, Status.PLANNING.value),
            (1, Status.IN_PROGRESS.value),
            (2, Status.PAUSED.value),
            (3, Status.COMPLETED.value),
            (4, Status.DROPPED.value),
        ):
            response = self.call_api(
                "post",
                "api_media_type_list",
                args=(MediaTypes.MOVIE.value,),
                payload={
                    "source": "manual",
                    "title": f"Manual Movie {status_value}",
                    "status": status_value,
                },
                headers=self.auth_headers,
            )

            self.assertEqual(response.status_code, 201, response.content)
            payload = response.json()
            self.assertEqual(payload["status"], status_value)
            movie = Movie.objects.get(
                item__media_id=payload["item"]["media_id"],
                user=self.user1,
            )
            self.assertEqual(movie.status, expected)

    def test_media_type_list_post_defaults_to_planning(self):
        """Media-type create endpoint should default to planning without status."""
        for extra in ({}, {"status": None}):
            response = self.call_api(
                "post",
                "api_media_type_list",
                args=(MediaTypes.MOVIE.value,),
                payload={
                    "source": "manual",
                    "title": f"Manual Movie {bool(extra)}",
                    **extra,
                },
                headers=self.auth_headers,
            )

            self.assertEqual(response.status_code, 201, response.content)
            movie = Movie.objects.get(
                item__media_id=response.json()["item"]["media_id"],
                user=self.user1,
            )
            self.assertEqual(movie.status, Status.PLANNING.value)

    def test_media_type_list_post_invalid_type_returns_bad_request(self):
        """Media-type create endpoint should reject unsupported media types."""
        response = self.call_api(
            "post",
            "api_media_type_list",
            args=("invalid",),
            payload={"source": "tmdb", "media_id": "501"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_media_type_list_post_missing_body_returns_bad_request(self):
        """Media-type create endpoint should reject missing payloads."""
        response = self.call_api(
            "post",
            "api_media_type_list",
            args=(MediaTypes.MOVIE.value,),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_media_type_list_post_missing_media_id_returns_bad_request(self):
        """Provider-backed create should require media_id."""
        response = self.call_api(
            "post",
            "api_media_type_list",
            args=(MediaTypes.MOVIE.value,),
            payload={"source": "tmdb"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    @patch("api.views.services.get_media_metadata")
    def test_media_detail_get_returns_expected_shape(self, mock_metadata):
        """Media detail GET should return a complete serialized payload."""
        # TODO: Use real mock data fixtures instead of hardcoding values
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        mock_metadata.return_value = {
            "media_id": 1,
            "source": "tmdb",
            "source_url": "https://www.themoviedb.org/tv/1",
            "media_type": "tv",
            "title": "Pride",
            "max_progress": 11,
            "image": "https://image.tmdb.org/t/p/w500/rnahKduAA2VZFgrXemu97Fh6OD2.jpg",
            "synopsis": "Haru Satonaka is the captain of an ice-hockey team, a star athlete who stakes everything on hockey but can only consider love as a game. Aki Murase is a woman who has been waiting for her lover who went abroad two years ago. These two persons start a relationship while frankly admitting to each other that it is only a love game. …The result is the unfolding of a drama of people with their respective pasts and with their pride as individuals.",
            "genres": ["Drama"],
            "score": 7.8,
            "score_count": 30,
            "details": {
                "format": "TV",
                "first_air_date": "2004-01-12",
                "last_air_date": "2004-03-22",
                "status": "Ended",
                "seasons": 1,
                "episodes": 11,
                "runtime": "1h 0m",
                "studios": ["Fuji Television Network"],
                "country": "Japan",
                "languages": ["Japanese"],
            },
            "related": {
                "seasons": [
                    {
                        "source": "tmdb",
                        "media_type": "season",
                        "image": "https://image.tmdb.org/t/p/w500/nCcGD18HmDFunCl8KBigqUPlIi8.jpg",
                        "media_id": 1,
                        "title": "Pride",
                        "season_number": 1,
                        "season_title": "Season 1",
                        "first_air_date": "2004-01-12",
                        "max_progress": 11,
                    }
                ],
                "recommendations": [
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/7t6iXlbfoBSfVyINLRHms5kqfze.jpg",
                        "media_id": 281401,
                        "title": "Finding Her Edge",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/poQgqeiKzA50etdQoJxELMA6M4s.jpg",
                        "media_id": 68780,
                        "title": "Star",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/qmQqRCHIdlQvnbFG3xh4OnkznVJ.jpg",
                        "media_id": 78749,
                        "title": "Transit Girls",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/g1sYAQt0OeCxzyfagSEqxUlsLnt.jpg",
                        "media_id": 271607,
                        "title": "The Fragrant Flower Blooms with Dignity",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/S0xduJgRyZCDVzTjtZVuHIGlhj.jpg",
                        "media_id": 156510,
                        "title": "Bibliophile Princess",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/i6gZ7fVpLBK3l5FkxCzMtEtT7Bz.jpg",
                        "media_id": 2822,
                        "title": "Felicity",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/tOKUKERvfaOf7Dy2IAKT5HYYXJJ.jpg",
                        "media_id": 133908,
                        "title": "SkyMed",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/7Zm7epVFEovMEVLpM6FvrjhaNXn.jpg",
                        "media_id": 881,
                        "title": "Days of Our Lives",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/lU2oU80UhGREf1dJBRb756KWEWJ.jpg",
                        "media_id": 37565,
                        "title": "Tsubasa RESERVoir CHRoNiCLE",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/mN3kMuh7Cy9byeySK6pymnpIOZO.jpg",
                        "media_id": 1054,
                        "title": "The Young and the Restless",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/ys1VsQYMNMlQIKt3eCx2DWUZcxW.jpg",
                        "media_id": 36837,
                        "title": "His and Her Circumstances",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/vxtmMvfTOYRL4fQn9B8Pla6oH2N.jpg",
                        "media_id": 22104,
                        "title": "Touch",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/mxkKDJNZg9z9WHC98qTGALPHlKL.jpg",
                        "media_id": 42893,
                        "title": "Hotaru no Hikari: It's Only a Little Light in My Life",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/qetD01amQRFX5ibXQ7rWLe6togE.jpg",
                        "media_id": 287591,
                        "title": "In the Clear Moonlit Dusk",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/zvUrE0KPWxKDPLtJWAfDIcdP7zl.jpg",
                        "media_id": 68786,
                        "title": "Hirugao: Love Affairs in the Afternoon",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/5k7bkqolsaJVCj321gLkuikk2Ax.jpg",
                        "media_id": 232926,
                        "title": "7th Time Loop: The Villainess Enjoys a Carefree Life Married to Her Worst Enemy!",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/nASkt8izgpR4toMYgiAnxfjWcE2.jpg",
                        "media_id": 94245,
                        "title": "Swagger",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/iKMCbTAkEgvA97M8Isdqp4t12Cf.jpg",
                        "media_id": 92875,
                        "title": "Sanditon",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/vycEpFfopE9M6AjCEubMOnrYSKm.jpg",
                        "media_id": 5325,
                        "title": "Army Wives",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/ejqfETzpBBnR89btARDKrLsv8dp.jpg",
                        "media_id": 72026,
                        "title": "Love and Lies",
                    },
                ],
            },
            "tvdb_id": 84831,
            "external_links": {
                "IMDb": "https://www.imdb.com/title/tt0416409/",
                "TVDB": "https://www.thetvdb.com/dereferrer/series/84831",
                "Wikidata": "https://www.wikidata.org/wiki/Q2040235",
            },
            "last_episode_season": 1,
            "next_episode_season": None,
            "providers": {
                "JP": {
                    "link": "https://www.themoviedb.org/tv/1/watch?locale=JP",
                    "flatrate": [
                        {
                            "logo_path": "/pbpMk2JmcoNnQwx5JGpXngfoWtp.jpg",
                            "provider_id": 8,
                            "provider_name": "Netflix",
                            "display_priority": 0,
                        },
                        {
                            "logo_path": "/dpR8r13zWDeUR0QkzWidrdMxa56.jpg",
                            "provider_id": 1796,
                            "provider_name": "Netflix Standard with Ads",
                            "display_priority": 24,
                        },
                        {
                            "logo_path": "/8QWktqRs0xcar91ncdWx1EJkTuY.jpg",
                            "provider_id": 2498,
                            "provider_name": "FOD Channel Amazon Channel",
                            "display_priority": 48,
                        },
                        {
                            "logo_path": "/9EWAEok40O0HVhV0MYJmlE4LhGy.jpg",
                            "provider_id": 2683,
                            "provider_name": "FOD",
                            "display_priority": 80,
                        },
                    ],
                }
            },
        }

        response = self.call_api(
            "get",
            "api_media_detail",
            args=(MediaTypes.TV.value, tv_item.source, tv_item.media_id),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_complete_media_structure(self, payload)

    @patch("api.views.services.get_media_metadata")
    def test_media_detail_get_returns_external_links(self, mock_metadata):
        """Media detail GET should return the external links of the media."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        mock_metadata.return_value = {
            "media_id": movie_item.media_id,
            "source": movie_item.source,
            "media_type": MediaTypes.MOVIE.value,
            "title": movie_item.title,
            "max_progress": 1,
            "details": {},
            "related": {},
            "external_links": {
                "IMDb": "https://www.imdb.com/title/tt0133093/",
                "Letterboxd": "https://www.letterboxd.com/tmdb/603",
            },
        }

        response = self.call_api(
            "get",
            "api_media_detail",
            args=(MediaTypes.MOVIE.value, movie_item.source, movie_item.media_id),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["external_links"],
            {
                "IMDb": "https://www.imdb.com/title/tt0133093/",
                "Letterboxd": "https://www.letterboxd.com/tmdb/603",
            },
        )
        self.assertIsNone(payload["time_to_beat"])

    @patch("api.views.services.get_media_metadata")
    def test_media_detail_get_returns_game_time_to_beat(self, mock_metadata):
        """Media detail GET should return the time to beat of a game."""
        game_item = self.items_by_type[MediaTypes.GAME.value][0]
        mock_metadata.return_value = {
            "media_id": game_item.media_id,
            "source": game_item.source,
            "media_type": MediaTypes.GAME.value,
            "title": game_item.title,
            "max_progress": None,
            "details": {},
            "related": {},
            "time_to_beat": {"hastily": 36000, "normally": 54000},
        }

        response = self.call_api(
            "get",
            "api_media_detail",
            args=(MediaTypes.GAME.value, game_item.source, game_item.media_id),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["time_to_beat"],
            {"hastily": 36000, "normally": 54000},
        )
        self.assertEqual(payload["external_links"], {})

    def test_media_detail_get_invalid_type_returns_bad_request(self):
        """Media detail endpoint should reject unsupported media types."""
        response = self.call_api(
            "get",
            "api_media_detail",
            args=("invalid", "tmdb", 501),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_media_detail_get_invalid_media_id_returns_internal_server_error(self):
        """Media detail endpoint currently returns 500 for invalid provider ids."""
        response = self.call_api(
            "get",
            "api_media_detail",
            args=(MediaTypes.MOVIE.value, "tmdb", 999999),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 500)

    @patch("api.views.services.get_media_metadata")
    def test_media_detail_patch_updates_media_fields(self, mock_metadata):
        """Media detail PATCH should update mutable media fields."""
        # TODO: Use real mock data fixtures instead of hardcoding values
        status = 2
        score = 8
        notes = "Great TV show!"
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        mock_metadata.return_value = {
            "media_id": 1,
            "source": "tmdb",
            "source_url": "https://www.themoviedb.org/tv/1",
            "media_type": "tv",
            "title": "Pride",
            "max_progress": 11,
            "image": "https://image.tmdb.org/t/p/w500/rnahKduAA2VZFgrXemu97Fh6OD2.jpg",
            "synopsis": "Haru Satonaka is the captain of an ice-hockey team, a star athlete who stakes everything on hockey but can only consider love as a game. Aki Murase is a woman who has been waiting for her lover who went abroad two years ago. These two persons start a relationship while frankly admitting to each other that it is only a love game. …The result is the unfolding of a drama of people with their respective pasts and with their pride as individuals.",
            "genres": ["Drama"],
            "score": 7.8,
            "score_count": 30,
            "details": {
                "format": "TV",
                "first_air_date": "2004-01-12",
                "last_air_date": "2004-03-22",
                "status": "Ended",
                "seasons": 1,
                "episodes": 11,
                "runtime": "1h 0m",
                "studios": ["Fuji Television Network"],
                "country": "Japan",
                "languages": ["Japanese"],
            },
            "related": {
                "seasons": [
                    {
                        "source": "tmdb",
                        "media_type": "season",
                        "image": "https://image.tmdb.org/t/p/w500/nCcGD18HmDFunCl8KBigqUPlIi8.jpg",
                        "media_id": 1,
                        "title": "Pride",
                        "season_number": 1,
                        "season_title": "Season 1",
                        "first_air_date": "2004-01-12",
                        "max_progress": 11,
                    }
                ],
                "recommendations": [
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/7t6iXlbfoBSfVyINLRHms5kqfze.jpg",
                        "media_id": 281401,
                        "title": "Finding Her Edge",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/poQgqeiKzA50etdQoJxELMA6M4s.jpg",
                        "media_id": 68780,
                        "title": "Star",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/qmQqRCHIdlQvnbFG3xh4OnkznVJ.jpg",
                        "media_id": 78749,
                        "title": "Transit Girls",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/g1sYAQt0OeCxzyfagSEqxUlsLnt.jpg",
                        "media_id": 271607,
                        "title": "The Fragrant Flower Blooms with Dignity",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/S0xduJgRyZCDVzTjtZVuHIGlhj.jpg",
                        "media_id": 156510,
                        "title": "Bibliophile Princess",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/i6gZ7fVpLBK3l5FkxCzMtEtT7Bz.jpg",
                        "media_id": 2822,
                        "title": "Felicity",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/tOKUKERvfaOf7Dy2IAKT5HYYXJJ.jpg",
                        "media_id": 133908,
                        "title": "SkyMed",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/7Zm7epVFEovMEVLpM6FvrjhaNXn.jpg",
                        "media_id": 881,
                        "title": "Days of Our Lives",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/lU2oU80UhGREf1dJBRb756KWEWJ.jpg",
                        "media_id": 37565,
                        "title": "Tsubasa RESERVoir CHRoNiCLE",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/mN3kMuh7Cy9byeySK6pymnpIOZO.jpg",
                        "media_id": 1054,
                        "title": "The Young and the Restless",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/ys1VsQYMNMlQIKt3eCx2DWUZcxW.jpg",
                        "media_id": 36837,
                        "title": "His and Her Circumstances",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/vxtmMvfTOYRL4fQn9B8Pla6oH2N.jpg",
                        "media_id": 22104,
                        "title": "Touch",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/mxkKDJNZg9z9WHC98qTGALPHlKL.jpg",
                        "media_id": 42893,
                        "title": "Hotaru no Hikari: It's Only a Little Light in My Life",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/qetD01amQRFX5ibXQ7rWLe6togE.jpg",
                        "media_id": 287591,
                        "title": "In the Clear Moonlit Dusk",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/zvUrE0KPWxKDPLtJWAfDIcdP7zl.jpg",
                        "media_id": 68786,
                        "title": "Hirugao: Love Affairs in the Afternoon",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/5k7bkqolsaJVCj321gLkuikk2Ax.jpg",
                        "media_id": 232926,
                        "title": "7th Time Loop: The Villainess Enjoys a Carefree Life Married to Her Worst Enemy!",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/nASkt8izgpR4toMYgiAnxfjWcE2.jpg",
                        "media_id": 94245,
                        "title": "Swagger",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/iKMCbTAkEgvA97M8Isdqp4t12Cf.jpg",
                        "media_id": 92875,
                        "title": "Sanditon",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/vycEpFfopE9M6AjCEubMOnrYSKm.jpg",
                        "media_id": 5325,
                        "title": "Army Wives",
                    },
                    {
                        "source": "tmdb",
                        "media_type": "tv",
                        "image": "https://image.tmdb.org/t/p/w500/ejqfETzpBBnR89btARDKrLsv8dp.jpg",
                        "media_id": 72026,
                        "title": "Love and Lies",
                    },
                ],
            },
            "tvdb_id": 84831,
            "external_links": {
                "IMDb": "https://www.imdb.com/title/tt0416409/",
                "TVDB": "https://www.thetvdb.com/dereferrer/series/84831",
                "Wikidata": "https://www.wikidata.org/wiki/Q2040235",
            },
            "last_episode_season": 1,
            "next_episode_season": None,
            "providers": {
                "JP": {
                    "link": "https://www.themoviedb.org/tv/1/watch?locale=JP",
                    "flatrate": [
                        {
                            "logo_path": "/pbpMk2JmcoNnQwx5JGpXngfoWtp.jpg",
                            "provider_id": 8,
                            "provider_name": "Netflix",
                            "display_priority": 0,
                        },
                        {
                            "logo_path": "/dpR8r13zWDeUR0QkzWidrdMxa56.jpg",
                            "provider_id": 1796,
                            "provider_name": "Netflix Standard with Ads",
                            "display_priority": 24,
                        },
                        {
                            "logo_path": "/8QWktqRs0xcar91ncdWx1EJkTuY.jpg",
                            "provider_id": 2498,
                            "provider_name": "FOD Channel Amazon Channel",
                            "display_priority": 48,
                        },
                        {
                            "logo_path": "/9EWAEok40O0HVhV0MYJmlE4LhGy.jpg",
                            "provider_id": 2683,
                            "provider_name": "FOD",
                            "display_priority": 80,
                        },
                    ],
                }
            },
        }

        response = self.call_api(
            "patch",
            "api_media_detail",
            args=(MediaTypes.TV.value, tv_item.source, tv_item.media_id),
            payload={"status": status, "score": score, "notes": notes},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_complete_media_structure(self, payload)
        self.assertEqual(payload["consumptions"][0]["status"], status)
        self.assertEqual(payload["consumptions"][0]["score"], score)
        self.assertEqual(payload["consumptions"][0]["notes"], notes)

    def test_media_detail_patch_invalid_type_returns_bad_request(self):
        """Media detail PATCH should reject unsupported media types."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        response = self.call_api(
            "patch",
            "api_media_detail",
            args=("invalid", movie_item.source, movie_item.media_id),
            payload={"status": 3},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_media_detail_patch_invalid_media_id_returns_not_found(self):
        """Media detail PATCH should return not found for unknown provider ids."""
        response = self.call_api(
            "patch",
            "api_media_detail",
            args=(MediaTypes.MOVIE.value, "tmdb", 999999),
            payload={"status": 3},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_media_detail_patch_with_unknown_field_returns_bad_request(self):
        """Media PATCH should reject fields outside the allowed whitelist."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        response = self.call_api(
            "patch",
            "api_media_detail",
            args=(MediaTypes.MOVIE.value, movie_item.source, movie_item.media_id),
            payload={"unknown_field": "value"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("no valid fields", response.json().get("detail", "").lower())

    def test_media_changes_history_returns_paginated_payload(self):
        """Changes history endpoint should return change entries."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        response = self.call_api(
            "get",
            "api_media_changes_history",
            args=(MediaTypes.MOVIE.value, movie_item.source, movie_item.media_id),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        check_pagination_structure(
            self,
            payload["pagination"],
            total=3,
            limit=20,
            offset=0,
        )
        self.assertIn("results", payload)
        for item in payload["results"]:
            check_changes_history_record_structure(self, item)

    def test_media_changes_history_invalid_type_returns_bad_request(self):
        """Changes history endpoint should reject unsupported media types."""
        response = self.call_api(
            "get",
            "api_media_changes_history",
            args=("invalid", "tmdb", 501),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_media_changes_history_invalid_media_id_returns_not_found(self):
        """Changes history endpoint should return not found for unknown provider ids."""
        response = self.call_api(
            "get",
            "api_media_changes_history",
            args=(MediaTypes.MOVIE.value, "tmdb", 999999),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_media_consumption_history_returns_paginated_payload(self):
        """Consumption history endpoint should return consumption entries."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        response = self.call_api(
            "get",
            "api_media_consumption_history",
            args=(MediaTypes.MOVIE.value, movie_item.source, movie_item.media_id),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        check_pagination_structure(
            self,
            payload["pagination"],
            total=1,
            limit=20,
            offset=0,
        )
        self.assertIn("results", payload)
        for item in payload["results"]:
            check_consumption_structure(self, item)

    def test_media_consumption_history_invalid_type_returns_bad_request(self):
        """Consumption history endpoint should reject unsupported media types."""
        response = self.call_api(
            "get",
            "api_media_consumption_history",
            args=("invalid", "tmdb", 501),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_media_consumption_history_invalid_media_id_returns_not_found(self):
        """Consumption history endpoint should return not found for unknown ids."""
        response = self.call_api(
            "get",
            "api_media_consumption_history",
            args=(MediaTypes.MOVIE.value, "tmdb", 999999),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_media_consumption_entry_detail_delete_removes_history_entry(self):
        """Entry-detail DELETE should remove an existing consumption row."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        consumption_id = self.movie_medias[0].id
        response = self.call_api(
            "delete",
            "api_media_consumption_entry_detail",
            args=(
                MediaTypes.MOVIE.value,
                movie_item.source,
                movie_item.media_id,
                consumption_id,
            ),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 204)

        get_response = self.call_api(
            "get",
            "api_media_consumption_entry_detail",
            args=(
                MediaTypes.MOVIE.value,
                movie_item.source,
                movie_item.media_id,
                consumption_id,
            ),
            headers=self.auth_headers,
        )
        self.assertEqual(get_response.status_code, 404)

    def test_media_consumption_entry_detail_get_returns_expected_structure(self):
        """Entry-detail endpoint should return a complete serialized payload."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        consumption_id = self.movie_medias[0].id
        response = self.call_api(
            "get",
            "api_media_consumption_entry_detail",
            args=(
                MediaTypes.MOVIE.value,
                movie_item.source,
                movie_item.media_id,
                consumption_id,
            ),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_consumption_structure(self, payload)

    def test_media_consumption_entry_detail_get_preserves_actual_progress(self):
        """Entry-detail endpoint should return the stored non-binary progress value."""
        game_item = self.items_by_type[MediaTypes.GAME.value][0]
        game_media = self.game_medias[0]
        game_media.progress = 120
        game_media.status = Status.IN_PROGRESS.value
        game_media.save(update_fields=["progress", "status"])

        response = self.call_api(
            "get",
            "api_media_consumption_entry_detail",
            args=(
                MediaTypes.GAME.value,
                game_item.source,
                game_item.media_id,
                game_media.id,
            ),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_consumption_structure(self, payload)
        self.assertEqual(payload["progress"], 120)
        self.assertEqual(payload["status"], 1)

    def test_media_consumption_entry_detail_invalid_type_methods(self):
        """Entry-detail endpoints should reject unsupported media types."""
        for method in ["get", "patch", "delete"]:
            response = self.call_api(
                method,
                "api_media_consumption_entry_detail",
                args=("invalid", "tmdb", 501, 1),
                payload={"notes": "x"} if method == "patch" else None,
                headers=self.auth_headers,
            )
            self.assertEqual(response.status_code, 400)

    def test_media_consumption_entry_detail_invalid_media_id_methods(self):
        """Entry-detail endpoints should return not found for unknown media ids."""
        for method in ["get", "patch", "delete"]:
            response = self.call_api(
                method,
                "api_media_consumption_entry_detail",
                args=(MediaTypes.MOVIE.value, "tmdb", 999999, 1),
                payload={"notes": "x"} if method == "patch" else None,
                headers=self.auth_headers,
            )
            self.assertEqual(response.status_code, 404)

    def test_media_consumption_entry_detail_invalid_consumption_id_methods(self):
        """Entry-detail endpoints should return not found for unknown consumption ids."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        invalid_consumption_id = 999999

        for method in ["get", "patch", "delete"]:
            response = self.call_api(
                method,
                "api_media_consumption_entry_detail",
                args=(
                    MediaTypes.MOVIE.value,
                    movie_item.source,
                    movie_item.media_id,
                    invalid_consumption_id,
                ),
                payload={"notes": "x"} if method == "patch" else None,
                headers=self.auth_headers,
            )
            self.assertEqual(response.status_code, 404)

    def test_media_consumption_entry_detail_patch_updates_history_entry(self):
        """Entry-detail PATCH should persist valid updates."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        consumption_id = self.movie_medias[0].id
        response = self.call_api(
            "patch",
            "api_media_consumption_entry_detail",
            args=(
                MediaTypes.MOVIE.value,
                movie_item.source,
                movie_item.media_id,
                consumption_id,
            ),
            payload={"notes": "updated-from-test"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_consumption_structure(self, payload)
        self.assertEqual(payload["notes"], "updated-from-test")

    def test_media_consumption_entry_detail_patch_accepts_datetime_strings(self):
        """Entry-detail PATCH should accept ISO datetime values for date fields."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        consumption_id = self.movie_medias[0].id
        response = self.call_api(
            "patch",
            "api_media_consumption_entry_detail",
            args=(
                MediaTypes.MOVIE.value,
                movie_item.source,
                movie_item.media_id,
                consumption_id,
            ),
            payload={
                "start_date": "2023-10-01T00:00:00Z",
                "end_date": "2023-10-02T15:30:00Z",
            },
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        check_consumption_structure(self, payload)
        self.assertTrue(payload["start_date"].startswith("2023-10-01T00:00:00"))
        self.assertTrue(payload["end_date"].startswith("2023-10-02T15:30:00"))

    def test_media_consumption_entry_detail_patch_invalid_payload_returns_bad_request(
        self,
    ):
        """Entry-detail PATCH should reject invalid payload values."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        consumption_id = self.movie_medias[0].id
        response = self.call_api(
            "patch",
            "api_media_consumption_entry_detail",
            args=(
                MediaTypes.MOVIE.value,
                movie_item.source,
                movie_item.media_id,
                consumption_id,
            ),
            payload={"end_date": "invalid-date"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_media_lists_get_returns_lists(self):
        """Media list relation endpoint should return associated lists."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]

        response = self.call_api(
            "get",
            "api_media_lists",
            args=(MediaTypes.MOVIE.value, movie_item.source, movie_item.media_id),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("results", payload)
        self.assertEqual(len(payload["results"]), 1)
        for item in payload["results"]:
            check_minimized_lists_structure(self, item)

    def test_media_lists_get_invalid_media_id_returns_empty_results(self):
        """Media list relation endpoint should return empty results for unknown media."""
        response = self.call_api(
            "get",
            "api_media_lists",
            args=(MediaTypes.MOVIE.value, "tmdb", 999999),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["results"], [])

    def test_media_lists_invalid_type_methods(self):
        """Media list relation endpoints should reject unsupported media types."""
        get_response = self.call_api(
            "get",
            "api_media_lists",
            args=("invalid", "tmdb", 501),
            headers=self.auth_headers,
        )
        self.assertEqual(get_response.status_code, 400)

        list_id = self.lists_by_name["favorites"].id
        put_response = self.call_api(
            "put",
            "api_media_lists",
            args=("invalid", "tmdb", 501, list_id),
            payload={},
            headers=self.auth_headers,
        )
        self.assertEqual(put_response.status_code, 400)

        delete_response = self.call_api(
            "delete",
            "api_media_lists",
            args=("invalid", "tmdb", 501, list_id),
            headers=self.auth_headers,
        )
        self.assertEqual(delete_response.status_code, 400)

    def test_media_list_detail_delete_removes_media_from_list(self):
        """Media list detail DELETE should remove media from an existing list."""
        list_id = self.lists_by_name["favorites"].id
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]

        response = self.call_api(
            "delete",
            "api_media_lists",
            args=(
                MediaTypes.MOVIE.value,
                movie_item.source,
                movie_item.media_id,
                list_id,
            ),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 204)
        get_response = self.call_api(
            "get",
            "api_media_lists",
            args=(MediaTypes.MOVIE.value, movie_item.source, movie_item.media_id),
            headers=self.auth_headers,
        )
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.json()["results"], [])

    def test_media_list_detail_delete_invalid_media_id_returns_not_found(self):
        """Media list detail DELETE should reject unknown media ids."""
        list_id = self.lists_by_name["favorites"].id
        response = self.call_api(
            "delete",
            "api_media_lists",
            args=(MediaTypes.MOVIE.value, "tmdb", 999999, list_id),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_media_list_detail_delete_invalid_list_id_returns_not_found(self):
        """Media list detail DELETE should reject unknown list ids."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        response = self.call_api(
            "delete",
            "api_media_lists",
            args=(
                MediaTypes.MOVIE.value,
                movie_item.source,
                movie_item.media_id,
                999999,
            ),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_media_list_detail_put_adds_media_to_list(self):
        """Media list detail PUT should add media when missing from list."""
        list_id = self.lists_by_name["favorites"].id
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][1]

        response = self.call_api(
            "put",
            "api_media_lists",
            args=(
                MediaTypes.MOVIE.value,
                movie_item.source,
                movie_item.media_id,
                list_id,
            ),
            payload={},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        get_response = self.call_api(
            "get",
            "api_media_lists",
            args=(MediaTypes.MOVIE.value, movie_item.source, movie_item.media_id),
            headers=self.auth_headers,
        )
        self.assertEqual(get_response.status_code, 200)
        payload = get_response.json()
        self.assertIn("results", payload)
        for item in payload["results"]:
            check_minimized_lists_structure(self, item)
            self.assertEqual(item["list_id"], list_id)

    def test_media_list_detail_put_invalid_media_id_returns_not_found(self):
        """Media list detail PUT should reject unknown media ids."""
        list_id = self.lists_by_name["favorites"].id
        response = self.call_api(
            "put",
            "api_media_lists",
            args=(MediaTypes.MOVIE.value, "tmdb", 999999, list_id),
            payload={},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_media_list_detail_put_invalid_list_id_returns_not_found(self):
        """Media list detail PUT should reject unknown list ids."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][1]
        response = self.call_api(
            "put",
            "api_media_lists",
            args=(
                MediaTypes.MOVIE.value,
                movie_item.source,
                movie_item.media_id,
                999999,
            ),
            payload={},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 404)

    @patch("api.views.services.get_media_metadata")
    def test_media_providers_returns_grouped_providers(self, mock_metadata):
        """Providers endpoint should group providers into no charge and paid."""
        self.user1.watch_provider_region = "US"
        self.user1.save()
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        mock_metadata.return_value = {"providers": WATCH_PROVIDERS_RESPONSE}

        response = self.call_api(
            "get",
            "api_media_providers",
            args=(MediaTypes.MOVIE.value, movie_item.source, movie_item.media_id),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["region"], "US")
        self.assertEqual(
            payload["link"],
            "https://www.themoviedb.org/movie/1/watch?locale=US",
        )
        self.assertEqual(
            [p["provider_name"] for p in payload["no_charge"]],
            ["Tubi TV", "Netflix"],
        )
        self.assertEqual(
            [p["provider_name"] for p in payload["paid"]],
            ["Apple TV"],
        )
        self.assertEqual(
            payload["paid"][0]["image"],
            "https://image.tmdb.org/t/p/w500/apple.jpg",
        )

    @patch("api.views.services.get_media_metadata")
    def test_media_providers_region_param_overrides_user_region(
        self,
        mock_metadata,
    ):
        """Providers endpoint should prefer the region query parameter."""
        self.user1.watch_provider_region = "US"
        self.user1.save()
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        mock_metadata.return_value = {"providers": WATCH_PROVIDERS_RESPONSE}

        response = self.call_api(
            "get",
            "api_media_providers",
            args=(MediaTypes.MOVIE.value, movie_item.source, movie_item.media_id),
            params={"region": "it"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["region"], "IT")
        self.assertIsNone(payload["link"])
        self.assertEqual(
            [p["provider_name"] for p in payload["no_charge"]],
            ["Amazon Prime Video"],
        )
        self.assertEqual(payload["paid"], [])

    @patch("api.views.services.get_media_metadata")
    def test_media_providers_unknown_region_returns_empty_groups(
        self,
        mock_metadata,
    ):
        """Providers endpoint should return empty groups for regions without data."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        mock_metadata.return_value = {"providers": WATCH_PROVIDERS_RESPONSE}

        response = self.call_api(
            "get",
            "api_media_providers",
            args=(MediaTypes.MOVIE.value, movie_item.source, movie_item.media_id),
            params={"region": "FR"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["no_charge"], [])
        self.assertEqual(payload["paid"], [])

    def test_media_providers_without_region_returns_bad_request(self):
        """Providers endpoint should require a region when the user has none set."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        response = self.call_api(
            "get",
            "api_media_providers",
            args=(MediaTypes.MOVIE.value, movie_item.source, movie_item.media_id),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_media_providers_unsupported_media_type_returns_bad_request(self):
        """Providers endpoint should reject media types without watch providers."""
        requests = [
            ("invalid", "tmdb", 1),
            (MediaTypes.ANIME.value, "mal", 1),
            (MediaTypes.MOVIE.value, "manual", 1),
        ]

        for args in requests:
            response = self.call_api(
                "get",
                "api_media_providers",
                args=args,
                params={"region": "US"},
                headers=self.auth_headers,
            )
            self.assertEqual(response.status_code, 400)

    @patch("api.views.services.get_media_metadata", side_effect=Exception("boom"))
    def test_media_providers_invalid_media_id_returns_internal_server_error(
        self,
        _mock_metadata,
    ):
        """Providers endpoint should surface provider lookup failures."""
        response = self.call_api(
            "get",
            "api_media_providers",
            args=(MediaTypes.MOVIE.value, "tmdb", 999999),
            params={"region": "US"},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 500)

    @patch("api.views.services.get_media_metadata")
    def test_media_recommendations_returns_related_items(self, mock_metadata):
        """Recommendations endpoint should return provider recommendations."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        recommended_item = self.items_by_type[MediaTypes.MOVIE.value][1]
        mock_metadata.return_value = {
            "related": {
                "recommendations": [
                    {
                        "media_id": recommended_item.media_id,
                        "source": recommended_item.source,
                        "media_type": recommended_item.media_type,
                        "title": recommended_item.title,
                        "image": recommended_item.image,
                    },
                ],
            },
        }

        response = self.call_api(
            "get",
            "api_media_recommendations",
            args=(MediaTypes.MOVIE.value, movie_item.source, movie_item.media_id),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["media_id"], recommended_item.media_id)
        self.assertEqual(payload[0]["title"], recommended_item.title)

    def test_media_recommendations_invalid_type_returns_bad_request(self):
        """Recommendations endpoint should reject unsupported media types."""
        response = self.call_api(
            "get",
            "api_media_recommendations",
            args=("invalid", "tmdb", 501),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    @patch("api.views.services.get_media_metadata", side_effect=Exception("boom"))
    def test_media_recommendations_invalid_media_id_returns_internal_server_error(
        self,
        _mock_metadata,
    ):
        """Recommendations endpoint should surface provider lookup failures."""
        response = self.call_api(
            "get",
            "api_media_recommendations",
            args=(MediaTypes.MOVIE.value, "tmdb", 999999),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 500)

    @patch("api.views.services.get_media_metadata")
    def test_media_seasons_get_returns_expected_structure(self, mock_metadata):
        """Media seasons endpoint should return paginated media payload."""
        tv_item = self.items_by_type[MediaTypes.TV.value][0]
        mock_metadata.return_value = {
            "related": {
                "seasons": [
                    {
                        "season_number": 1,
                        "season_title": "Season 1",
                        "image": "https://example.com/season-1.jpg",
                    },
                    {
                        "season_number": 2,
                        "season_title": "Season 2",
                        "image": "https://example.com/season-2.jpg",
                    },
                ],
            },
        }

        response = self.call_api(
            "get",
            "api_media_seasons",
            args=(MediaTypes.TV.value, tv_item.source, tv_item.media_id),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("pagination", payload)
        check_pagination_structure(
            self,
            payload["pagination"],
            total=2,
            limit=20,
            offset=0,
        )
        self.assertIn("results", payload)
        self.assertEqual(len(payload["results"]), 2)
        for item in payload["results"]:
            check_media_structure(self, item)

    def test_media_seasons_invalid_type_returns_bad_request(self):
        """Media seasons endpoint should reject non-tv media types."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        response = self.call_api(
            "get",
            "api_media_seasons",
            args=(MediaTypes.MOVIE.value, movie_item.source, movie_item.media_id),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    @patch("api.views.services.get_media_metadata", side_effect=Exception("boom"))
    def test_media_seasons_invalid_media_id_returns_internal_server_error(
        self,
        _mock_metadata,
    ):
        """Media seasons endpoint should surface provider lookup failures."""
        response = self.call_api(
            "get",
            "api_media_seasons",
            args=(MediaTypes.TV.value, "tmdb", 999999),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 500)

    @patch("api.views.services.get_media_metadata")
    def test_media_sync_returns_accepted_and_updates_item(self, mock_metadata):
        """Sync endpoint should refresh metadata and return accepted."""
        movie_item = self.items_by_type[MediaTypes.MOVIE.value][0]
        mock_metadata.return_value = {
            "title": "Movie 1 Synced",
            "image": "https://example.com/movie-1-synced.jpg",
        }

        response = self.call_api(
            "post",
            "api_media_sync",
            args=(MediaTypes.MOVIE.value, movie_item.source, movie_item.media_id),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertIn("Metadata synced successfully", payload["detail"])

        movie_item.refresh_from_db()
        self.assertEqual(movie_item.title, "Movie 1 Synced")
        self.assertEqual(movie_item.image, "https://example.com/movie-1-synced.jpg")

    @patch("api.views.services.get_media_metadata", side_effect=Exception("boom"))
    def test_media_sync_invalid_media_id_returns_internal_server_error(
        self, _mock_metadata
    ):
        """Sync endpoint should surface provider lookup failures."""
        response = self.call_api(
            "post",
            "api_media_sync",
            args=(MediaTypes.MOVIE.value, "tmdb", 999999),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 500)

    def test_media_sync_invalid_type_returns_bad_request(self):
        """Sync endpoint should reject unsupported media types."""
        response = self.call_api(
            "post",
            "api_media_sync",
            args=("invalid", "tmdb", 501),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)

    def test_media_sync_rejects_manual_source(self):
        """Sync endpoint should reject manual items."""
        response = self.call_api(
            "post",
            "api_media_sync",
            args=(MediaTypes.MOVIE.value, Sources.MANUAL.value, 701),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
