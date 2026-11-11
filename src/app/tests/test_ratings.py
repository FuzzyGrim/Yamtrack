from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from app.models import (
    Item,
    ItemRating,
    MediaManager,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from app.ratings import (
    EMPTY_RATING,
    format_public_value,
    normalize,
    refresh_item_rating,
    user_weights,
    weighted_average,
)
from users.models import MediaStatusChoices

METADATA = {
    "title": "Test Movie",
    "image": "http://example.com/image.jpg",
    "max_progress": 1,
    "score": 8.2,
    "score_count": 100,
    "external_links": {"IMDb": "https://www.imdb.com/title/tt0133093/"},
}


class RatingMathTests(TestCase):
    """Normalization, the weighted average, and the empty-score display."""

    def test_normalize_scales_and_keeps_missing_empty(self):
        """Each source converts onto 0-100, and a missing score stays missing."""
        self.assertEqual(
            normalize(Decimal("8.5"), Decimal(10)), (Decimal("8.5"), Decimal("85.0"))
        )
        self.assertEqual(
            normalize(Decimal(85), Decimal(100)), (Decimal("85.0"), Decimal("85.0"))
        )
        self.assertEqual(
            normalize(Decimal(4), Decimal(5)), (Decimal("4.0"), Decimal("80.0"))
        )
        self.assertEqual(normalize(None, Decimal(10)), (None, None))
        self.assertEqual(
            normalize(Decimal(0), Decimal(10)), (Decimal("0.0"), Decimal("0.0"))
        )

    def test_weighted_average_skips_missing_values_and_zero_weights(self):
        """Only sources that exist and have a weight above zero are averaged."""
        normalized = {
            "imdb": Decimal(80),
            "rotten_tomatoes": None,
            "metacritic": Decimal(60),
            "tmdb": Decimal(50),
            "letterboxd": Decimal(100),
        }
        weights = {
            "imdb": 1,
            "rotten_tomatoes": 1,
            "metacritic": 0,
            "tmdb": 1,
            "letterboxd": 0,
        }

        self.assertEqual(weighted_average(normalized, weights), Decimal("65.0"))

    def test_weighted_average_is_empty_when_nothing_counts(self):
        """No present, weighted source means there is no aggregate."""
        normalized = {"imdb": None, "tmdb": Decimal(80)}
        weights = {"imdb": 1, "tmdb": 0}

        self.assertIsNone(weighted_average(normalized, weights))

    def test_missing_score_displays_a_dash_and_zero_stays_zero(self):
        """An empty score is an en dash, so it cannot be read as a bad zero."""
        self.assertEqual(format_public_value(None), EMPTY_RATING)
        self.assertEqual(format_public_value(None), "\u2013")
        self.assertEqual(format_public_value(Decimal(0)), "0")
        self.assertEqual(format_public_value(Decimal("82.0")), "82")
        self.assertEqual(format_public_value(Decimal("8.5")), "8.5")


class PublicRatingSortTests(TestCase):
    """Media list ordering by the aggregated public rating."""

    def setUp(self):
        """Create a user and stop metadata lookups from leaving the test."""
        self.user = get_user_model().objects.create_user(
            username="sort-user",
            password="test-pass",  # noqa: S106
        )
        self.metadata = patch(
            "app.providers.services.get_media_metadata",
            return_value={
                "max_progress": 1,
                "title": "Test",
                "image": "http://example.com/a.jpg",
            },
        )
        self.metadata.start()
        self.addCleanup(self.metadata.stop)

    def _movie(self, media_id, title, status):
        item = Item.objects.create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title=title,
            image="http://example.com/a.jpg",
        )
        Movie.objects.create(item=item, user=self.user, status=status, score=5)
        return item

    def test_public_rating_sorts_descending_with_nulls_last(self):
        """Planning items sort by the public score, and missing scores go last."""
        high = self._movie("1", "Zebra", Status.PLANNING.value)
        low = self._movie("2", "Alpha", Status.COMPLETED.value)
        self._movie("3", "Middle", Status.PLANNING.value)
        ItemRating.objects.create(
            item=high,
            imdb_value=Decimal("9.0"),
            imdb_normalized=Decimal("90.0"),
        )
        ItemRating.objects.create(
            item=low,
            imdb_value=Decimal("4.0"),
            imdb_normalized=Decimal("40.0"),
        )

        self.assertEqual(
            self._titles(MediaStatusChoices.ALL), ["Zebra", "Alpha", "Middle"]
        )
        self.assertEqual(self._titles(MediaStatusChoices.PLANNING), ["Zebra", "Middle"])

    def test_null_source_is_not_treated_as_zero(self):
        """A missing source is skipped. A stored zero still counts."""
        skipped = self._movie("4", "Skipped", Status.PLANNING.value)
        zeroed = self._movie("5", "Zeroed", Status.PLANNING.value)
        ItemRating.objects.create(
            item=skipped,
            imdb_normalized=Decimal("80.0"),
            rotten_tomatoes_normalized=None,
        )
        ItemRating.objects.create(
            item=zeroed,
            imdb_normalized=Decimal("80.0"),
            rotten_tomatoes_normalized=Decimal("0.0"),
        )

        media = self._sorted()
        by_title = {entry.item.title: entry.public_rating for entry in media}

        self.assertEqual(by_title["Skipped"], Decimal("80.0"))
        self.assertEqual(by_title["Zeroed"], Decimal("40.0"))
        self.assertEqual([entry.item.title for entry in media], ["Skipped", "Zeroed"])

    def test_zero_weight_drops_that_source(self):
        """Letterboxd's default weight of 0 leaves it out of the aggregate."""
        item = self._movie("6", "Weighted", Status.PLANNING.value)
        ItemRating.objects.create(
            item=item,
            imdb_normalized=Decimal("10.0"),
            letterboxd_normalized=Decimal("100.0"),
        )
        self.assertEqual(self.user.rating_weight_letterboxd, 0)

        media = self._sorted().get()

        self.assertEqual(media.public_rating, Decimal("10.0"))
        self.assertEqual(
            media.public_rating,
            weighted_average(
                {"imdb": Decimal("10.0"), "letterboxd": Decimal("100.0")},
                user_weights(self.user),
            ),
        )

    def _sorted(self):
        return MediaManager().get_media_list(
            user=self.user,
            media_type=MediaTypes.MOVIE.value,
            status_filter=MediaStatusChoices.ALL,
            sort_filter="public_rating",
        )

    def _titles(self, status):
        media = MediaManager().get_media_list(
            user=self.user,
            media_type=MediaTypes.MOVIE.value,
            status_filter=status,
            sort_filter="public_rating",
        )
        return [entry.item.title for entry in media]


