import re
import threading
from datetime import UTC, datetime, timedelta
from html import unescape
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.utils import OperationalError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override

from app import statistics_cache
from app.models import (
    TV,
    Album,
    AlbumArtist,
    AlbumTracker,
    Anime,
    Artist,
    ArtistMember,
    ArtistTracker,
    BoardGame,
    Book,
    CollectionEntry,
    Comic,
    ComicIssue,
    CreditRoleType,
    Episode,
    Game,
    GameArtwork,
    Item,
    ItemPersonCredit,
    ItemProviderLink,
    ItemStudioCredit,
    ItemTag,
    Manga,
    MediaTypes,
    MetadataProviderPreference,
    Movie,
    Music,
    Person,
    Podcast,
    PodcastEpisode,
    PodcastShow,
    PodcastShowTracker,
    ProviderMetadataStatus,
    Season,
    Sources,
    Status,
    Studio,
    Tag,
    Track,
)
from app.providers import services, tmdb
from app.services import game_lengths as game_length_services
from app.services.metadata_resolution import AnimeTMDBIdentity, MetadataResolutionResult
from app.tests.card_props import card_models
from events.calendar.helpers import date_parser
from integrations.models import PlexAccount
from lists.models import CustomList, CustomListItem
from users.models import DateFormatChoices, RatingScaleChoices, TimeFormatChoices


def _record_sleep_threads(mock_sleep):
    """Record which threads call the patched ``time.sleep``.

    ``time.sleep`` is process-wide, so a background thread can trip a plain
    ``assert_not_called``. Tests check only their own thread instead.
    """
    threads = []
    mock_sleep.side_effect = lambda *args, **kwargs: threads.append(
        threading.get_ident()
    )
    return threads


