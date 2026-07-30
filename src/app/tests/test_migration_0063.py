import importlib

from django.apps import apps
from django.conf import settings
from django.test import TestCase

from app.models import Item, MediaTypes, Sources

migration_module = importlib.import_module(
    "app.migrations.0063_season_image_fallback_to_tv",
)


class SeasonImageFallbackTests(TestCase):
    """Data migration 0063 backfills season posters from the parent show."""

    def _run_migration(self):
        migration_module.fill_season_images_from_tv(apps, None)

    def test_missing_season_image_uses_tv_image(self):
        """A season without an image inherits the show's image."""
        Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image="http://example.com/tv.jpg",
        )
        season = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Breaking Bad",
            image=settings.IMG_NONE,
            season_number=1,
        )

        self._run_migration()

        season.refresh_from_db()
        self.assertEqual(season.image, "http://example.com/tv.jpg")

    def test_existing_season_image_is_kept(self):
        """A season with its own image is left untouched."""
        Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image="http://example.com/tv.jpg",
        )
        season = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Breaking Bad",
            image="http://example.com/season.jpg",
            season_number=1,
        )

        self._run_migration()

        season.refresh_from_db()
        self.assertEqual(season.image, "http://example.com/season.jpg")

    def test_season_without_parent_show_is_kept(self):
        """A season keeps the placeholder when no show image is available."""
        Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image=settings.IMG_NONE,
        )
        orphan = Item.objects.create(
            media_id="1399",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Game of Thrones",
            image=settings.IMG_NONE,
            season_number=1,
        )
        placeholder_parent = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Breaking Bad",
            image=settings.IMG_NONE,
            season_number=1,
        )

        self._run_migration()

        orphan.refresh_from_db()
        placeholder_parent.refresh_from_db()
        self.assertEqual(orphan.image, settings.IMG_NONE)
        self.assertEqual(placeholder_parent.image, settings.IMG_NONE)

    def test_other_source_show_image_is_not_reused(self):
        """A season only inherits from a show of the same source."""
        Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image="http://example.com/tv.jpg",
        )
        manual_season = Item.objects.create(
            media_id="1396",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="Manual Show",
            image=settings.IMG_NONE,
            season_number=1,
        )

        self._run_migration()

        manual_season.refresh_from_db()
        self.assertEqual(manual_season.image, settings.IMG_NONE)
