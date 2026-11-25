from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Item, MediaTypes, Sources

MOVIE_METADATA = {
    "media_id": "238",
    "title": "Test Movie",
    "media_type": MediaTypes.MOVIE.value,
    "source": Sources.TMDB.value,
    "image": "https://image.tmdb.org/t/p/w500/default.jpg",
    "max_progress": 1,
    "related": {"recommendations": []},
}

MOVIE_IMAGES = {
    "posters": [
        {
            "image": "https://image.tmdb.org/t/p/w500/poster.jpg",
            "original": "https://image.tmdb.org/t/p/original/poster.jpg",
            "width": 2000,
            "height": 3000,
            "aspect_ratio": 0.667,
            "language": "en",
            "vote_average": 5.8,
            "vote_count": 20,
            "source": Sources.TMDB.value,
        },
    ],
    "backdrops": [
        {
            "image": "https://image.tmdb.org/t/p/w500/backdrop.jpg",
            "original": "https://image.tmdb.org/t/p/original/backdrop.jpg",
            "width": 3840,
            "height": 2160,
            "aspect_ratio": 1.778,
            "language": None,
            "vote_average": 5.6,
            "vote_count": 12,
            "source": Sources.TMDB.value,
        },
    ],
    "screenshots": [],
    "logos": [
        {
            "image": "https://image.tmdb.org/t/p/w500/logo.png",
            "original": "https://image.tmdb.org/t/p/original/logo.png",
            "width": 800,
            "height": 310,
            "aspect_ratio": 2.58,
            "language": "en",
            "vote_average": 5.3,
            "vote_count": 3,
            "source": Sources.TMDB.value,
        },
    ],
}


@patch("api.helpers.fetch_images", return_value=MOVIE_IMAGES)
@patch("app.providers.services.get_media_metadata", return_value=MOVIE_METADATA)
class CoverViewTests(TestCase):
    """Test choosing a custom cover from the web interface."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.url_kwargs = {
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "media_id": "238",
        }
        self.details_url = reverse(
            "media_details",
            kwargs={**self.url_kwargs, "title": "test-movie"},
        )
        self.modal_url = reverse("cover_modal", kwargs=self.url_kwargs)
        self.save_url = reverse("cover_save", kwargs=self.url_kwargs)

    def create_item(self, custom_image=""):
        """Create the item of the movie, optionally with a custom cover."""
        return Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image=custom_image or MOVIE_METADATA["image"],
            custom_image=custom_image,
        )

    def test_details_page_offers_cover_change(self, *_):
        """The details page shows the change cover action for provider media."""
        response = self.client.get(self.details_url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_change_cover"])
        self.assertContains(response, "Change cover")

    def test_details_page_hides_cover_change_for_manual(self, mock_metadata, _):
        """Manual items have no images to choose from."""
        mock_metadata.return_value = {
            **MOVIE_METADATA,
            "source": Sources.MANUAL.value,
        }
        self.create_item()

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    **self.url_kwargs,
                    "source": Sources.MANUAL.value,
                    "title": "test-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_change_cover"])
        self.assertNotContains(response, "Change cover")

    def test_cover_modal_lists_images_without_logos(self, *_):
        """The modal shows the posters and backdrops but not the logos."""
        response = self.client.get(self.modal_url, {"return_url": self.details_url})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/fill_cover.html")
        self.assertEqual(
            [image_type for image_type, _ in response.context["image_groups"]],
            ["posters", "backdrops"],
        )
        self.assertContains(response, MOVIE_IMAGES["posters"][0]["original"])
        self.assertNotContains(response, "logo.png")
        self.assertIsNone(response.context["cover"]["custom_image"])

    def test_cover_modal_rejects_manual_source(self, *_):
        """Manual items cannot get a custom cover."""
        response = self.client.get(
            reverse(
                "cover_modal",
                kwargs={**self.url_kwargs, "source": Sources.MANUAL.value},
            ),
        )

        self.assertEqual(response.status_code, 400)

    def test_cover_save_sets_custom_cover(self, *_):
        """Choosing an image stores it as the custom cover of the item."""
        new_image = MOVIE_IMAGES["posters"][0]["original"]

        response = self.client.post(
            f"{self.save_url}?next={self.details_url}",
            {"image": new_image},
        )

        self.assertRedirects(response, self.details_url, fetch_redirect_response=False)
        item = Item.objects.get(media_id="238", media_type=MediaTypes.MOVIE.value)
        self.assertEqual(item.custom_image, new_image)
        self.assertEqual(item.image, new_image)

    def test_cover_save_rejects_unknown_image(self, *_):
        """Only images offered by the providers can be chosen."""
        self.create_item()

        response = self.client.post(
            f"{self.save_url}?next={self.details_url}",
            {"image": "https://example.com/not-from-the-provider.jpg"},
        )

        self.assertRedirects(response, self.details_url, fetch_redirect_response=False)
        item = Item.objects.get(media_id="238", media_type=MediaTypes.MOVIE.value)
        self.assertEqual(item.custom_image, "")
        self.assertEqual(item.image, MOVIE_METADATA["image"])

    def test_cover_save_without_image_resets_cover(self, *_):
        """Submitting without an image restores the provider image."""
        self.create_item(custom_image=MOVIE_IMAGES["posters"][0]["original"])

        response = self.client.post(f"{self.save_url}?next={self.details_url}")

        self.assertRedirects(response, self.details_url, fetch_redirect_response=False)
        item = Item.objects.get(media_id="238", media_type=MediaTypes.MOVIE.value)
        self.assertEqual(item.custom_image, "")
        self.assertEqual(item.image, MOVIE_METADATA["image"])

    def test_details_page_shows_custom_cover(self, *_):
        """The details page uses the custom cover instead of the provider image."""
        custom_image = MOVIE_IMAGES["posters"][0]["original"]
        self.create_item(custom_image=custom_image)

        response = self.client.get(self.details_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media"]["image"], custom_image)