class MediaDetailsViewTests(TestCase):

    @staticmethod
    def _card_titles(response):
        """Return every title and title link the page's cards show."""
        models = card_models(response.content)
        return {m["title"] for m in models} | {
            link["label"] for m in models for link in m["titleLinks"]
        }
    """Test the media details views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def _use_iso_dates(self):
        self.user.date_format = DateFormatChoices.ISO_8601
        self.user.save(update_fields=["date_format"])

    def _assert_activity_subtitle_without_stats_cards(
        self,
        response,
        primary_text,
        date_text,
        duration_text=None,
    ):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, primary_text)
        self.assertContains(response, date_text)
        if duration_text is not None:
            self.assertContains(response, duration_text)
        self.assertNotContains(response, "FIRST PLAYED")
        self.assertNotContains(response, "LAST PLAYED")
        self.assertNotContains(response, "WATCHED HOURS")
        self.assertNotContains(response, "TOTAL HOURS")
        self.assertNotContains(response, "AVG TIME")

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_view(self, mock_get_metadata):
        """Test the media details view."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }

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
        self.assertTemplateUsed(response, "app/media_details.html")

        self.assertIn("media", response.context)
        self.assertEqual(response.context["media"]["title"], "Test Movie")
        self.assertContains(
            response,
            f'hx-get="{reverse("activity_sessions_modal")}?media_type=movie&media_id=238&source=tmdb"',
            html=False,
        )
        self.assertContains(
            response,
            'class="order-1 mt-5 mb-6 flex flex-col gap-3 md:order-2 md:flex-row md:flex-wrap md:items-center"',
            html=False,
        )

        mock_get_metadata.assert_called_once_with(
            MediaTypes.MOVIE.value,
            "238",
            Sources.TMDB.value,
            language="en",
            user=self.user,
        )

    @patch("integrations.tasks.fetch_collection_metadata_for_item.delay")
    @patch("app.views.helpers.enrich_items_with_user_data")
    @patch("app.views.run_retryable_db_operation")
    @patch("app.views.metadata_utils.apply_item_metadata", return_value=[])
    @patch("app.providers.services.get_media_metadata")
    def test_media_details_shell_defers_secondary_content(
        self,
        mock_get_metadata,
        _mock_apply_item_metadata,
        mock_run_retryable_db_operation,
        mock_enrich_items,
        mock_fetch_delay,
    ):
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "source_url": "https://www.themoviedb.org/movie/238",
            "image": "http://example.com/image.jpg",
            "synopsis": "Test overview",
            "max_progress": 1,
            "details": {},
            "related": {
                "recommendations": [
                    {
                        "media_id": "550",
                        "title": "Fight Club",
                        "media_type": MediaTypes.MOVIE.value,
                        "source": Sources.TMDB.value,
                        "image": "http://example.com/fight-club.jpg",
                    }
                ],
            },
            "cast": [
                {
                    "name": "Actor One",
                    "image": "http://example.com/person.jpg",
                    "role": "Lead",
                }
            ],
            "crew": [],
            "studios_full": [],
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            end_date=datetime(2026, 3, 20, 18, 0, tzinfo=UTC),
            notes="## Great notes\n\nThis movie rules.",
        )
        PlexAccount.objects.create(
            user=self.user,
            plex_token="plex-token",
            plex_username="plex-user",
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": item.media_id,
                    "title": "test-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_details.html")
        self.assertContains(response, 'id="detail-secondary-content"', html=False)
        self.assertContains(response, "fragment=secondary")
        # The loading placeholder stacks like the real content (metadata first on phones).
        self.assertContains(
            response, "detail-secondary-layout flex flex-col md:flex-row", html=False
        )
        self.assertNotContains(response, "flex-col-reverse", html=False)
        self.assertNotContains(response, "Your Notes")
        self.assertNotContains(
            response, '<h2 class="text-xl font-bold">Collection</h2>', html=False
        )
        mock_run_retryable_db_operation.assert_not_called()
        mock_enrich_items.assert_not_called()
        mock_fetch_delay.assert_not_called()

    @patch("integrations.tasks.fetch_collection_metadata_for_item.delay")
    @patch("app.views.credits.sync_item_credits_from_metadata")
    @patch("app.views.metadata_utils.apply_item_metadata", return_value=[])
    @patch("app.providers.services.get_media_metadata")
    def test_media_details_secondary_fragment_renders_deferred_sections(
        self,
        mock_get_metadata,
        _mock_apply_item_metadata,
        _mock_sync_credits,
        mock_fetch_delay,
    ):
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "source_url": "https://www.themoviedb.org/movie/238",
            "image": "http://example.com/image.jpg",
            "synopsis": "Test overview",
            "max_progress": 1,
            "details": {},
            "related": {
                "recommendations": [
                    {
                        "media_id": "550",
                        "title": "Fight Club",
                        "media_type": MediaTypes.MOVIE.value,
                        "source": Sources.TMDB.value,
                        "image": "http://example.com/fight-club.jpg",
                    }
                ],
            },
            "cast": [
                {
                    "name": "Actor One",
                    "image": "http://example.com/person.jpg",
                    "role": "Lead",
                }
            ],
            "crew": [],
            "studios_full": [],
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            end_date=datetime(2026, 3, 20, 18, 0, tzinfo=UTC),
            notes="## Great notes\n\nThis movie rules.",
        )
        PlexAccount.objects.create(
            user=self.user,
            plex_token="plex-token",
            plex_username="plex-user",
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": item.media_id,
                    "title": "test-movie",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "app/components/detail_secondary_content.html"
        )
        self.assertContains(
            response, '<h2 class="text-xl font-bold">Your Notes</h2>', html=False
        )
        self.assertContains(
            response, '<h2 class="text-xl font-bold">Collection</h2>', html=False
        )
        self.assertContains(response, "media-grid-row-detail-cast", html=False)
        self.assertNotContains(
            response,
            'class="flex flex-col md:flex-row gap-8 md:gap-10 mb-2 md:mb-8"',
            html=False,
        )
        mock_fetch_delay.assert_called_once_with(
            user_id=self.user.id,
            item_id=item.id,
            lookup_policy="cached_only",
        )

    @patch("integrations.tasks.fetch_collection_metadata_for_item.delay")
    @patch("app.views.credits.sync_item_credits_from_metadata")
    @patch("app.views.metadata_utils.apply_item_metadata", return_value=[])
    @patch("app.providers.services.get_media_metadata")
    def test_media_details_secondary_fragment_syncs_reading_genres_without_error(
        self,
        mock_get_metadata,
        _mock_apply_item_metadata,
        _mock_sync_credits,
        _mock_fetch_delay,
    ):
        """Regression: syncing stale genres on a tracked reading item must not 500.

        When a book/comic/manga's stored genres differ from the freshly
        resolved metadata, the secondary fragment updates the item and
        invalidates the affected reading-activity statistics days. That path
        calls ``_collect_reading_activity_day_keys``, which previously raised
        ``NameError`` because it was never imported into this module, returning
        a 500 that left the lazy fragment's skeleton loaders spinning forever.
        """
        mock_get_metadata.return_value = {
            "media_id": "119735",
            "title": "Test Manga",
            "media_type": MediaTypes.MANGA.value,
            "source": Sources.MAL.value,
            "source_url": "https://myanimelist.net/manga/119735",
            "image": "http://example.com/image.jpg",
            "synopsis": "Test synopsis",
            "max_progress": 145,
            # Genres deliberately differ from the stored item below so the
            # genre-sync branch fires and marks genres as updated.
            "genres": ["Comedy", "Romance"],
            "details": {},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }
        item = Item.objects.create(
            media_id="119735",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Test Manga",
            image="http://example.com/image.jpg",
            genres=["Drama"],
        )
        Manga.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=10,
            start_date=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            end_date=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.MANGA.value,
                    "media_id": item.media_id,
                    "title": "test-manga",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "app/components/detail_secondary_content.html"
        )
        # The genre-sync branch ran and persisted the resolved genres.
        item.refresh_from_db()
        self.assertEqual(item.genres, ["Comedy", "Romance"])

    @patch("app.providers.services.session.get")
    def test_media_details_renders_service_unavailable_page_when_tmdb_is_unreachable(
        self,
        mock_get,
    ):
        mock_get.side_effect = requests.exceptions.ConnectionError("dns failure")

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "94998",
                    "title": "63-up",
                },
            ),
        )

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Service Unavailable", status_code=503)
        self.assertContains(
            response,
            "Could not reach The Movie Database",
            status_code=503,
        )
        self.assertContains(
            response,
            "Provider: The Movie Database",
            status_code=503,
        )
        self.assertContains(
            response,
            "Provider status: unavailable",
            status_code=503,
        )

    @patch("app.media_details_views._build_series_graph_data", return_value=None)
    @patch("app.providers.services.get_media_metadata")
    def test_secondary_fragment_skips_series_graph_scans(
        self,
        mock_get_metadata,
        mock_graph,
    ):
        """Only the shell renders the series graphs, so only it scans episodes."""
        item = Item.objects.create(
            media_id="279978",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Graph Show",
            image="https://example.com/graph-show.jpg",
        )
        TV.objects.create(item=item, user=self.user, status=Status.IN_PROGRESS.value)
        mock_get_metadata.return_value = {
            "media_id": item.media_id,
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "title": "Graph Show",
            "image": item.image,
            "synopsis": "",
            "details": {},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }
        url = reverse(
            "media_details",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.TV.value,
                "media_id": item.media_id,
                "title": "graph-show",
            },
        )

        response = self.client.get(url, {"fragment": "secondary"})
        self.assertEqual(response.status_code, 200)
        mock_graph.assert_not_called()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_graph.call_count, 3)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_keeps_tracked_tmdb_show_available_when_provider_returns_404(
        self,
        mock_get_metadata,
    ):
        """A tracked show remains usable when TMDB has removed its record."""
        item = Item.objects.create(
            media_id="279977",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Deleted Show",
            image="https://example.com/deleted-show.jpg",
            synopsis="Stored synopsis",
        )
        tv = TV.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        self.user.title_display_preference = "original"
        self.user.save(update_fields=["title_display_preference"])

        not_found_response = requests.Response()
        not_found_response.status_code = requests.codes.not_found
        mock_get_metadata.side_effect = services.ProviderAPIError(
            Sources.TMDB.value,
            requests.exceptions.HTTPError(response=not_found_response),
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": item.media_id,
                    "title": "deleted-show",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "app/components/detail_secondary_content.html",
        )
        self.assertEqual(response.context["media"]["title"], "Deleted Show")
        self.assertEqual(response.context["media"]["synopsis"], "Stored synopsis")
        self.assertEqual(response.context["current_instance"], tv)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_keeps_source_link_when_provider_is_unreachable(
        self,
        mock_get_metadata,
    ):
        """The "Source" chip survives falling back to stored Item metadata (#931-style)."""
        service_unavailable_response = requests.Response()
        service_unavailable_response.status_code = requests.codes.service_unavailable
        mock_get_metadata.side_effect = services.ProviderAPIError(
            Sources.IGDB.value,
            requests.exceptions.HTTPError(response=service_unavailable_response),
        )

        item = Item.objects.create(
            media_id="1473",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Zone of the Enders: The 2nd Runner",
            image="https://example.com/zoe.jpg",
            synopsis="Stored synopsis",
            source_url="https://www.igdb.com/games/zone-of-the-enders-the-2nd-runner",
        )
        Game.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": item.media_id,
                    "title": "zone-of-the-enders-the-2nd-runner",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        source_sections = [
            section
            for section in response.context["detail_link_sections"]
            if section["title"] in ("Source", "Tracking Source")
        ]
        self.assertTrue(
            source_sections, "Expected a Source link section in the fallback"
        )
        self.assertEqual(
            source_sections[0]["entries"][0]["url"],
            "https://www.igdb.com/games/zone-of-the-enders-the-2nd-runner",
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_action_row_before_chips_and_description(
        self, mock_get_metadata
    ):
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "synopsis": "Test overview",
            "score": 7.6,
            "score_count": 42000,
            "details": {},
            "related": {},
        }

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
        content = response.content.decode()
        self.assertIn(
            'class="order-1 mb-6 flex flex-col gap-3 md:order-2 md:flex-row md:flex-wrap md:items-center"',
            content,
        )
        self.assertIn(
            'class="flex w-full flex-wrap items-center gap-2 md:w-auto"',
            content,
        )
        self.assertIn(
            'class="inline-flex h-11 w-full items-center justify-center rounded-xl border border-[var(--color-surface-border)] bg-[var(--color-surface)] text-[var(--color-text)] shadow-sm transition-colors duration-200 hover:bg-[var(--color-surface-muted)] cursor-pointer md:size-11 md:w-11"',
            content,
        )
        self.assertIn("Add to tracker", content)
        self.assertIn('title="Add to custom lists"', content)
        self.assertIn('title="Manage tags"', content)
        self.assertIn('title="Sync metadata"', content)
        self.assertIn('method="post"', content)
        self.assertIn(
            reverse(
                "sync_metadata",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                },
            ),
            content,
        )
        self.assertNotIn('hx-post="', content)
        self.assertNotIn('<h2 class="text-xl font-bold mb-4">Actions</h2>', content)
        self.assertNotIn("mt-4 p-3 rounded-lg w-full flex items-center", content)
        self.assertIn("mobilePreviewText", content)
        self.assertIn("fullSynopsisText:", content)
        self.assertIn(
            'class="inline whitespace-nowrap align-baseline ml-1 text-[13px] font-medium text-[var(--color-link)] hover:text-[var(--color-link-hover)] focus:outline-none transition-colors cursor-pointer"',
            content,
        )
        self.assertIn(
            'class="mt-2 inline-flex text-[13px] font-medium text-[var(--color-link)] hover:text-[var(--color-link-hover)] focus:outline-none transition-colors cursor-pointer sm:hidden"',
            content,
        )
        self.assertIn(
            'class="hidden text-[var(--color-link)] hover:text-[var(--color-link-hover)] text-sm mt-2 focus:outline-none transition-colors cursor-pointer sm:inline-flex"',
            content,
        )
        # Movies/TV/seasons render the carousel-column layout: title + action row
        # come first, score chips move below them, then the synopsis.
        self.assertLess(content.index("Add to tracker"), content.index("tmdb-logo.png"))
        self.assertLess(content.index("tmdb-logo.png"), content.index("Test overview"))

    @patch("app.providers.services.get_media_metadata")
    def test_genres_move_to_tag_tooltip_only_when_viewer_has_one(
        self, mock_get_metadata
    ):
        """The carousel layout hides the sidebar Genres card, so only a page
        that also renders the tag tooltip (not a public view) may mark it hidden.
        """
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "synopsis": "Test overview",
            "genres": ["Action", "Science Fiction"],
            "details": {},
            "related": {},
        }
        detail_url = reverse(
            "media_details",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "media_id": "238",
                "title": "test-movie",
            },
        )

        response = self.client.get(detail_url, {"fragment": "secondary"})
        self.assertContains(response, "detail-sidebar-genres--in-tooltip")
        genre_section = response.context["detail_tag_sections"][0]
        self.assertEqual(genre_section["title"], "Genres")
        self.assertEqual(
            [entry["label"] for entry in genre_section["entries"]],
            ["Action", "Science Fiction"],
        )

        self.client.logout()
        response = self.client.get(detail_url, {"fragment": "secondary"})
        self.assertContains(response, "detail-sidebar-genres")
        self.assertNotContains(response, "detail-sidebar-genres--in-tooltip")

    @patch("app.providers.services.get_media_metadata")
    def test_phone_header_hooks_and_year_runtime_line(self, mock_get_metadata):
        """The phone header (compact or backdrop hero) hangs off these classes."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "synopsis": "Test overview",
            "genres": [],
            "details": {"release_date": "2020-03-05", "runtime": "2h 6min"},
            "related": {},
        }
        detail_url = reverse(
            "media_details",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "media_id": "238",
                "title": "test-movie",
            },
        )

        content = self.client.get(detail_url).content.decode()

        # Movies and TV reserve the hero up front so the page does not jump
        # when the carousel fragment brings the backdrop.
        self.assertIn('class="detail-carousel-grid detail-hero-pending"', content)
        self.assertIn('hx-trigger="load"', content)
        self.assertIn("detail-hero-waiting", content)
        self.assertIn("detail-carousel-grid__title", content)
        self.assertIn("detail-carousel-grid__actions", content)
        self.assertIn('<p class="detail-mobile-meta md:hidden">2020 · 2h 6min</p>', content)

    @patch("app.providers.services.get_media_metadata")
    def test_game_header_starts_compact_without_a_reserved_hero(self, mock_get_metadata):
        mock_get_metadata.return_value = {
            "media_id": "123",
            "title": "Test Game",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "image": "http://example.com/game.jpg",
            "synopsis": "Test overview",
            "genres": [],
            "details": {},
            "related": {},
        }
        detail_url = reverse(
            "media_details",
            kwargs={
                "source": Sources.IGDB.value,
                "media_type": MediaTypes.GAME.value,
                "media_id": "123",
                "title": "test-game",
            },
        )

        content = self.client.get(detail_url).content.decode()

        self.assertIn("detail-carousel-grid", content)
        self.assertIn('class="detail-carousel-grid"', content)
        self.assertNotIn("detail-carousel-grid detail-hero-pending", content)
        self.assertNotIn("detail-mobile-meta", content)

    @patch("app.providers.services.get_media_metadata")
    def test_warm_game_gallery_and_logo_render_in_initial_layout(self, metadata):
        media_id = "987654"
        metadata.return_value = {
            "media_id": media_id,
            "title": "Warm Game",
            "media_type": "game",
            "source": "igdb",
            "image": "https://x/poster.jpg",
            "synopsis": "",
            "genres": [],
            "details": {},
            "related": {},
        }
        cache_key = f"igdb_carousel_v4_{media_id}"
        cache.set(cache_key, {"video": None, "photos": [], "hero_image_id": None}, 60)
        self.addCleanup(cache.delete, cache_key)
        GameArtwork.objects.create(
            source="igdb",
            media_id=media_id,
            logo_url="https://x/logo.png",
            backdrop_url="https://x/hero.jpg",
            backdrop_provider="steamgriddb",
        )
        url = reverse(
            "media_details",
            kwargs={
                "source": "igdb",
                "media_type": "game",
                "media_id": media_id,
                "title": "warm-game",
            },
        )
        with (
            patch("lists.models.CustomList._get_igdb_carousel_media") as gallery,
            patch("app.game_artwork.gallery", return_value=[]),
        ):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("detail-mobile-hero md:hidden", content)
        self.assertIn("min-h-[12rem] detail-carousel-preview--overview", content)
        self.assertIn('src="https://x/logo.png"', content)
        self.assertEqual(content.count('id="detail-carousel-logo-slot"'), 1)
        self.assertNotIn('hx-trigger="load delay:100ms"', content)
        self.assertNotIn('class="detail-carousel-grid detail-hero-pending"', content)
        gallery.assert_not_called()

    @patch("app.providers.services.get_media_metadata")
    def test_comic_volume_issue_rows_render_shared_action_buttons(
        self, mock_get_metadata
    ):
        volume_metadata = {
            "media_id": "500",
            "title": "Test Volume",
            "media_type": MediaTypes.COMIC.value,
            "source": Sources.COMICVINE.value,
            "image": "http://example.com/volume.jpg",
            "issues": [
                {
                    "media_id": 114214,
                    "source": Sources.COMICVINE.value,
                    "media_type": MediaTypes.COMIC_ISSUE.value,
                    "title": "Tracked Issue",
                    "issue_number": "1",
                    "cover_date": "2024-01-01",
                    "image": "http://example.com/issue.jpg",
                },
                {
                    "media_id": 114215,
                    "source": Sources.COMICVINE.value,
                    "media_type": MediaTypes.COMIC_ISSUE.value,
                    "title": "Untracked Issue",
                    "issue_number": "2",
                    "cover_date": "2024-02-01",
                    "image": "http://example.com/issue2.jpg",
                },
            ],
        }
        issue_metadata = {
            "media_id": "114214",
            "title": "Tracked Issue",
            "media_type": MediaTypes.COMIC_ISSUE.value,
            "source": Sources.COMICVINE.value,
            "image": "http://example.com/issue.jpg",
            "max_progress": 1,
            "details": {},
        }
        mock_get_metadata.side_effect = lambda requested_media_type, *_args, **_kwargs: (
            issue_metadata
            if requested_media_type == MediaTypes.COMIC_ISSUE.value
            else volume_metadata
        )

        volume_item = Item.objects.create(
            media_id="500",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            title="Test Volume",
            image="http://example.com/volume.jpg",
        )
        issue_item = Item.objects.create(
            media_id="114214",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC_ISSUE.value,
            title="Tracked Issue",
            image="http://example.com/issue.jpg",
        )
        tracked_issue = ComicIssue.objects.create(
            item=issue_item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=timezone.now(),
            score=8,
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.COMICVINE.value,
                    "media_type": MediaTypes.COMIC.value,
                    "media_id": "500",
                    "title": "test-volume",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse(
                "lists_modal",
                args=[Sources.COMICVINE.value, MediaTypes.COMIC_ISSUE.value, "114214"],
            ),
        )
        self.assertContains(
            response,
            reverse(
                "lists_modal",
                args=[Sources.COMICVINE.value, MediaTypes.COMIC_ISSUE.value, "114215"],
            ),
        )
        self.assertContains(
            response,
            reverse(
                "history_modal",
                args=[Sources.COMICVINE.value, MediaTypes.COMIC_ISSUE.value, "114214"],
            ),
        )
        self.assertContains(
            response,
            reverse(
                "update_media_score",
                args=[MediaTypes.COMIC_ISSUE.value, tracked_issue.id],
            ),
        )
        self.assertContains(response, 'x-data="{ trackOpen: false }"', count=2)
        self.assertContains(response, "data-detail-children-root")
        self.assertContains(response, "data-detail-children-legacy")
        self.assertContains(
            response,
            'class="fixed inset-0 bg-black/50 flex items-center justify-center z-50"',
        )
        self.assertContains(
            response,
            'style="width: min(96vw, 72rem);"',
        )

    @patch("app.providers.services.get_media_metadata")
    def test_comic_issue_details_renders_collection_section(self, mock_get_metadata):
        """The comic issue detail page should expose collection add/summary UI."""
        mock_get_metadata.return_value = {
            "media_id": "114214",
            "title": "Tracked Issue",
            "media_type": MediaTypes.COMIC_ISSUE.value,
            "source": Sources.COMICVINE.value,
            "image": "http://example.com/issue.jpg",
            "max_progress": 1,
            "details": {
                "volume_id": "500",
                "volume_name": "Test Volume",
                "issue_number": "1",
            },
        }

        issue_item = Item.objects.create(
            media_id="114214",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC_ISSUE.value,
            title="Tracked Issue",
            image="http://example.com/issue.jpg",
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=issue_item,
            media_type="physical",
            purchase_price=4.99,
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.COMICVINE.value,
                    "media_type": MediaTypes.COMIC_ISSUE.value,
                    "media_id": "114214",
                    "title": "tracked-issue",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/comic_issue_details.html")
        self.assertTrue(response.context["has_collection_data"])
        self.assertIsNotNone(response.context["collection_entry"])
        self.assertContains(response, "COLLECTION")
        self.assertContains(
            response,
            reverse(
                "collection_modal",
                args=[
                    Sources.COMICVINE.value,
                    MediaTypes.COMIC_ISSUE.value,
                    "114214",
                ],
            ),
        )
        self.assertNotContains(response, "No collection data available")

    @patch("app.providers.services.get_media_metadata")
    def test_comic_issue_details_renders_collection_empty_state(
        self, mock_get_metadata
    ):
        """An untracked-for-collection comic issue shows the empty state, not stats fields."""
        mock_get_metadata.return_value = {
            "media_id": "114215",
            "title": "Untracked Issue",
            "media_type": MediaTypes.COMIC_ISSUE.value,
            "source": Sources.COMICVINE.value,
            "image": "http://example.com/issue2.jpg",
            "max_progress": 1,
            "details": {
                "volume_id": "500",
                "volume_name": "Test Volume",
                "issue_number": "2",
            },
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.COMICVINE.value,
                    "media_type": MediaTypes.COMIC_ISSUE.value,
                    "media_id": "114215",
                    "title": "untracked-issue",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_collection_data"])
        self.assertContains(response, "No collection data available")

    @patch("app.providers.services.get_media_metadata")
    def test_related_sections_and_steam_dlcs_render_shared_island(
        self, mock_get_metadata
    ):
        mock_get_metadata.return_value = {
            "media_id": "100",
            "title": "Test Game",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "image": "http://example.com/game.jpg",
            "synopsis": "Test game",
            "details": {},
            "related": {
                "dlcs": [
                    {
                        "media_id": "101",
                        "title": "Test DLC",
                        "media_type": MediaTypes.GAME.value,
                        "source": Sources.IGDB.value,
                        "image": "http://example.com/dlc.jpg",
                    }
                ],
                "standalone_expansions": [
                    {
                        "media_id": "102",
                        "title": "Test Expansion",
                        "media_type": MediaTypes.GAME.value,
                        "source": Sources.IGDB.value,
                        "image": "http://example.com/expansion.jpg",
                    }
                ],
            },
            "steam_dlcs": [
                {
                    "title": "Steam DLC",
                    "image": "http://example.com/steam.jpg",
                    "url": "https://store.steampowered.com/app/1/",
                }
            ],
            "cast": [],
            "crew": [],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "100",
                    "title": "test-game",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("data-detail-related-root", content)
        self.assertIn(">DLCs</h2>", content)
        self.assertIn(">Standalone Expansions</h2>", content)
        self.assertIn('"steamDlcs"', content)
        self.assertIn("https://store.steampowered.com/app/1/", content)
        self.assertIn('"title": "Test DLC"', content)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_related_sections_use_mobile_card_grid_preferences(
        self, mock_get_metadata
    ):
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Show",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "synopsis": "Test overview",
            "details": {},
            "related": {
                "seasons": [
                    {
                        "media_id": "238",
                        "title": "Test Show",
                        "media_type": MediaTypes.SEASON.value,
                        "source": Sources.TMDB.value,
                        "season_number": 1,
                        "season_title": "Season 1",
                        "image": "http://example.com/season1.jpg",
                    }
                ],
            },
            "cast": [
                {
                    "name": "Actor One",
                    "image": "http://example.com/person.jpg",
                    "role": "Lead",
                }
            ],
            "crew": [
                {
                    "name": "Crew One",
                    "image": "http://example.com/person2.jpg",
                    "department": "Directing",
                }
            ],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "238",
                    "title": "test-show",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(
            'class="detail-secondary-layout flex flex-col md:flex-row gap-6 md:gap-10"', content
        )
        # Seasons render as the shared scrollable rail, not the old detail grid.
        self.assertIn('data-home-row-wrapper="true"', content)
        self.assertIn("home-row-card", content)
        self.assertIn(
            'class="detail-secondary-sidebar w-full md:w-1/4 md:max-w-[250px] mx-auto lg:mx-0 flex flex-col"', content
        )
        self.assertIn('class="w-full md:w-3/4"', content)
        # Phones stack in DOM order: the metadata column comes before the content column.
        self.assertNotIn("flex-col-reverse", content)
        # Watch providers stay first on phones, directly under the synopsis.
        self.assertIn('class="md:hidden -order-2"', content)
        self.assertLess(
            content.index('class="detail-secondary-sidebar w-full md:w-1/4 md:max-w-[250px] mx-auto lg:mx-0 flex flex-col"'),
            content.index('class="w-full md:w-3/4"'),
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_notes_as_section_above_related_content(
        self, mock_get_metadata
    ):
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "synopsis": "Test overview",
            "details": {},
            "related": {},
            "cast": [
                {
                    "name": "Actor One",
                    "image": "http://example.com/person.jpg",
                    "role": "Lead",
                }
            ],
            "crew": [],
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            end_date=datetime(2026, 3, 20, 18, 0, tzinfo=UTC),
            notes="## Great notes\n\nThis movie rules.",
        )

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
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "app/components/detail_secondary_content.html"
        )
        content = response.content.decode()
        self.assertContains(
            response, '<h2 class="text-xl font-bold">Your Notes</h2>', html=False
        )
        self.assertContains(response, 'aria-label="Edit notes"', html=False)
        self.assertContains(
            response,
            'style="max-height: 12rem; overflow: hidden;"',
            html=False,
        )
        self.assertContains(
            response,
            ":style=\"isExpanded ? 'max-height: none; overflow: visible;' : 'max-height: 12rem; overflow: hidden;'\"",
            html=False,
        )
        self.assertLess(content.index("Your Notes"), content.index("Cast"))
        self.assertNotIn(">Edit<", content)
        self.assertNotIn("YOUR NOTES", content)

    @patch("app.providers.services.get_media_metadata")
    def test_public_media_notes_require_the_originating_list(self, mock_get_metadata):
        """Public media notes require the exact public list and enabled setting."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "synopsis": "Test overview",
            "details": {},
            "related": {},
            "cast": [],
            "crew": [],
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            notes="Public list note",
        )
        notes_list = CustomList.objects.create(
            name="Notes List",
            owner=self.user,
            visibility="public",
            include_notes=True,
        )
        hidden_list = CustomList.objects.create(
            name="Hidden List",
            owner=self.user,
            visibility="public",
            include_notes=False,
        )
        CustomListItem.objects.create(custom_list=notes_list, item=item)
        CustomListItem.objects.create(custom_list=hidden_list, item=item)

        detail_url = reverse(
            "media_details",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "media_id": "238",
                "title": "test-movie",
            },
        )
        self.client.logout()

        response = self.client.get(
            detail_url,
            {
                "fragment": "secondary",
                "public_view": "1",
                "public_list": hidden_list.public_reference,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Public list note")

        response = self.client.get(
            detail_url,
            {
                "fragment": "secondary",
                "public_view": "1",
                "public_list": notes_list.public_reference,
            },
        )
        self.assertContains(response, "Public list note")

        response = self.client.get(
            detail_url,
            {"fragment": "secondary", "public_view": "1", "public_list": "999999"},
        )
        self.assertNotContains(response, "Public list note")

        response = self.client.get(detail_url, {"fragment": "secondary"})
        self.assertNotContains(response, "Public list note")

    @patch("app.services.music.needs_discography_sync", return_value=False)
    @patch("app.services.music_scrobble.dedupe_artist_albums")
    @patch("app.providers.musicbrainz.get_artist")
    def test_music_artist_details_renders_shared_media_details_template(
        self,
        mock_get_artist,
        _mock_dedupe_artist_albums,
        _mock_needs_discography_sync,
    ):
        artist = Artist.objects.create(
            name="Test Artist",
            musicbrainz_id="artist-mbid",
            image="http://example.com/artist.jpg",
            discography_synced_at=timezone.now(),
        )
        Album.objects.create(
            title="Debut Album",
            artist=artist,
            musicbrainz_release_id="release-mbid",
            release_date=datetime(2024, 1, 15, tzinfo=UTC).date(),
            image="http://example.com/album.jpg",
        )
        ArtistTracker.objects.create(
            user=self.user,
            artist=artist,
            status=Status.IN_PROGRESS.value,
            score=8,
            notes="Artist notes",
            start_date=datetime(2026, 1, 10, 18, 0, tzinfo=UTC),
        )
        mock_get_artist.return_value = {
            "type": "Group",
            "country": "US",
            "genres": [{"name": "indie"}],
            "tags": [],
            "rating": 4.2,
            "rating_count": 132,
            "bio": "Artist biography",
            "image": "http://example.com/artist.jpg",
        }

        response = self.client.get(
            reverse(
                "music_artist_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "test-artist",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_details.html")
        self.assertNotContains(response, "app/music_artist_detail.html")
        self.assertEqual(response.context["music_detail_kind"], "artist")
        self.assertContains(response, "Discography")
        self.assertContains(response, "Debut Album")
        self.assertContains(
            response,
            f'hx-get="{reverse("artist_track_modal", args=[artist.id])}?instance_id=',
            html=False,
        )

    @patch("app.services.music.needs_discography_sync", return_value=False)
    @patch("app.services.music_scrobble.dedupe_artist_albums")
    @patch("app.providers.musicbrainz.get_artist")
    def test_music_artist_cover_refresh_preserves_scores_and_loaded_covers(
        self,
        mock_get_artist,
        _mock_dedupe_artist_albums,
        _mock_needs_discography_sync,
    ):
        artist = Artist.objects.create(
            name="Refresh Artist",
            musicbrainz_id="refresh-artist-mbid",
            image="http://example.com/artist.jpg",
            discography_synced_at=timezone.now(),
        )
        scored_album = Album.objects.create(
            title="Scored Album",
            artist=artist,
            musicbrainz_release_id="scored-release-mbid",
            image="http://example.com/scored-album.jpg",
        )
        Album.objects.create(
            title="Missing Cover Album",
            artist=artist,
            musicbrainz_release_id="missing-cover-release-mbid",
            image="",
        )
        AlbumTracker.objects.create(
            user=self.user,
            album=scored_album,
            status=Status.COMPLETED.value,
            score=7.5,
        )
        mock_get_artist.return_value = {
            "type": "Group",
            "country": "US",
            "genres": [],
            "tags": [],
            "rating": None,
            "rating_count": 0,
            "bio": "",
            "image": "http://example.com/artist.jpg",
        }

        detail_response = self.client.get(
            reverse(
                "music_artist_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "refresh-artist",
                },
            ),
        )

        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "7.5")
        self.assertContains(
            detail_response,
            reverse("prefetch_artist_covers", args=[artist.id]),
        )
        # The page render arms an early poll; the swapped-in fragment must
        # not, or every swap re-fires `load` and polls once per second.
        self.assertContains(
            detail_response, 'hx-trigger="load delay:500ms, every 5s"'
        )
        self.assertContains(
            detail_response,
            f'id="album-cover-{scored_album.id}"',
            html=False,
        )
        self.assertContains(detail_response, "hx-preserve", html=False)

        with patch("app.tasks.prefetch_album_covers_batch.delay") as mock_delay:
            refresh_response = self.client.get(
                reverse("prefetch_artist_covers", args=[artist.id]),
            )

        self.assertEqual(refresh_response.status_code, 200)
        mock_delay.assert_called_once_with([artist.id], limit_per_artist=None)
        self.assertContains(refresh_response, "7.5")
        self.assertContains(
            refresh_response,
            f'id="album-cover-{scored_album.id}"',
            html=False,
        )
        self.assertContains(refresh_response, "hx-preserve", html=False)
        self.assertContains(refresh_response, "Refreshing cover art")
        self.assertContains(refresh_response, 'hx-trigger="every 5s"')
        self.assertNotContains(refresh_response, "load delay")

    @patch("app.services.music.needs_discography_sync", return_value=False)
    @patch("app.services.music_scrobble.dedupe_artist_albums")
    @patch("app.providers.musicbrainz.get_artist")
    def test_music_artist_details_renders_band_members(
        self,
        mock_get_artist,
        _mock_dedupe_artist_albums,
        _mock_needs_discography_sync,
    ):
        band = Artist.objects.create(
            name="Test Band",
            musicbrainz_id="band-mbid",
            discography_synced_at=timezone.now(),
        )
        member = Artist.objects.create(
            name="Test Member",
            musicbrainz_id="member-mbid",
        )
        other_band = Artist.objects.create(
            name="Other Band",
            musicbrainz_id="other-band-mbid",
            discography_synced_at=timezone.now(),
        )
        ArtistMember.objects.create(
            band=band,
            member=member,
            role="guitar",
            is_current=True,
        )
        ArtistMember.objects.create(
            band=other_band,
            member=band,
            role="vocals",
            is_current=False,
        )
        mock_get_artist.return_value = {
            "type": "Group",
            "country": "US",
            "genres": [],
            "tags": [],
            "rating": None,
            "rating_count": 0,
            "bio": "",
            "image": "",
            "members": [],
        }

        response = self.client.get(
            reverse(
                "music_artist_details",
                kwargs={
                    "artist_id": band.id,
                    "artist_slug": "test-band",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Members")
        self.assertContains(response, "Test Member")
        self.assertContains(response, "guitar")
        self.assertContains(response, "Also In")
        self.assertContains(response, "Other Band")
        self.assertContains(response, "Former")
        # Members/Also In use the shared .media-card grid styling, not
        # the earlier bespoke circular-avatar markup.
        self.assertContains(response, 'class="media-card')
        self.assertContains(response, "aspect-[2/3]")
        # Related artists start with no image, so the async photo-refresh
        # poll should be wired up on the container.
        self.assertContains(
            response,
            reverse("prefetch_artist_relation_images", args=[band.id]),
        )
        self.assertContains(response, "Refreshing photos")

        poll_response = self.client.get(
            reverse("prefetch_artist_relation_images", args=[band.id]),
        )
        self.assertEqual(poll_response.status_code, 200)
        self.assertNotContains(
            poll_response,
            reverse("prefetch_artist_relation_images", args=[band.id]),
        )
        self.assertNotContains(poll_response, "Refreshing photos")

    @patch("app.services.music.needs_discography_sync", return_value=False)
    @patch("app.services.music_scrobble.dedupe_artist_albums")
    @patch("app.providers.musicbrainz.get_artist")
    def test_music_artist_details_no_poll_when_relation_images_present_or_known_missing(
        self,
        mock_get_artist,
        _mock_dedupe_artist_albums,
        _mock_needs_discography_sync,
    ):
        band = Artist.objects.create(
            name="Test Band",
            musicbrainz_id="band-mbid-2",
            discography_synced_at=timezone.now(),
        )
        member = Artist.objects.create(
            name="Test Member",
            musicbrainz_id="member-mbid-2",
            image="http://example.com/member.jpg",
        )
        ArtistMember.objects.create(
            band=band,
            member=member,
            role="drums",
            is_current=True,
        )
        known_missing_member = Artist.objects.create(
            name="Unknown Member",
            musicbrainz_id="member-mbid-3",
            image=settings.IMG_NONE,
        )
        ArtistMember.objects.create(
            band=band,
            member=known_missing_member,
            role="keys",
            is_current=True,
        )
        mock_get_artist.return_value = {
            "type": "Group",
            "country": "US",
            "genres": [],
            "tags": [],
            "rating": None,
            "rating_count": 0,
            "bio": "",
            "image": "",
            "members": [],
        }

        response = self.client.get(
            reverse(
                "music_artist_details",
                kwargs={
                    "artist_id": band.id,
                    "artist_slug": "test-band",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "http://example.com/member.jpg")
        self.assertContains(response, "media-card-poster")
        self.assertNotContains(
            response,
            reverse("prefetch_artist_relation_images", args=[band.id]),
        )

    @patch("app.services.music_scrobble.is_incomplete_album", return_value=False)
    @patch("app.services.music_scrobble.dedupe_artist_albums")
    def test_music_album_details_renders_shared_media_details_template(
        self,
        _mock_dedupe_artist_albums,
        _mock_is_incomplete_album,
    ):
        artist = Artist.objects.create(
            name="Test Artist",
            musicbrainz_id="artist-mbid",
            image="http://example.com/artist.jpg",
        )
        album = Album.objects.create(
            title="Debut Album",
            artist=artist,
            musicbrainz_release_id="release-mbid",
            tracks_populated=True,
            image="http://example.com/album.jpg",
            release_type="Album",
            release_date=datetime(2024, 1, 15, tzinfo=UTC).date(),
        )
        track = Track.objects.create(
            album=album,
            title="Track One",
            track_number=1,
            duration_ms=180000,
        )
        album_tracker = AlbumTracker.objects.create(
            user=self.user,
            album=album,
            status=Status.COMPLETED.value,
            score=7,
            notes="Album notes",
            start_date=datetime(2026, 2, 1, 18, 0, tzinfo=UTC),
            end_date=datetime(2026, 2, 2, 18, 0, tzinfo=UTC),
        )
        item = Item.objects.create(
            media_id="recording-1",
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            title="Track One",
            image="http://example.com/album.jpg",
        )
        Music.objects.create(
            item=item,
            user=self.user,
            artist=artist,
            album=album,
            track=track,
            status=Status.COMPLETED.value,
            start_date=datetime(2026, 2, 1, 18, 0, tzinfo=UTC),
            end_date=datetime(2026, 2, 2, 18, 0, tzinfo=UTC),
        )

        response = self.client.get(
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "test-artist",
                    "album_id": album.id,
                    "album_slug": "debut-album",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_details.html")
        self.assertNotContains(response, "app/music_album_detail.html")
        self.assertEqual(response.context["music_detail_kind"], "album")
        self.assertContains(response, "Track One")
        self.assertContains(response, artist.name)
        self.assertContains(response, "Debut Album")
        self.assertContains(
            response,
            f'hx-get="{reverse("album_track_modal", args=[album.id])}?instance_id={album_tracker.id}',
            html=False,
        )

    @patch("app.services.music_scrobble.is_incomplete_album", return_value=False)
    @patch("app.services.music_scrobble.dedupe_artist_albums")
    def test_music_album_details_renders_credited_artists(
        self,
        _mock_dedupe_artist_albums,
        _mock_is_incomplete_album,
    ):
        artist = Artist.objects.create(name="Artist One")
        second_artist = Artist.objects.create(name="Artist Two")
        album = Album.objects.create(title="Shared Album", artist=artist)
        AlbumArtist.objects.create(
            album=album,
            artist=artist,
            position=0,
            join_phrase=" & ",
        )
        AlbumArtist.objects.create(
            album=album,
            artist=second_artist,
            position=1,
            join_phrase="",
        )

        response = self.client.get(
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "artist-one",
                    "album_id": album.id,
                    "album_slug": "shared-album",
                },
            ),
        )

        first_url = reverse(
            "music_artist_details",
            kwargs={"artist_id": artist.id, "artist_slug": "artist-one"},
        )
        second_url = reverse(
            "music_artist_details",
            kwargs={"artist_id": second_artist.id, "artist_slug": "artist-two"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{first_url}"', html=False)
        self.assertContains(response, f'href="{second_url}"', html=False)
        self.assertContains(response, "Artist One</a> &", html=False)
        self.assertContains(response, "Artist Two</a>", html=False)

    @patch("app.services.music_scrobble.is_incomplete_album", return_value=False)
    @patch("app.services.music_scrobble.dedupe_artist_albums")
    def test_music_album_details_exposes_direct_and_implied_genres_in_tag_preview(
        self,
        _mock_dedupe_artist_albums,
        _mock_is_incomplete_album,
    ):
        artist = Artist.objects.create(name="Genre Artist")
        album = Album.objects.create(
            title="Genre Album",
            artist=artist,
            tracks_populated=True,
            genres=["Art Rock"],
            implied_genres=["Rock"],
        )
        detail_item = Item.objects.create(
            media_id=f"album-{album.id}",
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            title=album.title,
            genres=["Art Rock"],
            implied_genres=["Rock"],
            image="http://example.com/album.jpg",
        )
        tag = Tag.objects.create(user=self.user, name="Favorite")
        ItemTag.objects.create(item=detail_item, tag=tag)

        response = self.client.get(
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "genre-artist",
                    "album_id": album.id,
                    "album_slug": "genre-album",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        sections = response.context["detail_tag_sections"]
        section_titles = [section["title"] for section in sections]
        self.assertEqual(section_titles, ["Genres", "Implied Genres", "Tags"])
        self.assertEqual(sections[0]["entries"][0]["label"], "Art Rock")
        self.assertEqual(sections[1]["entries"][0]["label"], "Rock")
        self.assertEqual(sections[2]["entries"][0]["label"], "Favorite")
        self.assertContains(response, 'title="Manage tags"', html=False)
        self.assertContains(response, 'title="Add to custom lists"', html=False)

    @patch("app.services.music_scrobble.is_incomplete_album", return_value=False)
    @patch("app.services.music_scrobble.dedupe_artist_albums")
    def test_music_album_details_shows_empty_tags_section_without_album_item(
        self,
        _mock_dedupe_artist_albums,
        _mock_is_incomplete_album,
    ):
        artist = Artist.objects.create(name="Genre Artist")
        album = Album.objects.create(
            title="Genre Album",
            artist=artist,
            tracks_populated=True,
            genres=["Art Rock"],
            implied_genres=["Rock"],
        )

        response = self.client.get(
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "genre-artist",
                    "album_id": album.id,
                    "album_slug": "genre-album",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        sections = response.context["detail_tag_sections"]
        self.assertEqual(
            [section["title"] for section in sections],
            ["Genres", "Implied Genres", "Tags"],
        )
        self.assertEqual(sections[2]["entries"], [])
        self.assertEqual(sections[2]["empty_label"], "Click to add tags")

    @patch("app.providers.musicbrainz.get_release")
    @patch("app.services.music_scrobble.is_incomplete_album", return_value=False)
    @patch("app.services.music_scrobble.dedupe_artist_albums")
    def test_music_album_details_repairs_fallback_artist_credit(
        self,
        _mock_dedupe_artist_albums,
        _mock_is_incomplete_album,
        mock_get_release,
    ):
        artist = Artist.objects.create(
            name="Brian Eno",
            musicbrainz_id="ff95eb47-41c4-4f7f-a104-cdc30f02e872",
        )
        album = Album.objects.create(
            title="My Life in the Bush of Ghosts",
            artist=artist,
            musicbrainz_release_id="10eaf5b7-e319-42fd-babb-d3686ad347cf",
            tracks_populated=True,
        )
        AlbumArtist.objects.create(album=album, artist=artist)
        mock_get_release.return_value = {
            "artist_credits": [
                {
                    "artist_id": "ff95eb47-41c4-4f7f-a104-cdc30f02e872",
                    "name": "Brian Eno",
                    "sort_name": "Eno, Brian",
                    "join_phrase": "\u2014",
                },
                {
                    "artist_id": "641b56b5-6571-43dc-b5b1-2e822f349162",
                    "name": "David Byrne",
                    "sort_name": "Byrne, David",
                    "join_phrase": "",
                },
            ],
        }

        response = self.client.get(
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "brian-eno",
                    "album_id": album.id,
                    "album_slug": "my-life-in-the-bush-of-ghosts",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Brian Eno</a> \u2014 ", html=False)
        self.assertContains(response, "David Byrne</a>", html=False)

    def test_music_album_details_redirects_cross_artist_duplicate_to_canonical(self):
        user = get_user_model().objects.create_user(
            username="music_album_user",
            password="pass12345",
        )
        self.client.force_login(user)
        brian_eno = Artist.objects.create(name="Brian Eno")
        david_byrne = Artist.objects.create(name="David Byrne")
        canonical = Album.objects.create(
            title="My Life in the Bush of Ghosts",
            artist=brian_eno,
            musicbrainz_release_group_id="release-group-mbid",
        )
        duplicate = Album.objects.create(
            title="My Life in the Bush of Ghosts",
            artist=david_byrne,
            musicbrainz_release_group_id="release-group-mbid",
        )
        AlbumArtist.objects.create(album=canonical, artist=brian_eno)
        AlbumArtist.objects.create(album=duplicate, artist=david_byrne)
        AlbumTracker.objects.create(
            user=user,
            album=canonical,
            status=Status.IN_PROGRESS.value,
        )

        response = self.client.get(
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": david_byrne.id,
                    "artist_slug": "david-byrne",
                    "album_id": duplicate.id,
                    "album_slug": "my-life-in-the-bush-of-ghosts",
                },
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": brian_eno.id,
                    "artist_slug": "brian-eno",
                    "album_id": canonical.id,
                    "album_slug": "my-life-in-the-bush-of-ghosts",
                },
            ),
        )

    def test_legacy_music_artist_detail_redirects_to_canonical_route(self):
        artist = Artist.objects.create(name="Redirect Artist")

        response = self.client.get(reverse("artist_detail", args=[artist.id]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse(
                "music_artist_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "redirect-artist",
                },
            ),
        )

    def test_legacy_music_album_detail_redirects_to_canonical_route(self):
        artist = Artist.objects.create(name="Redirect Artist")
        album = Album.objects.create(title="Redirect Album", artist=artist)

        response = self.client.get(reverse("album_detail", args=[album.id]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "redirect-artist",
                    "album_id": album.id,
                    "album_slug": "redirect-album",
                },
            ),
        )

    def test_music_album_details_redirects_when_artist_path_is_wrong(self):
        artist = Artist.objects.create(name="Right Artist")
        wrong_artist = Artist.objects.create(name="Wrong Artist")
        album = Album.objects.create(title="Redirect Album", artist=artist)

        response = self.client.get(
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": wrong_artist.id,
                    "artist_slug": "wrong-artist",
                    "album_id": album.id,
                    "album_slug": "redirect-album",
                },
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "right-artist",
                    "album_id": album.id,
                    "album_slug": "redirect-album",
                },
            ),
        )

    def _track_details_kwargs(self, artist, album, track, artist_name, album_title):
        return {
            "artist_id": artist.id,
            "artist_slug": artist_name,
            "album_id": album.id,
            "album_slug": album_title,
            "track_id": track.id,
            "track_slug": "track-one",
        }

    def test_music_track_details_renders_shared_media_details_template(self):
        artist = Artist.objects.create(name="Test Artist")
        album = Album.objects.create(title="Debut Album", artist=artist)
        track = Track.objects.create(
            album=album,
            title="Track One",
            track_number=1,
            duration_ms=180000,
        )

        response = self.client.get(
            reverse(
                "music_track_details",
                kwargs=self._track_details_kwargs(
                    artist,
                    album,
                    track,
                    "test-artist",
                    "debut-album",
                ),
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_details.html")
        self.assertTemplateUsed(response, "app/components/detail_music_track.html")
        self.assertEqual(response.context["music_detail_kind"], "track")
        self.assertContains(response, "Track One")
        self.assertContains(response, artist.name)
        self.assertContains(response, "Debut Album")
        self.assertContains(
            response,
            reverse(
                "music_artist_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "test-artist",
                },
            ),
        )
        self.assertContains(
            response,
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "test-artist",
                    "album_id": album.id,
                    "album_slug": "debut-album",
                },
            ),
        )
        self.assertContains(response, "Not listened yet")

    def test_music_track_details_uses_track_genres_before_album_genres(self):
        artist = Artist.objects.create(name="Genre Artist")
        album = Album.objects.create(
            title="Genre Album",
            artist=artist,
            genres=["House"],
        )
        track = Track.objects.create(
            album=album,
            title="Track One",
            genres=["Bass House"],
        )

        response = self.client.get(
            reverse(
                "music_track_details",
                kwargs=self._track_details_kwargs(
                    artist,
                    album,
                    track,
                    "genre-artist",
                    "genre-album",
                ),
            ),
        )

        self.assertContains(response, "Bass House")
        self.assertNotContains(response, ">House<")

    def test_music_track_details_falls_back_to_album_genres(self):
        artist = Artist.objects.create(name="Fallback Artist")
        album = Album.objects.create(
            title="Fallback Album",
            artist=artist,
            genres=["Bass House"],
        )
        track = Track.objects.create(album=album, title="Track One", genres=[])

        response = self.client.get(
            reverse(
                "music_track_details",
                kwargs=self._track_details_kwargs(
                    artist,
                    album,
                    track,
                    "fallback-artist",
                    "fallback-album",
                ),
            ),
        )

        self.assertContains(response, "Bass House")

    def test_music_track_details_lists_play_history(self):
        artist = Artist.objects.create(name="Play Artist")
        album = Album.objects.create(title="Play Album", artist=artist)
        track = Track.objects.create(album=album, title="Track One")
        item = Item.objects.create(
            media_id="recording-track-1",
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            title="Track One",
        )
        Music.objects.create(
            item=item,
            user=self.user,
            artist=artist,
            album=album,
            track=track,
            status=Status.COMPLETED.value,
            end_date=datetime(2026, 2, 2, 18, 0, tzinfo=UTC),
        )

        response = self.client.get(
            reverse(
                "music_track_details",
                kwargs=self._track_details_kwargs(
                    artist,
                    album,
                    track,
                    "play-artist",
                    "play-album",
                ),
            ),
        )

        self.assertNotContains(response, "Not listened yet")

    def test_music_track_details_lists_play_history_with_origin_url(self):
        """A scrobble origin URL is shown on the track's play history."""
        artist = Artist.objects.create(name="Play Artist")
        album = Album.objects.create(title="Play Album", artist=artist)
        track = Track.objects.create(album=album, title="Track One")
        item = Item.objects.create(
            media_id="recording-track-origin",
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            title="Track One",
        )
        Music.objects.create(
            item=item,
            user=self.user,
            artist=artist,
            album=album,
            track=track,
            status=Status.COMPLETED.value,
            end_date=datetime(2026, 2, 2, 18, 0, tzinfo=UTC),
            origin_url="https://soundcloud.example/track",
        )

        response = self.client.get(
            reverse(
                "music_track_details",
                kwargs=self._track_details_kwargs(
                    artist,
                    album,
                    track,
                    "play-artist",
                    "play-album",
                ),
            ),
        )

        self.assertContains(response, "https://soundcloud.example/track")
        self.assertNotContains(response, "Not listened yet")

    def test_music_track_details_redirects_when_album_path_is_wrong(self):
        artist = Artist.objects.create(name="Right Artist")
        album = Album.objects.create(title="Right Album", artist=artist)
        other = Album.objects.create(title="Other Album", artist=artist)
        track = Track.objects.create(album=album, title="Track One")

        response = self.client.get(
            reverse(
                "music_track_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "right-artist",
                    "album_id": other.id,
                    "album_slug": "other-album",
                    "track_id": track.id,
                    "track_slug": "track-one",
                },
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse(
                "music_track_details",
                kwargs=self._track_details_kwargs(
                    artist,
                    album,
                    track,
                    "right-artist",
                    "right-album",
                ),
            ),
        )

    def test_music_track_details_redirects_when_artist_path_is_wrong(self):
        artist = Artist.objects.create(name="Right Artist")
        wrong_artist = Artist.objects.create(name="Wrong Artist")
        album = Album.objects.create(title="Right Album", artist=artist)
        track = Track.objects.create(album=album, title="Track One")

        response = self.client.get(
            reverse(
                "music_track_details",
                kwargs={
                    "artist_id": wrong_artist.id,
                    "artist_slug": "wrong-artist",
                    "album_id": album.id,
                    "album_slug": "right-album",
                    "track_id": track.id,
                    "track_slug": "track-one",
                },
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse(
                "music_track_details",
                kwargs=self._track_details_kwargs(
                    artist,
                    album,
                    track,
                    "right-artist",
                    "right-album",
                ),
            ),
        )

    def test_legacy_music_track_detail_redirects_to_canonical_route(self):
        artist = Artist.objects.create(name="Redirect Artist")
        album = Album.objects.create(title="Redirect Album", artist=artist)
        track = Track.objects.create(album=album, title="Track One")

        response = self.client.get(reverse("track_detail", args=[track.id]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse(
                "music_track_details",
                kwargs=self._track_details_kwargs(
                    artist,
                    album,
                    track,
                    "redirect-artist",
                    "redirect-album",
                ),
            ),
        )

    @patch("app.services.music_scrobble.is_incomplete_album", return_value=False)
    @patch("app.services.music_scrobble.dedupe_artist_albums")
    def test_music_album_track_title_links_to_track_details(
        self,
        _mock_dedupe_artist_albums,
        _mock_is_incomplete_album,
    ):
        artist = Artist.objects.create(name="Link Artist")
        album = Album.objects.create(
            title="Link Album",
            artist=artist,
            tracks_populated=True,
        )
        track = Track.objects.create(album=album, title="Linked Track")

        response = self.client.get(
            reverse(
                "music_album_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "link-artist",
                    "album_id": album.id,
                    "album_slug": "link-album",
                },
            ),
        )

        self.assertContains(
            response,
            reverse(
                "music_track_details",
                kwargs={
                    "artist_id": artist.id,
                    "artist_slug": "link-artist",
                    "album_id": album.id,
                    "album_slug": "link-album",
                    "track_id": track.id,
                    "track_slug": "linked-track",
                },
            ),
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_links_action_with_source_and_external_links(
        self,
        mock_get_metadata,
    ):
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "source_url": "https://www.themoviedb.org/movie/238",
            "external_links": {
                "IMDb": "https://www.imdb.com/title/tt0111161/",
            },
            "synopsis": "Test overview",
            "details": {},
            "related": {},
        }

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
        content = response.content.decode()
        self.assertIn('title="View source and external links"', content)
        self.assertIn("Source", content)
        self.assertIn("The Movie Database", content)
        self.assertIn("External links", content)
        self.assertIn("Letterboxd", content)
        self.assertIn("IMDb", content)
        self.assertIn("imdb-logo.png", content)
        self.assertIn("https://www.themoviedb.org/movie/238", content)
        self.assertIn("https://www.imdb.com/title/tt0111161/", content)
        self.assertNotIn("Tracking Source", content)
        self.assertNotIn("Metadata Source", content)
        self.assertNotIn("EXTERNAL LINKS", content)
        self.assertEqual(
            response.context["detail_link_sections"],
            [
                {
                    "title": "Source",
                    "entries": [
                        {
                            "label": "The Movie Database",
                            "url": "https://www.themoviedb.org/movie/238",
                            "brand": "tmdb",
                            "chip_classes": "border-cyan-400/18 bg-cyan-500/[0.07]",
                            "badge_classes": "border-cyan-400/28 bg-cyan-500/14",
                            "accent_classes": "text-[var(--color-text)]",
                            "logo_src": "/static/img/tmdb-logo.png",
                            "fallback_text": "TMDB",
                        }
                    ],
                },
                {
                    "title": "External links",
                    "entries": [
                        {
                            "label": "Letterboxd",
                            "url": "https://letterboxd.com/tmdb/238",
                            "brand": "letterboxd",
                            "chip_classes": "border-emerald-400/18 bg-emerald-500/[0.07]",
                            "badge_classes": "border-emerald-400/28 bg-emerald-500/14",
                            "accent_classes": "text-[var(--color-text)]",
                            "logo_src": "/static/img/letterboxd-logo.png",
                            "fallback_text": "LB",
                        },
                        {
                            "label": "IMDb",
                            "url": "https://www.imdb.com/title/tt0111161/",
                            "brand": "imdb",
                            "chip_classes": "border-amber-400/18 bg-amber-500/[0.07]",
                            "badge_classes": "border-amber-400/28 bg-amber-500/14",
                            "accent_classes": "text-[var(--color-text)]",
                            "logo_src": "/static/img/imdb-logo.png",
                            "fallback_text": "IMDb",
                        },
                    ],
                },
            ],
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_uses_tvdb_and_wikidata_logos_in_link_sections(
        self,
        mock_get_metadata,
    ):
        mock_get_metadata.return_value = {
            "media_id": "81189",
            "title": "Test Show",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TVDB.value,
            "image": "http://example.com/image.jpg",
            "source_url": "https://www.thetvdb.com/dereferrer/series/81189",
            "external_links": {
                "Wikidata": "https://www.wikidata.org/wiki/Q83495",
            },
            "synopsis": "Test overview",
            "details": {},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TVDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "81189",
                    "title": "test-show",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("tvdb-logo.png", content)
        self.assertIn("wikidata-logo.png", content)

        source_entry = response.context["detail_link_sections"][0]["entries"][0]
        external_entry = response.context["detail_link_sections"][1]["entries"][0]
        self.assertEqual(source_entry["label"], "TheTVDB")
        self.assertEqual(
            source_entry["chip_classes"], "border-teal-400/18 bg-teal-500/[0.07]"
        )
        self.assertEqual(source_entry["logo_src"], "/static/img/tvdb-logo.png")
        self.assertEqual(external_entry["label"], "Wikidata")
        self.assertEqual(
            external_entry["chip_classes"], "border-sky-400/18 bg-sky-500/[0.07]"
        )
        self.assertEqual(external_entry["logo_src"], "/static/img/wikidata-logo.png")

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_uses_mal_logo_in_link_sections(
        self,
        mock_get_metadata,
    ):
        mock_get_metadata.return_value = {
            "media_id": "52991",
            "title": "Frieren",
            "media_type": MediaTypes.MANGA.value,
            "source": Sources.MAL.value,
            "image": "http://example.com/image.jpg",
            "source_url": "https://myanimelist.net/manga/52991/Sousou_no_Frieren",
            "synopsis": "Test overview",
            "details": {},
            "related": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.MANGA.value,
                    "media_id": "52991",
                    "title": "frieren",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "myanimelist-logo.svg")

        source_entry = response.context["detail_link_sections"][0]["entries"][0]
        self.assertEqual(source_entry["label"], "MyAnimeList")
        self.assertEqual(
            source_entry["chip_classes"], "border-indigo-400/18 bg-indigo-500/[0.07]"
        )
        self.assertEqual(source_entry["logo_src"], "/static/img/myanimelist-logo.svg")

    @patch("app.media_details_views._queue_game_lengths_refresh", return_value=True)
    @patch("app.providers.services.get_media_metadata")
    def test_media_details_uses_igdb_and_hltb_logos_in_link_sections(
        self,
        mock_get_metadata,
        _mock_queue_game_lengths_refresh,
    ):
        mock_get_metadata.return_value = {
            "media_id": "325609",
            "title": "Dispatch",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "image": "http://example.com/image.jpg",
            "source_url": "https://www.igdb.com/games/dispatch",
            "external_links": {
                "HowLongToBeat": "https://howlongtobeat.com/game/160618",
            },
            "synopsis": "Test overview",
            "details": {},
            "related": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "325609",
                    "title": "dispatch",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("igdb-logo.png", content)
        self.assertIn("hltb-logo.png", content)

        source_entry = response.context["detail_link_sections"][0]["entries"][0]
        external_entry = response.context["detail_link_sections"][1]["entries"][0]
        self.assertEqual(source_entry["label"], "Internet Game Database")
        self.assertEqual(
            source_entry["chip_classes"], "border-orange-400/18 bg-orange-500/[0.07]"
        )
        self.assertEqual(source_entry["logo_src"], "/static/img/igdb-logo.png")
        self.assertEqual(external_entry["label"], "HowLongToBeat")
        self.assertEqual(
            external_entry["chip_classes"], "border-amber-400/18 bg-amber-500/[0.07]"
        )
        self.assertEqual(external_entry["logo_src"], "/static/img/hltb-logo.png")

    @patch("app.media_details_views._queue_game_lengths_refresh", return_value=True)
    @patch("app.providers.services.get_media_metadata")
    def test_game_details_show_estimated_youtube_watch_time(
        self,
        mock_get_metadata,
        _mock_queue_game_lengths_refresh,
    ):
        """Videos YouTube tags with the game show apart from playtime."""
        from django.utils import timezone

        from app.models import Video, VideoGameTag

        mock_get_metadata.return_value = {
            "media_id": "325609",
            "title": "Dispatch",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "image": "http://example.com/image.jpg",
            "synopsis": "Test overview",
            "details": {},
            "related": {},
        }
        Item.objects.create(
            media_id="325609",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Dispatch",
        )
        video_item = Item.objects.create(
            media_id="HQp_b8VHtgI",
            source=Sources.YOUTUBE.value,
            media_type=MediaTypes.VIDEO.value,
            title="Dispatch ep 1",
        )
        VideoGameTag.objects.create(
            item=video_item,
            status=VideoGameTag.Status.FOUND,
            game_title="Dispatch",
            checked_at=timezone.now(),
        )
        Video.objects.create(
            item=video_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=1800,
            length_seconds=3600,
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "325609",
                    "title": "dispatch",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertContains(response, "Watched on YouTube")
        self.assertEqual(response.context["youtube_watch"]["seconds"], 1800)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_notes_button_opens_notes_for_latest_entry(
        self,
        mock_get_metadata,
    ):
        """The Notes shortcut sits beside tags/lists and targets the tracked entry."""
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "synopsis": "Test overview",
            "max_progress": 1,
            "details": {},
            "related": {},
        }
        movie = Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
        )

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

        content = response.content.decode()
        self.assertEqual(content.count('title="Add or edit notes"'), 1)
        self.assertLess(
            content.index('title="Manage tags"'),
            content.index('title="Add or edit notes"'),
        )
        self.assertLess(
            content.index('title="Add or edit notes"'),
            content.index('title="Add to custom lists"'),
        )
        button = content[content.index('title="Add or edit notes"') :]
        button = button[: button.index("</button>")]
        self.assertIn('"focus": "notes"', button)
        # The entry is resolved when the button is clicked, not baked in here.
        self.assertNotIn("instance_id", button)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_notes_button_for_untracked_item_has_no_instance(
        self,
        mock_get_metadata,
    ):
        """With nothing tracked the shortcut opens the modal to add a first entry."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "synopsis": "Test overview",
            "details": {},
            "related": {},
        }

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

        content = response.content.decode()
        button = content[content.index('title="Add or edit notes"') :]
        button = button[: button.index("</button>")]
        self.assertIn('"focus": "notes"', button)
        self.assertNotIn("instance_id", button)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_tag_preview_sections_next_to_links(
        self,
        mock_get_metadata,
    ):
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
            genres=["Drama", "Mystery"],
        )
        tag = Tag.objects.create(user=self.user, name="Prestige TV")
        ItemTag.objects.create(tag=tag, item=item)

        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "source_url": "https://www.themoviedb.org/movie/238",
            "genres": ["Drama", "Mystery"],
            "synopsis": "Test overview",
            "details": {},
            "related": {},
        }

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
        content = response.content.decode()
        self.assertLess(
            content.index('title="View source and external links"'),
            content.index('title="Manage tags"'),
        )
        self.assertLess(
            content.index('title="Manage tags"'),
            content.index('title="Add to custom lists"'),
        )
        self.assertIn("Genres", content)
        self.assertIn("Tags", content)
        self.assertIn("Drama", content)
        self.assertIn("Mystery", content)
        self.assertIn("Prestige TV", content)
        self.assertEqual(
            response.context["detail_tag_sections"],
            [
                {
                    "title": "Genres",
                    "title_label": "Genres",
                    "tone": "violet",
                    "entries": [
                        {
                            "label": "Drama",
                            "chip_classes": "border-violet-400/18 bg-violet-500/[0.07] text-[var(--color-text)]",
                            "url": "/medialist/movie?genre=Drama",
                        },
                        {
                            "label": "Mystery",
                            "chip_classes": "border-violet-400/18 bg-violet-500/[0.07] text-[var(--color-text)]",
                            "url": "/medialist/movie?genre=Mystery",
                        },
                    ],
                },
                {
                    "title": "Tags",
                    "title_label": "Tags",
                    "tone": "slate",
                    "entries": [
                        {
                            "label": "Prestige TV",
                            "chip_classes": "border-slate-400/18 bg-slate-500/[0.07] text-[var(--color-text)]",
                        }
                    ],
                },
            ],
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_empty_tag_section_when_item_has_no_tags(
        self,
        mock_get_metadata,
    ):
        Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
            genres=["Drama", "Mystery"],
        )

        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "source_url": "https://www.themoviedb.org/movie/238",
            "genres": ["Drama", "Mystery"],
            "synopsis": "Test overview",
            "details": {},
            "related": {},
        }

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
        self.assertContains(response, "Genres")
        self.assertContains(response, "Tags")
        self.assertContains(response, "Click to add tags")
        self.assertContains(response, 'x-ref="manageTagsButton"')
        self.assertContains(response, "$refs.manageTagsButton.click()")
        self.assertEqual(
            response.context["detail_tag_sections"],
            [
                {
                    "title": "Genres",
                    "title_label": "Genres",
                    "tone": "violet",
                    "entries": [
                        {
                            "label": "Drama",
                            "chip_classes": "border-violet-400/18 bg-violet-500/[0.07] text-[var(--color-text)]",
                            "url": "/medialist/movie?genre=Drama",
                        },
                        {
                            "label": "Mystery",
                            "chip_classes": "border-violet-400/18 bg-violet-500/[0.07] text-[var(--color-text)]",
                            "url": "/medialist/movie?genre=Mystery",
                        },
                    ],
                },
                {
                    "title": "Tags",
                    "title_label": "Tags",
                    "tone": "slate",
                    "entries": [],
                    "empty_label": "Click to add tags",
                },
            ],
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_hides_streaming_section_when_watch_provider_region_disabled(
        self,
        mock_get_metadata,
    ):
        self.user.watch_provider_region = "UNSET"
        self.user.save(update_fields=["watch_provider_region"])
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "providers": {
                "US": {
                    "flatrate": [
                        {
                            "provider_name": "Netflix",
                            "logo_path": "/netflix.jpg",
                        },
                    ],
                },
            },
            "details": {},
            "related": {},
        }

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
        self.assertNotContains(response, "STREAMING")
        self.assertNotContains(
            response,
            "Watch provider region is not configured.",
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_prefers_stored_item_image_over_provider_image(
        self,
        mock_get_metadata,
    ):
        item = Item.objects.create(
            media_id="377938",
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            title="The Lord of the Rings",
            image="https://images.example.com/custom-cover.jpg",
        )
        mock_get_metadata.return_value = {
            "media_id": "377938",
            "title": "The Lord of the Rings",
            "media_type": MediaTypes.BOOK.value,
            "source": Sources.HARDCOVER.value,
            "image": "https://images.example.com/provider-cover.jpg",
            "details": {},
            "related": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.HARDCOVER.value,
                    "media_type": MediaTypes.BOOK.value,
                    "media_id": "377938",
                    "title": "the-lord-of-the-rings",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media"]["image"], item.image)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_shell_keeps_provider_score_from_stored_item(
        self,
        mock_get_metadata,
    ):
        """A synced item keeps its provider rating chip on shell loads (#1077)."""
        Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Godfather",
            image="https://images.example.com/godfather.jpg",
            provider_rating=8.1,
            provider_rating_count=1234,
            metadata_fetched_at=timezone.now(),
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "the-godfather",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        mock_get_metadata.assert_not_called()
        self.assertEqual(response.context["media"]["score"], 8.1)
        self.assertEqual(response.context["media"]["score_count"], 1234)
        self.assertContains(response, "1,234 votes")

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_shell_hides_score_chip_without_stored_rating(
        self,
        mock_get_metadata,
    ):
        """No stored rating still means no chip, matching a live render (#1077)."""
        Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Godfather",
            image="https://images.example.com/godfather.jpg",
            metadata_fetched_at=timezone.now(),
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "the-godfather",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        mock_get_metadata.assert_not_called()
        self.assertIsNone(response.context["media"]["score_count"])
        self.assertNotContains(response, "vote")

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_shell_rebuilds_external_links_from_stored_ids(
        self,
        mock_get_metadata,
    ):
        """External links survive the stored-metadata shortcut (#1077)."""
        Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Godfather",
            image="https://images.example.com/godfather.jpg",
            provider_external_ids={
                "imdb_id": "tt0068646",
                "tvdb_id": "81189",
                "wikidata_id": "Q47703",
            },
            metadata_fetched_at=timezone.now(),
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "the-godfather",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        mock_get_metadata.assert_not_called()
        external_section = next(
            section
            for section in response.context["detail_link_sections"]
            if section["title"] == "External links"
        )
        self.assertEqual(
            {entry["label"] for entry in external_section["entries"]},
            {"Letterboxd", "IMDb", "TVDB", "Wikidata"},
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_link_sections_do_not_duplicate_stored_external_links(
        self,
        mock_get_metadata,
    ):
        """A stored id must not double a link the live payload already carries."""
        Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Godfather",
            image="https://images.example.com/godfather.jpg",
            provider_external_ids={"imdb_id": "tt0068646"},
        )
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "The Godfather",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "https://images.example.com/godfather.jpg",
            "external_links": {"IMDb": "https://www.imdb.com/title/tt0068646/"},
            "details": {},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "the-godfather",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        external_section = next(
            section
            for section in response.context["detail_link_sections"]
            if section["title"] == "External links"
        )
        imdb_entries = [
            entry for entry in external_section["entries"] if entry["label"] == "IMDb"
        ]
        self.assertEqual(len(imdb_entries), 1)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_shell_keeps_book_series_and_progress_from_stored_item(
        self,
        mock_get_metadata,
    ):
        """Book series and page count survive the stored-metadata shortcut (#1077)."""
        Item.objects.create(
            media_id="377938",
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            title="The Fellowship of the Ring",
            image="https://images.example.com/custom-cover.jpg",
            series_name="The Lord of the Rings",
            series_position=1,
            number_of_pages=423,
            metadata_fetched_at=timezone.now(),
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.HARDCOVER.value,
                    "media_type": MediaTypes.BOOK.value,
                    "media_id": "377938",
                    "title": "the-fellowship-of-the-ring",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        mock_get_metadata.assert_not_called()
        self.assertEqual(
            response.context["media"]["series_name"],
            "The Lord of the Rings",
        )
        self.assertEqual(response.context["media"]["max_progress"], 423)
        self.assertContains(response, "The Lord of the Rings")

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_shell_skips_provider_call_when_metadata_already_fetched(
        self,
        mock_get_metadata,
    ):
        """Shell-phase loads reuse stored metadata once it's been fetched before (#879)."""
        Item.objects.create(
            media_id="377938",
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            title="The Lord of the Rings",
            image="https://images.example.com/custom-cover.jpg",
            synopsis="A hobbit's journey.",
            metadata_fetched_at=timezone.now(),
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.HARDCOVER.value,
                    "media_type": MediaTypes.BOOK.value,
                    "media_id": "377938",
                    "title": "the-lord-of-the-rings",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        mock_get_metadata.assert_not_called()

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_secondary_fetches_provider_for_fresh_book_metadata(
        self,
        mock_get_metadata,
    ):
        """Secondary-phase loads need the complete provider payload (#879)."""
        Item.objects.create(
            media_id="377938",
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            title="The Lord of the Rings",
            image="https://images.example.com/custom-cover.jpg",
            metadata_fetched_at=timezone.now(),
        )
        mock_get_metadata.return_value = {
            "media_id": "377938",
            "title": "The Lord of the Rings",
            "media_type": MediaTypes.BOOK.value,
            "source": Sources.HARDCOVER.value,
            "image": "https://images.example.com/provider-cover.jpg",
            "details": {},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.HARDCOVER.value,
                    "media_type": MediaTypes.BOOK.value,
                    "media_id": "377938",
                    "title": "the-lord-of-the-rings",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        mock_get_metadata.assert_called_once_with(
            MediaTypes.BOOK.value,
            "377938",
            Sources.HARDCOVER.value,
            language="en",
            user=self.user,
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_secondary_loads_full_fresh_tmdb_movie_metadata(
        self,
        mock_get_metadata,
    ):
        """Fresh stored movie metadata must not hide cast or recommendations."""
        Item.objects.create(
            media_id="10193",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Toy Story 3",
            image="https://images.example.com/toy-story-3.jpg",
            metadata_fetched_at=timezone.now(),
        )
        mock_get_metadata.return_value = {
            "media_id": "10193",
            "title": "Toy Story 3",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "source_url": "https://www.themoviedb.org/movie/10193",
            "image": "https://images.example.com/toy-story-3.jpg",
            "synopsis": "Woody and Buzz face a new chapter.",
            "max_progress": 1,
            "score": 8.0,
            "details": {"release_date": "2010-06-16"},
            "related": {
                "recommendations": [
                    {
                        "media_id": "862",
                        "title": "Toy Story",
                        "media_type": MediaTypes.MOVIE.value,
                        "source": Sources.TMDB.value,
                        "image": "https://images.example.com/toy-story.jpg",
                    },
                ],
            },
            "cast": [
                {
                    "name": "Tom Hanks",
                    "image": "https://images.example.com/tom-hanks.jpg",
                    "role": "Woody",
                },
            ],
            "crew": [],
            "studios_full": [],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "10193",
                    "title": "toy-story-3",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        mock_get_metadata.assert_called_once_with(
            MediaTypes.MOVIE.value,
            "10193",
            Sources.TMDB.value,
            language="en",
            user=self.user,
        )
        self.assertContains(response, "media-grid-row-detail-cast", html=False)
        self.assertContains(response, "Tom Hanks")
        self.assertContains(response, "Toy Story")

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_secondary_hides_recommendations_when_turned_off(
        self,
        mock_get_metadata,
    ):
        """Turning recommendations off removes the section and its card lookups."""
        Item.objects.create(
            media_id="10193",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Toy Story 3",
            image="https://images.example.com/toy-story-3.jpg",
            metadata_fetched_at=timezone.now(),
        )
        mock_get_metadata.return_value = {
            "media_id": "10193",
            "title": "Toy Story 3",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "source_url": "https://www.themoviedb.org/movie/10193",
            "image": "https://images.example.com/toy-story-3.jpg",
            "synopsis": "Woody and Buzz face a new chapter.",
            "max_progress": 1,
            "score": 8.0,
            "details": {"release_date": "2010-06-16"},
            "related": {
                "recommendations": [
                    {
                        "media_id": "862",
                        "title": "Sentinel Recommendation",
                        "media_type": MediaTypes.MOVIE.value,
                        "source": Sources.TMDB.value,
                        "image": "https://images.example.com/toy-story.jpg",
                    },
                ],
            },
            "cast": [],
            "crew": [],
            "studios_full": [],
        }
        url = reverse(
            "media_details",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "media_id": "10193",
                "title": "toy-story-3",
            },
        )

        response = self.client.get(url, {"fragment": "secondary"})
        self.assertContains(response, "Sentinel Recommendation")

        self.user.show_recommendations = False
        self.user.save(update_fields=["show_recommendations"])
        with patch("app.helpers.enrich_items_with_user_data") as mock_enrich:
            response = self.client.get(url, {"fragment": "secondary"})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Sentinel Recommendation")
        mock_enrich.assert_not_called()

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_secondary_refetches_stale_book_metadata(
        self,
        mock_get_metadata,
    ):
        """Secondary-phase loads still refresh once stored metadata is stale (#879)."""
        Item.objects.create(
            media_id="377938",
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            title="The Lord of the Rings",
            image="https://images.example.com/custom-cover.jpg",
            metadata_fetched_at=timezone.now()
            - timedelta(seconds=settings.CACHE_TIMEOUT + 1),
        )
        mock_get_metadata.return_value = {
            "media_id": "377938",
            "title": "The Lord of the Rings",
            "media_type": MediaTypes.BOOK.value,
            "source": Sources.HARDCOVER.value,
            "image": "https://images.example.com/provider-cover.jpg",
            "details": {},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.HARDCOVER.value,
                    "media_type": MediaTypes.BOOK.value,
                    "media_id": "377938",
                    "title": "the-lord-of-the-rings",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        mock_get_metadata.assert_called_once()

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_secondary_still_fetches_fresh_tv_metadata(
        self,
        mock_get_metadata,
    ):
        """TV secondary-phase loads always fetch live data for season/episode resolution (#879)."""
        Item.objects.create(
            media_id="1399",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Game of Thrones",
            image="https://images.example.com/custom-cover.jpg",
            metadata_fetched_at=timezone.now(),
        )
        mock_get_metadata.return_value = {
            "media_id": "1399",
            "title": "Game of Thrones",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "image": "https://images.example.com/custom-cover.jpg",
            "details": {},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "1399",
                    "title": "game-of-thrones",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        mock_get_metadata.assert_called()

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_repairs_stringified_title_payloads_on_existing_item(
        self,
        mock_get_metadata,
    ):
        item = Item.objects.create(
            media_id="81189",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="{'language': 'jpn', 'name': 'Sōdo Āto Onrain'}",
            original_title="{'language': 'jpn', 'name': 'Sōdo Āto Onrain'}",
            localized_title="{'language': 'jpn', 'name': 'Sōdo Āto Onrain'}",
            image="https://example.com/cover.jpg",
        )
        mock_get_metadata.return_value = {
            "media_id": "81189",
            "title": {"language": "jpn", "name": "Sōdo Āto Onrain"},
            "original_title": {"language": "jpn", "name": "Sōdo Āto Onrain"},
            "localized_title": {"language": "jpn", "name": "Sōdo Āto Onrain"},
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "image": "https://example.com/provider-cover.jpg",
            "details": {},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }

        shell_response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "81189",
                    "title": "sword-art-online",
                },
            ),
        )
        fragment_response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "81189",
                    "title": "sword-art-online",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(shell_response.status_code, 200)
        self.assertContains(shell_response, "Sōdo Āto Onrain")
        self.assertNotContains(
            shell_response, "{'language': 'jpn', 'name': 'Sōdo Āto Onrain'}"
        )
        self.assertEqual(fragment_response.status_code, 200)

        item.refresh_from_db()
        self.assertEqual(item.title, "Sōdo Āto Onrain")
        self.assertEqual(item.original_title, "Sōdo Āto Onrain")
        self.assertEqual(item.localized_title, "Sōdo Āto Onrain")

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_refreshes_stale_tvdb_titles_with_english_localized_text(
        self,
        mock_get_metadata,
    ):
        item = Item.objects.create(
            media_id="259640",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            title="ソードアート・オンライン",
            original_title="ソードアート・オンライン",
            localized_title="ソードアート・オンライン",
            image="https://example.com/cover.jpg",
        )
        mock_get_metadata.return_value = {
            "media_id": "259640",
            "title": "Sword Art Online",
            "original_title": "ソードアート・オンライン",
            "localized_title": "Sword Art Online",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TVDB.value,
            "image": "https://example.com/provider-cover.jpg",
            "synopsis": "English overview",
            "details": {},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }

        shell_response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TVDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "259640",
                    "title": "sword-art-online",
                },
            ),
        )
        fragment_response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TVDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "259640",
                    "title": "sword-art-online",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(shell_response.status_code, 200)
        self.assertContains(shell_response, "Sword Art Online")
        self.assertEqual(fragment_response.status_code, 200)

        item.refresh_from_db()
        self.assertEqual(item.title, "Sword Art Online")
        self.assertEqual(item.original_title, "ソードアート・オンライン")
        self.assertEqual(item.localized_title, "Sword Art Online")

    @override_settings(TVDB_API_KEY="test-tvdb-key")
    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_tvdb_show_when_mapped_tmdb_id_is_gone(
        self,
        mock_get_metadata,
    ):
        """A TVDB title must still render when its TMDB mapping 404s."""
        item = Item.objects.create(
            media_id="467589",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            title="Ask Hank Anything",
            image="https://example.com/cover.jpg",
        )
        # TVDB advertises a TMDB remote ID that TMDB has since deleted.
        ItemProviderLink.objects.create(
            item=item,
            provider=Sources.TMDB.value,
            provider_media_id="281366",
            provider_media_type=MediaTypes.TV.value,
        )
        tvdb_metadata = {
            "media_id": "467589",
            "title": "Ask Hank Anything",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TVDB.value,
            "image": "https://example.com/cover.jpg",
            "details": {},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }

        def metadata_side_effect(
            media_type,
            media_id,
            source,
            season_numbers=None,
            episode_number=None,
            language=None,
            user=None,
        ):
            del media_type, media_id, season_numbers, episode_number, language, user
            if source == Sources.TMDB.value:
                tmdb_response = requests.Response()
                tmdb_response.status_code = requests.codes.not_found
                raise services.ProviderAPIError(
                    Sources.TMDB.value,
                    requests.exceptions.HTTPError(response=tmdb_response),
                )
            return {
                **tvdb_metadata,
                "details": dict(tvdb_metadata["details"]),
                "related": dict(tvdb_metadata["related"]),
                "cast": list(tvdb_metadata["cast"]),
                "crew": list(tvdb_metadata["crew"]),
                "studios_full": list(tvdb_metadata["studios_full"]),
            }

        mock_get_metadata.side_effect = metadata_side_effect

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TVDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "467589",
                    "title": "ask-hank-anything",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["watch_providers"])

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_tvdb_series_with_null_country(
        self,
        mock_get_metadata,
    ):
        """TVDB omits country/languages; the detail page must not 500."""
        Item.objects.create(
            media_id="467209",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            title="Liar Game",
            image="https://example.com/cover.jpg",
        )
        mock_get_metadata.return_value = {
            "media_id": "467209",
            "title": "Liar Game",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TVDB.value,
            "image": "https://example.com/cover.jpg",
            "synopsis": "Synopsis",
            "details": {
                "format": "TV",
                "status": "Ended",
                "episodes": 12,
                "country": None,
                "languages": None,
            },
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TVDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "467209",
                    "title": "liar-game",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unknown")

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_provider_language_list_with_null_entry(
        self,
        mock_get_metadata,
    ):
        """A null element in a provider language list must not 500 the detail page."""
        Item.objects.create(
            media_id="316239",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Toonout",
            image="https://example.com/cover.jpg",
        )
        mock_get_metadata.return_value = {
            "media_id": "316239",
            "title": "Toonout",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "image": "https://example.com/cover.jpg",
            "synopsis": "Synopsis",
            "details": {
                "format": "TV",
                "status": "Ended",
                "episodes": 12,
                "country": None,
                "languages": ["English", None],
            },
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "316239",
                    "title": "toonout",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "English")

    @override_settings(TVDB_API_KEY="test-tvdb-key")
    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_grouped_anime_media_details_render_with_null_country(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
    ):
        """TVDB anime with a null country/languages must not 500 (#1223)."""
        Item.objects.create(
            media_id="467209",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Liar Game",
            image="https://example.com/cover.jpg",
        )
        base_metadata = {
            "media_id": "467209",
            "title": "Liar Game",
            "media_type": MediaTypes.ANIME.value,
            "identity_media_type": MediaTypes.TV.value,
            "library_media_type": MediaTypes.ANIME.value,
            "source": Sources.TVDB.value,
            "source_url": "https://www.thetvdb.com/dereferrer/series/467209",
            "max_progress": 12,
            "image": "https://example.com/cover.jpg",
            "synopsis": "Synopsis",
            "details": {
                "format": "TV",
                "status": "Ended",
                "episodes": 12,
                "country": None,
                "languages": None,
            },
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
            "external_links": {},
        }
        mock_get_metadata.return_value = base_metadata
        mock_resolve_detail_metadata.return_value = MetadataResolutionResult(
            display_provider=Sources.TVDB.value,
            identity_provider=Sources.TVDB.value,
            mapping_status="identity",
            header_metadata=base_metadata,
            grouped_preview=None,
            provider_media_id="467209",
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TVDB.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "467209",
                    "title": "liar-game-2026",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unknown")

    @patch("app.views.metadata_resolution.resolve_mal_tmdb_identity")
    @patch("app.providers.services.get_media_metadata")
    def test_media_details_enriches_mal_anime_with_tmdb_watch_providers(
        self,
        mock_get_metadata,
        mock_resolve_mal_tmdb_identity,
    ):
        """A tracked MAL anime should use its mapped TMDB provider payload."""
        self.user.watch_provider_region = "DE"
        self.user.save(update_fields=["watch_provider_region"])
        item = Item.objects.create(
            media_id="52991",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Frieren",
            image="https://example.com/frieren.jpg",
        )
        Anime.objects.create(
            item=item,
            user=self.user,
            status=Status.PLANNING.value,
        )
        mal_metadata = {
            "media_id": "52991",
            "title": "Frieren",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.MAL.value,
            "image": "https://example.com/frieren.jpg",
            "details": {"episodes": 28},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }
        tmdb_metadata = {
            **mal_metadata,
            "media_id": "209867",
            "source": Sources.TMDB.value,
            "providers": {
                "DE": {
                    "flatrate": [
                        {
                            "provider_id": 283,
                            "provider_name": "Crunchyroll",
                            "logo_path": "/crunchyroll.jpg",
                        },
                    ],
                },
            },
        }
        mock_get_metadata.side_effect = lambda *args, **_kwargs: (
            tmdb_metadata if args[2] == Sources.TMDB.value else mal_metadata
        )
        mock_resolve_mal_tmdb_identity.return_value = AnimeTMDBIdentity(
            media_id="209867",
            media_type=MediaTypes.TV.value,
            tvdb_id="407407",
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "52991",
                    "title": "frieren",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'data-watch-providers-placement="desktop"',
        )
        self.assertContains(
            response,
            'data-watch-providers-placement="mobile"',
        )
        self.assertEqual(
            response.context["watch_providers"][0]["provider_name"], "Crunchyroll"
        )
        item.refresh_from_db()
        self.assertEqual(
            item.watch_providers["DE"]["flatrate"][0]["provider_name"],
            "Crunchyroll",
        )

    @patch("app.views.metadata_resolution.resolve_mal_tmdb_identity")
    @patch("app.providers.services.get_media_metadata")
    def test_untracked_mal_anime_details_show_series_and_movie_providers(
        self,
        mock_get_metadata,
        mock_resolve_mal_tmdb_identity,
    ):
        """Search results should show providers before an anime is tracked."""
        self.user.watch_provider_region = "DE"
        self.user.save(update_fields=["watch_provider_region"])
        cases = (
            (
                "52991",
                "Frieren",
                AnimeTMDBIdentity(
                    media_id="209867",
                    media_type=MediaTypes.TV.value,
                    tvdb_id="407407",
                ),
                "Crunchyroll",
            ),
            (
                "199",
                "Spirited Away",
                AnimeTMDBIdentity(
                    media_id="129",
                    media_type=MediaTypes.MOVIE.value,
                    imdb_id="tt0245429",
                ),
                "Netflix",
            ),
        )

        for mal_id, title, identity, provider_name in cases:
            with self.subTest(title=title):
                mock_get_metadata.reset_mock()
                mock_resolve_mal_tmdb_identity.reset_mock()
                mock_resolve_mal_tmdb_identity.return_value = identity

                def metadata_side_effect(
                    media_type,
                    media_id,
                    source,
                    expected_identity=identity,
                    expected_mal_id=mal_id,
                    expected_title=title,
                    expected_provider_name=provider_name,
                    **_kwargs,
                ):
                    if source == Sources.TMDB.value:
                        self.assertEqual(media_type, expected_identity.media_type)
                        self.assertEqual(media_id, expected_identity.media_id)
                        return {
                            "providers": {
                                "DE": {
                                    "flatrate": [
                                        {
                                            "provider_id": 8,
                                            "provider_name": expected_provider_name,
                                            "logo_path": "/provider.jpg",
                                        },
                                    ],
                                },
                            },
                        }
                    return {
                        "media_id": expected_mal_id,
                        "title": expected_title,
                        "media_type": MediaTypes.ANIME.value,
                        "source": Sources.MAL.value,
                        "image": "https://example.com/anime.jpg",
                        "details": {},
                        "related": {},
                        "cast": [],
                        "crew": [],
                        "studios_full": [],
                    }

                mock_get_metadata.side_effect = metadata_side_effect
                response = self.client.get(
                    reverse(
                        "media_details",
                        kwargs={
                            "source": Sources.MAL.value,
                            "media_type": MediaTypes.ANIME.value,
                            "media_id": mal_id,
                            "title": title.lower().replace(" ", "-"),
                        },
                    ),
                    {"fragment": "secondary"},
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.context["watch_providers"][0]["provider_name"],
                    provider_name,
                )
                self.assertFalse(
                    Item.objects.filter(
                        media_id=mal_id,
                        source=Sources.MAL.value,
                        media_type=MediaTypes.ANIME.value,
                    ).exists(),
                )

    @patch("app.views.metadata_resolution.resolve_mal_tmdb_identity")
    @patch("app.providers.services.get_media_metadata")
    def test_untracked_mal_anime_details_tolerate_mapping_failure(
        self,
        mock_get_metadata,
        mock_resolve_mal_tmdb_identity,
    ):
        """Optional provider enrichment must not make MAL details unavailable."""
        self.user.watch_provider_region = "DE"
        self.user.save(update_fields=["watch_provider_region"])
        mock_get_metadata.return_value = {
            "media_id": "4081",
            "title": "Natsume's Book of Friends",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.MAL.value,
            "image": "https://example.com/natsume.jpg",
            "details": {"episodes": 13},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }
        mock_resolve_mal_tmdb_identity.side_effect = services.ProviderAPIError(
            "ANIME_MAPPING",
            RuntimeError("offline"),
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "4081",
                    "title": "natsume-yuujinchou",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["watch_providers"])

    @patch("app.views.metadata_resolution.resolve_mal_tmdb_identity")
    @patch("app.providers.services.get_media_metadata")
    def test_untracked_mal_anime_skips_provider_resolution_when_region_disabled(
        self,
        mock_get_metadata,
        mock_resolve_mal_tmdb_identity,
    ):
        """A disabled provider region must not trigger MAL-to-TMDB lookups."""
        self.user.watch_provider_region = "UNSET"
        self.user.save(update_fields=["watch_provider_region"])
        mock_get_metadata.return_value = {
            "media_id": "4081",
            "title": "Natsume's Book of Friends",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.MAL.value,
            "image": "https://example.com/natsume.jpg",
            "details": {},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "4081",
                    "title": "natsume-yuujinchou",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["watch_providers"])
        mock_resolve_mal_tmdb_identity.assert_not_called()

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_persists_movie_recommendation_metadata(
        self, mock_get_metadata
    ):
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "provider_keywords": ["Whodunit", "Holiday"],
            "provider_certification": "PG",
            "provider_collection_id": "44",
            "provider_collection_name": "Mystery Collection",
            "details": {
                "country": "US",
                "studios": ["Pixar Animation Studios"],
                "certification": "PG",
            },
            "cast": [],
            "crew": [],
            "studios_full": [],
        }

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
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.provider_keywords, ["Whodunit", "Holiday"])

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_trakt_score_card_when_data_exists(
        self, mock_get_metadata
    ):
        Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
            trakt_rating=7.88048,
            trakt_rating_count=123456,
            trakt_popularity_rank=9,
            trakt_popularity_score=3210.5,
            trakt_popularity_fetched_at=timezone.now(),
        )
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "details": {},
            "related": {},
        }

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
        self.assertContains(response, "trakt-logo.svg")
        self.assertContains(response, "7.8")
        self.assertNotContains(response, "7.88048")
        self.assertContains(response, "123,456 ratings")
        self.assertContains(
            response,
            'class="detail-score-card-shell order-2 mt-0 mb-5 w-full sm:order-1 sm:mt-4"',
            html=False,
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_hides_trakt_score_card_without_data(self, mock_get_metadata):
        Item.objects.create(
            media_id="239",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="No Trakt Movie",
            image="http://example.com/image.jpg",
        )
        mock_get_metadata.return_value = {
            "media_id": "239",
            "title": "No Trakt Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "details": {},
            "related": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "239",
                    "title": "no-trakt-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "trakt-logo.svg")

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_imdb_score_card_when_data_exists(
        self, mock_get_metadata
    ):
        Item.objects.create(
            media_id="240",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="IMDb Rated Movie",
            image="http://example.com/image.jpg",
            imdb_rating=8.34,
            imdb_rating_count=987654,
        )
        mock_get_metadata.return_value = {
            "media_id": "240",
            "title": "IMDb Rated Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "details": {},
            "related": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "240",
                    "title": "imdb-rated-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "imdb-logo.png")
        self.assertContains(response, "8.3")
        self.assertNotContains(response, "8.34")
        self.assertContains(response, "987,654 ratings")

    @patch("app.providers.services.get_media_metadata")
    def test_rating_chips_link_to_the_urls_in_the_links_dropdown(
        self, mock_get_metadata
    ):
        """#1083: a rating chip opens the page the Links dropdown already lists."""
        Item.objects.create(
            media_id="242",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Linked Ratings Movie",
            image="http://example.com/image.jpg",
            imdb_rating=7.5,
            imdb_rating_count=1000,
        )
        mock_get_metadata.return_value = {
            "media_id": "242",
            "title": "Linked Ratings Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "score": 7.1,
            "score_count": 42000,
            "source_url": "https://www.themoviedb.org/movie/242",
            "external_links": {"IMDb": "https://www.imdb.com/title/tt0000242/"},
            "details": {},
            "related": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "242",
                    "title": "linked-ratings-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        for label, url in (
            ("IMDb score", "https://www.imdb.com/title/tt0000242/"),
            ("The Movie Database score", "https://www.themoviedb.org/movie/242"),
        ):
            chip = soup.find(
                "span",
                class_="sr-only",
                string=lambda t, label=label: t and t.strip() == label,
            ).parent
            self.assertEqual(chip.name, "a", label)
            self.assertEqual(chip["href"], url)
            self.assertEqual(chip["target"], "_blank")

    @patch("app.providers.services.get_media_metadata")
    def test_rating_chip_stays_plain_without_a_link(self, mock_get_metadata):
        """#1083: no URL in the Links dropdown means the chip is not a link."""
        Item.objects.create(
            media_id="243",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Unlinked Rating Movie",
            image="http://example.com/image.jpg",
            imdb_rating=6.0,
            imdb_rating_count=500,
        )
        mock_get_metadata.return_value = {
            "media_id": "243",
            "title": "Unlinked Rating Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "details": {},
            "related": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "243",
                    "title": "unlinked-rating-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "html.parser")
        source_icon = soup.find("span", class_="sr-only", string="IMDb score").parent
        self.assertEqual(source_icon.name, "span")

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_hides_imdb_score_card_without_data(self, mock_get_metadata):
        Item.objects.create(
            media_id="241",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="No IMDb Movie",
            image="http://example.com/image.jpg",
        )
        mock_get_metadata.return_value = {
            "media_id": "241",
            "title": "No IMDb Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "details": {},
            "related": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "241",
                    "title": "no-imdb-movie",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "IMDb score")

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_source_score_chip_with_tmdb_logo(
        self, mock_get_metadata
    ):
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "score": 7.6,
            "score_count": 42000,
            "details": {},
            "related": {},
        }

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
        self.assertContains(response, "tmdb-logo.png")
        self.assertContains(response, "7.6")
        self.assertContains(response, "42,000 votes")
        self.assertContains(
            response,
            'class="detail-score-card-shell order-2 mt-0 mb-5 w-full sm:order-1 sm:mt-4"',
            html=False,
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_trakt_season_graph_from_stored_season_scores(
        self, mock_get_metadata
    ):
        Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
            image="http://example.com/image.jpg",
            trakt_rating=7.24742,
            trakt_rating_count=291,
            trakt_popularity_fetched_at=timezone.now(),
        )
        # The series graph is built from stored episode-level Trakt ratings
        # and rendered through the polling fragment endpoint.
        for season_number, rating, votes in ((1, 7.28, 150), (2, 7.55, 69)):
            Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Episode S{season_number}E1",
                image="http://example.com/episode.jpg",
                season_number=season_number,
                episode_number=1,
                trakt_rating=rating,
                trakt_rating_count=votes,
            )
        mock_get_metadata.return_value = {
            "media_id": "1668",
            "title": "Test TV Show",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "external_links": {"trakt": "https://trakt.tv/shows/test-tv-show"},
            "details": {},
            "related": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        fragment_url = reverse(
            "trakt_series_graph_fragment",
            kwargs={"source": Sources.TMDB.value, "media_id": "1668"},
        )
        self.assertContains(response, "trakt-logo.svg")
        self.assertContains(response, fragment_url)
        self.assertContains(response, "View series graph")
        self.assertContains(response, 'role="dialog"', html=False)
        self.assertContains(response, 'href="https://trakt.tv/shows/test-tv-show"')
        self.assertContains(response, "Trakt")

        # The poller must only exist while the modal is open: it sits inside an
        # x-if template (not merely x-show) so a closed modal issues no requests.
        soup = BeautifulSoup(response.content, "html.parser")
        poller = soup.find(attrs={"hx-get": fragment_url})
        self.assertIsNotNone(poller)
        self.assertIsNotNone(
            poller.find_parent("template", attrs={"x-if": "graphOpen"}),
        )

        fragment = self.client.get(fragment_url)
        self.assertEqual(fragment.status_code, 200)
        self.assertContains(fragment, "Series Graph")
        self.assertContains(fragment, "S1")
        self.assertContains(fragment, "7.28")
        self.assertContains(fragment, "150 votes")

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_source_score_chip_with_mal_logo(
        self, mock_get_metadata
    ):
        mock_get_metadata.return_value = {
            "media_id": "52991",
            "title": "Frieren",
            "media_type": MediaTypes.MANGA.value,
            "source": Sources.MAL.value,
            "image": "http://example.com/image.jpg",
            "score": 8.9,
            "score_count": 123456,
            "details": {},
            "related": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.MANGA.value,
                    "media_id": "52991",
                    "title": "frieren",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "myanimelist-logo.svg")
        self.assertContains(response, "8.9")
        self.assertContains(response, "123,456 votes")

    @patch("app.media_details_views._queue_game_lengths_refresh", return_value=True)
    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_source_score_chip_with_igdb_logo(
        self,
        mock_get_metadata,
        _mock_queue_game_lengths_refresh,
    ):
        mock_get_metadata.return_value = {
            "media_id": "325609",
            "title": "Dispatch",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "image": "http://example.com/image.jpg",
            "score": 83,
            "score_count": 12000,
            "details": {},
            "related": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "325609",
                    "title": "dispatch",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "igdb-logo.png")
        self.assertContains(response, "83")
        self.assertContains(response, "12,000 votes")

    @patch("app.providers.services.get_media_metadata")
    def test_tv_media_details_uses_same_title_spacing_as_score_chips(
        self, mock_get_metadata
    ):
        mock_get_metadata.return_value = {
            "media_id": "1668",
            "title": "Test TV Show",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "score": 7.6,
            "score_count": 42000,
            "details": {},
            "related": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<div class="mb-3 md:mb-1 text-center md:text-start">',
            html=False,
        )
        self.assertContains(
            response, '<h1 class="text-3xl font-bold">Test TV Show</h1>', html=False
        )

    @patch("app.providers.services.get_media_metadata")
    def test_tv_media_details_renders_progress_and_date_subtitle_without_history_card(
        self,
        mock_get_metadata,
    ):
        self.user.date_format = DateFormatChoices.ISO_8601
        self.user.save(update_fields=["date_format"])
        item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
            image="http://example.com/image.jpg",
        )
        tv = TV.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Season 1",
            image="http://example.com/season.jpg",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Episode 1",
            image="http://example.com/episode1.jpg",
            season_number=1,
            episode_number=1,
            runtime_minutes=45,
        )
        Episode.objects.create(
            item=episode_item,
            related_season=season,
            end_date=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        )
        Episode.objects.create(
            item=episode_item,
            related_season=season,
            end_date=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
        )
        mock_get_metadata.return_value = {
            "media_id": "1668",
            "title": "Test TV Show",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 8,
            "details": {},
            "related": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertContains(response, "Progress: 1/8")
        self.assertContains(response, "2026-03-01 - 2026-03-12")
        self.assertContains(response, "1h 30min watched")
        self.assertIn('<div class="mb-3 md:mb-1 text-center md:text-start">', content)
        self.assertIn("data-detail-activity-subtitle-root", content)
        self.assertIn('data-dates="2026-03-01 - 2026-03-12"', content)
        self.assertIn('data-duration="1h 30min watched"', content)
        self.assertNotContains(response, "Your History")
        self.assertNotContains(response, "FIRST PLAYED")
        self.assertNotContains(response, "LAST PLAYED")
        self.assertNotContains(response, "WATCHED HOURS")

    @patch("app.providers.services.get_media_metadata")
    def test_movie_media_details_renders_watch_subtitle_above_score_chips(
        self,
        mock_get_metadata,
    ):
        self.user.date_format = DateFormatChoices.ISO_8601
        self.user.save(update_fields=["date_format"])
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "score": 7.6,
            "score_count": 42000,
            "details": {},
            "related": {},
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
            runtime_minutes=95,
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            start_date=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            end_date=datetime(2026, 3, 1, 14, 0, tzinfo=UTC),
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            start_date=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
            end_date=datetime(2026, 3, 12, 14, 0, tzinfo=UTC),
        )

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
        self.assertEqual(response.context["play_stats"]["total_minutes"], 190)
        self.assertContains(response, "Watched 2 times")
        self.assertContains(response, "2026-03-01 - 2026-03-12")
        self.assertContains(response, "3h 10min watched")
        self.assertContains(
            response,
            'class="detail-score-card-shell order-2 mt-0 mb-5 w-full sm:order-1 sm:mt-4"',
            html=False,
        )
        self.assertContains(
            response,
            'class="w-full md:w-auto md:shrink-0"',
            html=False,
        )
        self.assertContains(
            response,
            'class="relative inline-flex w-full md:w-auto"',
            html=False,
        )
        self.assertContains(response, 'aria-label="More tracking actions"', html=False)
        self.assertContains(response, "Add new entry")
        self.assertContains(response, "Check-in")
        self.assertContains(response, '"is_create": true', html=False)
        self.assertNotContains(response, "Your History")
        self.assertNotContains(response, "FIRST PLAYED")
        self.assertNotContains(response, "LAST PLAYED")
        self.assertNotContains(response, "TOTAL HOURS")

    @patch("app.providers.services.get_media_metadata")
    def test_movie_media_details_uses_play_date_range_when_repeats_only_have_end_dates(
        self,
        mock_get_metadata,
    ):
        self.user.date_format = DateFormatChoices.ISO_8601
        self.user.save(update_fields=["date_format"])
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "score": 7.6,
            "score_count": 42000,
            "details": {},
            "related": {},
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
            runtime_minutes=95,
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            end_date=datetime(2019, 11, 19, 21, 0, tzinfo=UTC),
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            end_date=datetime(2020, 11, 28, 20, 0, tzinfo=UTC),
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            end_date=datetime(2025, 11, 28, 19, 0, tzinfo=UTC),
        )

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
        self.assertEqual(
            response.context["play_stats"]["first_played"],
            datetime(2019, 11, 19, 21, 0, tzinfo=UTC),
        )
        self.assertEqual(
            response.context["play_stats"]["last_played"],
            datetime(2025, 11, 28, 19, 0, tzinfo=UTC),
        )
        self.assertContains(response, "Watched 3 times")
        self.assertContains(response, "2019-11-19 - 2025-11-28")

    @patch("app.providers.services.get_media_metadata")
    def test_reading_media_details_render_activity_subtitle_without_stats_cards(
        self,
        mock_get_metadata,
    ):
        self._use_iso_dates()
        cases = [
            (
                MediaTypes.BOOK.value,
                Sources.OPENLIBRARY.value,
                "OL100M",
                "Tracked Book",
                Book,
            ),
            (
                MediaTypes.MANGA.value,
                Sources.MANGAUPDATES.value,
                "72274276213",
                "Tracked Manga",
                Manga,
            ),
            (
                MediaTypes.COMIC.value,
                Sources.COMICVINE.value,
                "4000-1",
                "Tracked Comic",
                Comic,
            ),
        ]

        for media_type, source, media_id, title, model in cases:
            with self.subTest(media_type=media_type):
                mock_get_metadata.return_value = {
                    "media_id": media_id,
                    "title": title,
                    "media_type": media_type,
                    "source": source,
                    "image": "http://example.com/cover.jpg",
                    "max_progress": 320,
                    "details": {},
                    "related": {},
                }
                item = Item.objects.create(
                    media_id=media_id,
                    source=source,
                    media_type=media_type,
                    title=title,
                    image="http://example.com/cover.jpg",
                )
                model.objects.create(
                    item=item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=120,
                    start_date=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
                    end_date=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
                )

                response = self.client.get(
                    reverse(
                        "media_details",
                        kwargs={
                            "source": source,
                            "media_type": media_type,
                            "media_id": media_id,
                            "title": title.lower().replace(" ", "-"),
                        },
                    ),
                )

                self._assert_activity_subtitle_without_stats_cards(
                    response,
                    "Progress: 120/320",
                    "2026-03-01 - 2026-03-12",
                )

    @patch("app.providers.services.get_media_metadata")
    def test_audiobook_activity_subtitle_shows_total_as_listening_time(
        self,
        mock_get_metadata,
    ):
        """An audiobook's total is minutes, so it reads like its progress."""
        self._use_iso_dates()
        mock_get_metadata.return_value = {
            "media_id": "abs-1",
            "title": "Project Hail Mary",
            "media_type": MediaTypes.BOOK.value,
            "source": Sources.AUDIOBOOKSHELF.value,
            "image": "http://example.com/cover.jpg",
            "max_progress": 966,
            "details": {"format": "audiobook"},
            "related": {},
        }
        item = Item.objects.create(
            media_id="abs-1",
            source=Sources.AUDIOBOOKSHELF.value,
            media_type=MediaTypes.BOOK.value,
            title="Project Hail Mary",
            image="http://example.com/cover.jpg",
            format="audiobook",
            runtime_minutes=966,
        )
        Book.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=358,
            start_date=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            end_date=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.AUDIOBOOKSHELF.value,
                    "media_type": MediaTypes.BOOK.value,
                    "media_id": "abs-1",
                    "title": "project-hail-mary",
                },
            ),
        )

        self._assert_activity_subtitle_without_stats_cards(
            response,
            "Progress: 5h 58min/16h 06min",
            "2026-03-01 - 2026-03-12",
        )

    @patch("app.providers.services.get_media_metadata")
    def test_audiobook_page_lists_formats_and_narrator(
        self,
        mock_get_metadata,
    ):
        """The Details card joins Book and Audiobook and shows the narrator."""
        mock_get_metadata.return_value = {
            "media_id": "abs-2",
            "title": "Project Hail Mary",
            "media_type": MediaTypes.BOOK.value,
            "source": Sources.AUDIOBOOKSHELF.value,
            "image": "http://example.com/cover.jpg",
            "max_progress": 966,
            "details": {
                "format": "audiobook",
                "author": ["Andy Weir"],
                "narrator": ["Ray Porter"],
            },
            "related": {},
        }
        ebook = Item.objects.create(
            media_id="OL-phm",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Project Hail Mary",
            authors=["Andy Weir"],
        )
        audio = Item.objects.create(
            media_id="abs-2",
            source=Sources.AUDIOBOOKSHELF.value,
            media_type=MediaTypes.BOOK.value,
            title="Project Hail Mary",
            authors=["Andy Weir"],
            format="audiobook",
            narrators=["Ray Porter"],
        )
        Book.objects.create(item=ebook, user=self.user, status=Status.PLANNING.value)
        Book.objects.create(
            item=audio, user=self.user, status=Status.IN_PROGRESS.value, progress=10,
        )
        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.AUDIOBOOKSHELF.value,
                    "media_type": MediaTypes.BOOK.value,
                    "media_id": "abs-2",
                    "title": "project-hail-mary",
                },
            ),
            {"fragment": "secondary"},
        )

        soup = BeautifulSoup(response.content, "html.parser")
        facts = {
            fact.h3.get_text(strip=True): fact
            for fact in soup.select(".detail-sidebar-fact")
            if fact.h3
        }
        self.assertEqual(
            facts["Format"].p.get_text(" ", strip=True).replace(" ,", ","),
            "Book, Audiobook",
        )
        self.assertIn("OL-phm", facts["Format"].a["href"])
        self.assertEqual(facts["Narrator"].get_text(" ", strip=True), "Narrator Ray Porter")

    @patch("app.providers.services.get_media_metadata")
    def test_game_media_details_renders_activity_subtitle_without_stats_cards(
        self,
        mock_get_metadata,
    ):
        self._use_iso_dates()
        mock_get_metadata.return_value = {
            "media_id": "game-123",
            "title": "Tracked Game",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "image": "http://example.com/game.jpg",
            "max_progress": 1000,
            "details": {},
            "related": {},
        }
        item = Item.objects.create(
            media_id="game-123",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Tracked Game",
            image="http://example.com/game.jpg",
            provider_game_lengths={"igdb": {"summary": {"normally_seconds": 8100}}},
            provider_game_lengths_source="igdb",
        )
        Game.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=135,
            start_date=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            end_date=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "game-123",
                    "title": "tracked-game",
                },
            ),
        )

        self._assert_activity_subtitle_without_stats_cards(
            response,
            "Progress: 2h 15min",
            "2026-03-01 - 2026-03-12",
        )

    @patch("app.providers.services.get_media_metadata")
    def test_boardgame_media_details_renders_activity_subtitle_without_stats_cards(
        self,
        mock_get_metadata,
    ):
        self._use_iso_dates()
        mock_get_metadata.return_value = {
            "media_id": "13",
            "title": "Tracked Board Game",
            "media_type": MediaTypes.BOARDGAME.value,
            "source": Sources.BGG.value,
            "image": "http://example.com/boardgame.jpg",
            "max_progress": 20,
            "details": {},
            "related": {},
        }
        item = Item.objects.create(
            media_id="13",
            source=Sources.BGG.value,
            media_type=MediaTypes.BOARDGAME.value,
            title="Tracked Board Game",
            image="http://example.com/boardgame.jpg",
        )
        BoardGame.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=7,
            start_date=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            end_date=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.BGG.value,
                    "media_type": MediaTypes.BOARDGAME.value,
                    "media_id": "13",
                    "title": "tracked-board-game",
                },
            ),
        )

        self._assert_activity_subtitle_without_stats_cards(
            response,
            "Progress: 7 plays",
            "2026-03-01 - 2026-03-12",
        )

    @patch("app.providers.services.get_media_metadata")
    def test_music_media_details_renders_activity_subtitle_without_stats_cards(
        self,
        mock_get_metadata,
    ):
        self._use_iso_dates()
        mock_get_metadata.return_value = {
            "media_id": "track-1",
            "title": "Tracked Song",
            "media_type": MediaTypes.MUSIC.value,
            "source": Sources.MUSICBRAINZ.value,
            "image": "http://example.com/track.jpg",
            "details": {},
            "related": {},
        }
        item = Item.objects.create(
            media_id="track-1",
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            title="Tracked Song",
            image="http://example.com/track.jpg",
            runtime_minutes=4,
        )
        Music.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=3,
            start_date=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            end_date=datetime(2026, 3, 1, 12, 10, tzinfo=UTC),
        )
        Music.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=4,
            start_date=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
            end_date=datetime(2026, 3, 12, 12, 10, tzinfo=UTC),
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MUSICBRAINZ.value,
                    "media_type": MediaTypes.MUSIC.value,
                    "media_id": "track-1",
                    "title": "tracked-song",
                },
            ),
        )

        self._assert_activity_subtitle_without_stats_cards(
            response,
            "Progress: 7 plays",
            "2026-03-01 - 2026-03-12",
            "28min listened",
        )

    def test_podcast_show_media_details_renders_activity_subtitle(self):
        self._use_iso_dates()
        show = PodcastShow.objects.create(
            podcast_uuid="itunes:1002937870",
            title="Tracked Podcast",
            author="Host",
            image="http://example.com/podcast.jpg",
            rss_feed_url="",
        )
        PodcastShowTracker.objects.create(
            user=self.user,
            show=show,
            status=Status.IN_PROGRESS.value,
        )
        episode_one = PodcastEpisode.objects.create(
            show=show,
            episode_uuid="pod-ep-1",
            title="Episode One",
            duration=1800,
        )
        episode_two = PodcastEpisode.objects.create(
            show=show,
            episode_uuid="pod-ep-2",
            title="Episode Two",
            duration=2700,
        )
        PodcastEpisode.objects.create(
            show=show,
            episode_uuid="pod-ep-3",
            title="Episode Three",
            duration=1800,
        )
        item_one = Item.objects.create(
            media_id="pod-ep-1",
            source=Sources.POCKETCASTS.value,
            media_type=MediaTypes.PODCAST.value,
            title="Episode One",
            image="http://example.com/podcast.jpg",
        )
        item_two = Item.objects.create(
            media_id="pod-ep-2",
            source=Sources.POCKETCASTS.value,
            media_type=MediaTypes.PODCAST.value,
            title="Episode Two",
            image="http://example.com/podcast.jpg",
        )
        Podcast.objects.create(
            item=item_one,
            user=self.user,
            show=show,
            episode=episode_one,
            status=Status.COMPLETED.value,
            progress=30,
            start_date=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            end_date=datetime(2026, 3, 1, 12, 30, tzinfo=UTC),
        )
        Podcast.objects.create(
            item=item_two,
            user=self.user,
            show=show,
            episode=episode_two,
            status=Status.COMPLETED.value,
            progress=45,
            start_date=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
            end_date=datetime(2026, 3, 12, 12, 45, tzinfo=UTC),
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.POCKETCASTS.value,
                    "media_type": MediaTypes.PODCAST.value,
                    "media_id": show.podcast_uuid,
                    "title": "tracked-podcast",
                },
            ),
        )

        self._assert_activity_subtitle_without_stats_cards(
            response,
            "Progress: 2/3",
            "2026-03-01 - 2026-03-12",
            "1h 15min listened",
        )

    @patch("app.providers.services.get_media_metadata")
    def test_podcast_media_details_without_episode_context_does_not_crash(
        self, mock_get_metadata
    ):
        """Generic podcast details should skip the episode section when no episode list is available."""
        mock_get_metadata.return_value = {
            "media_id": "missing-show",
            "title": "Fallback Podcast",
            "media_type": MediaTypes.PODCAST.value,
            "source": Sources.POCKETCASTS.value,
            "image": "http://example.com/podcast.jpg",
            "overview": "Podcast synopsis",
            "related": {},
            "details": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.POCKETCASTS.value,
                    "media_type": MediaTypes.PODCAST.value,
                    "media_id": "missing-show",
                    "title": "fallback-podcast",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fallback Podcast")
        self.assertNotContains(response, 'id="episodes-list"', html=False)

    def test_manual_music_media_details_does_not_crash(self):
        """Custom/manual music entries should render without KeyError: 'source' (#366)."""
        item = Item.objects.create(
            media_id="backrooms-ost",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MUSIC.value,
            title="Backrooms 2026 (OST)",
            image="http://example.com/album.jpg",
        )
        Music.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MANUAL.value,
                    "media_type": MediaTypes.MUSIC.value,
                    "media_id": "backrooms-ost",
                    "title": "backrooms-ost",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media"]["source"], Sources.MANUAL.value)
        self.assertEqual(
            response.context["media"]["media_type"], MediaTypes.MUSIC.value
        )
        self.assertEqual(response.context["media"]["media_id"], "backrooms-ost")

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_your_score_chip_with_edit_rating(
        self, mock_get_metadata
    ):
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "details": {},
            "related": {},
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            score=8,
        )

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
        self.assertContains(response, "Edit rating")
        self.assertContains(response, 'x-text="formatRating(rating)">8<', html=False)
        self.assertContains(
            response, "x-text=\"rating ? 'Edit rating' : 'Add rating'\"", html=False
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_hides_your_score_chip_when_ratings_disabled(
        self, mock_get_metadata
    ):
        self.user.rating_scale = RatingScaleChoices.DISABLED.value
        self.user.save(update_fields=["rating_scale"])
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "details": {},
            "related": {},
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            score=8,
        )

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
        self.assertNotContains(response, "Edit rating")
        self.assertNotContains(response, "Add rating")
        movie.refresh_from_db()
        self.assertEqual(movie.score, 8)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_your_score_chip_with_five_point_scale_suffix(
        self,
        mock_get_metadata,
    ):
        self.user.rating_scale = RatingScaleChoices.FIVE.value
        self.user.save(update_fields=["rating_scale"])
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "details": {},
            "related": {},
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            score=8,
        )

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
        self.assertContains(response, "Edit rating")
        self.assertContains(response, "ratingScaleMax: 5", html=False)
        self.assertContains(response, 'x-text="formatRating(rating)">4/5<', html=False)
        self.assertContains(
            response, "x-text=\"rating ? 'Edit rating' : 'Add rating'\"", html=False
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_your_score_chip_with_add_rating_when_empty(
        self, mock_get_metadata
    ):
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "max_progress": 1,
            "details": {},
            "related": {},
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.PLANNING.value,
            score=None,
        )

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
        self.assertContains(response, "Add rating")
        self.assertNotContains(response, "Click to edit")
        self.assertNotContains(response, "FIRST PLAYED")
        self.assertNotContains(response, "LAST PLAYED")
        self.assertNotContains(response, "TOTAL HOURS")
        self.assertContains(
            response, "x-text=\"rating ? 'Edit rating' : 'Add rating'\"", html=False
        )

    @override_settings(TVDB_API_KEY="test-tvdb-key")
    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_anime_details_keep_source_labels_but_move_metadata_controls_to_modal(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
    ):
        """Flat anime details keep source context while metadata controls live in the modal."""
        base_metadata = {
            "media_id": "52991",
            "title": "Frieren",
            "original_title": "Sousou no Frieren",
            "localized_title": "Frieren",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.MAL.value,
            "source_url": "https://myanimelist.net/anime/52991",
            "external_links": {
                "TVDB": "https://www.thetvdb.com/dereferrer/series/9350138",
            },
            "display_source_url": "https://www.thetvdb.com/dereferrer/series/9350138",
            "max_progress": 28,
            "image": "https://example.com/frieren.jpg",
            "synopsis": "A mage looks back.",
            "details": {"episodes": 28},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }
        mock_get_metadata.return_value = base_metadata
        item = Item.objects.create(
            media_id="52991",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Frieren",
            image="https://example.com/frieren.jpg",
        )
        Anime.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=1,
        )
        mock_resolve_detail_metadata.return_value = MetadataResolutionResult(
            display_provider=Sources.TVDB.value,
            identity_provider=Sources.MAL.value,
            mapping_status="mapped",
            header_metadata=base_metadata,
            grouped_preview={
                "media_id": "9350138",
                "source": Sources.TVDB.value,
                "media_type": MediaTypes.ANIME.value,
                "title": "Frieren: Beyond Journey's End",
                "original_title": "Sousou no Frieren",
                "localized_title": "Frieren: Beyond Journey's End",
                "related": {
                    "seasons": [
                        {
                            "season_number": 1,
                            "max_progress": None,
                            "episode_count": 28,
                            "first_air_date": timezone.now(),
                            "is_mapped_target": True,
                            "mapped_episode_start": 1,
                            "mapped_episode_end": 28,
                        },
                    ],
                },
            },
            provider_media_id="9350138",
            grouped_preview_target={
                "season_number": 1,
                "season_title": "Season 1",
                "episode_start": 1,
                "episode_end": 28,
            },
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "52991",
                    "title": "frieren",
                },
            ),
            # can_migrate_grouped_anime is computed for the secondary fragment.
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Metadata Provider")
        self.assertNotContains(response, "Grouped Series Preview")
        self.assertNotContains(response, "Migrate to Grouped Series")
        self.assertTrue(response.context["can_migrate_grouped_anime"])

    @override_settings(TVDB_API_KEY="test-tvdb-key")
    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_flat_anime_media_details_render_tv_style_subtitle(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
    ):
        self.user.date_format = DateFormatChoices.ISO_8601
        self.user.save(update_fields=["date_format"])
        base_metadata = {
            "media_id": "52991",
            "title": "Frieren",
            "original_title": "Sousou no Frieren",
            "localized_title": "Frieren",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.MAL.value,
            "source_url": "https://myanimelist.net/anime/52991",
            "max_progress": 28,
            "image": "https://example.com/frieren.jpg",
            "synopsis": "A mage looks back.",
            "details": {"episodes": 28, "runtime": "24m"},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }
        mock_get_metadata.return_value = base_metadata
        item = Item.objects.create(
            media_id="52991",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Frieren",
            image="https://example.com/frieren.jpg",
            runtime_minutes=24,
        )
        Anime.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=3,
            start_date=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            end_date=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
        )
        mock_resolve_detail_metadata.return_value = MetadataResolutionResult(
            display_provider=Sources.MAL.value,
            identity_provider=Sources.MAL.value,
            mapping_status="identity",
            header_metadata=base_metadata,
            grouped_preview=None,
            provider_media_id="52991",
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "52991",
                    "title": "frieren",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Progress: 3/28")
        self.assertContains(response, "2026-03-01 - 2026-03-12")
        self.assertContains(response, "1h 12min watched")
        self.assertContains(
            response,
            'class="mt-4 mb-5 flex flex-wrap gap-2"',
            html=False,
        )
        self.assertNotContains(response, "FIRST PLAYED")
        self.assertNotContains(response, "LAST PLAYED")
        self.assertNotContains(response, "WATCHED HOURS")

    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_flat_mal_anime_details_explain_missing_episode_cards(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
    ):
        base_metadata = {
            "media_id": "527",
            "title": "Pokemon",
            "original_title": "Pokemon",
            "localized_title": "Pokemon",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.MAL.value,
            "source_url": "https://myanimelist.net/anime/527",
            "max_progress": 276,
            "image": "https://example.com/pokemon.jpg",
            "synopsis": "Pocket monsters.",
            "details": {"episodes": 276},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }
        mock_get_metadata.return_value = base_metadata
        item = Item.objects.create(
            media_id="527",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Pokemon",
            image="https://example.com/pokemon.jpg",
        )
        mock_resolve_detail_metadata.side_effect = lambda *_args, **_kwargs: (
            MetadataResolutionResult(
                display_provider=Sources.MAL.value,
                identity_provider=Sources.MAL.value,
                mapping_status="identity",
                header_metadata={
                    **base_metadata,
                    "details": dict(base_metadata["details"]),
                    "related": dict(base_metadata["related"]),
                    "cast": list(base_metadata["cast"]),
                    "crew": list(base_metadata["crew"]),
                    "studios_full": list(base_metadata["studios_full"]),
                },
                grouped_preview=None,
                provider_media_id=item.media_id,
                grouped_preview_target=None,
            )
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "527",
                    "title": "pokemon",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Episode cards are not available from MyAnimeList metadata.",
        )
        self.assertContains(
            response,
            "Switch the metadata provider to TVDB or TMDB from Add to tracker",
        )

        # Episode previews use another provider; its outage must not discard
        # the anime's otherwise available details.
        with patch(
            "app.media_details_views._build_flat_anime_episode_preview",
            side_effect=services.ProviderAPIError(
                Sources.TMDB.value, requests.exceptions.Timeout()
            ),
        ):
            response = self.client.get(
                response.wsgi_request.path,
                {"fragment": "secondary"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ">Details</h2>")
        self.assertContains(
            response,
            "Episode cards are not available from MyAnimeList metadata.",
        )

    @override_settings(TVDB_API_KEY="test-tvdb-key")
    @patch("app.views.metadata_resolution.anime_mapping.find_entries_for_mal_id")
    @patch("app.views.anime_mapping.resolve_provider_series_id")
    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_flat_mal_anime_details_render_cross_season_episode_cards(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
        mock_resolve_provider_series_id,
        mock_find_entries_for_mal_id,
    ):
        base_metadata = {
            "media_id": "527",
            "title": "Pokemon",
            "original_title": "Pokemon",
            "localized_title": "Pokemon",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.MAL.value,
            "source_url": "https://myanimelist.net/anime/527",
            "max_progress": 4,
            "image": "https://example.com/pokemon.jpg",
            "synopsis": "Pocket monsters.",
            "details": {"episodes": 4},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }
        grouped_series_metadata = {
            "media_id": "76703",
            "source": Sources.TVDB.value,
            "media_type": MediaTypes.ANIME.value,
            "title": "Pokemon",
            "related": {
                "seasons": [
                    {
                        "season_number": 1,
                        "episode_count": 2,
                    },
                    {
                        "season_number": 2,
                        "episode_count": 2,
                    },
                ],
            },
        }
        grouped_payload = grouped_series_metadata | {
            "season/1": {
                "media_id": "76703",
                "source": Sources.TVDB.value,
                "media_type": MediaTypes.SEASON.value,
                "season_number": 1,
                "season_title": "Indigo League",
                "details": {"episodes": 2},
                "episodes": [
                    {
                        "episode_number": 1,
                        "air_date": "1997-04-01",
                        "image": "https://example.com/pokemon-ep1.jpg",
                        "name": "Pokemon, I Choose You!",
                        "overview": "Episode one.",
                        "runtime": 24,
                    },
                    {
                        "episode_number": 2,
                        "air_date": "1997-04-08",
                        "image": "https://example.com/pokemon-ep2.jpg",
                        "name": "Pokemon Emergency!",
                        "overview": "Episode two.",
                        "runtime": 24,
                    },
                ],
            },
            "season/2": {
                "media_id": "76703",
                "source": Sources.TVDB.value,
                "media_type": MediaTypes.SEASON.value,
                "season_number": 2,
                "season_title": "Orange Islands",
                "details": {"episodes": 2},
                "episodes": [
                    {
                        "episode_number": 1,
                        "air_date": "1999-01-28",
                        "image": "https://example.com/pokemon-ep3.jpg",
                        "name": "Pallet Party Panic",
                        "overview": "Episode three.",
                        "runtime": 24,
                    },
                    {
                        "episode_number": 2,
                        "air_date": "1999-02-04",
                        "image": "https://example.com/pokemon-ep4.jpg",
                        "name": "A Scare in the Air",
                        "overview": "Episode four.",
                        "runtime": 24,
                    },
                ],
            },
        }

        def metadata_side_effect(
            media_type,
            media_id,
            source,
            season_numbers=None,
            episode_number=None,
            language=None,
            user=None,
        ):
            del media_id, episode_number, language, user
            if media_type == MediaTypes.ANIME.value and source == Sources.TVDB.value:
                return grouped_series_metadata
            if media_type == "tv_with_seasons" and source == Sources.TVDB.value:
                self.assertEqual(season_numbers, [1, 2])
                return grouped_payload
            return base_metadata

        mock_get_metadata.side_effect = metadata_side_effect
        mock_resolve_provider_series_id.return_value = "76703"
        mock_find_entries_for_mal_id.return_value = [
            {
                "tvdb_id": 76703,
                "tvdb_season": -1,
                "tvdb_epoffset": 0,
                "mal_id": 527,
            },
        ]
        item = Item.objects.create(
            media_id="527",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Pokemon",
            image="https://example.com/pokemon.jpg",
        )
        mock_resolve_detail_metadata.side_effect = lambda *_args, **_kwargs: (
            MetadataResolutionResult(
                display_provider=Sources.MAL.value,
                identity_provider=Sources.MAL.value,
                mapping_status="identity",
                header_metadata={
                    **base_metadata,
                    "details": dict(base_metadata["details"]),
                    "related": dict(base_metadata["related"]),
                    "cast": list(base_metadata["cast"]),
                    "crew": list(base_metadata["crew"]),
                    "studios_full": list(base_metadata["studios_full"]),
                },
                grouped_preview=None,
                provider_media_id=item.media_id,
                grouped_preview_target=None,
            )
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "527",
                    "title": "pokemon",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pokemon, I Choose You!")
        self.assertContains(response, "Pokemon Emergency!")
        self.assertContains(response, "Pallet Party Panic")
        self.assertContains(response, "A Scare in the Air")
        # Each episode row renders card and compact-list layouts, each with
        # its own track button.
        self.assertContains(response, 'title="Track Episode"', count=8)
        self.assertNotContains(
            response,
            "Episode cards are not available from MyAnimeList metadata.",
        )
        preview_episodes = response.context["media"]["episodes"]
        self.assertTrue(all(episode["actions_enabled"] for episode in preview_episodes))
        self.assertEqual(preview_episodes[2]["season_number"], 2)
        self.assertEqual(preview_episodes[2]["episode_number"], 1)
        self.assertEqual(preview_episodes[2]["display_episode_number"], 3)
        self.assertEqual(preview_episodes[2]["source"], Sources.TVDB.value)
        self.assertEqual(preview_episodes[2]["media_id"], "76703")

    @override_settings(TVDB_API_KEY="test-tvdb-key")
    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_flat_mal_anime_details_render_mapped_episode_cards(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
    ):
        base_metadata = {
            "media_id": "52991",
            "title": "Frieren",
            "original_title": "Sousou no Frieren",
            "localized_title": "Frieren",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.MAL.value,
            "source_url": "https://myanimelist.net/anime/52991",
            "max_progress": 3,
            "image": "https://example.com/frieren.jpg",
            "synopsis": "A mage looks back.",
            "details": {"episodes": 3},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }
        grouped_payload = {
            "media_id": "9350138",
            "source": Sources.TVDB.value,
            "media_type": MediaTypes.TV.value,
            "title": "Frieren: Beyond Journey's End",
            "related": {
                "seasons": [
                    {
                        "season_number": 1,
                        "episode_count": 3,
                    },
                ],
            },
            "season/1": {
                "media_id": "9350138",
                "source": Sources.TVDB.value,
                "media_type": MediaTypes.SEASON.value,
                "season_number": 1,
                "season_title": "Season 1",
                "details": {"episodes": 3},
                "episodes": [
                    {
                        "episode_number": 1,
                        "air_date": "2023-09-29",
                        "image": "https://example.com/ep1.jpg",
                        "name": "The Journey's End",
                        "overview": "Episode one.",
                        "runtime": 24,
                    },
                    {
                        "episode_number": 2,
                        "air_date": "2023-10-06",
                        "image": "https://example.com/ep2.jpg",
                        "name": "It Didn't Have to Be Magic...",
                        "overview": "Episode two.",
                        "runtime": 24,
                    },
                    {
                        "episode_number": 3,
                        "air_date": "2023-10-13",
                        "image": "https://example.com/ep3.jpg",
                        "name": "Killing Magic",
                        "overview": "Episode three.",
                        "runtime": 24,
                    },
                ],
            },
        }

        def metadata_side_effect(media_type, *_args, **_kwargs):
            if media_type == "tv_with_seasons":
                return grouped_payload
            return base_metadata

        mock_get_metadata.side_effect = metadata_side_effect
        item = Item.objects.create(
            media_id="52991",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Frieren",
            image="https://example.com/frieren.jpg",
        )
        Anime.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=2,
        )
        mock_resolve_detail_metadata.side_effect = lambda *_args, **_kwargs: (
            MetadataResolutionResult(
                display_provider=Sources.MAL.value,
                identity_provider=Sources.MAL.value,
                mapping_status="identity",
                header_metadata={
                    **base_metadata,
                    "details": dict(base_metadata["details"]),
                    "related": dict(base_metadata["related"]),
                    "cast": list(base_metadata["cast"]),
                    "crew": list(base_metadata["crew"]),
                    "studios_full": list(base_metadata["studios_full"]),
                },
                grouped_preview=None,
                provider_media_id=item.media_id,
                grouped_preview_target=None,
            )
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "52991",
                    "title": "frieren",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The Journey&#x27;s End")
        self.assertContains(response, "It Didn&#x27;t Have to Be Magic...")
        self.assertContains(response, "Killing Magic")
        self.assertNotContains(
            response,
            "Episode cards are not available from MyAnimeList metadata.",
        )
        # The flat entry's progress marks the episodes it covers as watched.
        self.assertEqual(
            [
                episode["watched_by_progress"]
                for episode in response.context["media"]["episodes"]
            ],
            [True, True, False],
        )
        self.assertContains(response, "epWatched: true", count=2)
        self.assertContains(response, "epWatched: false", count=1)

    @override_settings(TVDB_API_KEY="test-tvdb-key")
    @patch("app.views.metadata_resolution.anime_mapping.find_entries_for_mal_id")
    @patch("app.views.anime_mapping.resolve_provider_series_id")
    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_flat_mal_anime_details_paginate_long_episode_preview(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
        mock_resolve_provider_series_id,
        mock_find_entries_for_mal_id,
    ):
        base_metadata = {
            "media_id": "527",
            "title": "Pokemon",
            "original_title": "Pokemon",
            "localized_title": "Pokemon",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.MAL.value,
            "source_url": "https://myanimelist.net/anime/527",
            "max_progress": 30,
            "image": "https://example.com/pokemon.jpg",
            "synopsis": "Pocket monsters.",
            "details": {"episodes": 30},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }
        grouped_series_metadata = {
            "media_id": "76703",
            "source": Sources.TVDB.value,
            "media_type": MediaTypes.ANIME.value,
            "title": "Pokemon",
            "related": {
                "seasons": [
                    {
                        "season_number": 1,
                        "episode_count": 30,
                    },
                ],
            },
        }
        grouped_payload = grouped_series_metadata | {
            "season/1": {
                "media_id": "76703",
                "source": Sources.TVDB.value,
                "media_type": MediaTypes.SEASON.value,
                "season_number": 1,
                "season_title": "Indigo League",
                "details": {"episodes": 30},
                "episodes": [
                    {
                        "episode_number": number,
                        "air_date": f"1997-04-{number:02d}",
                        "image": f"https://example.com/pokemon-{number}.jpg",
                        "name": f"Mapped Episode {number:02d}",
                        "overview": f"Episode {number}.",
                        "runtime": 24,
                    }
                    for number in range(1, 31)
                ],
            },
        }

        def metadata_side_effect(
            media_type,
            media_id,
            source,
            season_numbers=None,
            episode_number=None,
            language=None,
            user=None,
        ):
            del media_id, episode_number, language, user
            if media_type == MediaTypes.ANIME.value and source == Sources.TVDB.value:
                return {
                    **grouped_series_metadata,
                    "related": {
                        "seasons": list(grouped_series_metadata["related"]["seasons"]),
                    },
                }
            if media_type == "tv_with_seasons" and source == Sources.TVDB.value:
                self.assertEqual(season_numbers, [1])
                return {
                    **grouped_payload,
                    "related": {
                        "seasons": list(grouped_payload["related"]["seasons"]),
                    },
                    "season/1": {
                        **grouped_payload["season/1"],
                        "details": dict(grouped_payload["season/1"]["details"]),
                        "episodes": list(grouped_payload["season/1"]["episodes"]),
                    },
                }
            return {
                **base_metadata,
                "details": dict(base_metadata["details"]),
                "related": dict(base_metadata["related"]),
                "cast": list(base_metadata["cast"]),
                "crew": list(base_metadata["crew"]),
                "studios_full": list(base_metadata["studios_full"]),
            }

        mock_get_metadata.side_effect = metadata_side_effect
        mock_resolve_provider_series_id.return_value = "76703"
        mock_find_entries_for_mal_id.return_value = [
            {
                "tvdb_id": 76703,
                "tvdb_season": 1,
                "tvdb_epoffset": 0,
                "mal_id": 527,
            },
        ]
        item = Item.objects.create(
            media_id="527",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Pokemon",
            image="https://example.com/pokemon.jpg",
        )
        mock_resolve_detail_metadata.side_effect = lambda *_args, **_kwargs: (
            MetadataResolutionResult(
                display_provider=Sources.MAL.value,
                identity_provider=Sources.MAL.value,
                mapping_status="identity",
                header_metadata={
                    **base_metadata,
                    "details": dict(base_metadata["details"]),
                    "related": dict(base_metadata["related"]),
                    "cast": list(base_metadata["cast"]),
                    "crew": list(base_metadata["crew"]),
                    "studios_full": list(base_metadata["studios_full"]),
                },
                grouped_preview=None,
                provider_media_id=item.media_id,
                grouped_preview_target=None,
            )
        )

        page_one = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "527",
                    "title": "pokemon",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(page_one.status_code, 200)
        self.assertEqual(len(page_one.context["media"]["episodes"]), 25)
        self.assertEqual(
            page_one.context["episode_load_more"]["label"], "Episodes 26-30"
        )
        self.assertContains(page_one, "Mapped Episode 25")
        self.assertNotContains(page_one, "Mapped Episode 26")
        self.assertContains(page_one, "Show Episodes 26-30")

        page_two = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "527",
                    "title": "pokemon",
                },
            ),
            {"fragment": "secondary", "episode_page": 2},
        )

        self.assertEqual(page_two.status_code, 200)
        self.assertEqual(len(page_two.context["media"]["episodes"]), 5)
        self.assertEqual(
            [
                episode["episode_number"]
                for episode in page_two.context["media"]["episodes"]
            ],
            [26, 27, 28, 29, 30],
        )
        self.assertIsNone(page_two.context["episode_load_more"])
        self.assertContains(page_two, "Mapped Episode 26")
        self.assertContains(page_two, "Mapped Episode 30")
        self.assertNotContains(page_two, "Mapped Episode 25")
        self.assertNotContains(
            page_one,
            reverse(
                "lists_modal",
                args=[Sources.TVDB.value, MediaTypes.EPISODE.value, "76703", 1, 1],
            ),
        )

    @override_settings(TVDB_API_KEY="test-tvdb-key")
    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_grouped_anime_media_details_render_tv_style_subtitle(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
    ):
        self.user.date_format = DateFormatChoices.ISO_8601
        self.user.save(update_fields=["date_format"])
        item = Item.objects.create(
            media_id="9350138",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Frieren: Beyond Journey's End",
            image="https://example.com/frieren-grouped.jpg",
        )
        tv = TV.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="9350138",
            source=Sources.TVDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Season 1",
            image="https://example.com/frieren-season.jpg",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        episode_item = Item.objects.create(
            media_id="9350138",
            source=Sources.TVDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Episode 1",
            image="https://example.com/frieren-episode.jpg",
            season_number=1,
            episode_number=1,
            runtime_minutes=24,
        )
        Episode.objects.create(
            item=episode_item,
            related_season=season,
            end_date=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        )
        Episode.objects.create(
            item=episode_item,
            related_season=season,
            end_date=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
        )
        base_metadata = {
            "media_id": "9350138",
            "title": "Frieren: Beyond Journey's End",
            "original_title": "Sousou no Frieren",
            "localized_title": "Frieren: Beyond Journey's End",
            "media_type": MediaTypes.ANIME.value,
            "identity_media_type": MediaTypes.TV.value,
            "library_media_type": MediaTypes.ANIME.value,
            "source": Sources.TVDB.value,
            "source_url": "https://www.thetvdb.com/dereferrer/series/9350138",
            "max_progress": 28,
            "image": "https://example.com/frieren-grouped.jpg",
            "synopsis": "A mage looks back.",
            "details": {"episodes": 28, "runtime": "24m"},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
            "external_links": {},
        }
        mock_get_metadata.return_value = base_metadata
        mock_resolve_detail_metadata.return_value = MetadataResolutionResult(
            display_provider=Sources.TVDB.value,
            identity_provider=Sources.TVDB.value,
            mapping_status="identity",
            header_metadata=base_metadata,
            grouped_preview=None,
            provider_media_id="9350138",
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TVDB.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "9350138",
                    "title": "frieren-beyond-journeys-end",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Progress: 1/28")
        self.assertContains(response, "2026-03-01 - 2026-03-12")
        self.assertContains(response, "48min watched")
        self.assertContains(
            response,
            'class="mt-4 mb-5 flex flex-wrap gap-2"',
            html=False,
        )
        self.assertNotContains(response, "FIRST PLAYED")
        self.assertNotContains(response, "LAST PLAYED")
        self.assertNotContains(response, "WATCHED HOURS")

    @override_settings(TVDB_API_KEY="test-tvdb-key")
    def test_update_metadata_provider_preference_saves_override(self):
        """Saving a metadata provider override should persist a user preference only."""
        item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image="https://example.com/breaking-bad.jpg",
        )

        response = self.client.post(
            reverse(
                "update_metadata_provider_preference",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "1396",
                },
            ),
            {"provider": Sources.TVDB.value},
        )

        self.assertEqual(response.status_code, 302)
        preference = MetadataProviderPreference.objects.get(user=self.user, item=item)
        self.assertEqual(preference.provider, Sources.TVDB.value)
        item.refresh_from_db()
        self.assertEqual(item.source, Sources.TMDB.value)

    @patch("app.views.services.get_media_metadata")
    def test_update_metadata_provider_preference_snapshots_movie_for_custom(
        self,
        mock_get_metadata,
    ):
        """Switching a tracked item to Custom should snapshot the displayed metadata."""
        item = Item.objects.create(
            media_id="603",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Matrix",
            image="https://example.com/provider-matrix.jpg",
        )
        mock_get_metadata.return_value = {
            "media_id": "603",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "title": "The Matrix",
            "original_title": "The Matrix",
            "localized_title": "The Matrix",
            "image": "https://example.com/provider-matrix.jpg",
            "synopsis": "A computer hacker learns the truth.",
            "genres": ["Action", "Sci-Fi"],
            "max_progress": 1,
            "details": {
                "release_date": "1999-03-31",
                "runtime": "2h 16min",
                "status": "Released",
                "studios": ["Village Roadshow Pictures"],
                "country": "United States of America",
                "languages": ["English"],
            },
            "related": {},
        }

        response = self.client.post(
            reverse(
                "update_metadata_provider_preference",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "603",
                },
            ),
            {"provider": Sources.MANUAL.value},
        )

        self.assertEqual(response.status_code, 302)
        preference = MetadataProviderPreference.objects.get(user=self.user, item=item)
        self.assertEqual(preference.provider, Sources.MANUAL.value)

        item.refresh_from_db()
        self.assertEqual(item.source, Sources.TMDB.value)
        self.assertEqual(item.title, "The Matrix")
        self.assertEqual(item.genres, ["Action", "Sci-Fi"])
        self.assertEqual(item.runtime, "2h 16min")
        self.assertEqual(item.runtime_minutes, 136)
        self.assertEqual(
            item.release_datetime.date().isoformat(),
            "1999-03-31",
        )
        self.assertEqual(item.manual_metadata["title"], "The Matrix")
        self.assertEqual(
            item.manual_metadata["image"],
            "https://example.com/provider-matrix.jpg",
        )
        self.assertEqual(item.manual_metadata["genres"], ["Action", "Sci-Fi"])
        self.assertEqual(
            item.manual_metadata["details"]["release_date"],
            "1999-03-31",
        )

    @patch("app.views.services.get_media_metadata")
    def test_update_metadata_provider_preference_redirects_to_normalized_return_url(
        self,
        mock_get_metadata,
    ):
        """Provider saves should restore encoded list query separators."""
        item = Item.objects.create(
            media_id="603",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Matrix",
            image="https://example.com/provider-matrix.jpg",
        )
        mock_get_metadata.return_value = {
            "media_id": "603",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "title": "The Matrix",
            "image": "https://example.com/provider-matrix.jpg",
            "max_progress": 1,
            "details": {},
            "related": {},
        }

        response = self.client.post(
            reverse(
                "update_metadata_provider_preference",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "603",
                },
            ),
            {
                "provider": Sources.MANUAL.value,
                "return_url": (
                    "/medialist/movie%3Fstatus%3DPlanning&sort%3Drelease_date"
                    "&direction%3Ddesc&layout%3Dgrid"
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            "/medialist/movie?status=Planning&sort=release_date&direction=desc&layout=grid",
        )

        preference = MetadataProviderPreference.objects.get(user=self.user, item=item)
        self.assertEqual(preference.provider, Sources.MANUAL.value)

    @patch("app.views.tmdb.metadata_languages")
    def test_update_metadata_language_preference_saves_override(
        self,
        mock_metadata_languages,
    ):
        """Saving a metadata language override should persist a user preference."""
        mock_metadata_languages.return_value = [
            ("", "Server Default (en-US)"),
            ("fr", "French"),
        ]
        item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image="https://example.com/breaking-bad.jpg",
        )

        response = self.client.post(
            reverse(
                "update_metadata_language_preference",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "1396",
                },
            ),
            {"language": "fr"},
        )

        self.assertEqual(response.status_code, 302)
        preference = MetadataProviderPreference.objects.get(user=self.user, item=item)
        self.assertEqual(preference.language, "fr")
        self.assertEqual(preference.provider, "")

    @patch("app.views.tmdb.metadata_languages")
    def test_update_metadata_language_preference_does_not_clobber_provider(
        self,
        mock_metadata_languages,
    ):
        """Saving a language override should preserve an existing provider override."""
        mock_metadata_languages.return_value = [
            ("", "Server Default (en-US)"),
            ("fr", "French"),
        ]
        item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image="https://example.com/breaking-bad.jpg",
        )
        MetadataProviderPreference.objects.create(
            user=self.user,
            item=item,
            provider=Sources.TVDB.value,
        )

        response = self.client.post(
            reverse(
                "update_metadata_language_preference",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "1396",
                },
            ),
            {"language": "fr"},
        )

        self.assertEqual(response.status_code, 302)
        preference = MetadataProviderPreference.objects.get(user=self.user, item=item)
        self.assertEqual(preference.language, "fr")
        self.assertEqual(preference.provider, Sources.TVDB.value)

    @patch("app.views.tmdb.metadata_languages")
    def test_update_metadata_language_preference_rejects_invalid_language(
        self,
        mock_metadata_languages,
    ):
        """An unrecognized language code should not be persisted."""
        mock_metadata_languages.return_value = [
            ("", "Server Default (en-US)"),
            ("fr", "French"),
        ]
        item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image="https://example.com/breaking-bad.jpg",
        )

        response = self.client.post(
            reverse(
                "update_metadata_language_preference",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "1396",
                },
            ),
            {"language": "not-a-real-code"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            MetadataProviderPreference.objects.filter(
                user=self.user, item=item
            ).exists(),
        )

    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_migrated_flat_anime_shows_grouped_banner(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
    ):
        """Migrated legacy anime routes should link users to the grouped series."""
        base_metadata = {
            "media_id": "52991",
            "title": "Frieren",
            "original_title": "Sousou no Frieren",
            "localized_title": "Frieren",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.MAL.value,
            "max_progress": 28,
            "image": "https://example.com/frieren.jpg",
            "synopsis": "A mage looks back.",
            "details": {"episodes": 28},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }
        mock_get_metadata.return_value = base_metadata
        flat_item = Item.objects.create(
            media_id="52991",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Frieren",
            image="https://example.com/frieren.jpg",
        )
        grouped_item = Item.objects.create(
            media_id="9350138",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Frieren: Beyond Journey's End",
            image="https://example.com/grouped.jpg",
        )
        Anime.all_objects.create(
            item=flat_item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=28,
            migrated_to_item=grouped_item,
            migrated_at=timezone.now(),
        )

        mock_resolve_detail_metadata.return_value = MetadataResolutionResult(
            display_provider=Sources.MAL.value,
            identity_provider=Sources.MAL.value,
            mapping_status="identity",
            header_metadata=base_metadata,
            grouped_preview=None,
            provider_media_id="52991",
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "52991",
                    "title": "frieren",
                },
            ),
            # The migrated banner renders in the secondary fragment.
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "already been migrated to grouped series tracking"
        )
        self.assertContains(response, "Open grouped series")

    @patch("app.providers.services.get_media_metadata")
    def test_game_media_details_renders_cached_iron_meat_hltb_tables(
        self, mock_get_metadata
    ):
        Item.objects.create(
            media_id="129742",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Iron Meat",
            image="https://example.com/iron_meat.jpg",
            provider_external_ids={
                "hltb_game_id": 129742,
                "steam_app_id": 2592160,
                "itch_id": 0,
                "ign_uuid": "84fb8aca-cd19-4ff6-8919-c1b8ef5fa88a",
            },
            provider_game_lengths={
                "active_source": "hltb",
                "hltb": {
                    "game_id": 129742,
                    "url": "https://howlongtobeat.com/game/129742",
                    "summary": {
                        "main_minutes": 512,
                        "main_plus_minutes": 614,
                        "completionist_minutes": 1191,
                        "all_styles_minutes": 555,
                    },
                    "counts": {
                        "main": 1261,
                        "main_plus": 364,
                        "completionist": 108,
                        "all_styles": 1733,
                    },
                    "single_player_table": [
                        {
                            "label": "Main Story",
                            "count": 1261,
                            "average_minutes": 514,
                            "median_minutes": 510,
                            "rushed_minutes": 376,
                            "leisure_minutes": 634,
                        },
                    ],
                    "platform_table": [
                        {
                            "platform": "PC",
                            "count": 1479,
                            "main_minutes": 518,
                            "main_plus_minutes": 624,
                            "completionist_minutes": 1201,
                            "fastest_minutes": 240,
                            "slowest_minutes": 2581,
                        },
                    ],
                    "external_ids": {
                        "steam_app_id": 2592160,
                        "itch_id": 0,
                        "ign_uuid": "84fb8aca-cd19-4ff6-8919-c1b8ef5fa88a",
                    },
                    "raw": {},
                },
                "igdb": {
                    "game_id": 325609,
                    "summary": {
                        "hastily_seconds": 32400,
                        "normally_seconds": 32400,
                        "completely_seconds": 46800,
                        "count": 13,
                    },
                    "raw": [],
                },
            },
            provider_game_lengths_source="hltb",
            provider_game_lengths_match="steam_verified",
        )
        mock_get_metadata.return_value = {
            "media_id": "129742",
            "title": "Iron Meat",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "source_url": "https://www.igdb.com/games/iron-meat",
            "image": "https://example.com/iron_meat.jpg",
            "synopsis": "Test synopsis",
            "details": {
                "format": "Main game",
                "release_date": "2024-09-26",
                "platforms": ["PC", "PlayStation 5"],
            },
            "genres": ["Action"],
            "related": {},
            "external_links": {
                "HowLongToBeat": "https://howlongtobeat.com/?q=Iron+Meat",
            },
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "129742",
                    "title": "iron-meat",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Time to Beat")
        self.assertContains(response, "How Long to Beat")
        self.assertContains(response, "Main Story")
        self.assertContains(
            response, 'href="https://howlongtobeat.com/game/129742"', html=False
        )
        self.assertNotContains(response, "Based on 1,733 submissions.")
        self.assertNotContains(response, "SINGLE-PLAYER")
        self.assertNotContains(response, "Playstyle")
        self.assertEqual(
            response.context["media"]["external_links"]["HowLongToBeat"],
            "https://howlongtobeat.com/game/129742",
        )

    @patch("app.providers.services.get_media_metadata")
    def test_grouped_anime_details_do_not_render_hltb_links(self, mock_get_metadata):
        Item.objects.create(
            media_id="259640",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Sword Art Online",
            image="https://example.com/sao.jpg",
            provider_external_ids={"hltb_game_id": 160618},
        )
        mock_get_metadata.return_value = {
            "media_id": "259640",
            "title": "Sword Art Online",
            "localized_title": "Sword Art Online",
            "media_type": MediaTypes.ANIME.value,
            "identity_media_type": MediaTypes.TV.value,
            "library_media_type": MediaTypes.ANIME.value,
            "source": Sources.TVDB.value,
            "source_url": "https://www.thetvdb.com/dereferrer/series/259640",
            "image": "https://example.com/sao.jpg",
            "synopsis": "Players are trapped inside a virtual world.",
            "details": {"format": "TV"},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
            "external_links": {
                "TVDB": "https://www.thetvdb.com/dereferrer/series/259640",
            },
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TVDB.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "259640",
                    "title": "sword-art-online",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "How Long to Beat")
        self.assertNotIn("HowLongToBeat", response.context["media"]["external_links"])

    @patch("app.media_details_views._queue_game_lengths_refresh", return_value=True)
    @patch("app.providers.services.get_media_metadata")
    def test_game_media_details_renders_igdb_fallback_and_queues_hltb_refresh(
        self,
        mock_get_metadata,
        mock_queue_game_lengths_refresh,
    ):
        Item.objects.create(
            media_id="325609",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Dispatch",
            image="https://example.com/dispatch.jpg",
            provider_game_lengths={
                "active_source": "igdb",
                "igdb": {
                    "game_id": 325609,
                    "summary": {
                        "hastily_seconds": 32400,
                        "normally_seconds": 32400,
                        "completely_seconds": 46800,
                        "count": 13,
                    },
                    "raw": [{"game_id": 325609}],
                },
            },
            provider_game_lengths_source="igdb",
            provider_game_lengths_match="igdb_fallback",
        )
        mock_get_metadata.return_value = {
            "media_id": "325609",
            "title": "Dispatch",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "source_url": "https://www.igdb.com/games/dispatch",
            "image": "https://example.com/dispatch.jpg",
            "synopsis": "Test synopsis",
            "details": {
                "format": "Main game",
                "release_date": "2025-10-22",
                "platforms": ["PC", "PlayStation 5"],
            },
            "genres": ["Action"],
            "related": {},
            "external_links": {
                "HowLongToBeat": "https://howlongtobeat.com/?q=Dispatch",
            },
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "325609",
                    "title": "dispatch",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Internet Games Database")
        self.assertContains(
            response, 'href="https://www.igdb.com/games/dispatch"', html=False
        )
        self.assertContains(response, "Normally")
        self.assertContains(response, "13 submissions")
        mock_queue_game_lengths_refresh.assert_called_once()

    @patch("app.media_details_views._queue_game_lengths_refresh", return_value=True)
    @patch("app.providers.services.get_media_metadata")
    def test_game_media_details_queues_background_fetch_when_missing_game_lengths(
        self,
        mock_get_metadata,
        mock_queue_game_lengths_refresh,
    ):
        mock_get_metadata.return_value = {
            "media_id": "325609",
            "title": "Dispatch",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "source_url": "https://www.igdb.com/games/dispatch",
            "image": "https://example.com/dispatch.jpg",
            "synopsis": "Test synopsis",
            "details": {
                "format": "Main game",
                "release_date": "2025-10-22",
                "platforms": ["PC", "PlayStation 5"],
            },
            "genres": ["Action"],
            "related": {},
            "external_links": {
                "HowLongToBeat": "https://howlongtobeat.com/?q=Dispatch",
            },
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "325609",
                    "title": "dispatch",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "Fetching cached time-to-beat data in the background."
        )
        self.assertTrue(
            Item.objects.filter(
                media_id="325609",
                source=Sources.IGDB.value,
                media_type=MediaTypes.GAME.value,
            ).exists(),
        )
        mock_queue_game_lengths_refresh.assert_called_once()

    @patch("app.providers.services.get_media_metadata")
    @patch("app.media_details_views._queue_game_lengths_refresh")
    def test_game_media_details_shows_pending_when_refresh_lock_exists(
        self,
        mock_queue_game_lengths_refresh,
        mock_get_metadata,
    ):
        item = Item.objects.create(
            media_id="325609",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Dispatch",
            image="https://example.com/dispatch.jpg",
        )
        cache.set(
            game_length_services.get_game_lengths_refresh_lock_key(
                item.id,
                force=False,
                fetch_hltb=True,
            ),
            game_length_services.build_game_lengths_refresh_lock(
                force=False,
                fetch_hltb=True,
            ),
            timeout=game_length_services.GAME_LENGTHS_REFRESH_TTL,
        )
        mock_get_metadata.return_value = {
            "media_id": "325609",
            "title": "Dispatch",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "source_url": "https://www.igdb.com/games/dispatch",
            "image": "https://example.com/dispatch.jpg",
            "synopsis": "Test synopsis",
            "details": {
                "format": "Main game",
                "release_date": "2025-10-22",
                "platforms": ["PC", "PlayStation 5"],
            },
            "genres": ["Action"],
            "related": {},
            "external_links": {
                "HowLongToBeat": "https://howlongtobeat.com/?q=Dispatch",
            },
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "325609",
                    "title": "dispatch",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "Fetching cached time-to-beat data in the background."
        )
        mock_queue_game_lengths_refresh.assert_not_called()

    @patch("app.db_retry.time.sleep")
    @patch("app.views.Item.objects.get_or_create")
    @patch("app.providers.services.get_media_metadata")
    def test_game_media_details_renders_when_auto_create_hits_retryable_lock(
        self,
        mock_get_metadata,
        mock_get_or_create,
        _mock_sleep,
    ):
        sleeping_threads = _record_sleep_threads(_mock_sleep)
        mock_get_or_create.side_effect = OperationalError("database is locked")
        mock_get_metadata.return_value = {
            "media_id": "325609",
            "title": "Dispatch",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "source_url": "https://www.igdb.com/games/dispatch",
            "image": "https://example.com/dispatch.jpg",
            "synopsis": "Test synopsis",
            "details": {
                "format": "Main game",
                "release_date": "2025-10-22",
                "platforms": ["PC", "PlayStation 5"],
            },
            "genres": ["Action"],
            "related": {},
            "external_links": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "325609",
                    "title": "dispatch",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "app/components/detail_secondary_content.html"
        )
        self.assertContains(response, 'id="detail-secondary-content"', html=False)
        self.assertNotContains(
            response,
            "Some metadata updates were deferred because the database is busy.",
        )
        self.assertTrue(response.context["detail_persistence_deferred"])
        self.assertNotIn(threading.get_ident(), sleeping_threads)
        self.assertFalse(
            Item.objects.filter(
                media_id="325609",
                source=Sources.IGDB.value,
                media_type=MediaTypes.GAME.value,
            ).exists(),
        )

    @patch("app.db_retry.time.sleep")
    @patch("app.services.metadata_resolution.ItemProviderLink.objects.update_or_create")
    @patch("app.providers.services.get_media_metadata")
    def test_anime_media_details_does_not_upsert_provider_links(
        self,
        mock_get_metadata,
        mock_update_or_create,
        _mock_sleep,
    ):
        sleeping_threads = _record_sleep_threads(_mock_sleep)
        item = Item.objects.create(
            media_id="52991",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Frieren",
            image="https://example.com/frieren.jpg",
        )
        mock_update_or_create.side_effect = OperationalError("database is locked")
        mock_get_metadata.return_value = {
            "media_id": item.media_id,
            "title": "Frieren",
            "original_title": "Sousou no Frieren",
            "localized_title": "Frieren",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.MAL.value,
            "source_url": "https://myanimelist.net/anime/52991",
            "max_progress": 28,
            "image": "https://example.com/frieren.jpg",
            "synopsis": "A mage looks back.",
            "details": {"episodes": 28},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": item.media_id,
                    "title": "frieren",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            "Some metadata updates were deferred because the database is busy.",
        )
        self.assertFalse(response.context["detail_persistence_deferred"])
        mock_update_or_create.assert_not_called()
        self.assertNotIn(threading.get_ident(), sleeping_threads)

    def test_game_media_details_renders_when_metadata_save_hits_retryable_lock(self):
        # Deterministic starting point: whether the view reaches the metadata
        # save path depends on cached metadata left by earlier tests, which
        # made this assertion order-dependent.
        cache.clear()
        item = Item.objects.create(
            media_id="325609",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Dispatch",
            image="https://example.com/dispatch.jpg",
        )
        base_metadata = {
            "media_id": item.media_id,
            "title": "Dispatch",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "source_url": "https://www.igdb.com/games/dispatch",
            "image": "https://example.com/dispatch.jpg",
            "synopsis": "Test synopsis",
            "details": {
                "format": "Main game",
                "release_date": "2025-10-22",
                "platforms": ["PC", "PlayStation 5"],
            },
            "genres": ["Action"],
            "related": {},
            "external_links": {},
        }

        with (
            patch("app.db_retry.time.sleep"),
            patch(
                "app.providers.services.get_media_metadata", return_value=base_metadata
            ),
            patch(
                "app.views.metadata_utils.apply_item_metadata", return_value=["genres"]
            ),
            patch(
                "app.views.Item.save",
                side_effect=OperationalError("database is locked"),
            ),
        ):
            response = self.client.get(
                reverse(
                    "media_details",
                    kwargs={
                        "source": Sources.IGDB.value,
                        "media_type": MediaTypes.GAME.value,
                        "media_id": item.media_id,
                        "title": "dispatch",
                    },
                ),
                {"fragment": "secondary"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            "Some metadata updates were deferred because the database is busy.",
        )
        self.assertTrue(response.context["detail_persistence_deferred"])

    @patch("app.db_retry.time.sleep")
    @patch("app.views.credits.sync_item_credits_from_metadata")
    @patch("app.views.metadata_utils.apply_item_metadata", return_value=[])
    @patch("app.views.metadata_resolution.resolve_detail_metadata")
    @patch("app.providers.services.get_media_metadata")
    def test_tv_media_details_renders_when_credit_sync_hits_retryable_lock(
        self,
        mock_get_metadata,
        mock_resolve_detail_metadata,
        _mock_apply_item_metadata,
        mock_sync_credits,
        _mock_sleep,
    ):
        sleeping_threads = _record_sleep_threads(_mock_sleep)
        item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image="https://example.com/breaking-bad.jpg",
        )
        base_metadata = {
            "media_id": item.media_id,
            "title": "Breaking Bad",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "source_url": "https://www.themoviedb.org/tv/1396",
            "image": "https://example.com/breaking-bad.jpg",
            "synopsis": "Chemistry teacher cooks meth.",
            "details": {"episodes": 62},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }
        mock_get_metadata.return_value = base_metadata
        mock_resolve_detail_metadata.return_value = MetadataResolutionResult(
            display_provider=Sources.TMDB.value,
            identity_provider=Sources.TMDB.value,
            mapping_status="identity",
            header_metadata=base_metadata,
            grouped_preview=None,
            provider_media_id=item.media_id,
        )
        mock_sync_credits.side_effect = OperationalError("database is locked")

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": item.media_id,
                    "title": "breaking-bad",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            "Some metadata updates were deferred because the database is busy.",
        )
        self.assertTrue(response.context["detail_persistence_deferred"])
        self.assertNotIn(threading.get_ident(), sleeping_threads)

    @patch("integrations.tasks.fetch_collection_metadata_for_item.delay")
    @patch("app.views.credits.sync_item_credits_from_metadata")
    @patch("app.views.metadata_utils.apply_item_metadata", return_value=[])
    @patch("app.providers.services.get_media_metadata")
    def test_tvdb_tv_media_details_syncs_credits(
        self,
        mock_get_metadata,
        _mock_apply_item_metadata,
        mock_sync_credits,
        _mock_fetch_delay,
    ):
        """A TVDB-sourced TV item should also get its cast/crew persisted (#868)."""
        item = Item.objects.create(
            media_id="81189",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image="https://example.com/breaking-bad.jpg",
        )
        base_metadata = {
            "media_id": item.media_id,
            "title": "Breaking Bad",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TVDB.value,
            "source_url": "https://www.thetvdb.com/dereferrer/series/81189",
            "image": "https://example.com/breaking-bad.jpg",
            "synopsis": "Chemistry teacher cooks meth.",
            "details": {"episodes": 62},
            "related": {},
            "cast": [
                {
                    "person_id": "291115",
                    "name": "Bryan Cranston",
                    "image": "https://example.com/bryan.jpg",
                    "role": "Walter White",
                },
            ],
            "crew": [],
            "studios_full": [],
        }
        mock_get_metadata.return_value = base_metadata

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TVDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": item.media_id,
                    "title": "breaking-bad",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        mock_sync_credits.assert_called_once_with(item, base_metadata)

    @patch(
        "app.media_details_views._queue_game_lengths_refresh",
        side_effect=RuntimeError("queue unavailable"),
    )
    @patch("app.providers.services.get_media_metadata")
    def test_game_media_details_renders_when_refresh_enqueue_raises(
        self,
        mock_get_metadata,
        _mock_queue_game_lengths_refresh,
    ):
        Item.objects.create(
            media_id="325609",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Dispatch",
            image="https://example.com/dispatch.jpg",
        )
        mock_get_metadata.return_value = {
            "media_id": "325609",
            "title": "Dispatch",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "source_url": "https://www.igdb.com/games/dispatch",
            "image": "https://example.com/dispatch.jpg",
            "synopsis": "Test synopsis",
            "details": {
                "format": "Main game",
                "release_date": "2025-10-22",
                "platforms": ["PC", "PlayStation 5"],
            },
            "genres": ["Action"],
            "related": {},
            "external_links": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "325609",
                    "title": "dispatch",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            "Some metadata updates were deferred because the database is busy.",
        )
        self.assertTrue(response.context["detail_persistence_deferred"])

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_view(self, mock_process_episodes, mock_get_metadata):
        """Season details should render the shell before deferred episode work."""
        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }

        mock_process_episodes.return_value = [
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 1,
                "name": "Episode 1",
                "air_date": "2023-01-01",
                "watched": False,
            },
        ]

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/media_details.html")
        self.assertIn("media", response.context)
        self.assertEqual(response.context["media"]["title"], "Season 1")
        self.assertContains(response, 'id="detail-secondary-content"', html=False)
        self.assertContains(response, "fragment=secondary")
        self.assertNotContains(response, "Episode 1")
        self.assertEqual(response.context["display_provider"], Sources.TMDB.value)
        self.assertEqual(response.context["identity_provider"], Sources.TMDB.value)
        mock_process_episodes.assert_not_called()

        mock_get_metadata.assert_called_once_with(
            "tv_with_seasons",
            "1668",
            Sources.TMDB.value,
            [1],
            language="en",
        )

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_view_anonymous_public(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        """Anonymous users viewing a public list should not hit watch_provider_region."""
        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }
        mock_process_episodes.return_value = []

        self.client.logout()
        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
            {"public_view": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["watch_provider_region"])

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_public_season_notes_use_the_originating_list(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        """Public season details use the selected list owner's notes."""
        tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Season 1",
        )
        Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
            notes="Public season note",
        )
        public_list = CustomList.objects.create(
            name="Season Notes",
            owner=self.user,
            visibility="public",
            include_notes=True,
        )
        CustomListItem.objects.create(custom_list=public_list, item=season_item)
        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }
        mock_process_episodes.return_value = []

        self.client.logout()
        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
            {
                "fragment": "secondary",
                "public_view": "1",
                "public_list": public_list.public_reference,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["notes_entry"].notes, "Public season note")
        self.assertContains(response, "Public season note")

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_anime_season_details_finds_season_tracked_via_tv_identity(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        """The anime-bucket season page must find a season tracked via TV identity.

        Regression test for GitHub issue #623: a TVDB show tracked both as
        anime and with its seasons tracked via `Season.related_tv` pointing
        at the TV-identity `TV` row used to always show "Add to tracker" on
        the anime-identity season page, even though the season was already
        tracked.
        """
        tv_item = Item.objects.create(
            media_id="79824",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            title="Naruto Shippuden",
            image="http://example.com/image.jpg",
            library_media_type=MediaTypes.TV.value,
        )
        tv_identity = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="79824",
            source=Sources.TVDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=0,
            title="Naruto Shippuden",
            image="http://example.com/season.jpg",
            library_media_type=MediaTypes.TV.value,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv_identity,
            status=Status.COMPLETED.value,
        )

        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Naruto Shippuden",
            "media_id": "79824",
            "source": Sources.TVDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/0": {
                "title": "Specials",
                "season_title": "Specials",
                "media_id": "79824",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TVDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }
        mock_process_episodes.return_value = []

        response = self.client.get(
            reverse(
                "anime_season_details",
                kwargs={
                    "source": Sources.TVDB.value,
                    "media_id": "79824",
                    "title": "naruto-shippuden",
                    "season_number": 0,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_instance"], season)
        self.assertNotContains(response, "Add to tracker")

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_anime_season_details_numbered_season_via_tv_identity(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        """Same as season 0 above, but for a normal numbered season."""
        tv_item = Item.objects.create(
            media_id="79824",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            title="Naruto Shippuden",
            image="http://example.com/image.jpg",
            library_media_type=MediaTypes.TV.value,
        )
        tv_identity = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="79824",
            source=Sources.TVDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Naruto Shippuden",
            image="http://example.com/season.jpg",
            library_media_type=MediaTypes.TV.value,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv_identity,
            status=Status.IN_PROGRESS.value,
        )

        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Naruto Shippuden",
            "media_id": "79824",
            "source": Sources.TVDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "season_title": "Season 1",
                "media_id": "79824",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TVDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }
        mock_process_episodes.return_value = []

        response = self.client.get(
            reverse(
                "anime_season_details",
                kwargs={
                    "source": Sources.TVDB.value,
                    "media_id": "79824",
                    "title": "naruto-shippuden",
                    "season_number": 1,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_instance"], season)
        self.assertNotContains(response, "Add to tracker")

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_episode_details_view_anonymous_public(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        """Anonymous users viewing a public list should reach the episode page, not login."""
        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }
        mock_process_episodes.return_value = []

        self.client.logout()
        response = self.client.get(
            reverse(
                "episode_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                    "episode_number": 1,
                },
            ),
            {"public_view": "1"},
        )

        self.assertEqual(response.status_code, 200)

    @patch("app.views.tmdb.episode", return_value={})
    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_episode_details_prev_next_episode_numbers(
        self,
        mock_process_episodes,
        mock_get_metadata,
        _mock_episode,
    ):
        """Episode context exposes prev/next episode numbers, None at season edges."""
        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [{"episode_number": number} for number in range(1, 4)],
            },
        }
        mock_process_episodes.return_value = [
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": number,
                "title": f"Episode {number}",
                "air_date": f"2023-01-{number:02d}",
                "watched": False,
            }
            for number in range(1, 4)
        ]

        def get_episode_details(episode_number):
            return self.client.get(
                reverse(
                    "episode_details",
                    kwargs={
                        "source": Sources.TMDB.value,
                        "media_id": "1668",
                        "title": "test-tv-show",
                        "season_number": 1,
                        "episode_number": episode_number,
                    },
                ),
            )

        first = get_episode_details(1)
        self.assertEqual(first.status_code, 200)
        self.assertIsNone(first.context["prev_episode_number"])
        self.assertEqual(first.context["next_episode_number"], 2)
        first_content = first.content.decode()
        self.assertEqual(first_content.count('aria-label="Select episode"'), 2)
        self.assertIn('@click="open = !open"', first_content)
        self.assertIn("Episode 1", first_content)
        self.assertIn("Episode 2", first_content)
        self.assertNotIn(
            'class="flex h-11 w-full items-center justify-center space-x-2 px-4',
            first_content,
        )

        middle = get_episode_details(2)
        self.assertEqual(middle.status_code, 200)
        self.assertEqual(middle.context["prev_episode_number"], 1)
        self.assertEqual(middle.context["next_episode_number"], 3)

        last = get_episode_details(3)
        self.assertEqual(last.status_code, 200)
        self.assertEqual(last.context["prev_episode_number"], 2)
        self.assertIsNone(last.context["next_episode_number"])

    @patch("app.views.tmdb.episode")
    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_episode_details_honors_metadata_language(
        self,
        mock_process_episodes,
        mock_get_metadata,
        mock_episode,
    ):
        """Episode details must fetch season/episode metadata in the user's language.

        Movies and season pages already pass metadata_language; episode details
        previously always used TMDB_LANG (English), so History → episode showed
        English titles/synopses even when the preference was Italian/Spanish.
        """
        self.user.metadata_language = "it"
        self.user.save(update_fields=["metadata_language"])

        mock_get_metadata.return_value = {
            "title": "Those About to Die",
            "media_id": "218589",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Those About to Die",
                "season_title": "Stagione 1",
                "media_id": "218589",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [
                    {
                        "episode_number": 10,
                        "name": "Che i giochi abbiano inizio",
                        "overview": "Finale di stagione in italiano.",
                    }
                ],
            },
        }
        mock_process_episodes.return_value = [
            {
                "media_id": "218589",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 10,
                "title": "Che i giochi abbiano inizio",
                "overview": "Finale di stagione in italiano.",
                "air_date": "2024-07-18",
                "watched": True,
                "history": [],
            }
        ]
        mock_episode.return_value = {
            "episode_title": "Che i giochi abbiano inizio",
            "title": "Those About to Die",
            "cast": [],
            "crew": [],
        }

        response = self.client.get(
            reverse(
                "episode_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "218589",
                    "title": "those-about-to-die",
                    "season_number": 1,
                    "episode_number": 10,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["episode_title"],
            "Che i giochi abbiano inizio",
        )
        self.assertContains(response, "Finale di stagione in italiano.")

        mock_get_metadata.assert_called()
        _args, kwargs = mock_get_metadata.call_args
        self.assertEqual(kwargs.get("language"), "it")
        self.assertEqual(kwargs.get("user"), self.user)

        mock_episode.assert_called_once_with(
            "218589",
            1,
            10,
            language="it",
        )

    @patch("app.views.tmdb.episode", return_value={})
    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_public_episode_notes_use_the_originating_list(
        self,
        mock_process_episodes,
        mock_get_metadata,
        _mock_episode,
    ):
        """Public episode details use the selected list owner's notes."""
        tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Season 1",
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            title="Episode 1",
        )
        Episode.objects.create(
            item=episode_item,
            related_season=season,
            notes="Public episode note",
        )
        public_list = CustomList.objects.create(
            name="Episode Notes",
            owner=self.user,
            visibility="public",
            include_notes=True,
        )
        CustomListItem.objects.create(custom_list=public_list, item=episode_item)
        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }
        mock_process_episodes.return_value = []

        self.client.logout()
        response = self.client.get(
            reverse(
                "episode_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                    "episode_number": 1,
                },
            ),
            {"public_view": "1", "public_list": public_list.public_reference},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["notes_entry"].notes, "Public episode note")
        self.assertContains(response, "Public episode note")

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_secondary_fragment_renders_episodes(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }

        mock_process_episodes.return_value = [
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 1,
                "title": "Episode 1",
                "air_date": "2023-01-01",
                "actions_enabled": False,
            },
        ]

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "app/components/detail_secondary_content.html"
        )
        self.assertContains(response, "Episode 1")
        self.assertContains(
            response,
            reverse(
                "lists_modal",
                args=[Sources.TMDB.value, MediaTypes.EPISODE.value, "1668", 1, 1],
            ),
        )
        self.assertNotContains(
            response,
            'class="flex flex-col md:flex-row gap-8 md:gap-10 mb-2 md:mb-8"',
            html=False,
        )
        mock_process_episodes.assert_called_once()

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_secondary_fragment_skips_link_for_unnumbered_episode(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        """An episode with no numeric episode_number must not 500 via NoReverseMatch.

        Regression test for a follow-up finding on issue #1132: coercing a bad
        provider episode number to None (instead of leaving the raw bad value)
        must not crash the {% url 'episode_details' ... episode_number=... %}
        reversal in detail_secondary_content.html, which requires an int.
        """
        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }

        mock_process_episodes.return_value = [
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": None,
                "title": "Unannounced episode",
                "air_date": None,
                "actions_enabled": False,
            },
        ]

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unannounced episode")

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_renders_with_non_numeric_sibling_season_max_progress(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        """A sibling season card with a non-numeric max_progress must not 500.

        Regression test for issue #1132: bad provider data (e.g. a non-numeric
        episode count) previously reached {% blocktranslate count %} unmodified
        and raised TemplateSyntaxError, 500ing the whole season page.
        """
        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "related": {
                "seasons": [
                    {
                        "media_id": "1668",
                        "media_type": MediaTypes.SEASON.value,
                        "source": Sources.TMDB.value,
                        "season_number": 2,
                        "title": "Test TV Show",
                        "season_title": "Season 2",
                        "image": "http://example.com/season2.jpg",
                        "max_progress": "TBA",
                        "episode_count": "TBA",
                        "progress": 1,
                    },
                ],
            },
            "season/1": {
                "title": "Season 1",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
                "related": {
                    "seasons": [
                        {
                            "media_id": "1668",
                            "media_type": MediaTypes.SEASON.value,
                            "source": Sources.TMDB.value,
                            "season_number": 2,
                            "title": "Test TV Show",
                            "season_title": "Season 2",
                            "image": "http://example.com/season2.jpg",
                            "max_progress": "TBA",
                            "episode_count": "TBA",
                            "progress": 1,
                        },
                    ],
                },
            },
        }

        mock_process_episodes.return_value = []

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_renders_with_unknown_sibling_season_max_progress(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        """A season picker entry with no episode count must not 500 the page.

        Regression test for issue #1159: TVDB's series payload carries no
        episode count per season, so every related-season entry has
        max_progress=None. That None reached the season picker's
        {% blocktranslate count %} in detail_title_block.html and raised
        TemplateSyntaxError, 500ing every TVDB show's season page. The
        earlier #1132 fix only covered media_card.html and the secondary
        fragment, so the full page render stayed broken.
        """
        sibling_season = {
            "media_id": "1668",
            "media_type": MediaTypes.SEASON.value,
            "source": Sources.TVDB.value,
            "season_number": 2,
            "title": "Test TV Show",
            "season_title": "Season 2",
            "image": "http://example.com/season2.jpg",
            "max_progress": None,
            "episode_count": None,
        }
        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TVDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "related": {"seasons": [sibling_season]},
            "season/1": {
                "title": "Season 1",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TVDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
                "related": {"seasons": [sibling_season]},
            },
        }
        mock_process_episodes.return_value = []

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TVDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_persists_and_self_heals_episode_release_datetime(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        """Viewing a season page must persist release_datetime for every episode.

        Regression test: episode Items created while browsing a season page
        (as opposed to individually tracking/watching one) previously only
        picked up runtime/rating from the live provider payload and silently
        dropped release_datetime, leaving it NULL forever unless that exact
        episode was later tracked or a manual metadata sync ran. This left
        "not caught up" categorization permanently wrong for any unwatched
        episode created this way.
        """
        tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
            image="http://example.com/show.jpg",
        )
        related_tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TVDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            image="http://example.com/show.jpg",
            season_number=1,
        )
        Season.objects.create(
            item=season_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            related_tv=related_tv,
        )

        # Episode 1 already has a trusted release_datetime — must not be
        # clobbered by a differing value from the live payload.
        already_dated_episode = Item.objects.create(
            media_id="1668",
            source=Sources.TVDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="No Shortcuts",
            image=settings.IMG_NONE,
            season_number=1,
            episode_number=1,
            release_datetime=timezone.make_aware(datetime(2023, 1, 1)),
        )
        # Episode 2 exists (e.g. created by an earlier page view before its
        # air date was known) but was never given a release_datetime.
        stale_episode = Item.objects.create(
            media_id="1668",
            source=Sources.TVDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test TV Show",
            image=settings.IMG_NONE,
            season_number=1,
            episode_number=2,
            release_datetime=None,
        )
        concurrent_episode = Item.objects.create(
            media_id="1668",
            source=Sources.TVDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test TV Show",
            image=settings.IMG_NONE,
            season_number=1,
            episode_number=6,
            release_datetime=None,
        )
        concurrent_trusted_date = datetime(2024, 2, 2, tzinfo=UTC)

        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TVDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Test TV Show",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [
                    {
                        "episode_number": 1,
                        "name": "No Shortcuts",
                        "air_date": "2023-06-01",  # differs from the stored date
                        "runtime": 48,
                    },
                    {
                        "episode_number": 2,
                        "name": "Episode 2",
                        # TVDB normalizes its date-only value to an aware
                        # datetime before this view receives it.
                        "air_date": datetime(
                            2023,
                            1,
                            8,
                            tzinfo=timezone.get_fixed_timezone(840),
                        ),
                        "runtime": 42,
                    },
                    {
                        "episode_number": 3,
                        "name": "Episode 3",
                        "air_date": "2023-01-15",
                        "runtime": 44,
                    },
                    {
                        "episode_number": 4,
                        "name": "Episode 4",
                        "air_date": "0001-01-01",
                        "runtime": 45,
                    },
                    {
                        "episode_number": 5,
                        "name": "Episode 5",
                        "air_date": "9999-12-31",
                        "runtime": 46,
                    },
                    {
                        "episode_number": 6,
                        "name": "Episode 6",
                        "air_date": "2023-01-22",
                        "runtime": 47,
                    },
                ],
            },
        }
        mock_process_episodes.return_value = []

        original_bulk_update = Item.objects.bulk_update

        def bulk_update_after_concurrent_date(objects, fields, batch_size=None):
            Item.objects.filter(pk=concurrent_episode.pk).update(
                release_datetime=concurrent_trusted_date,
            )
            return original_bulk_update(objects, fields, batch_size=batch_size)

        with (
            patch.object(
                Item.objects,
                "bulk_update",
                side_effect=bulk_update_after_concurrent_date,
            ),
            patch("app.providers.trakt.is_configured", return_value=False),
            timezone.override("Pacific/Kiritimati"),
        ):
            response = self.client.get(
                reverse(
                    "season_details",
                    kwargs={
                        "source": Sources.TVDB.value,
                        "media_id": "1668",
                        "title": "test-tv-show",
                        "season_number": 1,
                    },
                ),
                {"fragment": "secondary"},
            )

        self.assertEqual(response.status_code, 200)

        already_dated_episode.refresh_from_db()
        self.assertEqual(
            already_dated_episode.release_datetime,
            timezone.make_aware(datetime(2023, 1, 1)),
            "an already-set release_datetime must not be overwritten",
        )

        stale_episode.refresh_from_db()
        self.assertEqual(
            stale_episode.release_datetime,
            date_parser("2023-01-08"),
            "a NULL release_datetime must self-heal from the live payload",
        )

        new_episode = Item.objects.get(
            media_id="1668",
            source=Sources.TVDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=3,
        )
        self.assertEqual(
            new_episode.release_datetime,
            date_parser("2023-01-15"),
            "a newly created episode must get release_datetime from the same payload",
        )
        self.assertIsNone(
            Item.objects.get(
                media_id="1668",
                source=Sources.TVDB.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=4,
            ).release_datetime,
            "unknown-date sentinels must remain NULL",
        )
        self.assertIsNone(
            Item.objects.get(
                media_id="1668",
                source=Sources.TVDB.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=5,
            ).release_datetime,
            "year-9999 unknown-date sentinels must remain NULL",
        )
        concurrent_episode.refresh_from_db()
        self.assertEqual(
            concurrent_episode.release_datetime,
            concurrent_trusted_date,
            "an intervening trusted date must survive the metadata batch update",
        )

    @override_settings(TIME_ZONE="Europe/Berlin")
    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_persists_date_only_air_date_as_unknown_time(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        """A date-only air_date must not shift or look released early.

        Regression test: the per-episode self-heal loop used to interpret a
        "YYYY-MM-DD" air_date as midnight in a timezone, which either shifted
        the stored release_datetime into the previous UTC day or made release
        state treat the episode as released part-way through its listed date.
        Date-only values now use the unknown-time sentinel.
        """
        tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
            image="http://example.com/show.jpg",
        )
        related_tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            image="http://example.com/show.jpg",
            season_number=1,
        )
        Season.objects.create(
            item=season_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            related_tv=related_tv,
        )

        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Test TV Show",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [
                    {
                        "episode_number": 1,
                        "name": "No Shortcuts",
                        "air_date": "2026-07-15",
                        "runtime": 48,
                    },
                ],
            },
        }
        mock_process_episodes.return_value = []

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)

        new_episode = Item.objects.get(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
        )
        self.assertEqual(
            new_episode.release_datetime,
            date_parser("2026-07-15"),
            "a date-only air_date must use the unknown-time sentinel, "
            "regardless of the server's configured TIME_ZONE",
        )

    @patch("app.views.trakt_popularity_service.refresh_trakt_popularity")
    @patch("app.providers.tmdb.get_tvdb_episode_image_map")
    @patch("app.helpers.get_tmdb_backdrop_image")
    @patch("app.providers.services.get_media_metadata")
    def test_season_details_missing_provider_metadata_marks_local_only_and_skips_fallback_network_calls(
        self,
        mock_get_metadata,
        mock_get_tmdb_backdrop_image,
        mock_get_tvdb_episode_image_map,
        mock_refresh_trakt_popularity,
    ):
        tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
            image="http://example.com/show.jpg",
        )
        related_tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            image="http://example.com/show.jpg",
            season_number=16,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            related_tv=related_tv,
        )
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Auditions: Europe (1)",
            image="http://example.com/episode.jpg",
            season_number=16,
            episode_number=1,
            release_datetime=timezone.make_aware(datetime(2026, 4, 15)),
        )
        Episode.objects.create(
            item=episode_item,
            related_season=season,
            end_date=timezone.make_aware(datetime(2026, 4, 17)),
        )
        mock_get_tmdb_backdrop_image.reset_mock()
        mock_get_tvdb_episode_image_map.reset_mock()
        mock_refresh_trakt_popularity.reset_mock()
        mock_get_metadata.return_value = {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/show.jpg",
            "synopsis": "Provider show synopsis.",
            "genres": ["Reality"],
            "related": {"seasons": [{"season_number": 15}]},
        }

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 16,
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        season_item.refresh_from_db()
        self.assertEqual(
            season_item.provider_metadata_status,
            ProviderMetadataStatus.LOCAL_ONLY_MISSING_SEASON.value,
        )
        self.assertEqual(
            response.context["media"]["provider_metadata_status"],
            ProviderMetadataStatus.LOCAL_ONLY_MISSING_SEASON.value,
        )
        # The banner itself renders in the shell; the fragment exposes the
        # local-only flag through context.
        self.assertTrue(response.context["season_provider_metadata_is_local_only"])
        self.assertContains(response, "Auditions: Europe (1)")
        self.assertIn(
            16,
            [
                season_entry["season_number"]
                for season_entry in response.context["tv"]["related"]["seasons"]
            ],
        )
        mock_get_tmdb_backdrop_image.assert_not_called()
        mock_get_tvdb_episode_image_map.assert_not_called()
        mock_refresh_trakt_popularity.assert_not_called()

    @patch("app.views.trakt_popularity_service.refresh_trakt_popularity")
    @patch("app.providers.tmdb.get_tvdb_episode_image_map")
    @patch("app.helpers.get_tmdb_backdrop_image")
    @patch("app.providers.services.get_media_metadata")
    def test_season_details_local_only_flag_skips_provider_metadata_lookup(
        self,
        mock_get_metadata,
        mock_get_tmdb_backdrop_image,
        mock_get_tvdb_episode_image_map,
        mock_refresh_trakt_popularity,
    ):
        tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
            image="http://example.com/show.jpg",
        )
        related_tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            image="http://example.com/show.jpg",
            season_number=16,
            provider_metadata_status=(
                ProviderMetadataStatus.LOCAL_ONLY_MISSING_SEASON.value
            ),
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            related_tv=related_tv,
        )
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Auditions: Europe (1)",
            image="http://example.com/episode.jpg",
            season_number=16,
            episode_number=1,
            release_datetime=timezone.make_aware(datetime(2026, 4, 15)),
        )
        Episode.objects.create(
            item=episode_item,
            related_season=season,
            end_date=timezone.make_aware(datetime(2026, 4, 17)),
        )
        mock_get_metadata.reset_mock()
        mock_get_tmdb_backdrop_image.reset_mock()
        mock_get_tvdb_episode_image_map.reset_mock()
        mock_refresh_trakt_popularity.reset_mock()

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 16,
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        # The banner itself renders in the shell; the fragment exposes the
        # local-only flag through context.
        self.assertTrue(response.context["season_provider_metadata_is_local_only"])
        self.assertContains(response, "Auditions: Europe (1)")
        mock_get_metadata.assert_not_called()
        mock_get_tmdb_backdrop_image.assert_not_called()
        mock_get_tvdb_episode_image_map.assert_not_called()
        mock_refresh_trakt_popularity.assert_not_called()

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_roll_up_season_level_collection_entry_when_no_episode_rows_exist(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        """Season details should treat a manual season entry as the full season."""
        Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
            image="http://example.com/image.jpg",
        )
        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Season 1",
            image="http://example.com/season.jpg",
            season_number=1,
        )
        Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Episode 1",
            image="http://example.com/ep1.jpg",
            season_number=1,
            episode_number=1,
        )
        Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Episode 2",
            image="http://example.com/ep2.jpg",
            season_number=1,
            episode_number=2,
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=season_item,
            media_type="digital",
            resolution="1080p",
        )

        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }
        mock_process_episodes.return_value = [
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 1,
                "name": "Episode 1",
                "air_date": "2023-01-01",
                "watched": False,
            },
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 2,
                "name": "Episode 2",
                "air_date": "2023-01-08",
                "watched": False,
            },
        ]

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["collection_stats"],
            {
                "collected_episodes": 2,
                "total_episodes": 2,
            },
        )
        self.assertContains(response, "COLLECTED EPISODES")
        self.assertContains(response, "2/2")

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_paginate_long_episode_lists(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }
        mock_process_episodes.return_value = [
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": number,
                "title": f"Long Episode {number:02d}",
                "image": "http://example.com/episode.jpg",
                "air_date": f"2023-01-{number:02d}",
                "overview": "",
                "runtime": "24m",
            }
            for number in range(1, 31)
        ]

        page_one = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(page_one.status_code, 200)
        self.assertEqual(len(page_one.context["media"]["episodes"]), 25)
        self.assertEqual(
            page_one.context["episode_load_more"]["label"], "Episodes 26-30"
        )
        self.assertContains(page_one, "Long Episode 25")
        self.assertNotContains(page_one, "Long Episode 26")
        self.assertContains(page_one, "Show Episodes 26-30")

        page_two = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
            {"fragment": "secondary", "episode_page": 2},
        )

        self.assertEqual(page_two.status_code, 200)
        self.assertEqual(len(page_two.context["media"]["episodes"]), 5)
        self.assertIsNone(page_two.context["episode_load_more"])
        self.assertContains(page_two, "Long Episode 26")
        self.assertContains(page_two, "Long Episode 30")
        self.assertNotContains(page_two, "Long Episode 25")

    @patch("app.views.trakt_popularity_service.refresh_trakt_popularity")
    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_refreshes_and_renders_trakt_score(
        self,
        mock_process_episodes,
        mock_get_metadata,
        mock_refresh_trakt_popularity,
    ):
        def _refresh(item, *, route_media_type, force):
            item.trakt_rating = 7.88048
            item.trakt_rating_count = 1849
            item.trakt_popularity_rank = 25
            item.trakt_popularity_score = 998.1
            item.trakt_popularity_fetched_at = timezone.now()
            item.save(
                update_fields=[
                    "trakt_rating",
                    "trakt_rating_count",
                    "trakt_popularity_rank",
                    "trakt_popularity_score",
                    "trakt_popularity_fetched_at",
                ],
            )
            return {
                "rating": item.trakt_rating,
                "votes": item.trakt_rating_count,
                "score": item.trakt_popularity_score,
                "rank": item.trakt_popularity_rank,
            }

        mock_refresh_trakt_popularity.side_effect = _refresh
        show_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
            image="http://example.com/image.jpg",
        )
        related_tv = TV.objects.create(
            item=show_item,
            user=self.user,
            status=Status.COMPLETED.value,
        )
        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Season 1",
            image="http://example.com/season.jpg",
            season_number=1,
        )
        Season.objects.create(
            item=season_item,
            user=self.user,
            status=Status.COMPLETED.value,
            related_tv=related_tv,
        )
        mock_get_metadata.return_value = {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }
        mock_process_episodes.return_value = []

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["trakt_score"]["rating"], 7.8)
        self.assertEqual(response.context["trakt_score"]["rating_count"], 1849)
        mock_refresh_trakt_popularity.assert_called_once()
        self.assertTrue(
            Item.objects.filter(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                season_number=1,
                trakt_rating=7.88048,
                trakt_rating_count=1849,
            ).exists(),
        )

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_prefers_stored_item_image_over_provider_image(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        mock_process_episodes.return_value = []
        Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            image="https://images.example.com/custom-season.jpg",
            season_number=1,
        )
        mock_get_metadata.return_value = {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/provider-season.jpg",
                "episodes": [],
            },
        }

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["media"]["image"],
            "https://images.example.com/custom-season.jpg",
        )

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_swaps_show_and_season_heading_sizes(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        mock_process_episodes.return_value = []
        mock_get_metadata.return_value = {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Test TV Show",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "score": 7.6,
                "score_count": 42000,
                "episodes": [],
            },
        }

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertRegex(
            content,
            r'<div class="mb-1 text-center md:text-start">\s*<div class="inline-flex items-center gap-2 md:flex md:gap-2">\s*<h1 class="text-3xl font-bold cursor-pointer hover:text-indigo-500 transition-colors duration-200">\s*<a href="[^"]+">Test TV Show</a>\s*</h1>',
        )
        self.assertIn(
            'class="relative flex flex-col gap-y-4 md:flex-row md:gap-y-0 items-center justify-between mb-1"',
            content,
        )
        self.assertIn(
            'aria-label="Select season"',
            content,
        )
        self.assertIn(
            '@click="open = !open"',
            content,
        )
        self.assertEqual(content.count('aria-label="Select season"'), 2)
        self.assertNotIn(
            'class="flex w-full items-center justify-center space-x-2 px-4 py-2',
            content,
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_hides_low_value_alternative_title_tooltip_for_english_locale(
        self,
        mock_get_metadata,
    ):
        mock_get_metadata.return_value = {
            "media_id": "15121",
            "title": "The Sound of Music",
            "original_title": "サウンド・オブ・ミュージック",
            "localized_title": "The Sound of Music",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "synopsis": "Test overview",
            "details": {},
            "related": {},
        }

        with override("en"):
            response = self.client.get(
                reverse(
                    "media_details",
                    kwargs={
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.MOVIE.value,
                        "media_id": "15121",
                        "title": "the-sound-of-music",
                    },
                ),
            )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('<h1 class="text-3xl font-bold">The Sound of Music</h1>', content)
        self.assertNotIn('aria-label="Show alternative title"', content)

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_anime_season_details_use_tooltip_for_alt_season_title(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        mock_process_episodes.return_value = []
        Item.objects.create(
            media_id="259640",
            source=Sources.TVDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=MediaTypes.ANIME.value,
            title="Sword Art Online",
            image="https://example.com/sao.jpg",
        )
        mock_get_metadata.return_value = {
            "title": "Sword Art Online",
            "media_id": "259640",
            "source": Sources.TVDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "https://example.com/sao.jpg",
            "season/3": {
                "title": "Sword Art Online",
                "season_title": "Alicization",
                "media_id": "259640",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TVDB.value,
                "image": "https://example.com/alicization.jpg",
                "episodes": [],
            },
        }

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TVDB.value,
                    "media_id": "259640",
                    "title": "sword-art-online",
                    "season_number": 3,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertRegex(
            content,
            r'<h1 class="text-3xl font-bold cursor-pointer hover:text-indigo-500 transition-colors duration-200">\s*<a href="[^"]+">Sword Art Online</a>\s*</h1>\s*<div class="relative shrink-0"',
        )
        self.assertIn('aria-label="Show alternative title"', content)
        self.assertIn(
            'aria-label="Select season"',
            content,
        )
        self.assertIn(">Season 3</span>", content)
        self.assertIn("<p>Alicization</p>", content)

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_details_renders_progress_and_date_subtitle_without_history_card(
        self,
        mock_process_episodes,
        mock_get_metadata,
    ):
        self.user.date_format = DateFormatChoices.ISO_8601
        self.user.time_format = TimeFormatChoices.HH_MM
        self.user.save(update_fields=["date_format", "time_format"])
        mock_process_episodes.return_value = []
        show_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
            image="http://example.com/image.jpg",
        )
        related_tv = TV.objects.create(
            item=show_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Season 1",
            image="http://example.com/season.jpg",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=related_tv,
            status=Status.IN_PROGRESS.value,
        )
        Episode.objects.create(
            item=Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Episode 1",
                image="http://example.com/episode1.jpg",
                season_number=1,
                episode_number=1,
            ),
            related_season=season,
            end_date=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        )
        Episode.objects.create(
            item=Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Episode 2",
                image="http://example.com/episode2.jpg",
                season_number=1,
                episode_number=2,
            ),
            related_season=season,
            end_date=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
        )
        mock_get_metadata.return_value = {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Test TV Show",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "max_progress": 8,
                "episodes": [],
            },
        }

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(
            'class="mb-3 flex flex-col items-center justify-between gap-y-4 md:mb-1 md:flex-row md:gap-y-0"',
            content,
        )
        self.assertIn(
            'aria-label="Select season"',
            content,
        )
        self.assertEqual(content.count('aria-label="Select season"'), 2)
        history_buttons = re.findall(
            r'<button[^>]*aria-label="View play history"[^>]*>.*?</button>',
            content,
            flags=re.DOTALL,
        )
        self.assertEqual(len(history_buttons), 2)
        self.assertTrue(
            any("season-progress-desktop-" in button for button in history_buttons),
        )
        for button in history_buttons:
            self.assertNotIn("Season 1", button)
        self.assertIn("Progress: 2/8", content)
        self.assertIn("2026-03-01 12:00 - 2026-03-12 12:00", content)
        self.assertNotIn(
            '<h2 class="text-sm font-medium text-[var(--color-text-muted)]">Season 1</h2>',
            content,
        )
        self.assertIn(
            'hx-get="/history/sessions?media_type=tv',
            content,
        )
        trigger_start = content.index('hx-get="/history/sessions')
        trigger_url = content[trigger_start:].split('"', 2)[1]
        self.assertEqual(
            unescape(trigger_url),
            "/history/sessions?media_type=tv&media_id=1668&source=tmdb&season_number=1",
        )
        self.assertEqual(content.count('id="session-history-modal-host"'), 1)
        self.assertNotIn("Your History", content)

    @patch("integrations.tasks.fetch_collection_metadata_for_item.delay")
    @patch("app.providers.services.get_media_metadata")
    def test_game_details_skips_collection_autofetch(
        self,
        mock_get_metadata,
        mock_fetch_delay,
    ):
        """Game details should not trigger collection auto-fetch."""
        mock_get_metadata.return_value = {
            "media_id": "game-123",
            "title": "Test Game",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "image": "http://example.com/game.jpg",
            "overview": "Test overview",
            "release_date": "2023-01-01",
        }

        Item.objects.create(
            media_id="game-123",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Test Game",
            image="http://example.com/game.jpg",
        )

        PlexAccount.objects.create(
            user=self.user,
            plex_token="plex-token",
            plex_username="plex-user",
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "game-123",
                    "title": "test-game",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["fetching_collection_data"])
        self.assertIsNone(response.context["item_id_for_polling"])
        mock_fetch_delay.assert_not_called()

    @patch("integrations.tasks.fetch_collection_metadata_for_item.delay")
    @patch("app.views.credits.sync_item_credits_from_metadata")
    @patch("app.views.metadata_utils.apply_item_metadata", return_value=[])
    @patch("app.providers.services.get_media_metadata")
    def test_movie_details_queue_cached_only_collection_autofetch(
        self,
        mock_get_metadata,
        _mock_apply_item_metadata,
        _mock_sync_credits,
        mock_fetch_delay,
    ):
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/movie.jpg",
        )
        mock_get_metadata.return_value = {
            "media_id": item.media_id,
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "source_url": "https://www.themoviedb.org/movie/238",
            "image": "http://example.com/movie.jpg",
            "synopsis": "Test synopsis",
            "details": {},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }

        PlexAccount.objects.create(
            user=self.user,
            plex_token="plex-token",
            plex_username="plex-user",
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": item.media_id,
                    "title": "test-movie",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["fetching_collection_data"])
        self.assertEqual(response.context["item_id_for_polling"], item.id)
        mock_fetch_delay.assert_called_once_with(
            user_id=self.user.id,
            item_id=item.id,
            lookup_policy="cached_only",
        )

    @patch("integrations.tasks.fetch_collection_metadata_for_item.delay")
    @patch("app.providers.tmdb.process_episodes", return_value=[])
    @patch("app.providers.services.get_media_metadata")
    def test_season_collection_uses_the_correct_show_identity(
        self,
        mock_get_metadata,
        _mock_process_episodes,
        mock_fetch_delay,
    ):
        PlexAccount.objects.create(
            user=self.user,
            plex_token="plex-token",
            plex_username="plex-user",
        )
        cases = (
            ("season_details", MediaTypes.TV.value, MediaTypes.TV.value, False),
            (
                "anime_season_details",
                MediaTypes.TV.value,
                MediaTypes.ANIME.value,
                False,
            ),
            (
                "anime_season_details",
                MediaTypes.ANIME.value,
                MediaTypes.ANIME.value,
                False,
            ),
            ("anime_season_details", MediaTypes.TV.value, MediaTypes.TV.value, True),
        )
        for index, (route, identity_type, library_type, tracked) in enumerate(cases):
            with self.subTest(route=route, identity=identity_type, tracked=tracked):
                media_id = str(166800 + index)
                show_item = Item.objects.create(
                    media_id=media_id,
                    source=Sources.TMDB.value,
                    media_type=identity_type,
                    library_media_type=library_type,
                    title="Test TV Show",
                )
                Item.objects.create(
                    media_id=media_id,
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.TV.value,
                    library_media_type=(
                        MediaTypes.ANIME.value
                        if library_type == MediaTypes.TV.value
                        else MediaTypes.TV.value
                    ),
                    title="Other library identity",
                )
                season_item = Item.objects.create(
                    media_id=media_id,
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.SEASON.value,
                    library_media_type=(
                        library_type
                        if tracked
                        else (
                            MediaTypes.ANIME.value
                            if route == "anime_season_details"
                            else MediaTypes.SEASON.value
                        )
                    ),
                    season_number=1,
                    title="Season 1",
                )
                mock_get_metadata.return_value = {
                    "title": "Test TV Show",
                    "media_id": media_id,
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "image": "http://example.com/show.jpg",
                    "season/1": {
                        "title": "Season 1",
                        "season_title": "Season 1",
                        "media_id": media_id,
                        "media_type": MediaTypes.SEASON.value,
                        "source": Sources.TMDB.value,
                        "image": "http://example.com/season.jpg",
                        "episodes": [],
                    },
                }
                if tracked:
                    show = TV.objects.create(
                        item=show_item,
                        user=self.user,
                        status=Status.PLANNING.value,
                    )
                    Season.objects.create(
                        item=season_item,
                        user=self.user,
                        related_tv=show,
                        status=Status.PLANNING.value,
                    )
                mock_fetch_delay.reset_mock()

                with self.assertNoLogs("app.season_details_views", level="ERROR"):
                    response = self.client.get(
                        reverse(
                            route,
                            kwargs={
                                "source": Sources.TMDB.value,
                                "media_id": media_id,
                                "title": "test-tv-show",
                                "season_number": 1,
                            },
                        ),
                        {"fragment": "secondary"},
                    )

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["fetching_collection_data"])
                self.assertEqual(response.context["item_id_for_polling"], show_item.id)
                mock_fetch_delay.assert_called_once_with(
                    user_id=self.user.id,
                    item_id=show_item.id,
                    lookup_policy="cached_only",
                )

    @patch("integrations.tasks.fetch_collection_metadata_for_item.delay")
    @patch("app.providers.tmdb.process_episodes", return_value=[])
    @patch("app.providers.services.get_media_metadata")
    def test_season_details_queue_cached_only_collection_autofetch(
        self,
        mock_get_metadata,
        _mock_process_episodes,
        mock_fetch_delay,
    ):
        show_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
            image="http://example.com/show.jpg",
        )
        Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Season 1",
            image="http://example.com/season.jpg",
        )
        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/show.jpg",
            "season/1": {
                "title": "Season 1",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [],
            },
        }

        PlexAccount.objects.create(
            user=self.user,
            plex_token="plex-token",
            plex_username="plex-user",
        )

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                },
            ),
            # Collection auto-fetch runs when rendering the secondary fragment.
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["fetching_collection_data"])
        self.assertEqual(response.context["item_id_for_polling"], show_item.id)
        mock_fetch_delay.assert_called_once_with(
            user_id=self.user.id,
            item_id=show_item.id,
            lookup_policy="cached_only",
        )

    @patch("app.views._should_queue_game_lengths_refresh", return_value=False)
    @patch("app.providers.services.get_media_metadata")
    def test_game_details_backfills_studio_credits_and_renders_links(
        self,
        mock_get_metadata,
        _mock_should_queue_game_lengths_refresh,
    ):
        """Game details should sync studio credits and render studio detail links."""
        stale_metadata = {
            "media_id": "1942",
            "title": "The Witcher 3: Wild Hunt",
            "media_type": MediaTypes.GAME.value,
            "source": Sources.IGDB.value,
            "source_url": "https://www.igdb.com/games/the-witcher-3-wild-hunt",
            "image": "http://example.com/witcher3.jpg",
            "synopsis": "A test game synopsis",
            "details": {
                "format": "Main game",
                "release_date": "2015-05-19",
                "companies": "CD Projekt Red",
            },
            "related": {},
        }
        refreshed_metadata = {
            **stale_metadata,
            "studios_full": [
                {
                    "studio_id": "1",
                    "name": "CD Projekt Red",
                    "logo": "https://images.igdb.com/igdb/image/upload/t_logo_med/logo123.png",
                },
            ],
        }
        call_count = {"count": 0}

        def _metadata_side_effect(*_args, **_kwargs):
            call_count["count"] += 1
            if call_count["count"] == 1:
                return stale_metadata
            return refreshed_metadata

        mock_get_metadata.side_effect = _metadata_side_effect

        item = Item.objects.create(
            media_id="1942",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="The Witcher 3: Wild Hunt",
            image="http://example.com/witcher3.jpg",
        )

        cache_key = f"{Sources.IGDB.value}_{MediaTypes.GAME.value}_1942"
        cache.set(cache_key, stale_metadata)

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.IGDB.value,
                    "media_type": MediaTypes.GAME.value,
                    "media_id": "1942",
                    "title": "the-witcher-3-wild-hunt",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(call_count["count"], 2)
        self.assertContains(response, "CD Projekt Red")
        self.assertContains(
            response,
            reverse(
                "studio_detail",
                kwargs={
                    "source": Sources.IGDB.value,
                    "studio_id": "1",
                    "name": "cd-projekt-red",
                },
            ),
        )
        studio = Studio.objects.get(
            source=Sources.IGDB.value,
            source_studio_id="1",
        )
        self.assertTrue(
            ItemStudioCredit.objects.filter(
                item=item,
                studio=studio,
            ).exists(),
        )
        self.assertEqual(len(response.context["studios_linked"]), 1)
        self.assertEqual(
            response.context["studios_linked"][0]["name"], "CD Projekt Red"
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_renders_cast_and_crew_links(self, mock_get_metadata):
        """Movie details should render cast/crew links to person pages."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "source_url": "https://www.themoviedb.org/movie/238",
            "image": "http://example.com/image.jpg",
            "synopsis": "Test synopsis",
            "details": {"format": "Movie"},
            "cast": [
                {
                    "person_id": "10",
                    "name": "John Actor",
                    "role": "Hero",
                },
            ],
            "crew": [
                {
                    "person_id": "11",
                    "name": "Jane Director",
                    "role": "Director",
                    "department": "Directing",
                },
            ],
            "studios_full": [
                {
                    "studio_id": "20",
                    "name": "Studio One",
                },
            ],
        }

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
            # Cast/crew render in the deferred secondary fragment.
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "John Actor")
        self.assertContains(response, "Jane Director")
        self.assertContains(response, "Studio One")
        self.assertContains(
            response,
            reverse(
                "person_detail",
                kwargs={
                    "source": Sources.TMDB.value,
                    "person_id": "10",
                    "name": "john-actor",
                },
            ),
        )

    @patch("app.providers.tmdb._build_specials_season_from_tvdb", return_value=None)
    @patch("app.providers.tmdb.services.api_request")
    def test_tv_details_keep_cast_and_crew_after_status_saves_and_reopen(
        self,
        mock_api_request,
        mock_build_specials,
    ):
        """Saving TV tracking statuses should not remove cast/crew from reopened details."""
        tmdb.cache.clear()
        self.client.login(**self.credentials)
        append_requests = []

        def _mock_api_request(source, _method, url, params=None):
            self.assertEqual(source, Sources.TMDB.value)
            path_parts = urlparse(url).path.rstrip("/").split("/")
            self.assertGreaterEqual(len(path_parts), 3)
            self.assertEqual(path_parts[-2], "tv")
            media_id = path_parts[-1]

            append_to_response = params["append_to_response"]
            append_requests.append(append_to_response)
            response = {
                "id": int(media_id),
                "name": "Breaking Bad",
                "original_name": "Breaking Bad",
                "poster_path": "/breaking-bad.jpg",
                "overview": "A chemistry teacher turns to crime.",
                "genres": [],
                "vote_average": 9.5,
                "vote_count": 1000,
                "production_companies": [],
                "production_countries": [],
                "spoken_languages": [],
                "recommendations": {"results": []},
                "external_ids": {"tvdb_id": "81189"},
                "watch/providers": {"results": {}},
                "episode_run_time": [47],
                "first_air_date": "2008-01-20",
                "last_air_date": "2013-09-29",
                "status": "Ended",
                "number_of_seasons": 1,
                "number_of_episodes": 7,
                "seasons": [
                    {
                        "season_number": 1,
                        "name": "Season 1",
                        "air_date": "2008-01-20",
                        "episode_count": 7,
                        "poster_path": "/season1.jpg",
                    },
                ],
            }

            if "aggregate_credits" in append_to_response:
                response["aggregate_credits"] = {
                    "cast": [
                        {
                            "id": 10,
                            "name": "John Actor",
                            "profile_path": None,
                            "known_for_department": "Acting",
                            "gender": 2,
                            "order": 0,
                            "roles": [
                                {
                                    "character": "Walter White",
                                    "episode_count": 7,
                                },
                            ],
                        },
                    ],
                    "crew": [
                        {
                            "id": 11,
                            "name": "Jane Director",
                            "profile_path": None,
                            "known_for_department": "Directing",
                            "gender": 1,
                            "department": "Directing",
                            "order": 0,
                            "jobs": [{"job": "Director"}],
                        },
                    ],
                }

            if "alternative_titles" in append_to_response:
                response["alternative_titles"] = {"results": []}

            if "season/1" in append_to_response:
                response["season/1"] = {
                    "name": "Season 1",
                    "overview": "Season overview",
                    "season_number": 1,
                    "poster_path": "/season1.jpg",
                    "air_date": "2008-01-20",
                    "vote_average": 9.0,
                    "episodes": [
                        {
                            "episode_number": 1,
                            "name": "Pilot",
                            "overview": "Episode overview",
                            "still_path": None,
                            "runtime": 58,
                            "vote_count": 100,
                            "air_date": "2008-01-20",
                        },
                    ],
                }
                response["season/1/watch/providers"] = {"results": {}}

            return response

        mock_api_request.side_effect = _mock_api_request
        for index, status in enumerate(Status.values, start=1396):
            with self.subTest(status=status):
                media_id = str(index)
                detail_url = reverse(
                    "media_details",
                    kwargs={
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.TV.value,
                        "media_id": media_id,
                        "title": "breaking-bad",
                    },
                )

                # Cast/crew render in the deferred secondary fragment.
                untracked_response = self.client.get(
                    detail_url,
                    {"fragment": "secondary"},
                )

                self.assertEqual(untracked_response.status_code, 200)
                self.assertContains(untracked_response, "John Actor")
                self.assertContains(untracked_response, "Jane Director")

                save_response = self.client.post(
                    f"{reverse('media_save')}?next={detail_url}",
                    {
                        "media_id": media_id,
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.TV.value,
                        "status": status,
                        "score": "",
                        "notes": "",
                    },
                )

                self.assertEqual(save_response.status_code, 302)
                self.assertEqual(save_response.url, detail_url)
                self.assertTrue(
                    TV.objects.filter(
                        user=self.user,
                        item__media_id=media_id,
                        item__source=Sources.TMDB.value,
                        status=status,
                    ).exists(),
                )

                tracked_response = self.client.get(
                    detail_url,
                    {"fragment": "secondary"},
                )
                reopened_response = self.client.get(
                    detail_url,
                    {"fragment": "secondary"},
                )

                self.assertEqual(tracked_response.status_code, 200)
                self.assertContains(tracked_response, "John Actor")
                self.assertContains(tracked_response, "Jane Director")
                self.assertEqual(reopened_response.status_code, 200)
                self.assertContains(reopened_response, "John Actor")
                self.assertContains(reopened_response, "Jane Director")

        self.assertTrue(any("season/0" in request for request in append_requests))
        self.assertTrue(
            all(
                "aggregate_credits" in request
                for request in append_requests
                if "season/0" in request
            ),
        )
        self.assertGreaterEqual(mock_build_specials.call_count, 1)

    @patch("app.providers.services.get_media_metadata")
    def test_tv_details_view_adds_specials_from_regular_path(self, mock_get_metadata):
        """TV details should show a specials season when season 0 is enriched."""

        def metadata_side_effect(
            media_type,
            media_id,
            source,
            season_numbers=None,
            episode_number=None,
            language=None,
            user=None,
        ):
            del episode_number, language, user
            self.assertEqual(media_id, "114410")
            self.assertEqual(source, Sources.TMDB.value)
            if media_type == MediaTypes.TV.value:
                return {
                    "media_id": "114410",
                    "title": "Chainsaw Man",
                    "media_type": MediaTypes.TV.value,
                    "source": Sources.TMDB.value,
                    "image": "http://example.com/show.jpg",
                    "tvdb_id": "10196540",
                    "details": {
                        "runtime": "24m",
                        "first_air_date": "2022-10-12",
                    },
                    "related": {
                        "seasons": [
                            {
                                "source": Sources.TMDB.value,
                                "media_type": MediaTypes.SEASON.value,
                                "media_id": "114410",
                                "title": "Chainsaw Man",
                                "season_number": 1,
                                "season_title": "Chainsaw Man",
                                "image": settings.IMG_NONE,
                            },
                        ],
                    },
                    "cast": [{"name": "Denji"}],
                    "crew": [{"name": "Makima"}],
                    "studios_full": [],
                }

            self.assertEqual(media_type, "tv_with_seasons")
            if season_numbers == [0]:
                return {
                    "season/0": {
                        "season_number": 0,
                        "season_title": "Specials",
                    },
                    "related": {
                        "seasons": [
                            {
                                "source": Sources.TMDB.value,
                                "media_type": MediaTypes.SEASON.value,
                                "media_id": "114410",
                                "title": "Chainsaw Man",
                                "season_number": 0,
                                "season_title": "Specials",
                                "image": "http://example.com/specials.jpg",
                            },
                            {
                                "source": Sources.TMDB.value,
                                "media_type": MediaTypes.SEASON.value,
                                "media_id": "114410",
                                "title": "Chainsaw Man",
                                "season_number": 1,
                                "season_title": "Chainsaw Man",
                                "image": "http://example.com/season1.jpg",
                            },
                        ],
                    },
                }

            self.assertEqual(season_numbers, [0, 1])
            return {
                "season/0": {
                    "season_number": 0,
                    "season_title": "Specials",
                    "image": "http://example.com/specials.jpg",
                    "details": {"episodes": 1},
                },
                "season/1": {
                    "season_number": 1,
                    "season_title": "Season 1",
                    "image": "http://example.com/season1.jpg",
                    "details": {"episodes": 12},
                },
            }

        mock_get_metadata.side_effect = metadata_side_effect

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "114410",
                    "title": "chainsaw-man",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        seasons = response.context["media"]["related"]["seasons"]
        self.assertEqual(seasons[0]["item"]["season_number"], 0)
        self.assertContains(
            response,
            reverse(
                "season_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "114410",
                    "title": "chainsaw-man",
                    "season_number": 0,
                },
            ),
        )
        self.assertEqual(mock_get_metadata.call_count, 3)
        self.assertEqual(
            mock_get_metadata.call_args_list[0].args,
            (
                MediaTypes.TV.value,
                "114410",
                Sources.TMDB.value,
            ),
        )
        self.assertEqual(
            mock_get_metadata.call_args_list[1].args,
            (
                "tv_with_seasons",
                "114410",
                Sources.TMDB.value,
                [0],
            ),
        )
        self.assertEqual(
            mock_get_metadata.call_args_list[2].args,
            (
                "tv_with_seasons",
                "114410",
                Sources.TMDB.value,
                [0, 1],
            ),
        )

    @patch("app.providers.services.get_media_metadata")
    def test_tv_details_view_uses_special_watch_for_show_end_date(
        self,
        mock_get_metadata,
    ):
        """TV details should show the most recent special watch in the history card."""
        watched_main = datetime(2023, 8, 28, 12, 0, tzinfo=UTC)
        watched_special = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)

        tv_item = Item.objects.create(
            media_id="114410",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Chainsaw Man",
            image="http://example.com/show.jpg",
        )
        tv = TV.objects.create(
            user=self.user,
            item=tv_item,
            status=Status.IN_PROGRESS.value,
        )

        season_one_item = Item.objects.create(
            media_id="114410",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Chainsaw Man",
            image="http://example.com/season1.jpg",
            season_number=1,
        )
        season_one = Season.objects.create(
            user=self.user,
            item=season_one_item,
            related_tv=tv,
            status=Status.COMPLETED.value,
        )
        Episode.objects.create(
            item=Item.objects.create(
                media_id="114410",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Episode 12",
                image="http://example.com/ep12.jpg",
                season_number=1,
                episode_number=12,
            ),
            related_season=season_one,
            end_date=watched_main,
        )

        specials_item = Item.objects.create(
            media_id="114410",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Chainsaw Man",
            image="http://example.com/specials.jpg",
            season_number=0,
        )
        specials = Season.objects.create(
            user=self.user,
            item=specials_item,
            related_tv=tv,
            status=Status.COMPLETED.value,
        )
        Episode.objects.create(
            item=Item.objects.create(
                media_id="114410",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Special 1",
                image="http://example.com/s00e01.jpg",
                season_number=0,
                episode_number=1,
            ),
            related_season=specials,
            end_date=watched_special,
        )

        mock_get_metadata.return_value = {
            "media_id": "114410",
            "title": "Chainsaw Man",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/show.jpg",
            "details": {
                "runtime": "24m",
            },
            "related": {
                "seasons": [
                    {
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.SEASON.value,
                        "media_id": "114410",
                        "title": "Chainsaw Man",
                        "season_number": 0,
                        "season_title": "Specials",
                        "image": "http://example.com/specials.jpg",
                    },
                    {
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.SEASON.value,
                        "media_id": "114410",
                        "title": "Chainsaw Man",
                        "season_number": 1,
                        "season_title": "Season 1",
                        "image": "http://example.com/season1.jpg",
                    },
                ],
            },
            "cast": [],
            "crew": [],
            "studios_full": [],
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "114410",
                    "title": "chainsaw-man",
                },
            ),
            # Season cards render in the deferred secondary fragment.
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_instance"].end_date, watched_special)
        self.assertEqual(response.context["current_instance"].progress, 12)
        titles = self._card_titles(response)
        self.assertIn("Specials", titles)
        self.assertIn("Season 1", titles)

    @patch("app.providers.services.get_media_metadata")
    def test_anime_details_view_renders_named_season_card_titles(
        self, mock_get_metadata
    ):
        def metadata_side_effect(
            media_type,
            media_id,
            source,
            season_numbers=None,
            episode_number=None,
            language=None,
            user=None,
        ):
            del episode_number, language, user
            self.assertEqual(media_id, "76703")
            self.assertEqual(source, Sources.TVDB.value)
            if media_type == MediaTypes.ANIME.value:
                return {
                    "media_id": "76703",
                    "title": "Pokemon",
                    "media_type": MediaTypes.ANIME.value,
                    "source": Sources.TVDB.value,
                    "image": "https://example.com/pokemon.jpg",
                    "details": {
                        "episodes": 82,
                    },
                    "related": {
                        "seasons": [
                            {
                                "source": Sources.TVDB.value,
                                "media_type": MediaTypes.SEASON.value,
                                "media_id": "76703",
                                "title": "Pokemon",
                                "season_number": 1,
                                "season_title": "Pokemon",
                                "image": "https://example.com/indigo-league.jpg",
                            },
                            {
                                "source": Sources.TVDB.value,
                                "media_type": MediaTypes.SEASON.value,
                                "media_id": "76703",
                                "title": "Pokemon",
                                "season_number": 2,
                                "season_title": "Pokemon",
                                "image": "https://example.com/orange-islands.jpg",
                            },
                        ],
                    },
                    "cast": [],
                    "crew": [],
                    "studios_full": [],
                }

            self.assertEqual(media_type, "tv_with_seasons")
            self.assertEqual(season_numbers, [1, 2])
            return {
                "season/1": {
                    "season_number": 1,
                    "season_title": "Indigo League",
                    "image": "https://example.com/indigo-league.jpg",
                    "details": {"episodes": 52},
                },
                "season/2": {
                    "season_number": 2,
                    "season_title": "Orange Islands",
                    "image": "https://example.com/orange-islands.jpg",
                    "details": {"episodes": 36},
                },
            }

        mock_get_metadata.side_effect = metadata_side_effect

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TVDB.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "76703",
                    "title": "pokemon",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        titles = self._card_titles(response)
        self.assertIn("Indigo League", titles)
        self.assertIn("Orange Islands", titles)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_backfills_author_credits_and_renders_links(
        self,
        mock_get_metadata,
    ):
        mock_get_metadata.return_value = {
            "media_id": "OL123M",
            "title": "Linked Book",
            "media_type": MediaTypes.BOOK.value,
            "source": Sources.OPENLIBRARY.value,
            "source_url": "https://openlibrary.org/books/OL123M",
            "image": "http://example.com/book.jpg",
            "synopsis": "Book synopsis",
            "max_progress": 300,
            "details": {
                "author": ["Open Author"],
                "publish_date": "2000-01-01",
            },
            "authors_full": [
                {
                    "person_id": "OL1A",
                    "name": "Open Author",
                    "image": "http://example.com/author.jpg",
                    "role": "Author",
                    "sort_order": 0,
                },
            ],
            "related": {},
        }

        item = Item.objects.create(
            media_id="OL123M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Linked Book",
            image="http://example.com/book.jpg",
        )
        Book.objects.create(
            user=self.user,
            item=item,
            status=Status.COMPLETED.value,
            progress=300,
            start_date=timezone.now(),
            end_date=timezone.now(),
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.OPENLIBRARY.value,
                    "media_type": MediaTypes.BOOK.value,
                    "media_id": "OL123M",
                    "title": "linked-book",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        author_person = Person.objects.get(
            source=Sources.OPENLIBRARY.value,
            source_person_id="OL1A",
        )
        self.assertTrue(
            ItemPersonCredit.objects.filter(
                item=item,
                person=author_person,
                role_type=CreditRoleType.AUTHOR.value,
            ).exists(),
        )
        self.assertContains(
            response,
            reverse(
                "person_detail",
                kwargs={
                    "source": Sources.OPENLIBRARY.value,
                    "person_id": "OL1A",
                    "name": "open-author",
                },
            ),
        )
        html = response.content.decode()
        self.assertEqual(
            html.count(
                'text-sm font-semibold text-[var(--color-text-muted)]">AUTHOR</h3>'
            ),
            1,
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_uses_authors_full_fallback_without_item(
        self, mock_get_metadata
    ):
        mock_get_metadata.return_value = {
            "media_id": "72274276213",
            "title": "Metadata Only Manga",
            "media_type": MediaTypes.MANGA.value,
            "source": Sources.MANGAUPDATES.value,
            "source_url": "https://www.mangaupdates.com/series/72274276213",
            "image": "http://example.com/manga.jpg",
            "synopsis": "Manga synopsis",
            "details": {
                "authors": ["Manga Author"],
            },
            "authors_full": [
                {
                    "person_id": "55",
                    "name": "Manga Author",
                    "image": "http://example.com/manga-author.jpg",
                    "role": "Author",
                    "sort_order": 0,
                },
            ],
            "related": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MANGAUPDATES.value,
                    "media_type": MediaTypes.MANGA.value,
                    "media_id": "72274276213",
                    "title": "metadata-only-manga",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ItemPersonCredit.objects.count(), 0)
        self.assertContains(
            response,
            reverse(
                "person_detail",
                kwargs={
                    "source": Sources.MANGAUPDATES.value,
                    "person_id": "55",
                    "name": "manga-author",
                },
            ),
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_refreshes_stale_author_cache_and_renders_links(
        self,
        mock_get_metadata,
    ):
        stale_metadata = {
            "media_id": "OL999M",
            "title": "Cached Book",
            "media_type": MediaTypes.BOOK.value,
            "source": Sources.OPENLIBRARY.value,
            "source_url": "https://openlibrary.org/books/OL999M",
            "image": "http://example.com/book.jpg",
            "synopsis": "Book synopsis",
            "max_progress": 320,
            "details": {
                "author": ["Cached Author"],
                "publish_date": "1999-01-01",
            },
            "related": {},
        }
        refreshed_metadata = {
            **stale_metadata,
            "authors_full": [
                {
                    "person_id": "OL9A",
                    "name": "Cached Author",
                    "image": "http://example.com/author.jpg",
                    "role": "Author",
                    "sort_order": 0,
                },
            ],
        }
        call_count = {"count": 0}

        def _metadata_side_effect(*_args, **_kwargs):
            call_count["count"] += 1
            if call_count["count"] == 1:
                return stale_metadata
            return refreshed_metadata

        mock_get_metadata.side_effect = _metadata_side_effect

        item = Item.objects.create(
            media_id="OL999M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Cached Book",
            image="http://example.com/book.jpg",
        )
        Book.objects.create(
            user=self.user,
            item=item,
            status=Status.COMPLETED.value,
            progress=320,
            start_date=timezone.now(),
            end_date=timezone.now(),
        )

        cache_key = f"{Sources.OPENLIBRARY.value}_{MediaTypes.BOOK.value}_OL999M"
        cache.set(cache_key, stale_metadata)

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.OPENLIBRARY.value,
                    "media_type": MediaTypes.BOOK.value,
                    "media_id": "OL999M",
                    "title": "cached-book",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        detail_calls = [
            call
            for call in mock_get_metadata.call_args_list
            if call.args[:3]
            == (
                MediaTypes.BOOK.value,
                "OL999M",
                Sources.OPENLIBRARY.value,
            )
        ]
        self.assertGreaterEqual(len(detail_calls), 2)
        self.assertContains(
            response,
            reverse(
                "person_detail",
                kwargs={
                    "source": Sources.OPENLIBRARY.value,
                    "person_id": "OL9A",
                    "name": "cached-author",
                },
            ),
        )

        author_person = Person.objects.get(
            source=Sources.OPENLIBRARY.value,
            source_person_id="OL9A",
        )
        self.assertTrue(
            ItemPersonCredit.objects.filter(
                item=item,
                person=author_person,
                role_type=CreditRoleType.AUTHOR.value,
            ).exists(),
        )

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_refreshes_stale_tmdb_tv_credit_cache(
        self,
        mock_get_metadata,
    ):
        stale_metadata = {
            "media_id": "1396",
            "title": "Breaking Bad",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "source_url": "https://www.themoviedb.org/tv/1396",
            "image": "http://example.com/breaking-bad.jpg",
            "synopsis": "A chemistry teacher turns to crime.",
            "details": {
                "format": "TV",
                "status": "Ended",
                "seasons": 1,
                "episodes": 7,
                "runtime": "47m",
            },
            "cast": [],
            "crew": [],
            "providers": {},
            "related": {"seasons": [], "recommendations": []},
        }
        refreshed_metadata = {
            **stale_metadata,
            "cast": [
                {
                    "person_id": "10",
                    "name": "John Actor",
                    "role": "Walter White",
                    "image": "http://example.com/john-actor.jpg",
                },
            ],
            "crew": [
                {
                    "person_id": "11",
                    "name": "Jane Director",
                    "role": "Director",
                    "department": "Directing",
                    "image": "http://example.com/jane-director.jpg",
                },
            ],
        }
        detail_call_count = {"count": 0}

        def _metadata_side_effect(media_type, media_id, source, *args, **kwargs):
            self.assertEqual(
                (media_type, media_id, source),
                (MediaTypes.TV.value, "1396", Sources.TMDB.value),
            )
            self.assertFalse(args)
            self.assertEqual(set(kwargs) - {"language", "user"}, set())
            detail_call_count["count"] += 1
            if detail_call_count["count"] == 1:
                return stale_metadata
            return refreshed_metadata

        mock_get_metadata.side_effect = _metadata_side_effect

        cache_key = f"{Sources.TMDB.value}_{MediaTypes.TV.value}_1396"
        cache.set(cache_key, stale_metadata)

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "1396",
                    "title": "breaking-bad",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(detail_call_count["count"], 2)
        self.assertContains(response, "John Actor")
        self.assertContains(response, "Jane Director")
        self.assertIsNone(cache.get(cache_key))

    def test_podcast_media_details_renders_for_show_with_no_user_plays(self):
        """Podcast details should render even when episodes have no play history."""
        show = PodcastShow.objects.create(
            podcast_uuid="itunes:1002937870",
            title="Dear Hank & John",
            author="Hank and John",
            image="http://example.com/podcast.jpg",
            rss_feed_url="",
        )
        PodcastEpisode.objects.create(
            show=show,
            episode_uuid="dhj-episode-1",
            title="Episode One",
            duration=3600,
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.POCKETCASTS.value,
                    "media_type": MediaTypes.PODCAST.value,
                    "media_id": show.podcast_uuid,
                    "title": "dear-hank-john",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dear Hank &amp; John")
        self.assertContains(response, "Episode One")
        self.assertNotContains(response, "Mark All Played")

    def test_manual_podcast_show_media_details_renders(self):
        """A custom podcast show opens the modern show page, not a 404 (#1624)."""
        show = PodcastShow.objects.create(
            podcast_uuid="manual-show-uuid",
            source=Sources.MANUAL.value,
            title="Custom Podcast",
            image="http://example.com/podcast.jpg",
        )
        PodcastShowTracker.objects.create(
            user=self.user,
            show=show,
            status=Status.IN_PROGRESS.value,
        )
        PodcastEpisode.objects.create(
            show=show,
            episode_uuid="manual-episode-1",
            title="Manual Episode",
            duration=1800,
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MANUAL.value,
                    "media_type": MediaTypes.PODCAST.value,
                    "media_id": show.podcast_uuid,
                    "title": "custom-podcast",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Custom Podcast")
        self.assertContains(response, "Manual Episode")

    @patch("app.services.podcast_import.import_show_from_feed_url")
    def test_gpodder_feed_url_redirects_to_imported_show(self, mock_import):
        """A GPodder search result's feed URL should open its imported show."""
        show = PodcastShow.objects.create(
            podcast_uuid="gp_linux_outlaws",
            source=Sources.GPODDER.value,
            title="Linux Outlaws",
            rss_feed_url="",
        )
        mock_import.return_value = show

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.GPODDER.value,
                    "media_type": MediaTypes.PODCAST.value,
                    "media_id": "https://example.com/linux.xml",
                    "title": "linux-outlaws",
                },
            ),
        )

        self.assertRedirects(
            response,
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.GPODDER.value,
                    "media_type": MediaTypes.PODCAST.value,
                    "media_id": show.podcast_uuid,
                    "title": "linux-outlaws",
                },
            ),
        )
        mock_import.assert_called_once_with("https://example.com/linux.xml")

    def test_detail_action_track_modal_is_cloaked(self):
        """The detail page's track modal must not flash before Alpine hides it.

        detail_action_buttons.html's overlay is a full-screen
        `fixed inset-0 bg-black/50` div; without x-cloak it renders visible
        until Alpine boots and applies x-show="trackOpen".
        """
        show = PodcastShow.objects.create(
            podcast_uuid="itunes:1002937872",
            title="Cloak Check",
            image="http://example.com/podcast.jpg",
            rss_feed_url="",
        )
        PodcastEpisode.objects.create(
            show=show,
            episode_uuid="cloak-episode-1",
            title="Episode One",
            duration=3600,
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.POCKETCASTS.value,
                    "media_type": MediaTypes.PODCAST.value,
                    "media_id": show.podcast_uuid,
                    "title": "cloak-check",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            response.content.decode(),
            r'x-show="trackOpen"[^>]*\sx-cloak\b',
        )

    def test_podcast_media_details_renders_show_and_episode_website_links(self):
        """Issue #1014: the links have to reach the page users actually open.

        /podcast/show/<id>/ rendered them, but the route the app links to is
        /details/<source>/podcast/<show_uuid>/<slug>, which builds its own
        context and carried neither the show's website nor the episodes'.
        """
        show = PodcastShow.objects.create(
            podcast_uuid="itunes:1002937871",
            title="Voicemail Dump Truck",
            author="Mary",
            image="http://example.com/podcast.jpg",
            rss_feed_url="",
            website_url="https://www.spreaker.com/show/voicemail-dump-truck",
        )
        PodcastEpisode.objects.create(
            show=show,
            episode_uuid="vdt-episode-1",
            title="Dog Sunscreen",
            duration=3600,
            website_url="https://www.spreaker.com/episode/dog-sunscreen",
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.POCKETCASTS.value,
                    "media_type": MediaTypes.PODCAST.value,
                    "media_id": show.podcast_uuid,
                    "title": "voicemail-dump-truck",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "https://www.spreaker.com/show/voicemail-dump-truck",
        )
        self.assertContains(
            response,
            "https://www.spreaker.com/episode/dog-sunscreen",
        )

    def test_podcast_media_details_renders_for_show_without_artwork(self):
        """Podcast details should render when the show has no artwork.

        The episode rows fall back to a microphone glyph when there is no
        image to show. Every other podcast test here gives the show an image,
        so the fallback branch was never rendered and a missing glyph took the
        whole page down with a 500.
        """
        show = PodcastShow.objects.create(
            podcast_uuid="itunes:1002937872",
            title="Artless Show",
            author="Host",
            image="",
            rss_feed_url="",
        )
        PodcastEpisode.objects.create(
            show=show,
            episode_uuid="artless-episode-1",
            title="Episode Without Art",
            duration=1800,
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.POCKETCASTS.value,
                    "media_type": MediaTypes.PODCAST.value,
                    "media_id": show.podcast_uuid,
                    "title": "artless-show",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Episode Without Art")

    def test_podcast_episode_fragment_renders_for_show_with_no_user_plays(self):
        """Podcast episode HTMX fragments should render when no play history exists."""
        show = PodcastShow.objects.create(
            podcast_uuid="gpodder-show-1",
            title="Dear Hank & John",
            author="Hank and John",
            image="http://example.com/podcast.jpg",
            rss_feed_url="",
            source=Sources.GPODDER.value,
        )
        PodcastEpisode.objects.create(
            show=show,
            episode_uuid="https://api.spreaker.com/episode/74415563",
            title="Episode Two",
            duration=1800,
        )

        response = self.client.get(
            reverse("podcast_episodes_api", kwargs={"show_id": show.id}),
            {"format": "html", "page": 1, "page_size": 20},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Episode Two")
        self.assertContains(
            response,
            'id="history-podcast-https---api-spreaker-com-episode-74415563"',
            html=False,
        )
        self.assertContains(
            response,
            'hx-target="#history-podcast-https---api-spreaker-com-episode-74415563"',
            html=False,
        )

    @patch("events.tasks.reload_calendar.apply_async")
    @patch("integrations.podcast_rss.fetch_episodes_from_rss")
    def test_podcast_mark_all_played_fetches_rss_episodes_without_name_errors(
        self,
        mock_fetch_episodes_from_rss,
        mock_reload_calendar,
    ):
        """Mark-all-played should import RSS episodes and complete without missing imports."""
        published_at = timezone.now() - timedelta(days=1)
        show = PodcastShow.objects.create(
            podcast_uuid="itunes:1002937871",
            title="Rss Backfill Show",
            author="Host",
            image="http://example.com/podcast.jpg",
            rss_feed_url="https://example.com/feed.xml",
        )
        mock_fetch_episodes_from_rss.return_value = [
            {
                "title": "Episode From RSS",
                "published": published_at,
                "duration": 1800,
                "audio_url": "https://example.com/episode.mp3",
                "episode_number": 7,
                "season_number": 1,
            },
        ]

        response = self.client.post(
            reverse("podcast_mark_all_played", kwargs={"show_id": show.id}),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            PodcastEpisode.objects.filter(show=show, title="Episode From RSS").count(),
            1,
        )
        self.assertEqual(Podcast.objects.filter(user=self.user, show=show).count(), 1)
        mock_reload_calendar.assert_called_once()

    @patch("app.tasks.enqueue_genre_backfill_items", return_value=1)
    def test_media_details_genre_update_refreshes_reading_top_genres(
        self, _mock_enqueue_genre_backfill_items
    ):
        """Saving reading genres from details should invalidate stale day caches."""
        played_at = timezone.now() - timedelta(days=30)
        item = Item.objects.create(
            media_id="377938",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.BOOK.value,
            title="The Lord of the Rings",
            image="http://example.com/book.jpg",
            genres=[],
        )
        Book.objects.create(
            user=self.user,
            item=item,
            status=Status.COMPLETED.value,
            progress=900,
            start_date=played_at,
            end_date=played_at,
        )

        statistics_cache.build_stats_for_day(self.user.id, played_at.date())
        stale_stats = statistics_cache.refresh_statistics_cache(
            self.user.id, "All Time"
        )
        self.assertEqual(stale_stats["book_consumption"]["top_genres"], [])

        with patch("app.providers.services.get_media_metadata") as mock_get_metadata:
            mock_get_metadata.return_value = {
                "media_id": "377938",
                "title": "The Lord of the Rings",
                "media_type": MediaTypes.BOOK.value,
                "source": Sources.MANUAL.value,
                "image": "http://example.com/book.jpg",
                "max_progress": 1178,
                "genres": ["Fantasy"],
                "details": {"number_of_pages": 1178},
            }
            response = self.client.get(
                reverse(
                    "media_details",
                    kwargs={
                        "source": Sources.MANUAL.value,
                        "media_type": MediaTypes.BOOK.value,
                        "media_id": "377938",
                        "title": "the-lord-of-the-rings",
                    },
                ),
                {"fragment": "secondary"},
            )
        self.assertEqual(response.status_code, 200)

        item.refresh_from_db()
        self.assertEqual(item.genres, ["Fantasy"])

        statistics_cache.invalidate_statistics_cache(self.user.id, "All Time")
        refreshed_stats = statistics_cache.refresh_statistics_cache(
            self.user.id, "All Time"
        )
        refreshed_genres = [
            entry["name"] for entry in refreshed_stats["book_consumption"]["top_genres"]
        ]
        self.assertIn("Fantasy", refreshed_genres)

    @patch("app.providers.services.get_media_metadata")
    def test_tv_media_details_uses_episode_runtime_fallback_when_metadata_runtime_missing(
        self,
        mock_get_metadata,
    ):
        """TV details should show a derived runtime when provider runtime is missing."""
        show_item = Item.objects.create(
            media_id="91239",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Bridgerton",
            image="http://example.com/show.jpg",
            runtime_minutes=999999,
        )
        Item.objects.create(
            media_id="91239",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            title="Episode 1",
            runtime_minutes=52,
        )
        Item.objects.create(
            media_id="91239",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
            title="Episode 2",
            runtime_minutes=54,
        )

        mock_get_metadata.return_value = {
            "media_id": "91239",
            "title": "Bridgerton",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "source_url": "https://www.themoviedb.org/tv/91239",
            "image": "http://example.com/show.jpg",
            "synopsis": "Test synopsis",
            "details": {
                "format": "TV",
                "runtime": None,
                "seasons": 1,
            },
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
            "providers": {},
            "external_links": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "91239",
                    "title": "bridgerton",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["media"]["details"]["runtime"], "53m")
        show_item.refresh_from_db()
        self.assertEqual(show_item.runtime_minutes, 999999)

    @patch("app.providers.services.get_media_metadata")
    def test_tv_media_details_roll_up_show_level_collection_entry_when_no_granular_rows_exist(
        self,
        mock_get_metadata,
    ):
        """TV details should treat a show-level collection entry as a full-show fallback."""
        show_item = Item.objects.create(
            media_id="tv-collection-rollup",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Collected Show",
            image="http://example.com/show.jpg",
        )
        Item.objects.create(
            media_id="tv-collection-rollup",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Collected Show",
            image="http://example.com/season.jpg",
        )
        Item.objects.create(
            media_id="tv-collection-rollup",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            title="Episode 1",
            image="http://example.com/episode1.jpg",
        )
        Item.objects.create(
            media_id="tv-collection-rollup",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
            title="Episode 2",
            image="http://example.com/episode2.jpg",
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=show_item,
            media_type="digital",
            resolution="1080p",
        )

        mock_get_metadata.return_value = {
            "media_id": "tv-collection-rollup",
            "title": "Collected Show",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "source_url": "https://www.themoviedb.org/tv/tv-collection-rollup",
            "image": "http://example.com/show.jpg",
            "synopsis": "Test synopsis",
            "details": {
                "format": "TV",
                "runtime": "53m",
                "episodes": 2,
                "seasons": 1,
            },
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
            "providers": {},
            "external_links": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "tv-collection-rollup",
                    "title": "collected-show",
                },
            ),
            # Collection stats are computed for the secondary fragment.
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["collection_stats"],
            {
                "collected_seasons": 1,
                "total_seasons": 1,
                "collected_episodes": 2,
                "total_episodes": 2,
            },
        )
        self.assertContains(response, "COLLECTED SEASONS")
        self.assertContains(response, "1/1")
        self.assertContains(response, "COLLECTED EPISODES")
        self.assertContains(response, "2/2")

    @patch("app.providers.services.get_media_metadata")
    def test_tv_media_details_count_collected_season_as_collected_show_episodes(
        self,
        mock_get_metadata,
    ):
        """TV details should count a collected season as all of that season's episodes."""
        Item.objects.create(
            media_id="tv-season-rollup",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Collected Season Show",
            image="http://example.com/show.jpg",
        )
        season_item = Item.objects.create(
            media_id="tv-season-rollup",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Collected Season Show",
            image="http://example.com/season1.jpg",
        )
        Item.objects.create(
            media_id="tv-season-rollup",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=2,
            title="Collected Season Show",
            image="http://example.com/season2.jpg",
        )
        Item.objects.create(
            media_id="tv-season-rollup",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            title="Episode 1",
            image="http://example.com/episode1.jpg",
        )
        Item.objects.create(
            media_id="tv-season-rollup",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
            title="Episode 2",
            image="http://example.com/episode2.jpg",
        )
        Item.objects.create(
            media_id="tv-season-rollup",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=2,
            episode_number=1,
            title="Episode 3",
            image="http://example.com/episode3.jpg",
        )
        CollectionEntry.objects.create(
            user=self.user,
            item=season_item,
            media_type="digital",
            resolution="1080p",
        )

        mock_get_metadata.return_value = {
            "media_id": "tv-season-rollup",
            "title": "Collected Season Show",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "source_url": "https://www.themoviedb.org/tv/tv-season-rollup",
            "image": "http://example.com/show.jpg",
            "synopsis": "Test synopsis",
            "details": {
                "format": "TV",
                "runtime": "53m",
                "episodes": 3,
                "seasons": 2,
            },
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
            "providers": {},
            "external_links": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "tv-season-rollup",
                    "title": "collected-season-show",
                },
            ),
            # Collection stats are computed for the secondary fragment.
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["collection_stats"],
            {
                "collected_seasons": 1,
                "total_seasons": 2,
                "collected_episodes": 2,
                "total_episodes": 3,
            },
        )
        self.assertContains(response, "COLLECTED SEASONS")
        self.assertContains(response, "1/2")
        self.assertContains(response, "COLLECTED EPISODES")
        self.assertContains(response, "2/3")

    @patch("app.providers.services.get_media_metadata")
    def test_anime_media_details_collection_stats_ignore_episode_list_payload(
        self,
        mock_get_metadata,
    ):
        """A missing episode count must not leak the episode list into the stats."""
        Item.objects.create(
            media_id="anime-episode-list-count",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Long Running Anime",
            image="http://example.com/anime.jpg",
        )

        mock_get_metadata.return_value = {
            "media_id": "anime-episode-list-count",
            "title": "Long Running Anime",
            "media_type": MediaTypes.ANIME.value,
            "source": Sources.MAL.value,
            "source_url": "https://myanimelist.net/anime/anime-episode-list-count",
            "image": "http://example.com/anime.jpg",
            "synopsis": "Test synopsis",
            # Providers omit the episode count for some long-running shows.
            "details": {"format": "TV", "runtime": "25m", "episodes": None},
            # The detail page stores the episode preview under this key.
            "episodes": [
                {
                    "media_id": "anime-episode-list-count",
                    "media_type": MediaTypes.EPISODE.value,
                    "source": Sources.TVDB.value,
                    "season_number": 1,
                    "episode_number": episode_number,
                }
                for episode_number in (1, 2, 3)
            ],
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
            "providers": {},
            "external_links": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "media_id": "anime-episode-list-count",
                    "title": "long-running-anime",
                },
            ),
            # Collection stats are computed for the secondary fragment.
            {"fragment": "secondary"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["collection_stats"]["total_episodes"], 3)
        self.assertContains(response, "COLLECTED EPISODES")
        self.assertContains(response, "0/3")
        self.assertNotContains(response, "&#x27;media_id&#x27;")

    @patch("app.providers.services.get_media_metadata")
    def test_tv_media_details_play_stats_skip_placeholder_episode_runtimes(
        self,
        mock_get_metadata,
    ):
        """TV details totals should ignore placeholder episode runtimes."""
        watched_at = timezone.now()
        show_item = Item.objects.create(
            media_id="91239",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Bridgerton",
            image="http://example.com/show.jpg",
        )
        tv = TV.objects.create(
            item=show_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="91239",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Bridgerton",
            image="http://example.com/season.jpg",
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        valid_episode_item = Item.objects.create(
            media_id="91239",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            title="Episode 1",
            runtime_minutes=45,
        )
        placeholder_episode_item = Item.objects.create(
            media_id="91239",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
            title="Episode 2",
            runtime_minutes=999998,
        )
        Episode.objects.create(
            item=valid_episode_item,
            related_season=season,
            end_date=watched_at,
        )
        Episode.objects.create(
            item=placeholder_episode_item,
            related_season=season,
            end_date=watched_at + timedelta(minutes=1),
        )

        mock_get_metadata.return_value = {
            "media_id": "91239",
            "title": "Bridgerton",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "source_url": "https://www.themoviedb.org/tv/91239",
            "image": "http://example.com/show.jpg",
            "synopsis": "Test synopsis",
            "details": {
                "format": "TV",
                "runtime": None,
                "seasons": 1,
            },
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
            "providers": {},
            "external_links": {},
        }

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": "91239",
                    "title": "bridgerton",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["play_stats"]["total_minutes"], 45)
        self.assertEqual(response.context["play_stats"]["episode_count"], 1)

    @patch("app.providers.services.get_media_metadata")
    def test_tv_media_details_show_total_runtime_uses_same_calculation_as_media_list(
        self,
        mock_get_metadata,
    ):
        """TV details should show shared total runtime while watched time moves into the subtitle."""
        now = timezone.now()
        show_item = Item.objects.create(
            media_id="fallout-runtime-shared",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Shared Runtime Show",
            image="http://example.com/show.jpg",
            runtime_minutes=25,
        )
        tv = TV.objects.create(
            item=show_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="fallout-runtime-shared",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Shared Runtime Show",
            image="http://example.com/season.jpg",
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )

        first_episode_item = Item.objects.create(
            media_id="fallout-runtime-shared",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            title="Episode 1",
            runtime_minutes=52,
            release_datetime=now - timedelta(days=3),
        )
        second_episode_item = Item.objects.create(
            media_id="fallout-runtime-shared",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
            title="Episode 2",
            runtime_minutes=58,
            release_datetime=now - timedelta(days=2),
        )
        third_episode_item = Item.objects.create(
            media_id="fallout-runtime-shared",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=3,
            title="Episode 3",
            runtime_minutes=47,
            release_datetime=now - timedelta(days=1),
        )

        Episode.objects.create(
            item=first_episode_item,
            related_season=season,
            end_date=now - timedelta(days=1),
        )
        Episode.objects.create(
            item=second_episode_item,
            related_season=season,
            end_date=now,
        )
        Episode.objects.create(
            item=third_episode_item,
            related_season=season,
        )

        mock_get_metadata.return_value = {
            "media_id": "fallout-runtime-shared",
            "title": "Shared Runtime Show",
            "media_type": MediaTypes.TV.value,
            "source": Sources.TMDB.value,
            "source_url": "https://www.themoviedb.org/tv/fallout-runtime-shared",
            "image": "http://example.com/show.jpg",
            "synopsis": "Test synopsis",
            "details": {
                "format": "TV",
                "runtime": "25m",
                "seasons": 1,
                "episodes": 3,
            },
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
            "providers": {},
            "external_links": {},
        }

        detail_url = reverse(
            "media_details",
            kwargs={
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.TV.value,
                "media_id": "fallout-runtime-shared",
                "title": "shared-runtime-show",
            },
        )
        detail_response = self.client.get(detail_url)

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.context["media"]["details"]["runtime"], "25m")
        self.assertEqual(
            detail_response.context["media"]["details"]["total_runtime"], "2h 37min"
        )
        self.assertEqual(detail_response.context["play_stats"]["total_minutes"], 110)
        self.assertContains(detail_response, "1h 50min watched")
        # The runtime stat card renders in the deferred secondary fragment.
        fragment_response = self.client.get(detail_url, {"fragment": "secondary"})
        self.assertContains(fragment_response, "RUNTIME (1 SEASON)")
        self.assertContains(fragment_response, "2h 37min")
        self.assertNotContains(detail_response, "FIRST PLAYED")
        self.assertNotContains(detail_response, "LAST PLAYED")
        self.assertNotContains(detail_response, "WATCHED HOURS")

        list_response = self.client.get(
            reverse("medialist", args=[MediaTypes.TV.value])
            + "?layout=table&search=Shared+Runtime+Show",
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "2h 37min")

    @patch("app.providers.openlibrary.book")
    def test_audiobookshelf_book_details_does_not_call_openlibrary(
        self,
        mock_openlibrary_book,
    ):
        """Audiobookshelf detail pages should render using local metadata."""
        item = Item.objects.create(
            media_id="f9e2ce45ec9315a7c54c",
            source=Sources.AUDIOBOOKSHELF.value,
            media_type=MediaTypes.BOOK.value,
            title="The Blade Itself",
            image="https://img.example/blade.jpg",
            runtime_minutes=1320,
            authors=["Joe Abercrombie"],
            format="audiobook",
        )

        Book.objects.create(
            user=self.user,
            item=item,
            status=Status.IN_PROGRESS.value,
            progress=60,
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.AUDIOBOOKSHELF.value,
                    "media_type": MediaTypes.BOOK.value,
                    "media_id": "f9e2ce45ec9315a7c54c",
                    "title": "the-blade-itself",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The Blade Itself")
        mock_openlibrary_book.assert_not_called()

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_refreshes_missing_item_image(self, mock_get_metadata):
        """Viewing a detail page replaces a missing Item image with the provider's."""
        fresh_image = "https://images.example.com/godfather.jpg"
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "The Godfather",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": fresh_image,
            "max_progress": 1,
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Godfather",
            image=settings.IMG_NONE,
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            end_date=datetime(2024, 1, 1, tzinfo=UTC),
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "the-godfather",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.image, fresh_image)

    @patch("app.providers.services.get_media_metadata")
    def test_media_details_keeps_existing_item_image(self, mock_get_metadata):
        """Viewing a detail page does NOT overwrite an Item that already has an image."""
        existing_image = "https://images.example.com/existing.jpg"
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "The Godfather",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "https://images.example.com/different.jpg",
            "max_progress": 1,
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Godfather",
            image=existing_image,
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            end_date=datetime(2024, 1, 1, tzinfo=UTC),
        )

        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": "238",
                    "title": "the-godfather",
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.image, existing_image)

    @patch("integrations.tasks.fetch_collection_metadata_for_item.delay")
    @patch("app.views.credits.sync_item_credits_from_metadata")
    @patch("app.views.metadata_utils.apply_item_metadata", return_value=[])
    @patch("app.providers.services.get_media_metadata")
    def test_first_saved_note_swaps_into_a_pre_existing_section(
        self,
        mock_get_metadata,
        _mock_apply_item_metadata,
        _mock_sync_credits,
        _mock_fetch_delay,
    ):
        """The notes section anchor exists before there is any note to show.

        media_save returns the section as an hx-swap-oob fragment. htmx drops an
        OOB fragment whose target is not already in the DOM, so gating the
        section on having a note meant the *first* note saved never appeared
        until a reload.
        """
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "synopsis": "Test overview",
            "max_progress": 1,
            "details": {},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
        )

        fragment = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "media_id": item.media_id,
                    "title": "test-movie",
                },
            ),
            {"fragment": "secondary"},
        )

        self.assertEqual(fragment.status_code, 200)
        # The anchor is present with no notes yet, and carries no stray heading.
        self.assertContains(fragment, 'id="detail-notes-section"', html=False)
        self.assertNotContains(
            fragment, '<h2 class="text-xl font-bold">Your Notes</h2>', html=False
        )

        saved = self.client.post(
            reverse("media_save"),
            {
                "media_id": item.media_id,
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "status": Status.COMPLETED.value,
                "progress": 1,
                "repeats": 0,
                "notes": "First note ever",
            },
            headers={"hx-request": "true"},
        )

        self.assertEqual(saved.status_code, 200)
        body = saved.content.decode()
        section = body[body.index('id="detail-notes-section"') :]
        self.assertIn('hx-swap-oob="outerHTML"', section)
        self.assertIn('<h2 class="text-xl font-bold">Your Notes</h2>', section)
        self.assertIn("First note ever", section)

    @patch("integrations.tasks.fetch_collection_metadata_for_item.delay")
    @patch("app.views.credits.sync_item_credits_from_metadata")
    @patch("app.views.metadata_utils.apply_item_metadata", return_value=[])
    @patch("app.providers.services.get_media_metadata")
    def test_clearing_the_last_note_leaves_no_bare_heading(
        self,
        mock_get_metadata,
        _mock_apply_item_metadata,
        _mock_sync_credits,
        _mock_fetch_delay,
    ):
        """The mirror case: an emptied section must not keep its heading."""
        mock_get_metadata.return_value = {
            "media_id": "238",
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "image": "http://example.com/image.jpg",
            "synopsis": "Test overview",
            "max_progress": 1,
            "details": {},
            "related": {},
            "cast": [],
            "crew": [],
            "studios_full": [],
        }
        item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            progress=1,
            notes="Soon to be cleared",
        )

        saved = self.client.post(
            reverse("media_save"),
            {
                "media_id": item.media_id,
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "status": Status.COMPLETED.value,
                "progress": 1,
                "repeats": 0,
                "instance_id": movie.id,
                "notes": "",
            },
            headers={"hx-request": "true"},
        )

        self.assertEqual(saved.status_code, 200)
        body = saved.content.decode()
        section = body[body.index('id="detail-notes-section"') :]
        self.assertIn('hx-swap-oob="outerHTML"', section)
        self.assertNotIn('<h2 class="text-xl font-bold">Your Notes</h2>', section)

    def test_editing_an_episode_note_swaps_the_section_back(self):
        """Episode saves push the notes section back like movie saves do.

        Episode rows are scoped through related_season rather than a `user`
        field, so the notes OOB helper cannot build its own `filter(user=...)`
        queryset — episodes were left out entirely and an edited note only
        showed up after a reload.
        """
        tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Season 1",
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            title="Episode 1",
        )
        episode = Episode.objects.create(
            item=episode_item,
            related_season=season,
            notes="note before the edit",
            end_date=datetime(2026, 8, 1, tzinfo=UTC),
        )

        next_path = reverse(
            "episode_details",
            kwargs={
                "source": Sources.TMDB.value,
                "media_id": "1668",
                "title": "test-tv-show",
                "season_number": 1,
                "episode_number": 1,
            },
        )
        response = self.client.post(
            f"{reverse('episode_save')}?next={next_path}",
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 1,
                "instance_id": episode.id,
                "status": Status.COMPLETED.value,
                "end_date": "2026-08-01T00:00",
                "notes": "note after the edit",
            },
            headers={"hx-request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('id="detail-notes-section"', body)
        section = body[body.index('id="detail-notes-section"') :]
        self.assertIn('hx-swap-oob="outerHTML"', section)
        self.assertIn("note after the edit", section)
        self.assertNotIn("note before the edit", section)
        # The swapped-in blocks must keep the episode page's own modal
        # namespace, not fall back to the movie/season component ids.
        self.assertIn(
            f"episode-notes-modal-tmdb-1668-1-1-{episode.id}",
            section,
        )

    @patch("app.views.tmdb.episode", return_value={})
    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_episode_notes_blocks_get_distinct_modal_targets(
        self,
        mock_process_episodes,
        mock_get_metadata,
        _mock_episode,
    ):
        """Every watch note renders its own edit-modal target.

        The target id is built by concatenating the episode's modal id with the
        watch's pk. Django's `add` filter returns "" for str + int, so an
        unstringified pk silently emptied every id and left the edit buttons
        pointing at "#".
        """
        tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Season 1",
        )
        season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=tv,
            status=Status.IN_PROGRESS.value,
        )
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            title="Episode 1",
        )
        watches = [
            Episode.objects.create(
                item=episode_item,
                related_season=season,
                notes=f"Watch note {index}",
                end_date=datetime(2026, 8, index, tzinfo=UTC),
            )
            for index in (1, 2)
        ]

        mock_get_metadata.side_effect = lambda *_args, **_kwargs: {
            "title": "Test TV Show",
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "http://example.com/image.jpg",
            "season/1": {
                "title": "Season 1",
                "season_title": "Season 1",
                "media_id": "1668",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": "http://example.com/season.jpg",
                "episodes": [{"episode_number": 1}],
            },
        }
        mock_process_episodes.return_value = [
            {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 1,
                "title": "Episode 1",
                "air_date": "2023-01-01",
                "watched": True,
                "history": watches,
            },
        ]

        response = self.client.get(
            reverse(
                "episode_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "1668",
                    "title": "test-tv-show",
                    "season_number": 1,
                    "episode_number": 1,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [entry.notes for entry in response.context["notes_entries"]],
            ["Watch note 1", "Watch note 2"],
        )

        html = response.content.decode()
        section = html[html.index('id="detail-notes-section"') :]
        targets = re.findall(r'hx-target="#([^"]*)"', section)
        anchors = re.findall(r'<div id="([^"]*)"></div>', section)

        expected = [
            f"{response.context['episode_notes_modal_target_id']}-{watch.pk}"
            for watch in watches
        ]
        self.assertEqual(targets, expected)
        self.assertEqual(anchors, expected)

        # The Notes shortcut in the action row opens the same modal with the
        # notes field focused; the modal picks the latest watch when it opens.
        button = html[html.index('title="Add or edit notes"') :]
        button = button[: button.index("</button>")]
        self.assertIn('"focus": "notes"', button)
        self.assertNotIn("instance_id", button)
        self.assertNotIn("is_create", button)
        self.assertIn('"episode_number": "1"', button)


class AnimeNextEpisodeRedirectTests(TestCase):
    """Test the anime_next_episode click-time redirect view."""

    def setUp(self):
        self.credentials = {"username": "nextep", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.item = Item.objects.create(
            media_id="51553",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Witch Hat Atelier",
            image="http://example.com/anime.jpg",
        )
        Anime.objects.create(
            item=self.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=3,
        )
        self.url = reverse(
            "anime_next_episode",
            kwargs={"media_id": "51553", "title": "witch-hat-atelier"},
        )
        self.detail_url = reverse(
            "media_details",
            kwargs={
                "source": Sources.MAL.value,
                "media_type": MediaTypes.ANIME.value,
                "media_id": "51553",
                "title": "witch-hat-atelier",
            },
        )

    @patch("app.views._build_flat_anime_episode_preview")
    @patch("app.views.services.get_media_metadata")
    def test_redirects_to_mapped_next_episode(self, mock_metadata, mock_preview):
        mock_metadata.return_value = {"title": "Witch Hat Atelier", "details": {}}
        mock_preview.return_value = [
            {
                "source": Sources.TMDB.value,
                "media_id": "241259",
                "season_number": 1,
                "episode_number": episode_number,
                "display_episode_number": episode_number,
            }
            for episode_number in range(1, 6)
        ]

        response = self.client.get(self.url)

        self.assertRedirects(
            response,
            reverse(
                "anime_episode_details",
                kwargs={
                    "source": Sources.TMDB.value,
                    "media_id": "241259",
                    "title": "witch-hat-atelier",
                    "season_number": 1,
                    "episode_number": 4,
                },
            ),
            fetch_redirect_response=False,
        )

    @patch("app.views._build_flat_anime_episode_preview")
    @patch("app.views.services.get_media_metadata")
    def test_falls_back_to_detail_page_without_mapping(
        self, mock_metadata, mock_preview
    ):
        mock_metadata.return_value = {"title": "Witch Hat Atelier", "details": {}}
        mock_preview.return_value = None

        response = self.client.get(self.url)

        self.assertRedirects(response, self.detail_url, fetch_redirect_response=False)


class EpisodePickerTemplateContractTests(SimpleTestCase):
    def test_long_title_is_constrained_inside_episode_picker(self):
        template = Path(
            settings.BASE_DIR, "templates", "app", "episode_details.html"
        ).read_text(encoding="utf-8")

        self.assertIn('class="relative min-w-0 flex-1 md:max-w-xs"', template)
        self.assertIn(
            'class="block min-w-0 flex-1 truncate text-sm font-medium"',
            template,
        )


class ManualComicIssueDetailsTests(TestCase):
    """What the Metadata editor saves on a comic issue shows on its page."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="issue-details", password="x"
        )
        self.client.login(username="issue-details", password="x")
        self.item = Item.objects.create(
            media_id="manual-issue-details",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.COMIC_ISSUE.value,
            title="Manual Issue",
            publishers="Nova Press",
            format="Trade Paperback",
            country="US",
            languages=["English", "French"],
            authors=["Ada Writer", "Ben Artist"],
            genres=["Sci-Fi"],
            manual_metadata={
                "synopsis": "Lost signal.",
                "genres": ["Sci-Fi"],
                "details": {
                    "release_date": "2024-03-06",
                    "publisher": "Nova Press",
                    "format": "Trade Paperback",
                    "country": "US",
                    "languages": ["English", "French"],
                    "author": ["Ada Writer", "Ben Artist"],
                },
            },
        )
        CollectionEntry.objects.create(user=self.user, item=self.item)
        MetadataProviderPreference.objects.create(
            user=self.user,
            item=self.item,
            provider=Sources.MANUAL.value,
        )

    def test_saved_metadata_is_visible_on_the_page(self):
        response = self.client.get(
            reverse(
                "media_details",
                kwargs={
                    "source": Sources.MANUAL.value,
                    "media_type": MediaTypes.COMIC_ISSUE.value,
                    "media_id": "manual-issue-details",
                    "title": "manual-issue",
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nova Press")
        self.assertContains(response, "Trade Paperback")
        self.assertContains(response, "English, French")
        self.assertContains(response, "Ada Writer, Ben Artist")
        self.assertContains(response, "Sci-Fi")
        self.assertContains(response, "2024-03-06")
