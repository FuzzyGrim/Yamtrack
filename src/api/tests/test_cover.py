from unittest.mock import patch

from app.helpers import apply_custom_image
from app.models import Item, MediaTypes, Sources

from .base import YamtrackApiTestCase

PROVIDER_IMAGE = "https://image.tmdb.org/t/p/w500/provider.jpg"
NEW_COVER = "https://image.tmdb.org/t/p/original/new.jpg"

MOVIE_METADATA = {
    "media_id": "701",
    "source": "tmdb",
    "media_type": "movie",
    "title": "Movie 1",
    "image": PROVIDER_IMAGE,
    "max_progress": 1,
    "details": {},
    "related": {},
}

MOVIE_IMAGES = {
    "posters": [
        {
            "image": "https://image.tmdb.org/t/p/w500/new.jpg",
            "original": NEW_COVER,
        },
    ],
    "backdrops": [],
    "logos": [],
}


@patch("api.helpers.fetch_images", return_value=MOVIE_IMAGES)
@patch("api.views.services.get_media_metadata", return_value=MOVIE_METADATA)
class MediaCoverTests(YamtrackApiTestCase):
    """Validate the custom cover endpoints."""

    def get_movie_item(self):
        """Return the movie item used by the tests."""
        return Item.objects.get(
            media_id="701",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
        )

    def put_cover(self, image):
        """Set the movie cover through the API."""
        return self.call_api(
            "put",
            "api_media_cover",
            args=(MediaTypes.MOVIE.value, "tmdb", "701"),
            payload={"image": image},
            headers=self.auth_headers,
        )

    def test_put_sets_custom_cover(self, _mock_metadata, _mock_images):
        """PUT should save the chosen image as the cover of the item."""
        response = self.put_cover(NEW_COVER)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"image": NEW_COVER, "custom_image": NEW_COVER},
        )
        item = self.get_movie_item()
        self.assertEqual(item.custom_image, NEW_COVER)
        self.assertEqual(item.image, NEW_COVER)

    def test_custom_cover_survives_metadata_sync(
        self,
        _mock_metadata,
        _mock_images,
    ):
        """Saving provider metadata should not replace the custom cover."""
        self.put_cover(NEW_COVER)

        # Same write the metadata sync does
        Item.objects.update_or_create(
            media_id="701",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            season_number=None,
            defaults={"title": "Movie 1", "image": PROVIDER_IMAGE},
        )
        item = self.get_movie_item()
        item.image = PROVIDER_IMAGE
        item.save(update_fields=["title"])

        self.assertEqual(self.get_movie_item().image, NEW_COVER)

    def test_get_and_media_detail_return_custom_cover(
        self,
        _mock_metadata,
        _mock_images,
    ):
        """GET cover and the media details should return the custom cover."""
        self.put_cover(NEW_COVER)

        cover = self.call_api(
            "get",
            "api_media_cover",
            args=(MediaTypes.MOVIE.value, "tmdb", "701"),
            headers=self.auth_headers,
        )
        detail = self.call_api(
            "get",
            "api_media_detail",
            args=(MediaTypes.MOVIE.value, "tmdb", "701"),
            headers=self.auth_headers,
        )

        self.assertEqual(cover.status_code, 200)
        self.assertEqual(cover.json()["image"], NEW_COVER)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["image"], NEW_COVER)

    def test_get_without_custom_cover_returns_provider_image(
        self,
        _mock_metadata,
        _mock_images,
    ):
        """GET cover without a custom cover should return the provider image."""
        response = self.call_api(
            "get",
            "api_media_cover",
            args=(MediaTypes.MOVIE.value, "tmdb", "701"),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"image": PROVIDER_IMAGE, "custom_image": None},
        )

    def test_delete_resets_to_provider_image(self, _mock_metadata, _mock_images):
        """DELETE should restore the provider image."""
        self.put_cover(NEW_COVER)

        response = self.call_api(
            "delete",
            "api_media_cover",
            args=(MediaTypes.MOVIE.value, "tmdb", "701"),
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"image": PROVIDER_IMAGE, "custom_image": None},
        )
        item = self.get_movie_item()
        self.assertEqual(item.custom_image, "")
        self.assertEqual(item.image, PROVIDER_IMAGE)

    def test_put_creates_item_when_missing(self, _mock_metadata, _mock_images):
        """PUT should create the item when the media is not stored yet."""
        Item.objects.filter(media_id="701", source=Sources.TMDB.value).delete()

        response = self.put_cover(NEW_COVER)

        self.assertEqual(response.status_code, 200)
        item = self.get_movie_item()
        self.assertEqual(item.title, "Movie 1")
        self.assertEqual(item.image, NEW_COVER)

    def test_put_rejects_images_not_offered(self, _mock_metadata, _mock_images):
        """PUT should only accept images from the media images endpoint."""
        for payload in (
            {"image": "https://evil.example.com/tracker.png"},
            {"image": ""},
            {},
        ):
            response = self.call_api(
                "put",
                "api_media_cover",
                args=(MediaTypes.MOVIE.value, "tmdb", "701"),
                payload=payload,
                headers=self.auth_headers,
            )
            self.assertEqual(response.status_code, 400, payload)

        self.assertEqual(self.get_movie_item().custom_image, "")

    def test_cover_unsupported_requests_return_bad_request(
        self,
        _mock_metadata,
        _mock_images,
    ):
        """Cover should reject manual items and seasons of non tv media."""
        requests = [
            ("api_media_cover", (MediaTypes.MOVIE.value, "manual", "1")),
            ("api_media_season_cover", (MediaTypes.MOVIE.value, "tmdb", "701", 1)),
        ]

        for url_name, args in requests:
            response = self.call_api(
                "get",
                url_name,
                args=args,
                headers=self.auth_headers,
            )
            self.assertEqual(response.status_code, 400)