class RefreshRatingTests(TestCase):
    """External refresh, including the no-key path and the backfill cap."""

    def setUp(self):
        """Start from an empty OMDb budget."""
        cache.clear()

    @override_settings(OMDB_API_KEY="")
    @patch("app.providers.services.api_request")
    @patch("app.providers.services.get_media_metadata", return_value=METADATA)
    def test_no_api_key_stores_tmdb_and_leaves_omdb_empty(
        self, _metadata, mock_request
    ):
        """Without a key, TMDB is stored and the OMDb fields stay null."""
        item = self._movie("200")
        ItemRating.objects.create(
            item=item,
            letterboxd_value=Decimal("4.0"),
            letterboxd_normalized=Decimal("80.0"),
        )

        self.assertEqual(refresh_item_rating(item), "updated")

        mock_request.assert_not_called()
        rating = item.public_rating
        rating.refresh_from_db()
        self.assertEqual(rating.tmdb_value, Decimal("8.2"))
        self.assertEqual(rating.tmdb_normalized, Decimal("82.0"))
        self.assertIsNone(rating.imdb_value)
        self.assertIsNone(rating.rotten_tomatoes_value)
        self.assertIsNone(rating.metacritic_value)
        self.assertIsNotNone(rating.ratings_updated_at)
        self.assertEqual(rating.letterboxd_value, Decimal("4.0"))

    @override_settings(OMDB_API_KEY="")
    @patch("app.providers.services.get_media_metadata")
    def test_unknown_tmdb_score_stays_null(self, mock_metadata):
        """TMDB's 0.0 placeholder for an unknown score is stored as null."""
        mock_metadata.return_value = {**METADATA, "score": 0.0, "score_count": 0}
        item = self._movie("201")

        refresh_item_rating(item)

        self.assertIsNone(item.public_rating.tmdb_value)
        self.assertIsNone(item.public_rating.tmdb_normalized)

    @override_settings(OMDB_API_KEY="test-key", OMDB_DAILY_LIMIT=1)
    @patch("app.providers.services.api_request")
    @patch("app.providers.services.get_media_metadata")
    def test_backfill_stops_at_the_daily_cap(self, mock_metadata, mock_request):
        """The backfill updates what it can and leaves the rest for the next run."""
        mock_request.return_value = {
            "Response": "True",
            "imdbRating": "8.0",
            "Metascore": "80",
            "Ratings": [{"Source": "Rotten Tomatoes", "Value": "90%"}],
        }
        mock_metadata.side_effect = lambda _media_type, media_id, _source: {
            "score": 7.5,
            "score_count": 20,
            "external_links": {"IMDb": f"https://www.imdb.com/title/tt{media_id}/"},
        }
        first = self._movie("301")
        second = self._movie("302")
        anime = Item.objects.create(
            media_id="9",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Anime",
            image="http://example.com/anime.jpg",
        )

        call_command("backfill_public_ratings", batch_size=1)

        first.public_rating.refresh_from_db()
        self.assertEqual(first.public_rating.imdb_value, Decimal("8.0"))
        self.assertEqual(first.public_rating.rotten_tomatoes_value, Decimal(90))
        self.assertIsNotNone(first.public_rating.ratings_updated_at)
        self.assertFalse(ItemRating.objects.filter(item=second).exists())
        anime.public_rating.refresh_from_db()
        self.assertIsNone(anime.public_rating.imdb_value)
        self.assertIsNotNone(anime.public_rating.ratings_updated_at)
        mock_request.assert_called_once()

    def _movie(self, media_id):
        return Item.objects.create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title=f"Movie {media_id}",
            image="http://example.com/a.jpg",
        )


