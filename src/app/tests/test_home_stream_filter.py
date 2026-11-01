from types import SimpleNamespace

from django.test import SimpleTestCase

from app.home import _is_stream_available
from app.models import MediaTypes, Sources
from users.models import WATCH_PROVIDER_REGION_UNSET


def _media(source, media_type, stream_availability):
    """Build a lightweight stand-in for a BasicMedia instance."""
    item = SimpleNamespace(
        source=source,
        media_type=media_type,
        stream_availability=stream_availability,
    )
    return SimpleNamespace(item=item)


class IsStreamAvailableTest(SimpleTestCase):
    """Test the home page stream-availability filter predicate."""

    def test_available_no_charge(self):
        """No-charge bit set is always available, regardless of paid preference."""
        media = _media(Sources.TMDB.value, MediaTypes.MOVIE.value, {"US": 1})

        self.assertTrue(_is_stream_available(media, "US", include_paid=False))
        self.assertTrue(_is_stream_available(media, "US", include_paid=True))

    def test_available_only_via_paid_requires_preference(self):
        """Paid-only availability counts only when include_paid is True."""
        media = _media(Sources.TMDB.value, MediaTypes.MOVIE.value, {"US": 2})

        self.assertFalse(_is_stream_available(media, "US", include_paid=False))
        self.assertTrue(_is_stream_available(media, "US", include_paid=True))

    def test_unavailable(self):
        """A zero bitmask for the region is unavailable."""
        media = _media(Sources.TMDB.value, MediaTypes.MOVIE.value, {"US": 0})

        self.assertFalse(_is_stream_available(media, "US", include_paid=True))

    def test_non_applicable_media_type_always_shown(self):
        """Non-TMDB / non-eligible media types are never filtered out."""
        media = _media(Sources.MAL.value, MediaTypes.ANIME.value, {})

        self.assertTrue(_is_stream_available(media, "US", include_paid=False))

    def test_tmdb_but_ineligible_media_type_always_shown(self):
        """TMDB media types outside movie/tv/season (e.g. episode) are always shown."""
        media = _media(Sources.TMDB.value, MediaTypes.EPISODE.value, {"US": 0})

        self.assertTrue(_is_stream_available(media, "US", include_paid=False))

    def test_unset_region_always_shown(self):
        """With no region configured, nothing can be judged, so it's shown."""
        media = _media(Sources.TMDB.value, MediaTypes.MOVIE.value, {})

        self.assertTrue(
            _is_stream_available(
                media,
                WATCH_PROVIDER_REGION_UNSET,
                include_paid=False,
            ),
        )

    def test_missing_bitmask_fails_open(self):
        """A region never backfilled for this item is shown, not hidden."""
        media = _media(Sources.TMDB.value, MediaTypes.MOVIE.value, {"GB": 1})

        self.assertTrue(_is_stream_available(media, "US", include_paid=False))
