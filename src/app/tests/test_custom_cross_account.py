from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.helpers import enrich_items_with_user_data
from app.models import Item, MediaTypes, Movie, Sources, Status


class CrossAccountTrackingCustomizationTest(TestCase):
    """Regression tests for the personal cross-account search customization."""

    def test_search_enrichment_lists_all_tracking_accounts(self):
        first_user = get_user_model().objects.create_user(username="first")
        second_user = get_user_model().objects.create_user(username="second")
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/movie.jpg",
        )
        Movie.objects.create(
            item=item,
            user=first_user,
            status=Status.IN_PROGRESS.value,
            progress=0,
        )
        Movie.objects.create(
            item=item,
            user=second_user,
            status=Status.COMPLETED.value,
            progress=1,
        )

        request = MagicMock()
        request.user = first_user
        results = enrich_items_with_user_data(
            request,
            [{
                "media_id": "238",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "title": "Test Movie",
                "image": "http://example.com/movie.jpg",
            }],
            "search",
        )

        self.assertEqual(results[0]["tracking_users"], ["first", "second"])