SEASON_COVER = "https://image.tmdb.org/t/p/original/season.jpg"


class MediaSeasonCoverTests(YamtrackApiTestCase):
    """Validate the custom season cover endpoint."""

    @patch(
        "api.views.tmdb.media_images",
        return_value={
            "posters": [
                {
                    "image": "https://image.tmdb.org/t/p/w500/season.jpg",
                    "original": SEASON_COVER,
                },
            ],
        },
    )
    @patch(
        "api.views.services.get_media_metadata",
        return_value={
            "media_id": "1001",
            "source": "tmdb",
            "media_type": "season",
            "title": "TV 1",
            "season_number": 1,
            "image": PROVIDER_IMAGE,
        },
    )
    def test_put_sets_season_cover(self, _mock_metadata, mock_images):
        """PUT should set the cover of the season item."""
        response = self.call_api(
            "put",
            "api_media_season_cover",
            args=(MediaTypes.TV.value, "tmdb", "1001", 1),
            payload={"image": SEASON_COVER},
            headers=self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        item = Item.objects.get(
            media_id="1001",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
        )
        self.assertEqual(item.image, SEASON_COVER)
        mock_images.assert_called_once_with("tv", "1001", season_number="1")


class ApplyCustomImageTests(YamtrackApiTestCase):
    """Validate the helper used by the web app details pages."""

    def test_apply_custom_image_replaces_only_when_set(self):
        """The helper should only replace the image when a cover is chosen."""
        metadata = {"image": PROVIDER_IMAGE, "title": "Movie 1"}

        unchanged = apply_custom_image(
            metadata,
            MediaTypes.MOVIE.value,
            "701",
            Sources.TMDB.value,
        )
        Item.objects.filter(media_id="701", source=Sources.TMDB.value).update(
            custom_image=NEW_COVER,
        )
        changed = apply_custom_image(
            metadata,
            MediaTypes.MOVIE.value,
            "701",
            Sources.TMDB.value,
        )

        self.assertIs(unchanged, metadata)
        self.assertEqual(changed["image"], NEW_COVER)
        self.assertEqual(metadata["image"], PROVIDER_IMAGE)