class LetterboxdViewTests(TestCase):
    """The manual Letterboxd field on the detail page."""

    def setUp(self):
        """Log in a user who counts Letterboxd in the aggregate."""
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="boxd",
            password="test-pass",  # noqa: S106
        )
        self.user.rating_weight_letterboxd = 1
        self.user.save(update_fields=["rating_weight_letterboxd"])
        self.client.login(username="boxd", password="test-pass")  # noqa: S106
        self.metadata = patch(
            "app.providers.services.get_media_metadata",
            return_value={
                "title": "Test Movie",
                "image": "http://example.com/image.jpg",
                "max_progress": 1,
                "media_id": "238",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
            },
        )
        self.metadata.start()
        self.addCleanup(self.metadata.stop)

    def _post(self, value):
        return self.client.post(
            reverse(
                "update_letterboxd_rating",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            ),
            {"letterboxd": value},
        )

    @override_settings(OMDB_API_KEY="")
    def test_zero_is_stored_and_blank_clears_the_score(self):
        """0 is a real score. An empty field is missing and renders as a dash."""
        response = self._post("0")
        rating = ItemRating.objects.get(item__media_id="238")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(rating.letterboxd_value, Decimal("0.0"))
        self.assertEqual(rating.letterboxd_normalized, Decimal("0.0"))
        self.assertContains(response, 'text-white">0')

        response = self._post("")
        rating.refresh_from_db()

        self.assertIsNone(rating.letterboxd_value)
        self.assertIsNone(rating.letterboxd_normalized)
        self.assertContains(response, 'text-gray-400">\u2013')

    @override_settings(OMDB_API_KEY="")
    def test_detail_page_shows_a_dash_when_unrated(self):
        """The detail page shows the breakdown without inventing a zero."""
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
        self.assertContains(response, "PUBLIC RATING")
        self.assertContains(response, "\u2013")
        self.assertContains(response, 'placeholder="\u2013"')

    def test_demo_user_cannot_edit_the_shared_score(self):
        """The public demo account cannot change a shared Letterboxd score."""
        self.user.is_demo = True
        self.user.save(update_fields=["is_demo"])

        response = self._post("4")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ItemRating.objects.exists())

    def test_out_of_range_score_is_rejected(self):
        """Scores outside 0-5 are not stored."""
        response = self._post("9")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(ItemRating.objects.exists())
