from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import timezone

from app.discover import cache_repo, tab_cache
from app.discover.entry_candidates import _entries_to_candidates
from app.discover.match_signals import (
    _comfort_match_signal,
    _row_match_signal,
    _row_match_signal_with_details,
)
from app.discover.movie_comfort import _prefer_strong_phase_opening_window
from app.discover.provider_candidates import (
    _mal_anime_fallback_candidates,
    _mal_anime_ranking_candidates,
    _musicbrainz_coming_soon_recording_candidates,
    _provider_row_candidates,
)
from app.discover.providers.trakt_adapter import TraktDiscoverAdapter
from app.discover.registry import ALL_MEDIA_KEY
from app.discover.row_cache_schema import (
    ANIME_TRAKT_ROW_SCHEMA_VERSION,
    ROW_CACHE_ACTIVITY_VERSION_META_KEY,
    ROW_CACHE_SCHEMA_META_KEY,
)
from app.discover.schemas import CandidateItem, RowDefinition, RowResult
from app.discover.service import (
    MAX_ITEMS_PER_ROW,
    _apply_comfort_confidence,
    _apply_top_picks_source_quotas,
    _apply_wildcard_novelty,
    _build_and_cache_row,
    _build_comfort_debug_payload,
    _clear_out_next_candidates,
    _comfort_candidates,
    _get_all_media_component_rows,
    _prepare_row_from_candidates,
    _rewatch_counts,
    _top_picks_candidates,
    get_discover_rows,
)
from app.models import (
    TV,
    Anime,
    CreditRoleType,
    DiscoverFeedback,
    DiscoverFeedbackType,
    Episode,
    Item,
    ItemPersonCredit,
    ItemStudioCredit,
    MediaTypes,
    Movie,
    Person,
    Season,
    Sources,
    Status,
    Studio,
)
from events.models import Event


class DiscoverServiceTests(TestCase):
    """Service-level Discover tests."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="discover-service-user",
            password="testpass",
        )
        # The holiday window opens on 15 November. These tests assert the
        # ordinary ranking, so they must not depend on the day they run. Each
        # scoring module imports its own copy of the check, so patch both.
        for module in ("comfort_scoring", "movie_comfort"):
            patcher = patch(
                f"app.discover.{module}._is_holiday_window", return_value=False
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _row_snapshot(rows):
        return {
            row.key: [f"{item.media_id}:{item.title}" for item in row.items[:6]]
            for row in rows
        }

    @staticmethod
    def _comparison_titles(candidates, *, top_n=3):
        payload = _build_comfort_debug_payload(candidates, top_n=top_n)
        summary = payload["comparison_summary"]
        return (
            summary["legacy_top_titles"],
            summary["current_top_titles"],
            payload,
        )

    @patch("app.discover.providers.trakt_adapter.services.api_request")
    @patch(
        "app.discover.providers.trakt_adapter.trakt_provider.is_configured",
        return_value=False,
    )
    @patch("app.discover.service.TMDB_ADAPTER.current_cycle")
    def test_unconfigured_trakt_rows_fall_back_to_tmdb_for_movies_and_tv(
        self,
        mock_current_cycle,
        _mock_trakt_configured,
        mock_trakt_request,
    ):
        row_definition = RowDefinition(
            key="trending_right_now",
            title="Trending Right Now",
            mission="Cultural Moment",
            why="What everyone has been watching this week.",
            source="trakt",
        )

        for media_type in (MediaTypes.MOVIE.value, MediaTypes.TV.value):
            with self.subTest(media_type=media_type):
                mock_current_cycle.return_value = [
                    CandidateItem(
                        media_type=media_type,
                        source=Sources.TMDB.value,
                        media_id=f"fallback-{media_type}",
                        title=f"Fallback {media_type}",
                        image="https://example.com/poster.jpg",
                    ),
                ]

                row = _build_and_cache_row(
                    self.user,
                    media_type,
                    row_definition,
                    {},
                    defer_artwork=True,
                )

                self.assertEqual(row.source_state, "fallback")
                self.assertEqual(
                    [item.media_id for item in row.items],
                    [f"fallback-{media_type}"],
                )
                mock_current_cycle.assert_called_with(media_type, limit=100)
                cached_payload, _ = cache_repo.get_row_cache(
                    self.user.id,
                    media_type,
                    row_definition.key,
                )
                self.assertEqual(cached_payload["source_state"], "fallback")
                self.assertEqual(
                    [item["media_id"] for item in cached_payload["items"]],
                    [f"fallback-{media_type}"],
                )

        mock_trakt_request.assert_not_called()

    @patch("app.discover.provider_candidates._api_cached_results")
    def test_mal_anime_ranking_candidates_normalize_provider_fields(
        self,
        mock_cached_results,
    ):
        mock_cached_results.return_value = [
            {
                "node": {
                    "id": 123,
                    "title": "Original Title",
                    "alternative_titles": {"en": "Localized Title"},
                    "main_picture": {"large": "https://example.com/anime.jpg"},
                    "start_date": "2026-01-02",
                    "genres": [{"name": "Action"}, {"name": "Fantasy"}],
                    "mean": 8.7,
                    "num_scoring_users": 456,
                },
                "ranking": {"rank": 2},
            },
        ]

        candidates = _mal_anime_ranking_candidates(
            ranking_type="airing",
            row_key="trending_right_now",
            source_reason="MAL airing ranking",
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.media_type, MediaTypes.ANIME.value)
        self.assertEqual(candidate.source, Sources.MAL.value)
        self.assertEqual(candidate.media_id, "123")
        self.assertEqual(candidate.title, "Localized Title")
        self.assertEqual(candidate.original_title, "Original Title")
        self.assertEqual(candidate.localized_title, "Localized Title")
        self.assertEqual(candidate.image, "https://example.com/anime.jpg")
        self.assertEqual(candidate.release_date, "2026-01-02")
        self.assertEqual(candidate.genres, ["Action", "Fantasy"])
        self.assertEqual(candidate.rating, 8.7)
        self.assertEqual(candidate.rating_count, 456)
        self.assertEqual(candidate.popularity, 998.0)
        self.assertEqual(candidate.row_key, "trending_right_now")
        self.assertEqual(candidate.source_reason, "MAL airing ranking")

        mock_cached_results.assert_called_once()
        provider, endpoint, params = mock_cached_results.call_args.args[:3]
        self.assertEqual(provider, Sources.MAL.value)
        self.assertEqual(endpoint, "/anime/ranking:airing")
        self.assertEqual(params["ranking_type"], "airing")

    @patch("app.discover.provider_candidates._mal_anime_ranking_candidates")
    def test_mal_anime_fallback_maps_rows_and_uses_airing_when_upcoming_is_empty(
        self,
        mock_ranking_candidates,
    ):
        candidate = CandidateItem(
            media_type=MediaTypes.ANIME.value,
            source=Sources.MAL.value,
            media_id="123",
            title="Fallback Anime",
        )
        mock_ranking_candidates.return_value = [candidate]

        expected_rankings = {
            "trending_right_now": ("airing", "MAL airing ranking"),
            "all_time_greats_unseen": ("bypopularity", "MAL popular ranking"),
            "coming_soon": ("upcoming", "MAL upcoming ranking"),
        }
        for row_key, (ranking_type, source_reason) in expected_rankings.items():
            with self.subTest(row_key=row_key):
                self.assertEqual(
                    _mal_anime_fallback_candidates(row_key),
                    [candidate],
                )
                mock_ranking_candidates.assert_called_with(
                    ranking_type=ranking_type,
                    row_key=row_key,
                    source_reason=source_reason,
                    limit=100,
                )

        mock_ranking_candidates.reset_mock()
        mock_ranking_candidates.side_effect = [[], [candidate]]
        self.assertEqual(
            _mal_anime_fallback_candidates("coming_soon"),
            [candidate],
        )
        self.assertEqual(
            [
                call.kwargs["ranking_type"]
                for call in mock_ranking_candidates.call_args_list
            ],
            ["upcoming", "airing"],
        )

    @patch("app.discover.providers.trakt_adapter.services.api_request")
    @patch(
        "app.discover.providers.trakt_adapter.trakt_provider.is_configured",
        return_value=False,
    )
    @patch("app.discover.service._mal_anime_fallback_candidates")
    def test_unconfigured_trakt_anime_rows_fall_back_to_mal(
        self,
        mock_mal_fallback,
        _mock_trakt_configured,
        mock_trakt_request,
    ):
        def fallback_candidate(row_key):
            return [
                CandidateItem(
                    media_type=MediaTypes.ANIME.value,
                    source=Sources.MAL.value,
                    media_id=f"mal-{row_key}",
                    title=f"MAL {row_key}",
                    image="https://example.com/anime.jpg",
                ),
            ]

        mock_mal_fallback.side_effect = fallback_candidate

        row_definitions = {
            row_key: RowDefinition(
                key=row_key,
                title=row_key.replace("_", " ").title(),
                mission="Anime Discover",
                why="Anime fallback",
                source="trakt",
            )
            for row_key in (
                "trending_right_now",
                "all_time_greats_unseen",
                "coming_soon",
            )
        }

        for row_key, row_definition in row_definitions.items():
            with self.subTest(row_key=row_key):
                row = _build_and_cache_row(
                    self.user,
                    MediaTypes.ANIME.value,
                    row_definition,
                    {},
                    defer_artwork=True,
                )

                self.assertEqual(row.source_state, "fallback")
                self.assertEqual(
                    [item.media_id for item in row.items],
                    [f"mal-{row_key}"],
                )
                cached_payload, _ = cache_repo.get_row_cache(
                    self.user.id,
                    MediaTypes.ANIME.value,
                    row_key,
                )
                self.assertEqual(cached_payload["source_state"], "fallback")
                self.assertEqual(
                    cached_payload["meta"][ROW_CACHE_SCHEMA_META_KEY],
                    ANIME_TRAKT_ROW_SCHEMA_VERSION,
                )

        mock_trakt_request.assert_not_called()

    @patch("app.discover.service._build_and_cache_row")
    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    def test_anime_row_cache_schema_bump_rebuilds_previous_payload(
        self,
        _mock_profile,
        mock_build_and_cache_row,
    ):
        row_key = "trending_right_now"
        cached_row = RowResult(
            key=row_key,
            title="Trending Right Now",
            mission="Cultural Moment",
            why="Anime fallback",
            source="trakt",
            items=[],
        )
        cached_payload = cached_row.to_dict()
        cached_payload["meta"] = {
            ROW_CACHE_SCHEMA_META_KEY: ANIME_TRAKT_ROW_SCHEMA_VERSION - 1,
            ROW_CACHE_ACTIVITY_VERSION_META_KEY: tab_cache.get_activity_version(
                self.user.id,
                MediaTypes.ANIME.value,
            ),
        }
        cache_repo.set_row_cache(
            self.user.id,
            MediaTypes.ANIME.value,
            row_key,
            cached_payload,
            ttl_seconds=3600,
        )

        rebuilt_row = RowResult(
            key=row_key,
            title="Trending Right Now",
            mission="Cultural Moment",
            why="Anime fallback",
            source="trakt",
            items=[
                CandidateItem(
                    media_type=MediaTypes.ANIME.value,
                    source=Sources.MAL.value,
                    media_id="123",
                    title="Rebuilt Anime",
                ),
            ],
        )
        mock_build_and_cache_row.return_value = rebuilt_row

        rows = get_discover_rows(
            self.user,
            MediaTypes.ANIME.value,
            row_keys=[row_key],
        )

        self.assertEqual([item.title for item in rows[0].items], ["Rebuilt Anime"])
        mock_build_and_cache_row.assert_called_once()

    @patch("app.discover.service._build_and_cache_row")
    @patch("app.discover.service.get_rows")
    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    def test_get_discover_rows_rebuilds_cache_when_activity_version_changes(
        self,
        _mock_profile,
        mock_get_rows,
        mock_build_and_cache_row,
    ):
        row_definition = RowDefinition(
            key="custom_row",
            title="Custom Row",
            mission="Mission",
            why="Why",
            source="local",
            min_items=1,
        )
        mock_get_rows.return_value = [row_definition]

        cached_row = RowResult(
            key="custom_row",
            title="Custom Row",
            mission="Mission",
            why="Why",
            source="local",
            items=[
                CandidateItem(
                    media_type=MediaTypes.MOVIE.value,
                    source=Sources.TMDB.value,
                    media_id="100",
                    title="Cached Movie",
                    image="https://example.com/cached.jpg",
                ),
            ],
        )
        cached_version = tab_cache.get_activity_version(
            self.user.id, MediaTypes.MOVIE.value
        )
        cached_payload = cached_row.to_dict()
        cached_payload["meta"] = {
            ROW_CACHE_ACTIVITY_VERSION_META_KEY: cached_version,
        }
        cache_repo.set_row_cache(
            self.user.id,
            MediaTypes.MOVIE.value,
            row_definition.key,
            cached_payload,
            ttl_seconds=3600,
        )
        tab_cache.bump_activity_version(self.user.id, MediaTypes.MOVIE.value)

        rebuilt_row = RowResult(
            key="custom_row",
            title="Custom Row",
            mission="Mission",
            why="Why",
            source="local",
            items=[
                CandidateItem(
                    media_type=MediaTypes.MOVIE.value,
                    source=Sources.TMDB.value,
                    media_id="101",
                    title="Rebuilt Movie",
                    image="https://example.com/rebuilt.jpg",
                ),
            ],
        )
        mock_build_and_cache_row.return_value = rebuilt_row

        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)

        self.assertEqual([row.items[0].title for row in rows], ["Rebuilt Movie"])
        mock_build_and_cache_row.assert_called_once()

    @patch("app.discover.service._get_all_media_component_rows")
    def test_all_media_default_uses_trending_row_from_each_enabled_media_type(
        self,
        mock_component_rows,
    ):
        def build_row(media_type: str, key: str, title: str) -> RowResult:
            return RowResult(
                key=key,
                title=title,
                mission="Test Mission",
                why="Test explanation",
                source="test",
                items=[
                    CandidateItem(
                        media_type=media_type,
                        source="tmdb",
                        media_id=f"{media_type}-{key}",
                        title=f"{media_type}-{title}",
                        image="https://example.com/poster.jpg",
                    ),
                ],
            )

        mock_component_rows.side_effect = [
            [
                build_row(
                    MediaTypes.MOVIE.value,
                    "trending_right_now",
                    "Trending Right Now",
                ),
                build_row(
                    MediaTypes.MOVIE.value,
                    "all_time_greats_unseen",
                    "All-Time Greats You Haven't Seen",
                ),
            ],
            [
                build_row(
                    MediaTypes.TV.value,
                    "trending_right_now",
                    "Trending Right Now",
                ),
                build_row(
                    MediaTypes.TV.value,
                    "all_time_greats_unseen",
                    "All-Time Greats You Haven't Seen",
                ),
            ],
        ]

        with patch.object(
            self.user,
            "get_enabled_media_types",
            return_value=[MediaTypes.MOVIE.value, MediaTypes.TV.value],
        ):
            rows = get_discover_rows(self.user, ALL_MEDIA_KEY, show_more=False)

        self.assertEqual(
            [row.key for row in rows],
            ["trending_right_now", "trending_right_now"],
        )
        self.assertEqual(
            [row.title for row in rows],
            [
                "Movies: Trending Right Now",
                "TV Shows: Trending Right Now",
            ],
        )
        mock_component_rows.assert_any_call(
            self.user,
            MediaTypes.MOVIE.value,
            show_more=False,
            include_debug=False,
            defer_artwork=False,
        )
        mock_component_rows.assert_any_call(
            self.user,
            MediaTypes.TV.value,
            show_more=False,
            include_debug=False,
            defer_artwork=False,
        )

    @patch("app.discover.service.get_discover_rows", return_value=[])
    def test_all_media_component_rows_only_request_trending_when_collapsed(
        self,
        mock_get_discover_rows,
    ):
        _get_all_media_component_rows(
            self.user,
            MediaTypes.MOVIE.value,
            show_more=False,
            include_debug=False,
            defer_artwork=False,
        )

        mock_get_discover_rows.assert_called_once_with(
            self.user,
            MediaTypes.MOVIE.value,
            show_more=False,
            include_debug=False,
            defer_artwork=False,
            row_keys=["trending_right_now"],
        )

    @patch("app.discover.service._get_all_media_component_rows")
    def test_all_media_show_more_includes_all_rows_from_each_enabled_media_type(
        self,
        mock_component_rows,
    ):
        def build_row(media_type: str, key: str, title: str) -> RowResult:
            return RowResult(
                key=key,
                title=title,
                mission="Test Mission",
                why="Test explanation",
                source="test",
                items=[
                    CandidateItem(
                        media_type=media_type,
                        source="tmdb",
                        media_id=f"{media_type}-{key}",
                        title=f"{media_type}-{title}",
                        image="https://example.com/poster.jpg",
                    ),
                ],
            )

        mock_component_rows.side_effect = [
            [
                build_row(
                    MediaTypes.MOVIE.value,
                    "trending_right_now",
                    "Trending Right Now",
                ),
                build_row(
                    MediaTypes.MOVIE.value,
                    "all_time_greats_unseen",
                    "All-Time Greats You Haven't Seen",
                ),
            ],
            [
                build_row(
                    MediaTypes.TV.value,
                    "trending_right_now",
                    "Trending Right Now",
                ),
                build_row(
                    MediaTypes.TV.value,
                    "all_time_greats_unseen",
                    "All-Time Greats You Haven't Seen",
                ),
            ],
        ]

        with patch.object(
            self.user,
            "get_enabled_media_types",
            return_value=[MediaTypes.MOVIE.value, MediaTypes.TV.value],
        ):
            rows = get_discover_rows(self.user, ALL_MEDIA_KEY, show_more=True)

        self.assertEqual(
            [row.key for row in rows],
            [
                "trending_right_now",
                "all_time_greats_unseen",
                "trending_right_now",
                "all_time_greats_unseen",
            ],
        )
        self.assertEqual(
            [row.title for row in rows],
            [
                "Movies: Trending Right Now",
                "Movies: All-Time Greats You Haven't Seen",
                "TV Shows: Trending Right Now",
                "TV Shows: All-Time Greats You Haven't Seen",
            ],
        )

    @patch("app.discover.provider_candidates.musicbrainz.get_cover_art")
    @patch("app.discover.provider_candidates._api_cached_results")
    def test_music_coming_soon_candidates_defer_cover_art_fetches(
        self,
        mock_cached_results,
        mock_get_cover_art,
    ):
        mock_cached_results.return_value = [
            {
                "id": "recording-1",
                "title": "Spring Single",
                "artist-credit": [
                    {
                        "name": "Example Artist",
                        "artist": {"name": "Example Artist"},
                    },
                ],
                "first-release-date": "2026-04-01",
                "releases": [
                    {
                        "id": "release-1",
                        "date": "2026-04-01",
                        "release-group": {"id": "group-1"},
                    },
                ],
            },
        ]

        candidates = _musicbrainz_coming_soon_recording_candidates(
            row_key="coming_soon",
            source_reason="Upcoming music",
            limit=10,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].image, settings.IMG_NONE)
        mock_get_cover_art.assert_not_called()

    @patch("app.discover.provider_candidates.services.get_media_metadata")
    def test_trakt_ranked_rows_hydrate_first_buffered_reserve_candidate(
        self,
        mock_get_media_metadata,
    ):
        mock_get_media_metadata.side_effect = lambda _media_type, media_id, _source: {
            "image": f"https://example.com/{media_id}.jpg",
        }
        row_definition = RowDefinition(
            key="all_time_greats_unseen",
            title="All-Time Greats You Haven't Seen",
            mission="Must-watch classics still missing",
            why="Must-watch classics still missing",
            source="trakt",
        )
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id=str(index),
                title=f"Movie {index}",
                image=settings.IMG_NONE,
            )
            for index in range(1, MAX_ITEMS_PER_ROW + 2)
        ]

        row, needs_async_artwork_refresh = _prepare_row_from_candidates(
            self.user,
            MediaTypes.MOVIE.value,
            row_definition,
            {},
            candidates,
            defer_artwork=False,
        )

        self.assertFalse(needs_async_artwork_refresh)
        self.assertEqual(
            row.items[MAX_ITEMS_PER_ROW].image,
            f"https://example.com/{MAX_ITEMS_PER_ROW + 1}.jpg",
        )

    @patch("app.discover.service.TMDB_ADAPTER.top_rated", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.upcoming", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.current_cycle", return_value=[])
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated",
        return_value=[],
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular", return_value=[]
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly",
        side_effect=RuntimeError("trakt down"),
    )
    def test_row_failure_isolated_to_single_row(
        self,
        _mock_trending,
        _mock_popular,
        _mock_anticipated,
        _mock_current_cycle,
        _mock_upcoming,
        _mock_top_rated,
    ):
        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)
        self.assertIsInstance(rows, list)
        self.assertNotIn("trending_right_now", [row.key for row in rows])

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service.TMDB_ADAPTER.top_rated", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.upcoming", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.current_cycle", return_value=[])
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated",
        return_value=[],
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular", return_value=[]
    )
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly")
    def test_trending_row_rebuilds_when_cached_source_changes(
        self,
        mock_trending,
        _mock_popular,
        _mock_anticipated,
        _mock_current_cycle,
        _mock_upcoming,
        _mock_top_rated,
        _mock_profile,
    ):
        stale_payload = {
            "key": "trending_right_now",
            "title": "Trending Right Now",
            "mission": "Cultural Moment",
            "why": "What everyone is watching",
            "source": "tmdb",
            "items": [
                CandidateItem(
                    media_type=MediaTypes.MOVIE.value,
                    source="tmdb",
                    media_id="1",
                    title="Old Cached Item",
                ).to_dict(),
            ],
            "is_stale": False,
            "show_more": False,
            "source_state": "cache",
        }
        cache_repo.set_row_cache(
            self.user.id,
            MediaTypes.MOVIE.value,
            "trending_right_now",
            stale_payload,
            ttl_seconds=3600,
        )

        mock_trending.return_value = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="101",
                title="Fresh One",
                image="https://example.com/101.jpg",
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="102",
                title="Fresh Two",
                image="https://example.com/102.jpg",
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="103",
                title="Fresh Three",
                image="https://example.com/103.jpg",
            ),
        ]

        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)
        trending_row = next(row for row in rows if row.key == "trending_right_now")

        mock_trending.assert_called_once_with(limit=100)
        self.assertEqual(trending_row.source, "trakt")
        self.assertEqual(trending_row.why, "What everyone has been watching this week.")
        self.assertEqual(
            [item.title for item in trending_row.items],
            ["Fresh One", "Fresh Two", "Fresh Three"],
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service.TMDB_ADAPTER.top_rated", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.upcoming", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.current_cycle", return_value=[])
    @patch("app.discover.service.get_tracked_keys_by_media_type")
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated",
        return_value=[],
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular", return_value=[]
    )
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly")
    def test_trending_row_overfetches_filters_and_caps_to_twelve(
        self,
        mock_trending,
        _mock_popular,
        _mock_anticipated,
        mock_tracked_keys,
        _mock_current_cycle,
        _mock_upcoming,
        _mock_top_rated,
        _mock_profile,
    ):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id=str(index),
                title=f"Movie {index}",
                image=f"https://example.com/{index}.jpg",
            )
            for index in range(1, 21)
        ]
        mock_trending.return_value = candidates

        blocked = {
            (MediaTypes.MOVIE.value, "tmdb", "1"),
            (MediaTypes.MOVIE.value, "tmdb", "2"),
            (MediaTypes.MOVIE.value, "tmdb", "3"),
        }

        def tracked_side_effect(user, media_type, statuses=None):
            if statuses == {
                Status.COMPLETED.value,
                Status.DROPPED.value,
                Status.PLANNING.value,
            }:
                return blocked
            return set()

        mock_tracked_keys.side_effect = tracked_side_effect

        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)
        trending_row = next(row for row in rows if row.key == "trending_right_now")

        mock_trending.assert_called_once_with(limit=100)
        self.assertEqual(len(trending_row.items), 12)
        self.assertTrue(
            all(item.media_id not in {"1", "2", "3"} for item in trending_row.items)
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service.TMDB_ADAPTER.top_rated", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.upcoming", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.current_cycle", return_value=[])
    @patch("app.discover.service._queue_stale_refresh")
    @patch("app.discover.provider_candidates.services.get_media_metadata")
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated",
        return_value=[],
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular", return_value=[]
    )
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly")
    def test_trending_row_defers_missing_artwork_hydration_when_enabled(
        self,
        mock_trending,
        _mock_popular,
        _mock_anticipated,
        mock_get_metadata,
        mock_queue_stale_refresh,
        _mock_current_cycle,
        _mock_upcoming,
        _mock_top_rated,
        _mock_profile,
    ):
        mock_trending.return_value = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="701",
                title="Deferred Art One",
                image=None,
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="702",
                title="Deferred Art Two",
                image=None,
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="703",
                title="Deferred Art Three",
                image=None,
            ),
        ]

        rows = get_discover_rows(
            self.user,
            MediaTypes.MOVIE.value,
            show_more=False,
            defer_artwork=True,
        )
        trending_row = next(row for row in rows if row.key == "trending_right_now")

        self.assertEqual(
            [item.image for item in trending_row.items], [None, None, None]
        )
        mock_get_metadata.assert_not_called()
        mock_queue_stale_refresh.assert_called_once_with(
            self.user.id,
            MediaTypes.MOVIE.value,
            "trending_right_now",
            False,
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service.TMDB_ADAPTER.top_rated", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.upcoming", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.current_cycle", return_value=[])
    @patch("app.discover.service._queue_stale_refresh")
    @patch("app.discover.provider_candidates.services.get_media_metadata")
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated",
        return_value=[],
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular", return_value=[]
    )
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly")
    def test_cached_trending_row_with_missing_artwork_queues_refresh_when_deferred(
        self,
        mock_trending,
        _mock_popular,
        _mock_anticipated,
        mock_get_metadata,
        mock_queue_stale_refresh,
        _mock_current_cycle,
        _mock_upcoming,
        _mock_top_rated,
        _mock_profile,
    ):
        cache_repo.set_row_cache(
            self.user.id,
            MediaTypes.MOVIE.value,
            "trending_right_now",
            {
                "key": "trending_right_now",
                "title": "Trending Right Now",
                "mission": "Cultural Moment",
                "why": "What everyone has been watching this week.",
                "source": "trakt",
                "items": [
                    CandidateItem(
                        media_type=MediaTypes.MOVIE.value,
                        source="tmdb",
                        media_id="801",
                        title="Cached Missing Art",
                        image=settings.IMG_NONE,
                    ).to_dict(),
                    CandidateItem(
                        media_type=MediaTypes.MOVIE.value,
                        source="tmdb",
                        media_id="802",
                        title="Cached Missing Art Two",
                        image=settings.IMG_NONE,
                    ).to_dict(),
                    CandidateItem(
                        media_type=MediaTypes.MOVIE.value,
                        source="tmdb",
                        media_id="803",
                        title="Cached Missing Art Three",
                        image=settings.IMG_NONE,
                    ).to_dict(),
                ],
                "is_stale": False,
                "show_more": False,
                "source_state": "cache",
            },
            ttl_seconds=3600,
        )

        rows = get_discover_rows(
            self.user,
            MediaTypes.MOVIE.value,
            show_more=False,
            defer_artwork=True,
        )
        trending_row = next(row for row in rows if row.key == "trending_right_now")

        self.assertEqual(len(trending_row.items), 3)
        self.assertEqual(trending_row.items[0].title, "Cached Missing Art")
        mock_trending.assert_not_called()
        mock_get_metadata.assert_not_called()
        mock_queue_stale_refresh.assert_called_once_with(
            self.user.id,
            MediaTypes.MOVIE.value,
            "trending_right_now",
            False,
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service.TMDB_ADAPTER.top_rated", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.upcoming", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.current_cycle", return_value=[])
    @patch("app.discover.provider_candidates.services.get_media_metadata")
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated",
        return_value=[],
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular", return_value=[]
    )
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly")
    def test_trending_row_hydrates_missing_artwork_from_tmdb(
        self,
        mock_trending,
        _mock_popular,
        _mock_anticipated,
        mock_get_metadata,
        _mock_current_cycle,
        _mock_upcoming,
        _mock_top_rated,
        _mock_profile,
    ):
        mock_trending.return_value = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="201",
                title="Needs Art One",
                image=None,
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="202",
                title="Needs Art Two",
                image=None,
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="203",
                title="Needs Art Three",
                image=None,
            ),
        ]

        def metadata_side_effect(
            media_type, media_id, source, season_numbers=None, episode_number=None
        ):
            return {"image": f"https://image.tmdb.org/t/p/w500/{media_id}.jpg"}

        mock_get_metadata.side_effect = metadata_side_effect

        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)
        trending_row = next(row for row in rows if row.key == "trending_right_now")

        self.assertEqual(
            [item.image for item in trending_row.items],
            [
                "https://image.tmdb.org/t/p/w500/201.jpg",
                "https://image.tmdb.org/t/p/w500/202.jpg",
                "https://image.tmdb.org/t/p/w500/203.jpg",
            ],
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service.TMDB_ADAPTER.top_rated", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.upcoming", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.current_cycle", return_value=[])
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated",
        return_value=[],
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular", return_value=[]
    )
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly")
    def test_trending_row_rebuilds_cached_payload_with_missing_artwork(
        self,
        mock_trending,
        _mock_popular,
        _mock_anticipated,
        _mock_current_cycle,
        _mock_upcoming,
        _mock_top_rated,
        _mock_profile,
    ):
        stale_art_payload = {
            "key": "trending_right_now",
            "title": "Trending Right Now",
            "mission": "Cultural Moment",
            "why": "What everyone has been watching this week.",
            "source": "trakt",
            "items": [
                CandidateItem(
                    media_type=MediaTypes.MOVIE.value,
                    source="tmdb",
                    media_id="501",
                    title="Old Missing Art",
                    image=settings.IMG_NONE,
                ).to_dict(),
            ],
            "is_stale": False,
            "show_more": False,
            "source_state": "cache",
        }
        cache_repo.set_row_cache(
            self.user.id,
            MediaTypes.MOVIE.value,
            "trending_right_now",
            stale_art_payload,
            ttl_seconds=3600,
        )

        mock_trending.return_value = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="601",
                title="Fresh With Art",
                image="https://example.com/fresh-with-art.jpg",
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="602",
                title="Fresh With Art 2",
                image="https://example.com/fresh-with-art-2.jpg",
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="603",
                title="Fresh With Art 3",
                image="https://example.com/fresh-with-art-3.jpg",
            ),
        ]

        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)
        trending_row = next(row for row in rows if row.key == "trending_right_now")

        mock_trending.assert_called_once_with(limit=100)
        self.assertEqual(
            [item.title for item in trending_row.items],
            ["Fresh With Art", "Fresh With Art 2", "Fresh With Art 3"],
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service.TMDB_ADAPTER.top_rated", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.upcoming", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.current_cycle", return_value=[])
    @patch("app.discover.service.get_tracked_keys_by_media_type")
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated",
        return_value=[],
    )
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly")
    def test_all_time_greats_unseen_expands_popular_fetch_and_persists_pull_hint(
        self,
        mock_trending,
        mock_popular,
        _mock_anticipated,
        mock_tracked_keys,
        _mock_current_cycle,
        _mock_upcoming,
        _mock_top_rated,
        _mock_profile,
    ):
        def build_candidate(index: int) -> CandidateItem:
            return CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id=str(index),
                title=f"Movie {index}",
                image=f"https://example.com/{index}.jpg",
            )

        page_one = [build_candidate(index) for index in range(1, 101)]
        page_two = [build_candidate(index) for index in range(101, 201)]
        mock_trending.return_value = [build_candidate(89)]

        def popular_side_effect(page, limit):
            if page == 1:
                return page_one[:limit]
            if page == 2:
                return page_two[:limit]
            return []

        mock_popular.side_effect = popular_side_effect

        blocked = {
            (MediaTypes.MOVIE.value, "tmdb", str(index)) for index in range(1, 89)
        }

        def tracked_side_effect(user, media_type, statuses=None):
            if media_type == MediaTypes.MOVIE.value and statuses == {
                Status.COMPLETED.value,
                Status.DROPPED.value,
                Status.PLANNING.value,
            }:
                return blocked
            return set()

        mock_tracked_keys.side_effect = tracked_side_effect

        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)
        canon_row = next(row for row in rows if row.key == "all_time_greats_unseen")

        self.assertEqual(mock_popular.call_count, 1)
        self.assertEqual(
            mock_popular.call_args_list[0].kwargs, {"page": 1, "limit": 100}
        )
        self.assertEqual(len(canon_row.items), 12)
        self.assertTrue(
            all(
                item.media_id not in {str(index) for index in range(1, 89)}
                for item in canon_row.items
            )
        )
        self.assertIn("89", {item.media_id for item in canon_row.items})

        cached_payload, _ = cache_repo.get_row_cache(
            self.user.id,
            MediaTypes.MOVIE.value,
            "all_time_greats_unseen",
        )
        self.assertIsNotNone(cached_payload)
        self.assertEqual(
            cached_payload.get("meta", {}).get("adaptive_pull_target"), 100
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service.TMDB_ADAPTER.top_rated", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.upcoming", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.current_cycle", return_value=[])
    @patch("app.discover.service.get_tracked_keys_by_media_type", return_value=set())
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated",
        return_value=[],
    )
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly")
    def test_all_time_greats_unseen_keeps_overlap_items_when_cached_overlap(
        self,
        mock_trending,
        mock_popular,
        _mock_anticipated,
        _mock_tracked_keys,
        _mock_current_cycle,
        _mock_upcoming,
        _mock_top_rated,
        _mock_profile,
    ):
        def build_candidate(index: int) -> CandidateItem:
            return CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id=str(index),
                title=f"Movie {index}",
                image=f"https://example.com/{index}.jpg",
            )

        mock_trending.return_value = [
            build_candidate(89),
            build_candidate(5000),
            build_candidate(5001),
        ]
        mock_popular.return_value = [build_candidate(index) for index in range(89, 189)]

        cached_payload = {
            "key": "all_time_greats_unseen",
            "title": "All-Time Greats You Haven't Seen",
            "mission": "Canon",
            "why": "Must-watch classics still missing",
            "source": "trakt",
            "items": [build_candidate(index).to_dict() for index in range(89, 101)],
            "is_stale": False,
            "show_more": False,
            "source_state": "cache",
            "meta": {"adaptive_pull_target": 100},
        }
        cache_repo.set_row_cache(
            self.user.id,
            MediaTypes.MOVIE.value,
            "all_time_greats_unseen",
            cached_payload,
            ttl_seconds=3600,
        )

        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)
        canon_row = next(row for row in rows if row.key == "all_time_greats_unseen")
        canon_ids = {item.media_id for item in canon_row.items}

        self.assertEqual(len(canon_row.items), 12)
        self.assertIn("89", canon_ids)
        self.assertIn("90", canon_ids)
        self.assertIn("100", canon_ids)
        self.assertNotIn("101", canon_ids)

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service.TMDB_ADAPTER.top_rated", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.upcoming", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.current_cycle", return_value=[])
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated",
        return_value=[],
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular", return_value=[]
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly",
        return_value=[],
    )
    def test_all_time_greats_unseen_rebuilds_when_cached_schema_is_old(
        self,
        _mock_trending,
        mock_popular,
        _mock_anticipated,
        _mock_current_cycle,
        _mock_upcoming,
        _mock_top_rated,
        _mock_profile,
    ):
        stale_payload = {
            "key": "all_time_greats_unseen",
            "title": "All-Time Greats You Haven't Seen",
            "mission": "Canon",
            "why": "Must-watch classics still missing",
            "source": "trakt",
            "items": [
                CandidateItem(
                    media_type=MediaTypes.MOVIE.value,
                    source="tmdb",
                    media_id="999",
                    title="Old Cached Canon",
                ).to_dict(),
            ],
            "is_stale": False,
            "show_more": False,
            "source_state": "cache",
            "meta": {"adaptive_pull_target": 100, "schema_version": 1},
        }
        cache_repo.set_row_cache(
            self.user.id,
            MediaTypes.MOVIE.value,
            "all_time_greats_unseen",
            stale_payload,
            ttl_seconds=3600,
        )

        mock_popular.return_value = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="1001",
                title="Fresh Canon One",
                image="https://example.com/1001.jpg",
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="1002",
                title="Fresh Canon Two",
                image="https://example.com/1002.jpg",
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="1003",
                title="Fresh Canon Three",
                image="https://example.com/1003.jpg",
            ),
        ]

        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)
        canon_row = next(row for row in rows if row.key == "all_time_greats_unseen")

        self.assertNotEqual(
            [item.title for item in canon_row.items], ["Old Cached Canon"]
        )
        self.assertGreaterEqual(mock_popular.call_count, 1)

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service.TMDB_ADAPTER.top_rated", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.upcoming", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.current_cycle", return_value=[])
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated",
        return_value=[],
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular",
        side_effect=RuntimeError("trakt down"),
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly",
        return_value=[],
    )
    def test_all_time_greats_unseen_uses_cached_row_when_rebuild_fails(
        self,
        _mock_trending,
        mock_popular,
        _mock_anticipated,
        _mock_current_cycle,
        _mock_upcoming,
        mock_top_rated,
        _mock_profile,
    ):
        cached_payload = {
            "key": "all_time_greats_unseen",
            "title": "All-Time Greats You Haven't Seen",
            "mission": "Canon",
            "why": "Must-watch classics still missing",
            "source": "trakt",
            "items": [
                CandidateItem(
                    media_type=MediaTypes.MOVIE.value,
                    source="tmdb",
                    media_id="999",
                    title="Old Cached Canon",
                    image="https://example.com/999.jpg",
                ).to_dict(),
                CandidateItem(
                    media_type=MediaTypes.MOVIE.value,
                    source="tmdb",
                    media_id="1000",
                    title="Old Cached Canon Two",
                    image="https://example.com/1000.jpg",
                ).to_dict(),
                CandidateItem(
                    media_type=MediaTypes.MOVIE.value,
                    source="tmdb",
                    media_id="1001",
                    title="Old Cached Canon Three",
                    image="https://example.com/1001.jpg",
                ).to_dict(),
            ],
            "is_stale": False,
            "show_more": False,
            "source_state": "cache",
            "meta": {"adaptive_pull_target": 100, "schema_version": 1},
        }
        cache_repo.set_row_cache(
            self.user.id,
            MediaTypes.MOVIE.value,
            "all_time_greats_unseen",
            cached_payload,
            ttl_seconds=3600,
        )

        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)
        canon_row = next(row for row in rows if row.key == "all_time_greats_unseen")

        self.assertEqual(
            [item.title for item in canon_row.items],
            [
                "Old Cached Canon",
                "Old Cached Canon Two",
                "Old Cached Canon Three",
            ],
        )
        self.assertTrue(canon_row.is_stale)
        self.assertEqual(canon_row.source_state, "stale")
        self.assertGreaterEqual(mock_popular.call_count, 1)
        mock_top_rated.assert_not_called()

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service.TMDB_ADAPTER.upcoming", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.current_cycle", return_value=[])
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated",
        return_value=[],
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular",
        side_effect=RuntimeError("trakt down"),
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly",
        return_value=[],
    )
    @patch("app.discover.service.TMDB_ADAPTER.top_rated")
    def test_all_time_greats_unseen_falls_back_to_tmdb_when_trakt_fails_without_cache(
        self,
        mock_top_rated,
        _mock_trending,
        _mock_popular,
        _mock_anticipated,
        _mock_current_cycle,
        _mock_upcoming,
        _mock_profile,
    ):
        mock_top_rated.return_value = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="1001",
                title="Fallback Canon One",
                image="https://example.com/1001.jpg",
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="1002",
                title="Fallback Canon Two",
                image="https://example.com/1002.jpg",
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="1003",
                title="Fallback Canon Three",
                image="https://example.com/1003.jpg",
            ),
        ]

        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)
        canon_row = next(row for row in rows if row.key == "all_time_greats_unseen")

        self.assertEqual(
            [item.title for item in canon_row.items],
            [
                "Fallback Canon One",
                "Fallback Canon Two",
                "Fallback Canon Three",
            ],
        )
        self.assertEqual(canon_row.source_state, "fallback")

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service.TMDB_ADAPTER.top_rated", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.upcoming", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.current_cycle", return_value=[])
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated",
        return_value=[],
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular", return_value=[]
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly",
        return_value=[],
    )
    @patch("app.discover.service._comfort_candidates")
    def test_comfort_rewatches_rebuilds_when_cached_schema_is_old(
        self,
        mock_comfort_candidates,
        _mock_trending,
        _mock_popular,
        _mock_anticipated,
        _mock_current_cycle,
        _mock_upcoming,
        _mock_top_rated,
        _mock_profile,
    ):
        stale_payload = {
            "key": "comfort_rewatches",
            "title": "Comfort Rewatches",
            "mission": "Comfort",
            "why": "Past favorites that feel ready to revisit again.",
            "source": "local",
            "items": [
                CandidateItem(
                    media_type=MediaTypes.MOVIE.value,
                    source="tmdb",
                    media_id="old-1",
                    title="Old Comfort",
                    final_score=0.2,
                ).to_dict(),
                CandidateItem(
                    media_type=MediaTypes.MOVIE.value,
                    source="tmdb",
                    media_id="old-2",
                    title="Old Comfort 2",
                    final_score=0.2,
                ).to_dict(),
                CandidateItem(
                    media_type=MediaTypes.MOVIE.value,
                    source="tmdb",
                    media_id="old-3",
                    title="Old Comfort 3",
                    final_score=0.2,
                ).to_dict(),
            ],
            "is_stale": False,
            "show_more": False,
            "source_state": "cache",
        }
        cache_repo.set_row_cache(
            self.user.id,
            MediaTypes.MOVIE.value,
            "comfort_rewatches",
            stale_payload,
            ttl_seconds=3600,
        )

        mock_comfort_candidates.return_value = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="new-1",
                title="Fresh Comfort One",
                final_score=0.4,
                score_breakdown={"user_score": 9.5, "days_since_activity": 300.0},
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="new-2",
                title="Fresh Comfort Two",
                final_score=0.35,
                score_breakdown={"user_score": 9.0, "days_since_activity": 220.0},
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="new-3",
                title="Fresh Comfort Three",
                final_score=0.33,
                score_breakdown={"user_score": 8.5, "days_since_activity": 180.0},
            ),
        ]

        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)
        comfort_row = next(row for row in rows if row.key == "comfort_rewatches")

        self.assertEqual(
            [item.title for item in comfort_row.items[:3]],
            ["Fresh Comfort One", "Fresh Comfort Two", "Fresh Comfort Three"],
        )
        self.assertNotIn("Old Comfort", [item.title for item in comfort_row.items])
        self.assertGreaterEqual(mock_comfort_candidates.call_count, 1)

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service.TMDB_ADAPTER.top_rated", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.upcoming", return_value=[])
    @patch("app.discover.service.TMDB_ADAPTER.current_cycle", return_value=[])
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly")
    def test_coming_soon_row_uses_trakt_anticipated_in_third_slot(
        self,
        mock_trending,
        mock_popular,
        mock_anticipated,
        _mock_current_cycle,
        _mock_upcoming,
        _mock_top_rated,
        _mock_profile,
    ):
        def build_candidate(media_id: int, title: str) -> CandidateItem:
            return CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id=str(media_id),
                title=title,
                image=f"https://example.com/{media_id}.jpg",
            )

        mock_trending.return_value = [
            build_candidate(10001, "Trending One"),
            build_candidate(10002, "Trending Two"),
            build_candidate(10003, "Trending Three"),
        ]
        mock_popular.return_value = [
            build_candidate(20001, "Popular One"),
            build_candidate(20002, "Popular Two"),
            build_candidate(20003, "Popular Three"),
        ]
        mock_anticipated.return_value = [
            build_candidate(30001, "Upcoming One"),
            build_candidate(30002, "Upcoming Two"),
            build_candidate(30003, "Upcoming Three"),
        ]

        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)
        keys = [row.key for row in rows]
        coming_soon_row = next(row for row in rows if row.key == "coming_soon")

        self.assertEqual(
            keys[:3], ["trending_right_now", "all_time_greats_unseen", "coming_soon"]
        )
        mock_anticipated.assert_called_once_with(page=1, limit=100)
        self.assertEqual(coming_soon_row.source, "trakt")
        self.assertEqual(
            [item.title for item in coming_soon_row.items],
            ["Upcoming One", "Upcoming Two", "Upcoming Three"],
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service._comfort_candidates")
    @patch("app.discover.service._top_picks_candidates")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly")
    def test_movie_rows_render_exactly_five_in_expected_order(
        self,
        mock_trending,
        mock_popular,
        mock_anticipated,
        mock_top_picks,
        mock_comfort,
        _mock_profile,
    ):
        def candidate(media_id: str, title: str) -> CandidateItem:
            return CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id=media_id,
                title=title,
                image=f"https://example.com/{media_id}.jpg",
                final_score=0.9,
            )

        mock_trending.return_value = [
            candidate("100", "Trending 1"),
            candidate("101", "Trending 2"),
            candidate("102", "Trending 3"),
        ]
        mock_popular.return_value = [
            candidate("200", "Canon 1"),
            candidate("201", "Canon 2"),
            candidate("202", "Canon 3"),
        ]
        mock_anticipated.return_value = [
            candidate("300", "Soon 1"),
            candidate("301", "Soon 2"),
            candidate("302", "Soon 3"),
        ]
        mock_top_picks.return_value = [
            candidate("400", "Pick 1"),
            candidate("401", "Pick 2"),
            candidate("402", "Pick 3"),
        ]
        mock_comfort.return_value = [
            candidate("500", "Comfort 1"),
            candidate("501", "Comfort 2"),
            candidate("502", "Comfort 3"),
        ]

        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)
        self.assertEqual(
            [row.key for row in rows],
            [
                "trending_right_now",
                "all_time_greats_unseen",
                "coming_soon",
                "top_picks_for_you",
                "comfort_rewatches",
            ],
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service._comfort_candidates")
    @patch("app.discover.service._top_picks_candidates")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_anticipated")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_popular")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_watched_weekly")
    def test_tv_rows_render_in_expected_order(
        self,
        mock_trending,
        mock_popular,
        mock_anticipated,
        mock_top_picks,
        mock_comfort,
        _mock_profile,
    ):
        def candidate(media_id: str, title: str) -> CandidateItem:
            return CandidateItem(
                media_type=MediaTypes.TV.value,
                source="tmdb",
                media_id=media_id,
                title=title,
                image=f"https://example.com/{media_id}.jpg",
                final_score=0.9,
            )

        mock_trending.return_value = [
            candidate("100", "Trending 1"),
            candidate("101", "Trending 2"),
            candidate("102", "Trending 3"),
        ]
        mock_popular.return_value = [
            candidate("200", "Canon 1"),
            candidate("201", "Canon 2"),
            candidate("202", "Canon 3"),
        ]
        mock_anticipated.return_value = [
            candidate("300", "Soon 1"),
            candidate("301", "Soon 2"),
            candidate("302", "Soon 3"),
        ]
        mock_top_picks.return_value = [
            candidate("400", "Pick 1"),
            candidate("401", "Pick 2"),
            candidate("402", "Pick 3"),
        ]
        mock_comfort.return_value = [
            candidate("500", "Comfort 1"),
            candidate("501", "Comfort 2"),
            candidate("502", "Comfort 3"),
        ]

        rows = get_discover_rows(
            self.user,
            MediaTypes.TV.value,
            show_more=False,
            defer_artwork=True,
        )
        self.assertEqual(
            [row.key for row in rows],
            [
                "trending_right_now",
                "all_time_greats_unseen",
                "coming_soon",
                "top_picks_for_you",
                "clear_out_next",
                "comfort_rewatches",
            ],
        )
        mock_trending.assert_called_once_with(limit=100, media_type=MediaTypes.TV.value)
        mock_popular.assert_called_once_with(
            page=1, limit=100, media_type=MediaTypes.TV.value
        )
        mock_anticipated.assert_called_once_with(
            page=1, limit=100, media_type=MediaTypes.TV.value
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service._comfort_candidates")
    @patch("app.discover.service._top_picks_candidates")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_anticipated")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_popular")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_watched_weekly")
    def test_anime_rows_render_in_expected_order_and_uses_anime_filter(
        self,
        mock_trending,
        mock_popular,
        mock_anticipated,
        mock_top_picks,
        mock_comfort,
        _mock_profile,
    ):
        def candidate(media_id: str, title: str) -> CandidateItem:
            return CandidateItem(
                media_type=MediaTypes.ANIME.value,
                source="tmdb",
                media_id=media_id,
                title=title,
                image=f"https://example.com/{media_id}.jpg",
                final_score=0.9,
            )

        mock_trending.return_value = [
            candidate("100", "Trending 1"),
            candidate("101", "Trending 2"),
            candidate("102", "Trending 3"),
        ]
        mock_popular.return_value = [
            candidate("200", "Canon 1"),
            candidate("201", "Canon 2"),
            candidate("202", "Canon 3"),
        ]
        mock_anticipated.return_value = [
            candidate("300", "Soon 1"),
            candidate("301", "Soon 2"),
            candidate("302", "Soon 3"),
        ]
        mock_top_picks.return_value = [
            candidate("400", "Pick 1"),
            candidate("401", "Pick 2"),
            candidate("402", "Pick 3"),
        ]
        mock_comfort.return_value = [
            candidate("500", "Comfort 1"),
            candidate("501", "Comfort 2"),
            candidate("502", "Comfort 3"),
        ]

        rows = get_discover_rows(
            self.user,
            MediaTypes.ANIME.value,
            show_more=False,
            defer_artwork=True,
        )
        self.assertEqual(
            [row.key for row in rows],
            [
                "trending_right_now",
                "all_time_greats_unseen",
                "coming_soon",
                "top_picks_for_you",
                "clear_out_next",
                "comfort_rewatches",
            ],
        )
        mock_trending.assert_called_once_with(
            limit=100,
            media_type=MediaTypes.ANIME.value,
            trakt_genres=["anime"],
        )
        mock_popular.assert_called_once_with(
            page=1,
            limit=100,
            media_type=MediaTypes.ANIME.value,
            trakt_genres=["anime"],
        )
        mock_anticipated.assert_called_once_with(
            page=1,
            limit=100,
            media_type=MediaTypes.ANIME.value,
            trakt_genres=["anime"],
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service._comfort_candidates", return_value=[])
    @patch("app.discover.service._top_picks_candidates", return_value=[])
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.show_anticipated",
        return_value=[],
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.show_popular", return_value=[]
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.show_watched_weekly",
        return_value=[],
    )
    def test_tv_and_anime_rows_keep_all_slots_when_personalized_rows_empty(
        self,
        _mock_trending,
        _mock_popular,
        _mock_anticipated,
        _mock_top_picks,
        _mock_comfort,
        _mock_profile,
    ):
        tv_rows = get_discover_rows(self.user, MediaTypes.TV.value, show_more=False)
        anime_rows = get_discover_rows(
            self.user, MediaTypes.ANIME.value, show_more=False
        )

        expected_order = [
            "trending_right_now",
            "all_time_greats_unseen",
            "coming_soon",
            "top_picks_for_you",
            "clear_out_next",
            "comfort_rewatches",
        ]
        self.assertEqual([row.key for row in tv_rows], expected_order)
        self.assertEqual([row.key for row in anime_rows], expected_order)

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service._comfort_candidates", return_value=[])
    @patch("app.discover.service._top_picks_candidates", return_value=[])
    @patch("app.discover.service._provider_row_candidates", return_value=[])
    def test_remaining_media_types_keep_all_five_slots_when_rows_are_empty(
        self,
        _mock_provider_rows,
        _mock_top_picks,
        _mock_comfort,
        _mock_profile,
    ):
        expected_order = [
            "trending_right_now",
            "all_time_greats_unseen",
            "coming_soon",
            "top_picks_for_you",
            "comfort_rewatches",
        ]
        media_types = [
            MediaTypes.MUSIC.value,
            MediaTypes.PODCAST.value,
            MediaTypes.BOOK.value,
            MediaTypes.COMIC.value,
            MediaTypes.MANGA.value,
            MediaTypes.GAME.value,
            MediaTypes.BOARDGAME.value,
        ]
        for media_type in media_types:
            with self.subTest(media_type=media_type):
                rows = get_discover_rows(self.user, media_type, show_more=False)
                self.assertEqual([row.key for row in rows], expected_order)

    @patch("app.discover.provider_candidates._igdb_games_candidates", return_value=[])
    def test_game_provider_rows_dispatch_to_igdb(self, mock_igdb_candidates):
        self.assertEqual(
            _provider_row_candidates(MediaTypes.GAME.value, "trending_right_now"),
            [],
        )
        self.assertEqual(
            _provider_row_candidates(MediaTypes.GAME.value, "all_time_greats_unseen"),
            [],
        )
        self.assertEqual(
            _provider_row_candidates(MediaTypes.GAME.value, "coming_soon"),
            [],
        )
        self.assertEqual(mock_igdb_candidates.call_count, 3)

    @patch(
        "app.discover.provider_candidates._musicbrainz_coming_soon_recording_candidates"
    )
    @patch("app.discover.provider_candidates._itunes_top_podcasts_candidates")
    @patch("app.discover.provider_candidates._bgg_hot_candidates")
    @patch("app.discover.provider_candidates._comicvine_coming_soon_volume_candidates")
    @patch("app.discover.provider_candidates._openlibrary_coming_soon_candidates")
    def test_provider_coming_soon_dispatch_for_remaining_media_types(
        self,
        mock_book_soon,
        mock_comic_soon,
        mock_bgg_hot,
        mock_podcast_soon,
        mock_music_soon,
    ):
        book_candidates = [
            CandidateItem(
                media_type=MediaTypes.BOOK.value,
                source=Sources.OPENLIBRARY.value,
                media_id="OL1M",
                title="Upcoming Book",
            ),
        ]
        comic_candidates = [
            CandidateItem(
                media_type=MediaTypes.COMIC.value,
                source=Sources.COMICVINE.value,
                media_id="1001",
                title="Upcoming Comic",
            ),
        ]
        boardgame_candidates = [
            CandidateItem(
                media_type=MediaTypes.BOARDGAME.value,
                source=Sources.BGG.value,
                media_id="2001",
                title="Upcoming Board Game",
            ),
        ]
        podcast_candidates = [
            CandidateItem(
                media_type=MediaTypes.PODCAST.value,
                source=Sources.POCKETCASTS.value,
                media_id="3001",
                title="Upcoming Podcast",
            ),
        ]
        music_candidates = [
            CandidateItem(
                media_type=MediaTypes.MUSIC.value,
                source=Sources.MUSICBRAINZ.value,
                media_id="4f8ec6dc-8f57-4f4b-a510-56ca25d31f5f",
                title="Upcoming Track",
            ),
        ]

        mock_book_soon.return_value = book_candidates
        mock_comic_soon.return_value = comic_candidates
        mock_bgg_hot.return_value = boardgame_candidates
        mock_podcast_soon.return_value = podcast_candidates
        mock_music_soon.return_value = music_candidates

        self.assertEqual(
            _provider_row_candidates(MediaTypes.BOOK.value, "coming_soon"),
            book_candidates,
        )
        self.assertEqual(
            _provider_row_candidates(MediaTypes.COMIC.value, "coming_soon"),
            comic_candidates,
        )
        self.assertEqual(
            _provider_row_candidates(MediaTypes.BOARDGAME.value, "coming_soon"),
            boardgame_candidates,
        )
        self.assertEqual(
            _provider_row_candidates(MediaTypes.PODCAST.value, "coming_soon"),
            podcast_candidates,
        )
        self.assertEqual(
            _provider_row_candidates(MediaTypes.MUSIC.value, "coming_soon"),
            music_candidates,
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.provider_candidates.services.get_media_metadata")
    @patch("app.discover.service._queue_stale_refresh")
    @patch("app.discover.service._comfort_candidates", return_value=[])
    @patch("app.discover.service._top_picks_candidates", return_value=[])
    @patch("app.discover.service._provider_row_candidates")
    def test_boardgame_provider_row_defers_missing_artwork_hydration_when_enabled(
        self,
        mock_provider_candidates,
        _mock_top_picks,
        _mock_comfort,
        mock_queue_stale_refresh,
        mock_get_metadata,
        _mock_profile,
    ):
        def provider_side_effect(media_type, row_key):
            if (
                media_type == MediaTypes.BOARDGAME.value
                and row_key == "trending_right_now"
            ):
                return [
                    CandidateItem(
                        media_type=MediaTypes.BOARDGAME.value,
                        source=Sources.BGG.value,
                        media_id="9001",
                        title="Deferred Board Game Art",
                        image=None,
                    ),
                ]
            return []

        mock_provider_candidates.side_effect = provider_side_effect

        rows = get_discover_rows(
            self.user,
            MediaTypes.BOARDGAME.value,
            show_more=False,
            defer_artwork=True,
        )
        trending_row = next(row for row in rows if row.key == "trending_right_now")

        self.assertEqual(len(trending_row.items), 1)
        self.assertIsNone(trending_row.items[0].image)
        mock_get_metadata.assert_not_called()
        mock_queue_stale_refresh.assert_any_call(
            self.user.id,
            MediaTypes.BOARDGAME.value,
            "trending_right_now",
            False,
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service._queue_stale_refresh")
    @patch("app.discover.provider_candidates.services.get_media_metadata")
    @patch("app.discover.service._comfort_candidates", return_value=[])
    @patch("app.discover.service._top_picks_candidates", return_value=[])
    @patch("app.discover.service._provider_row_candidates")
    def test_boardgame_provider_row_hydrates_missing_artwork_when_not_deferred(
        self,
        mock_provider_candidates,
        _mock_top_picks,
        _mock_comfort,
        mock_get_metadata,
        mock_queue_stale_refresh,
        _mock_profile,
    ):
        def provider_side_effect(media_type, row_key):
            if (
                media_type == MediaTypes.BOARDGAME.value
                and row_key == "trending_right_now"
            ):
                return [
                    CandidateItem(
                        media_type=MediaTypes.BOARDGAME.value,
                        source=Sources.BGG.value,
                        media_id="9002",
                        title="Hydrated Board Game Art",
                        image=None,
                    ),
                ]
            return []

        mock_provider_candidates.side_effect = provider_side_effect
        mock_get_metadata.return_value = {"image": "https://example.com/boardgame.jpg"}

        rows = get_discover_rows(
            self.user,
            MediaTypes.BOARDGAME.value,
            show_more=False,
            defer_artwork=False,
        )
        trending_row = next(row for row in rows if row.key == "trending_right_now")

        self.assertEqual(len(trending_row.items), 1)
        self.assertEqual(
            trending_row.items[0].image, "https://example.com/boardgame.jpg"
        )
        mock_queue_stale_refresh.assert_not_called()
        mock_get_metadata.assert_called()

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch(
        "app.discover.service._comfort_candidates",
        side_effect=RuntimeError("comfort row failed"),
    )
    @patch(
        "app.discover.service._top_picks_candidates",
        side_effect=RuntimeError("top picks row failed"),
    )
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_anticipated")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_popular")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_watched_weekly")
    def test_tv_rows_still_render_empty_personalized_slots_when_row_build_fails(
        self,
        mock_trending,
        mock_popular,
        mock_anticipated,
        _mock_top_picks,
        _mock_comfort,
        _mock_profile,
    ):
        def candidate(media_id: str, title: str) -> CandidateItem:
            return CandidateItem(
                media_type=MediaTypes.TV.value,
                source="tmdb",
                media_id=media_id,
                title=title,
                image=f"https://example.com/{media_id}.jpg",
                final_score=0.9,
            )

        mock_trending.return_value = [
            candidate("100", "Trending 1"),
            candidate("101", "Trending 2"),
            candidate("102", "Trending 3"),
        ]
        mock_popular.return_value = [
            candidate("200", "Canon 1"),
            candidate("201", "Canon 2"),
            candidate("202", "Canon 3"),
        ]
        mock_anticipated.return_value = [
            candidate("300", "Soon 1"),
            candidate("301", "Soon 2"),
            candidate("302", "Soon 3"),
        ]

        rows = get_discover_rows(
            self.user,
            MediaTypes.TV.value,
            show_more=False,
            defer_artwork=True,
        )
        row_map = {row.key: row for row in rows}

        self.assertEqual(
            [row.key for row in rows],
            [
                "trending_right_now",
                "all_time_greats_unseen",
                "coming_soon",
                "top_picks_for_you",
                "clear_out_next",
                "comfort_rewatches",
            ],
        )
        self.assertEqual(row_map["top_picks_for_you"].items, [])
        self.assertEqual(row_map["top_picks_for_you"].source_state, "error")
        self.assertEqual(row_map["clear_out_next"].items, [])
        self.assertEqual(row_map["clear_out_next"].source_state, "live")
        self.assertEqual(row_map["comfort_rewatches"].items, [])
        self.assertEqual(row_map["comfort_rewatches"].source_state, "error")

    @patch(
        "app.discover.service.cache_repo.set_row_cache",
        side_effect=OperationalError("database is locked"),
    )
    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service._comfort_candidates")
    @patch("app.discover.service._top_picks_candidates")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_anticipated")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_popular")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_watched_weekly")
    def test_tv_rows_render_live_items_when_cache_write_is_locked(
        self,
        mock_trending,
        mock_popular,
        mock_anticipated,
        mock_top_picks,
        mock_comfort,
        _mock_profile,
        _mock_set_row_cache,
    ):
        def candidate(media_id: str, title: str) -> CandidateItem:
            return CandidateItem(
                media_type=MediaTypes.TV.value,
                source=Sources.TMDB.value,
                media_id=media_id,
                title=title,
                image=f"https://example.com/{media_id}.jpg",
                final_score=0.9,
            )

        mock_trending.return_value = [
            candidate("100", "Trending 1"),
            candidate("101", "Trending 2"),
            candidate("102", "Trending 3"),
        ]
        mock_popular.return_value = [
            candidate("200", "Canon 1"),
            candidate("201", "Canon 2"),
            candidate("202", "Canon 3"),
        ]
        mock_anticipated.return_value = [
            candidate("300", "Soon 1"),
            candidate("301", "Soon 2"),
            candidate("302", "Soon 3"),
        ]
        mock_top_picks.return_value = [
            candidate("400", "Pick 1"),
            candidate("401", "Pick 2"),
            candidate("402", "Pick 3"),
        ]
        mock_comfort.return_value = [
            candidate("500", "Comfort 1"),
            candidate("501", "Comfort 2"),
            candidate("502", "Comfort 3"),
        ]

        rows = get_discover_rows(self.user, MediaTypes.TV.value, show_more=False)
        row_map = {row.key: row for row in rows}

        self.assertEqual(row_map["top_picks_for_you"].source_state, "live")
        self.assertEqual(row_map["comfort_rewatches"].source_state, "live")
        self.assertEqual(
            [item.title for item in row_map["top_picks_for_you"].items],
            ["Pick 1", "Pick 2", "Pick 3"],
        )
        self.assertEqual(
            [item.title for item in row_map["comfort_rewatches"].items],
            ["Comfort 1", "Comfort 2", "Comfort 3"],
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_anticipated")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_popular")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_watched_weekly")
    def test_tv_top_picks_uses_planning_entries_without_activity_field_errors(
        self,
        mock_trending,
        mock_popular,
        mock_anticipated,
        _mock_profile,
    ):
        def provider_candidate(media_id: str, title: str) -> CandidateItem:
            return CandidateItem(
                media_type=MediaTypes.TV.value,
                source="tmdb",
                media_id=media_id,
                title=title,
                image=f"https://example.com/provider-{media_id}.jpg",
                final_score=0.9,
            )

        mock_trending.return_value = [
            provider_candidate("100", "Trending 1"),
            provider_candidate("101", "Trending 2"),
            provider_candidate("102", "Trending 3"),
        ]
        mock_popular.return_value = [
            provider_candidate("200", "Canon 1"),
            provider_candidate("201", "Canon 2"),
            provider_candidate("202", "Canon 3"),
        ]
        mock_anticipated.return_value = [
            provider_candidate("300", "Soon 1"),
            provider_candidate("301", "Soon 2"),
            provider_candidate("302", "Soon 3"),
        ]

        for media_id, title in [("9001", "Planned TV One"), ("9002", "Planned TV Two")]:
            item = Item.objects.create(
                media_id=media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
                title=title,
                image=f"https://example.com/{media_id}.jpg",
            )
            TV.objects.create(
                user=self.user,
                item=item,
                status=Status.PLANNING.value,
            )

        rows = get_discover_rows(self.user, MediaTypes.TV.value, show_more=False)
        top_picks_row = next(row for row in rows if row.key == "top_picks_for_you")
        planning_media_ids = list(
            TV.objects.filter(
                user=self.user,
                status=Status.PLANNING.value,
            ).values_list("item__media_id", flat=True),
        )

        self.assertEqual(top_picks_row.source_state, "live")
        self.assertGreaterEqual(
            len(top_picks_row.items),
            1,
            msg=(
                "TV top picks unexpectedly empty. "
                f"planning_media_ids={planning_media_ids}; "
                f"row_snapshot={self._row_snapshot(rows)}"
            ),
        )
        self.assertTrue(
            any(item.media_id in {"9001", "9002"} for item in top_picks_row.items),
            msg=(
                "TV top picks did not include planning entries. "
                f"planning_media_ids={planning_media_ids}; "
                f"rendered={[f'{item.media_id}:{item.title}' for item in top_picks_row.items]}"
            ),
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_anticipated")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_popular")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_watched_weekly")
    def test_tv_top_picks_rebuilds_when_cached_schema_is_old(
        self,
        mock_trending,
        mock_popular,
        mock_anticipated,
        _mock_profile,
    ):
        stale_payload = {
            "key": "top_picks_for_you",
            "title": "Top Picks For You",
            "mission": "Personal Taste Match",
            "why": "New-to-you shows tailored to your taste.",
            "source": "local",
            "items": [
                CandidateItem(
                    media_type=MediaTypes.TV.value,
                    source=Sources.TMDB.value,
                    media_id="old-tv-pick",
                    title="Old Cached TV Pick",
                    image="https://example.com/old-tv-pick.jpg",
                ).to_dict(),
            ],
            "is_stale": False,
            "show_more": False,
            "source_state": "cache",
        }
        cache_repo.set_row_cache(
            self.user.id,
            MediaTypes.TV.value,
            "top_picks_for_you",
            stale_payload,
            ttl_seconds=3600,
        )

        def provider_candidate(media_id: str, title: str) -> CandidateItem:
            return CandidateItem(
                media_type=MediaTypes.TV.value,
                source=Sources.TMDB.value,
                media_id=media_id,
                title=title,
                image=f"https://example.com/provider-{media_id}.jpg",
            )

        mock_trending.return_value = [
            provider_candidate("100", "Trending 1"),
            provider_candidate("101", "Trending 2"),
            provider_candidate("102", "Trending 3"),
        ]
        mock_popular.return_value = [
            provider_candidate("200", "Canon 1"),
            provider_candidate("201", "Canon 2"),
            provider_candidate("202", "Canon 3"),
        ]
        mock_anticipated.return_value = [
            provider_candidate("300", "Soon 1"),
            provider_candidate("301", "Soon 2"),
            provider_candidate("302", "Soon 3"),
        ]

        planning_item = Item.objects.create(
            media_id="tv-9001",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Fresh Planned TV Pick",
            image="https://example.com/tv-9001.jpg",
        )
        TV.objects.create(
            user=self.user,
            item=planning_item,
            status=Status.PLANNING.value,
        )

        rows = get_discover_rows(self.user, MediaTypes.TV.value, show_more=False)
        top_picks_row = next(row for row in rows if row.key == "top_picks_for_you")
        rendered_ids = [item.media_id for item in top_picks_row.items]

        self.assertIn(
            "tv-9001",
            rendered_ids,
            msg=(
                "TV top picks cache should rebuild when schema requirements change. "
                f"rendered={self._row_snapshot(rows)}"
            ),
        )
        self.assertNotIn("old-tv-pick", rendered_ids)

    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_anticipated")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_popular")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_watched_weekly")
    def test_tv_personalized_rows_return_local_results(
        self,
        mock_trending,
        mock_popular,
        mock_anticipated,
    ):
        def provider_candidate(media_id: str, title: str) -> CandidateItem:
            return CandidateItem(
                media_type=MediaTypes.TV.value,
                source=Sources.TMDB.value,
                media_id=media_id,
                title=title,
                image=f"https://example.com/{media_id}.jpg",
            )

        mock_trending.return_value = [
            provider_candidate("100", "Trending 1"),
            provider_candidate("101", "Trending 2"),
            provider_candidate("102", "Trending 3"),
        ]
        mock_popular.return_value = [
            provider_candidate("200", "Canon 1"),
            provider_candidate("201", "Canon 2"),
            provider_candidate("202", "Canon 3"),
        ]
        mock_anticipated.return_value = [
            provider_candidate("300", "Soon 1"),
            provider_candidate("301", "Soon 2"),
            provider_candidate("302", "Soon 3"),
        ]

        director = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="tv-row-director",
            name="Pete Docter",
        )
        lead = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="tv-row-lead",
            name="Amy Poehler",
        )
        studio = Studio.objects.create(
            source=Sources.TMDB.value,
            source_studio_id="tv-row-studio",
            name="Pixar Animation Studios",
        )

        def build_item(media_id: str, title: str) -> Item:
            item = Item.objects.create(
                media_id=media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
                title=title,
                image=f"https://example.com/{media_id}.jpg",
                genres=["Animation", "Family"],
                provider_keywords=["Holiday", "Whodunit"],
                provider_certification="PG",
                provider_collection_id="tv-row-collection",
                provider_collection_name="Mystery Collection",
                runtime_minutes=52,
                release_datetime=timezone.now() - timedelta(days=365 * 2),
                studios=["Pixar Animation Studios"],
                provider_rating=8.6,
                provider_rating_count=4800,
            )
            ItemPersonCredit.objects.create(
                item=item,
                person=director,
                role_type=CreditRoleType.CREW.value,
                role="Director",
                department="Directing",
            )
            ItemPersonCredit.objects.create(
                item=item,
                person=lead,
                role_type=CreditRoleType.CAST.value,
                role="Lead",
                sort_order=0,
            )
            ItemStudioCredit.objects.create(item=item, studio=studio)
            return item

        planning_item = build_item("tv-plan-1", "Planned Cozy Mystery")
        in_progress_item = build_item("tv-progress-1", "In Progress Cozy Mystery")
        caught_up_item = build_item("tv-caught-up-1", "Caught Up Mystery Show")
        comfort_item = build_item("tv-comfort-1", "Comfort Mystery Show")
        recent_item = build_item("tv-recent-1", "Recent Mystery Show")

        with patch(
            "app.models.providers.services.get_media_metadata",
            return_value={"max_progress": 1},
        ):
            TV.objects.create(
                user=self.user,
                item=planning_item,
                status=Status.PLANNING.value,
            )
            TV.objects.create(
                user=self.user,
                item=in_progress_item,
                status=Status.IN_PROGRESS.value,
            )
            caught_up_entry = TV.objects.create(
                user=self.user,
                item=caught_up_item,
                status=Status.IN_PROGRESS.value,
            )
            comfort_entry = TV.objects.create(
                user=self.user,
                item=comfort_item,
                score=10,
                status=Status.COMPLETED.value,
            )
            recent_entry = TV.objects.create(
                user=self.user,
                item=recent_item,
                score=9,
                status=Status.COMPLETED.value,
            )

        caught_up_season_item = Item.objects.create(
            media_id="tv-caught-up-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Caught Up Mystery Show",
            image="https://example.com/tv-caught-up-season.jpg",
            season_number=1,
        )
        caught_up_season = Season.objects.create(
            user=self.user,
            item=caught_up_season_item,
            related_tv=caught_up_entry,
            status=Status.IN_PROGRESS.value,
        )
        caught_up_episode_item = Item.objects.create(
            media_id="tv-caught-up-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Caught Up Mystery Show",
            image="https://example.com/tv-caught-up-episode.jpg",
            season_number=1,
            episode_number=1,
            release_datetime=timezone.now() - timedelta(days=7),
        )
        Episode.objects.bulk_create(
            [
                Episode(
                    item=caught_up_episode_item,
                    related_season=caught_up_season,
                    end_date=timezone.now() - timedelta(days=1),
                ),
            ],
        )
        Event.objects.create(
            item=caught_up_season_item,
            content_number=1,
            datetime=timezone.now() - timedelta(days=1),
        )

        TV.objects.filter(pk=comfort_entry.pk).update(
            created_at=timezone.now() - timedelta(days=220)
        )
        TV.objects.filter(pk=recent_entry.pk).update(
            created_at=timezone.now() - timedelta(days=20)
        )

        rows = get_discover_rows(self.user, MediaTypes.TV.value, show_more=False)
        row_map = {row.key: row for row in rows}

        self.assertGreaterEqual(
            len(row_map["top_picks_for_you"].items),
            1,
            msg=f"TV top picks blank: {self._row_snapshot(rows)}",
        )
        self.assertGreaterEqual(
            len(row_map["clear_out_next"].items),
            1,
            msg=f"TV clear-out-next blank: {self._row_snapshot(rows)}",
        )
        self.assertGreaterEqual(
            len(row_map["comfort_rewatches"].items),
            1,
            msg=f"TV comfort row blank: {self._row_snapshot(rows)}",
        )
        self.assertIn(
            "tv-plan-1",
            {item.media_id for item in row_map["top_picks_for_you"].items},
            msg=f"TV top picks missing planning item: {self._row_snapshot(rows)}",
        )
        self.assertIn(
            "tv-progress-1",
            {item.media_id for item in row_map["clear_out_next"].items},
            msg=f"TV clear-out-next missing in-progress item: {self._row_snapshot(rows)}",
        )
        self.assertNotIn(
            "tv-caught-up-1",
            {item.media_id for item in row_map["clear_out_next"].items},
            msg=f"TV clear-out-next should exclude caught-up shows: {self._row_snapshot(rows)}",
        )
        self.assertIn(
            "tv-comfort-1",
            {item.media_id for item in row_map["comfort_rewatches"].items},
            msg=f"TV comfort row missing comfort item: {self._row_snapshot(rows)}",
        )

    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_anticipated")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_popular")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_watched_weekly")
    def test_anime_personalized_rows_return_local_results(
        self,
        mock_trending,
        mock_popular,
        mock_anticipated,
    ):
        def provider_candidate(media_id: str, title: str) -> CandidateItem:
            return CandidateItem(
                media_type=MediaTypes.ANIME.value,
                source=Sources.TMDB.value,
                media_id=media_id,
                title=title,
                image=f"https://example.com/{media_id}.jpg",
            )

        mock_trending.return_value = [
            provider_candidate("100", "Trending 1"),
            provider_candidate("101", "Trending 2"),
            provider_candidate("102", "Trending 3"),
        ]
        mock_popular.return_value = [
            provider_candidate("200", "Canon 1"),
            provider_candidate("201", "Canon 2"),
            provider_candidate("202", "Canon 3"),
        ]
        mock_anticipated.return_value = [
            provider_candidate("300", "Soon 1"),
            provider_candidate("301", "Soon 2"),
            provider_candidate("302", "Soon 3"),
        ]

        director = Person.objects.create(
            source=Sources.MAL.value,
            source_person_id="anime-row-director",
            name="Hayao Miyazaki",
        )
        lead = Person.objects.create(
            source=Sources.MAL.value,
            source_person_id="anime-row-lead",
            name="Maaya Sakamoto",
        )
        studio = Studio.objects.create(
            source=Sources.MAL.value,
            source_studio_id="anime-row-studio",
            name="Studio Pierrot",
        )

        def build_item(media_id: str, title: str) -> Item:
            item = Item.objects.create(
                media_id=media_id,
                source=Sources.MAL.value,
                media_type=MediaTypes.ANIME.value,
                title=title,
                image=f"https://example.com/{media_id}.jpg",
                genres=["Animation", "Fantasy"],
                provider_keywords=["Found Family", "Holiday"],
                provider_certification="PG",
                provider_collection_id="anime-row-collection",
                provider_collection_name="Magic Collection",
                runtime_minutes=24,
                release_datetime=timezone.now() - timedelta(days=365 * 3),
                studios=["Studio Pierrot"],
                provider_rating=8.8,
                provider_rating_count=7200,
            )
            ItemPersonCredit.objects.create(
                item=item,
                person=director,
                role_type=CreditRoleType.CREW.value,
                role="Director",
                department="Directing",
            )
            ItemPersonCredit.objects.create(
                item=item,
                person=lead,
                role_type=CreditRoleType.CAST.value,
                role="Lead",
                sort_order=0,
            )
            ItemStudioCredit.objects.create(item=item, studio=studio)
            return item

        planning_item = build_item("anime-plan-1", "Planned Comfort Anime")
        in_progress_item = build_item("anime-progress-1", "In Progress Comfort Anime")
        caught_up_item = build_item("anime-caught-up-1", "Caught Up Comfort Anime")
        comfort_item = build_item("anime-comfort-1", "Comfort Rewatch Anime")
        recent_item = build_item("anime-recent-1", "Recent Comfort Anime")

        with patch(
            "app.models.providers.services.get_media_metadata",
            return_value={"max_progress": 10},
        ):
            Anime.objects.create(
                user=self.user,
                item=planning_item,
                status=Status.PLANNING.value,
            )
            Anime.objects.create(
                user=self.user,
                item=in_progress_item,
                status=Status.IN_PROGRESS.value,
            )
            Anime.objects.create(
                user=self.user,
                item=caught_up_item,
                status=Status.IN_PROGRESS.value,
                progress=1,
            )
            Anime.objects.create(
                user=self.user,
                item=comfort_item,
                score=10,
                status=Status.COMPLETED.value,
                end_date=timezone.now() - timedelta(days=150),
            )
            Anime.objects.create(
                user=self.user,
                item=recent_item,
                score=9,
                status=Status.COMPLETED.value,
                end_date=timezone.now() - timedelta(days=18),
            )

        Event.objects.create(
            item=caught_up_item,
            content_number=1,
            datetime=timezone.now() - timedelta(days=1),
        )

        rows = get_discover_rows(
            self.user,
            MediaTypes.ANIME.value,
            show_more=False,
            defer_artwork=True,
        )
        row_map = {row.key: row for row in rows}

        self.assertGreaterEqual(
            len(row_map["top_picks_for_you"].items),
            1,
            msg=f"Anime top picks blank: {self._row_snapshot(rows)}",
        )
        self.assertGreaterEqual(
            len(row_map["clear_out_next"].items),
            1,
            msg=f"Anime clear-out-next blank: {self._row_snapshot(rows)}",
        )
        self.assertGreaterEqual(
            len(row_map["comfort_rewatches"].items),
            1,
            msg=f"Anime comfort row blank: {self._row_snapshot(rows)}",
        )
        self.assertIn(
            "anime-plan-1",
            {item.media_id for item in row_map["top_picks_for_you"].items},
            msg=f"Anime top picks missing planning item: {self._row_snapshot(rows)}",
        )
        self.assertIn(
            "anime-progress-1",
            {item.media_id for item in row_map["clear_out_next"].items},
            msg=f"Anime clear-out-next missing in-progress item: {self._row_snapshot(rows)}",
        )
        self.assertNotIn(
            "anime-caught-up-1",
            {item.media_id for item in row_map["clear_out_next"].items},
            msg=f"Anime clear-out-next should exclude caught-up titles: {self._row_snapshot(rows)}",
        )
        self.assertIn(
            "anime-comfort-1",
            {item.media_id for item in row_map["comfort_rewatches"].items},
            msg=f"Anime comfort row missing comfort item: {self._row_snapshot(rows)}",
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service._comfort_candidates", return_value=[])
    @patch("app.discover.service._top_picks_candidates", return_value=[])
    @patch("app.discover.provider_candidates.services.get_media_metadata")
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.show_anticipated",
        return_value=[],
    )
    @patch(
        "app.discover.provider_candidates.TRAKT_ADAPTER.show_popular", return_value=[]
    )
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.show_watched_weekly")
    def test_tv_trending_row_hydrates_artwork_for_missing_images(
        self,
        mock_trending,
        _mock_popular,
        _mock_anticipated,
        mock_get_metadata,
        _mock_top_picks,
        _mock_comfort,
        _mock_profile,
    ):
        mock_trending.return_value = [
            CandidateItem(
                media_type=MediaTypes.TV.value,
                source="tmdb",
                media_id="701",
                title="Needs Art TV One",
                image=None,
                release_date="2017-10-02T00:00:00.000Z",
            ),
            CandidateItem(
                media_type=MediaTypes.TV.value,
                source="tmdb",
                media_id="702",
                title="Needs Art TV Two",
                image=None,
                release_date="2018-10-02",
            ),
        ]

        def metadata_side_effect(
            media_type, media_id, source, season_numbers=None, episode_number=None
        ):
            return {"image": f"https://image.tmdb.org/t/p/w500/{media_id}.jpg"}

        mock_get_metadata.side_effect = metadata_side_effect

        rows = get_discover_rows(self.user, MediaTypes.TV.value, show_more=False)
        trending_row = next(row for row in rows if row.key == "trending_right_now")

        self.assertEqual(
            [item.image for item in trending_row.items],
            [
                "https://image.tmdb.org/t/p/w500/701.jpg",
                "https://image.tmdb.org/t/p/w500/702.jpg",
            ],
        )

    def test_rewatch_counts_tv_normalizes_episode_volume(self):
        show_item = Item.objects.create(
            media_id="tv-900",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Episode Weighted Show",
            image="http://example.com/tv-900.jpg",
        )
        tv_entry = TV.objects.create(
            item=show_item,
            user=self.user,
            status=Status.COMPLETED.value,
        )

        season_item = Item.objects.create(
            media_id="tv-900",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Episode Weighted Show",
            image="http://example.com/tv-900-s1.jpg",
            season_number=1,
        )
        season_entry = Season.objects.create(
            item=season_item,
            user=self.user,
            status=Status.COMPLETED.value,
            related_tv=tv_entry,
        )

        episode_items = [
            Item.objects.create(
                media_id="tv-900",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Episode Weighted Show E{episode_number}",
                image=f"http://example.com/tv-900-e{episode_number}.jpg",
                season_number=1,
                episode_number=episode_number,
            )
            for episode_number in (1, 2, 3)
        ]
        now = timezone.now()
        Episode.objects.bulk_create(
            [
                Episode(
                    item=episode_items[0], related_season=season_entry, end_date=now
                ),
                Episode(
                    item=episode_items[1], related_season=season_entry, end_date=now
                ),
                Episode(
                    item=episode_items[2], related_season=season_entry, end_date=now
                ),
                Episode(
                    item=episode_items[0], related_season=season_entry, end_date=now
                ),
                Episode(
                    item=episode_items[1], related_season=season_entry, end_date=now
                ),
            ],
        )

        counts = _rewatch_counts(
            self.user,
            TV,
            [show_item.id],
            media_type=MediaTypes.TV.value,
        )
        self.assertIn(show_item.id, counts)
        self.assertAlmostEqual(counts[show_item.id], 5.0 / 3.0, places=4)

    def test_trakt_release_date_normalization_for_show_candidates(self):
        self.assertEqual(
            TraktDiscoverAdapter._normalized_release_date("2017-10-02T00:00:00.000Z"),
            "2017-10-02",
        )
        self.assertEqual(
            TraktDiscoverAdapter._normalized_release_date("2018-11-05"),
            "2018-11-05",
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service._comfort_candidates", return_value=[])
    @patch("app.discover.service._top_picks_candidates")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly")
    def test_top_picks_row_backfills_after_dedupe_using_buffered_candidates(
        self,
        mock_trending,
        mock_popular,
        mock_anticipated,
        mock_top_picks,
        _mock_comfort,
        _mock_profile,
    ):
        def candidate(media_id: str, title: str) -> CandidateItem:
            return CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id=media_id,
                title=title,
                image=f"https://example.com/{media_id}.jpg",
                final_score=0.8,
            )

        mock_trending.return_value = [
            candidate("100", "Trending 1"),
            candidate("101", "Trending 2"),
            candidate("102", "Trending 3"),
        ]
        mock_popular.return_value = [
            candidate("200", "Canon 1"),
            candidate("201", "Canon 2"),
            candidate("202", "Canon 3"),
        ]
        mock_anticipated.return_value = [
            candidate("300", "Soon 1"),
            candidate("301", "Soon 2"),
            candidate("302", "Soon 3"),
        ]

        duplicate_ids = [
            "100",
            "101",
            "102",
            "200",
            "201",
            "202",
            "300",
            "301",
            "302",
            "100",
            "200",
            "300",
        ]
        top_pick_candidates = [
            candidate(media_id, f"Pick duplicate {index}")
            for index, media_id in enumerate(duplicate_ids, start=1)
        ]
        top_pick_candidates.extend(
            candidate(str(600 + index), f"Pick fresh {index}") for index in range(12)
        )
        mock_top_picks.return_value = top_pick_candidates

        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)
        top_picks_row = next(row for row in rows if row.key == "top_picks_for_you")

        self.assertEqual(len(top_picks_row.items), 12)
        self.assertTrue(
            all(item.media_id.startswith("6") for item in top_picks_row.items)
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service._comfort_candidates", return_value=[])
    @patch("app.discover.service._top_picks_candidates", return_value=[])
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly")
    def test_movie_rows_hide_rows_four_to_six_when_no_personalized_data(
        self,
        mock_trending,
        mock_popular,
        mock_anticipated,
        _mock_top_picks,
        _mock_comfort,
        _mock_profile,
    ):
        def candidate(media_id: str, title: str) -> CandidateItem:
            return CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id=media_id,
                title=title,
                image=f"https://example.com/{media_id}.jpg",
            )

        mock_trending.return_value = [
            candidate("100", "Trending 1"),
            candidate("101", "Trending 2"),
            candidate("102", "Trending 3"),
        ]
        mock_popular.return_value = [
            candidate("200", "Canon 1"),
            candidate("201", "Canon 2"),
            candidate("202", "Canon 3"),
        ]
        mock_anticipated.return_value = [
            candidate("300", "Soon 1"),
            candidate("301", "Soon 2"),
            candidate("302", "Soon 3"),
        ]

        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)
        self.assertEqual(
            [row.key for row in rows],
            ["trending_right_now", "all_time_greats_unseen", "coming_soon"],
        )

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service._comfort_candidates", return_value=[])
    @patch("app.discover.service._top_picks_candidates")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly")
    def test_top_picks_row_sets_debug_payload_when_enabled(
        self,
        mock_trending,
        mock_popular,
        mock_anticipated,
        mock_top_picks,
        _mock_comfort,
        _mock_profile,
    ):
        def candidate(media_id: str, title: str) -> CandidateItem:
            return CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id=media_id,
                title=title,
                image=f"https://example.com/{media_id}.jpg",
                final_score=0.8,
                score_breakdown={
                    "phase_fit": 0.7,
                    "hot_recency": 0.1,
                    "phase_family_contribution": 0.3,
                    "hot_recency_contribution": 0.02,
                    "rating_contribution": 0.04,
                    "rewatch_contribution": 0.0,
                },
            )

        mock_trending.return_value = [
            candidate("100", "Trending 1"),
            candidate("101", "Trending 2"),
            candidate("102", "Trending 3"),
        ]
        mock_popular.return_value = [
            candidate("200", "Canon 1"),
            candidate("201", "Canon 2"),
            candidate("202", "Canon 3"),
        ]
        mock_anticipated.return_value = [
            candidate("300", "Soon 1"),
            candidate("301", "Soon 2"),
            candidate("302", "Soon 3"),
        ]
        mock_top_picks.return_value = [
            candidate("400", "Pick 1"),
            candidate("401", "Pick 2"),
            candidate("402", "Pick 3"),
        ]

        rows = get_discover_rows(
            self.user,
            MediaTypes.MOVIE.value,
            show_more=False,
            include_debug=True,
        )
        top_picks_row = next(row for row in rows if row.key == "top_picks_for_you")

        self.assertIsNotNone(top_picks_row.debug_payload)
        self.assertIn("top_candidates", top_picks_row.debug_payload)
        self.assertIn("tag_signal", top_picks_row.debug_payload)
        self.assertGreaterEqual(len(top_picks_row.debug_payload["top_candidates"]), 1)

    @patch("app.discover.service.get_or_compute_taste_profile", return_value={})
    @patch("app.discover.service._comfort_candidates")
    @patch("app.discover.service._top_picks_candidates")
    @patch("app.discover.service.get_tracked_keys_by_media_type")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_anticipated")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_popular")
    @patch("app.discover.provider_candidates.TRAKT_ADAPTER.movie_watched_weekly")
    def test_comfort_rewatches_allows_tracked_items(
        self,
        mock_trending,
        mock_popular,
        mock_anticipated,
        mock_tracked_keys,
        mock_top_picks,
        mock_comfort,
        _mock_profile,
    ):
        def candidate(media_id: str, title: str) -> CandidateItem:
            return CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id=media_id,
                title=title,
                image=f"https://example.com/{media_id}.jpg",
                final_score=0.8,
            )

        mock_trending.return_value = [
            candidate("100", "Trending 1"),
            candidate("101", "Trending 2"),
            candidate("102", "Trending 3"),
        ]
        mock_popular.return_value = [
            candidate("200", "Canon 1"),
            candidate("201", "Canon 2"),
            candidate("202", "Canon 3"),
        ]
        mock_anticipated.return_value = [
            candidate("300", "Soon 1"),
            candidate("301", "Soon 2"),
            candidate("302", "Soon 3"),
        ]
        mock_top_picks.return_value = [
            candidate("400", "Pick 1"),
            candidate("401", "Pick 2"),
            candidate("402", "Pick 3"),
        ]
        mock_comfort.return_value = [
            candidate("900", "Comfort blocked"),
            candidate("901", "Comfort 2"),
            candidate("902", "Comfort 3"),
        ]
        mock_tracked_keys.return_value = {
            (MediaTypes.MOVIE.value, "tmdb", "900"),
        }

        rows = get_discover_rows(self.user, MediaTypes.MOVIE.value, show_more=False)
        comfort_row = next(row for row in rows if row.key == "comfort_rewatches")
        self.assertIn("900", {item.media_id for item in comfort_row.items})

    def test_wildcard_novelty_rerank_boosts_lower_exposure_candidate(self):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="1",
                title="High Exposure",
                genres=["Action"],
                final_score=0.8,
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="2",
                title="Low Exposure",
                genres=["Mystery"],
                final_score=0.78,
            ),
        ]

        reranked = _apply_wildcard_novelty(
            candidates,
            {"recent_genre_affinity": {"action": 1.0, "mystery": 0.0}},
        )

        self.assertEqual(reranked[0].media_id, "2")
        self.assertGreater(reranked[0].final_score, reranked[1].final_score)
        self.assertIsNotNone(reranked[0].display_score)
        self.assertAlmostEqual(
            reranked[0].display_score,
            round(max(0.0, min(1.0, 0.45 + (reranked[0].final_score * 0.39))), 6),
        )

    @patch("app.discover.service.TMDB_ADAPTER.genre_discovery")
    @patch("app.discover.service.TMDB_ADAPTER.related")
    @patch("app.discover.service._planning_candidates")
    def test_top_picks_candidates_use_local_planning_pool(
        self,
        mock_planning,
        mock_related,
        mock_genre_discovery,
    ):
        mock_planning.return_value = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="1001",
                title="Plan One",
                genres=["Action"],
                popularity=80.0,
                rating=8.0,
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="1002",
                title="Plan Two",
                genres=["Drama"],
                popularity=70.0,
                rating=7.0,
            ),
        ]
        mock_related.return_value = []
        mock_genre_discovery.return_value = []

        profile_payload = {
            "genre_affinity": {"action": 1.0, "drama": 0.5},
            "recent_genre_affinity": {"action": 1.0},
        }
        candidates = _top_picks_candidates(
            self.user,
            MediaTypes.MOVIE.value,
            "top_picks_for_you",
            profile_payload,
        )

        self.assertEqual([item.media_id for item in candidates], ["1001", "1002"])
        self.assertTrue(all(item.display_score is not None for item in candidates))
        for candidate in candidates:
            self.assertGreaterEqual(candidate.display_score, 0.0)
            self.assertLessEqual(candidate.display_score, 1.0)
            self.assertIn("rewatch_bonus", candidate.score_breakdown)
            self.assertIn("inactivity_norm", candidate.score_breakdown)
            self.assertIn("tag_signal_mode", candidate.score_breakdown)
            self.assertEqual(candidate.score_breakdown["pool_source"], "planning")
        # No watch history means no anchors, so related is never fetched;
        # taste discovery runs once on the top profile genres.
        mock_related.assert_not_called()
        mock_genre_discovery.assert_called_once()

    @patch("app.discover.service.TMDB_ADAPTER.genre_discovery")
    @patch("app.discover.service.TMDB_ADAPTER.related")
    @patch("app.discover.service._planning_candidates")
    def test_movie_top_picks_excludes_future_release_candidates(
        self,
        mock_planning,
        mock_related,
        mock_genre_discovery,
    ):
        today = timezone.localdate()
        mock_planning.return_value = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="released-pick",
                title="Released Pick",
                genres=["Comedy"],
                release_date=(today - timedelta(days=14)).isoformat(),
                popularity=80.0,
                rating=8.0,
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="future-pick",
                title="Future Pick",
                genres=["Comedy"],
                release_date=(today + timedelta(days=14)).isoformat(),
                popularity=85.0,
                rating=8.0,
            ),
        ]
        mock_related.return_value = []
        mock_genre_discovery.return_value = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="future-provider-pick",
                title="Future Provider Pick",
                genres=["Comedy"],
                release_date=(today + timedelta(days=30)).isoformat(),
                popularity=90.0,
                rating=8.5,
            ),
        ]

        profile_payload = {
            "genre_affinity": {"comedy": 1.0},
            "recent_genre_affinity": {"comedy": 1.0},
        }
        candidates = _top_picks_candidates(
            self.user,
            MediaTypes.MOVIE.value,
            "top_picks_for_you",
            profile_payload,
        )

        # Upcoming titles are dropped from planning AND provider sources.
        self.assertEqual([item.media_id for item in candidates], ["released-pick"])
        mock_genre_discovery.assert_called_once()

    def test_movie_top_picks_use_planning_age_not_watch_history_defaults(self):
        item = Item.objects.create(
            media_id="movie-plan-debug",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Planning Debug Movie",
            image="https://example.com/movie-plan-debug.jpg",
            genres=["Adventure", "Comedy"],
            provider_keywords=["Family", "Adventure"],
            provider_certification="PG",
            provider_collection_name="Weekend Picks",
            runtime_minutes=101,
            release_datetime=timezone.now() - timedelta(days=365 * 2),
            studios=["Universal Pictures"],
            provider_popularity=80.0,
            provider_rating=7.9,
            provider_rating_count=5000,
        )
        planning_entry = Movie.objects.create(
            user=self.user,
            item=item,
            status=Status.PLANNING.value,
        )
        Movie.objects.filter(pk=planning_entry.pk).update(
            created_at=timezone.now() - timedelta(days=49),
        )

        profile_payload = {
            "phase_keyword_affinity": {"family": 1.0, "adventure": 0.9},
            "recent_keyword_affinity": {"family": 1.0, "adventure": 0.9},
            "phase_studio_affinity": {"universal pictures": 1.0},
            "recent_studio_affinity": {"universal pictures": 0.9},
            "phase_collection_affinity": {"weekend picks": 1.0},
            "recent_collection_affinity": {"weekend picks": 0.9},
            "phase_certification_affinity": {"PG": 1.0},
            "recent_certification_affinity": {"PG": 1.0},
            "phase_runtime_bucket_affinity": {"90_109": 1.0},
            "recent_runtime_bucket_affinity": {"90_109": 1.0},
            "phase_decade_affinity": {"2020s": 0.8},
            "recent_decade_affinity": {"2020s": 0.8},
            "phase_genre_affinity": {"adventure": 1.0, "comedy": 0.9},
            "recent_genre_affinity": {"adventure": 1.0, "comedy": 0.9},
            "comfort_library_affinity": {
                "keywords": {"family": 1.0, "adventure": 0.9},
                "collections": {"weekend picks": 1.0},
                "studios": {"universal pictures": 1.0},
                "genres": {"adventure": 1.0, "comedy": 0.9},
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"90_109": 1.0},
                "decades": {"2020s": 0.8},
            },
            "comfort_rewatch_affinity": {
                "keywords": {"family": 1.0, "adventure": 0.9},
                "collections": {"weekend picks": 1.0},
                "studios": {"universal pictures": 1.0},
                "genres": {"adventure": 1.0, "comedy": 0.9},
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"90_109": 1.0},
                "decades": {"2020s": 0.8},
            },
        }

        candidates = _top_picks_candidates(
            self.user,
            MediaTypes.MOVIE.value,
            "top_picks_for_you",
            profile_payload,
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertIn("days_since_planned", candidate.score_breakdown)
        self.assertNotIn("days_since_activity", candidate.score_breakdown)
        self.assertEqual(candidate.score_breakdown["inactivity_norm"], 0.0)
        self.assertEqual(candidate.score_breakdown["release_status"], "released")
        self.assertEqual(candidate.score_breakdown["title_history_present"], 0.0)

    def test_movie_top_picks_debug_template_uses_planning_labels(self):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="top-picks-debug",
                title="Top Picks Debug",
                genres=["Adventure", "Comedy"],
                keywords=["family", "adventure"],
                studios=["universal pictures"],
                collection_name="Weekend Picks",
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                release_date=(timezone.localdate() - timedelta(days=30)).isoformat(),
                popularity=80.0,
                rating_count=5000,
                row_key="top_picks_for_you",
                score_breakdown={
                    "planning_entry": 1.0,
                    "days_since_planned": 49.0,
                    "rewatch_count": 1.0,
                },
            ),
        ]
        reranked = _apply_comfort_confidence(
            candidates,
            {
                "phase_keyword_affinity": {"family": 1.0, "adventure": 0.9},
                "recent_keyword_affinity": {"family": 1.0, "adventure": 0.9},
                "phase_studio_affinity": {"universal pictures": 1.0},
                "recent_studio_affinity": {"universal pictures": 0.9},
                "phase_collection_affinity": {"weekend picks": 1.0},
                "recent_collection_affinity": {"weekend picks": 0.9},
                "phase_certification_affinity": {"PG": 1.0},
                "recent_certification_affinity": {"PG": 1.0},
                "phase_runtime_bucket_affinity": {"90_109": 1.0},
                "recent_runtime_bucket_affinity": {"90_109": 1.0},
                "phase_decade_affinity": {"2020s": 0.8},
                "recent_decade_affinity": {"2020s": 0.8},
                "phase_genre_affinity": {"adventure": 1.0, "comedy": 0.9},
                "recent_genre_affinity": {"adventure": 1.0, "comedy": 0.9},
                "comfort_library_affinity": {
                    "keywords": {"family": 1.0, "adventure": 0.9},
                    "collections": {"weekend picks": 1.0},
                    "studios": {"universal pictures": 1.0},
                    "genres": {"adventure": 1.0, "comedy": 0.9},
                    "directors": {},
                    "lead_cast": {},
                    "certifications": {"PG": 1.0},
                    "runtime_buckets": {"90_109": 1.0},
                    "decades": {"2020s": 0.8},
                },
                "comfort_rewatch_affinity": {
                    "keywords": {"family": 1.0, "adventure": 0.9},
                    "collections": {"weekend picks": 1.0},
                    "studios": {"universal pictures": 1.0},
                    "genres": {"adventure": 1.0, "comedy": 0.9},
                    "directors": {},
                    "lead_cast": {},
                    "certifications": {"PG": 1.0},
                    "runtime_buckets": {"90_109": 1.0},
                    "decades": {"2020s": 0.8},
                },
            },
            use_movie_rewatch_model=True,
        )
        row = RowResult(
            key="top_picks_for_you",
            title="Top Picks For You",
            mission="Personal Taste Match",
            why="New-to-you movies tailored to your taste.",
            source="local",
            items=[],
            debug_payload=_build_comfort_debug_payload(reranked, top_n=1),
        )

        content = render_to_string(
            "app/components/discover_row.html",
            {
                "row": row,
                "discover_debug": True,
                "show_more": False,
                "selected_media_type": MediaTypes.MOVIE.value,
                "user": self.user,
            },
        )

        self.assertIn("release-status released", content)
        self.assertIn("planned-days 49.0", content)
        self.assertIn("title-history no", content)
        self.assertIn("planning-conf 0.000", content)
        self.assertIn("matched-history [none]", content)
        self.assertNotIn("last-watch-days", content)

    def test_movie_top_picks_planning_confidence_prefers_real_history_neighbors(self):
        for index in range(3):
            history_item = Item.objects.create(
                media_id=f"history-rich-{index}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"History Rich {index}",
                image=f"https://example.com/history-rich-{index}.jpg",
                genres=["Adventure", "Comedy"],
                provider_keywords=["Family", "Adventure"],
                provider_certification="PG",
                provider_collection_name="Weekend Picks",
                runtime_minutes=101,
                release_datetime=timezone.now() - timedelta(days=365 * 2),
                studios=["Universal Pictures"],
                provider_popularity=75.0,
                provider_rating=7.8,
                provider_rating_count=4000,
            )
            Movie.objects.create(
                user=self.user,
                item=history_item,
                status=Status.COMPLETED.value,
                end_date=timezone.now() - timedelta(days=150 - (index * 30)),
            )

        profile_payload = {
            "phase_keyword_affinity": {
                "family": 1.0,
                "adventure": 0.9,
                "detective": 1.0,
                "mystery": 0.9,
            },
            "recent_keyword_affinity": {
                "family": 1.0,
                "adventure": 0.9,
                "detective": 1.0,
                "mystery": 0.9,
            },
            "phase_collection_affinity": {
                "weekend picks": 1.0,
                "mystery nights": 1.0,
            },
            "recent_collection_affinity": {
                "weekend picks": 1.0,
                "mystery nights": 1.0,
            },
            "phase_studio_affinity": {
                "universal pictures": 1.0,
                "focus features": 1.0,
            },
            "recent_studio_affinity": {
                "universal pictures": 1.0,
                "focus features": 1.0,
            },
            "phase_certification_affinity": {"PG": 1.0},
            "recent_certification_affinity": {"PG": 1.0},
            "phase_runtime_bucket_affinity": {"90_109": 1.0},
            "recent_runtime_bucket_affinity": {"90_109": 1.0},
            "phase_decade_affinity": {"2020s": 1.0},
            "recent_decade_affinity": {"2020s": 1.0},
            "phase_genre_affinity": {
                "adventure": 1.0,
                "comedy": 0.9,
                "mystery": 1.0,
                "drama": 0.9,
            },
            "recent_genre_affinity": {
                "adventure": 1.0,
                "comedy": 0.9,
                "mystery": 1.0,
                "drama": 0.9,
            },
            "comfort_library_affinity": {
                "keywords": {
                    "family": 1.0,
                    "adventure": 0.9,
                    "detective": 1.0,
                    "mystery": 0.9,
                },
                "collections": {
                    "weekend picks": 1.0,
                    "mystery nights": 1.0,
                },
                "studios": {
                    "universal pictures": 1.0,
                    "focus features": 1.0,
                },
                "genres": {
                    "adventure": 1.0,
                    "comedy": 0.9,
                    "mystery": 1.0,
                    "drama": 0.9,
                },
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"90_109": 1.0},
                "decades": {"2020s": 1.0},
            },
            "comfort_rewatch_affinity": {
                "keywords": {
                    "family": 1.0,
                    "adventure": 0.9,
                    "detective": 1.0,
                    "mystery": 0.9,
                },
                "collections": {
                    "weekend picks": 1.0,
                    "mystery nights": 1.0,
                },
                "studios": {
                    "universal pictures": 1.0,
                    "focus features": 1.0,
                },
                "genres": {
                    "adventure": 1.0,
                    "comedy": 0.9,
                    "mystery": 1.0,
                    "drama": 0.9,
                },
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"90_109": 1.0},
                "decades": {"2020s": 1.0},
            },
        }
        candidate_common = {
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "certification": "PG",
            "runtime_bucket": "90_109",
            "release_decade": "2020s",
            "release_date": (timezone.localdate() - timedelta(days=60)).isoformat(),
            "popularity": 80.0,
            "rating": 7.8,
            "rating_count": 5000,
            "row_key": "top_picks_for_you",
            "score_breakdown": {
                "planning_entry": 1.0,
                "days_since_planned": 81.0,
                "rewatch_count": 1.0,
            },
        }

        def build_candidate(kind: str) -> CandidateItem:
            if kind == "rich":
                return CandidateItem(
                    media_id="planned-rich",
                    title="Planned Rich",
                    image="https://example.com/planned-rich.jpg",
                    keywords=["family", "adventure"],
                    collection_name="Weekend Picks",
                    studios=["Universal Pictures"],
                    genres=["Adventure", "Comedy"],
                    score_breakdown=dict(candidate_common["score_breakdown"]),
                    **{
                        key: value
                        for key, value in candidate_common.items()
                        if key != "score_breakdown"
                    },
                )
            return CandidateItem(
                media_id="planned-sparse",
                title="Planned Sparse",
                image="https://example.com/planned-sparse.jpg",
                keywords=["detective", "mystery"],
                collection_name="Mystery Nights",
                studios=["Focus Features"],
                genres=["Mystery", "Drama"],
                score_breakdown=dict(candidate_common["score_breakdown"]),
                **{
                    key: value
                    for key, value in candidate_common.items()
                    if key != "score_breakdown"
                },
            )

        without_history = _apply_comfort_confidence(
            [build_candidate("sparse"), build_candidate("rich")],
            profile_payload,
            use_movie_rewatch_model=True,
            user=None,
        )
        without_history_scores = {
            candidate.media_id: candidate.score_breakdown["planning_confidence"]
            for candidate in without_history
        }
        self.assertEqual(without_history_scores["planned-sparse"], 0.0)
        self.assertEqual(without_history_scores["planned-rich"], 0.0)

        reranked = _apply_comfort_confidence(
            [build_candidate("sparse"), build_candidate("rich")],
            profile_payload,
            use_movie_rewatch_model=True,
            user=self.user,
        )

        self.assertEqual(reranked[0].media_id, "planned-rich")
        self.assertGreater(
            reranked[0].score_breakdown["planning_confidence"],
            reranked[1].score_breakdown["planning_confidence"],
        )
        self.assertGreater(reranked[0].score_breakdown["similar_watched_count"], 0)
        self.assertGreater(
            reranked[0].score_breakdown["rich_history_family_count"],
            reranked[1].score_breakdown["rich_history_family_count"],
        )
        self.assertIn(
            "keywords",
            reranked[0].score_breakdown["matched_history_families"],
        )
        self.assertNotIn(
            "keywords",
            reranked[1].score_breakdown["matched_history_families"],
        )
        payload = _build_comfort_debug_payload(reranked, top_n=2)
        self.assertGreater(payload["contribution_totals"]["planning_confidence"], 0.0)
        self.assertIn("planning_confidence", payload["top_candidates"][0])

    def test_movie_top_picks_world_quality_debug_shows_before_and_after_titles(self):
        profile_payload = {
            "phase_keyword_affinity": {"cozy": 1.0, "mystery": 0.9},
            "recent_keyword_affinity": {"cozy": 1.0, "mystery": 0.9},
            "phase_studio_affinity": {"studio home": 1.0},
            "recent_studio_affinity": {"studio home": 1.0},
            "phase_collection_affinity": {"weekend picks": 1.0},
            "recent_collection_affinity": {"weekend picks": 1.0},
            "phase_certification_affinity": {"PG": 1.0},
            "recent_certification_affinity": {"PG": 1.0},
            "phase_runtime_bucket_affinity": {"90_109": 1.0},
            "recent_runtime_bucket_affinity": {"90_109": 1.0},
            "phase_decade_affinity": {"2020s": 1.0},
            "recent_decade_affinity": {"2020s": 1.0},
            "phase_genre_affinity": {"adventure": 1.0, "family": 0.9},
            "recent_genre_affinity": {"adventure": 1.0, "family": 0.9},
            "comfort_library_affinity": {
                "keywords": {"cozy": 1.0, "mystery": 0.9},
                "collections": {"weekend picks": 1.0},
                "studios": {"studio home": 1.0},
                "genres": {"adventure": 1.0, "family": 0.9},
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"90_109": 1.0},
                "decades": {"2020s": 1.0},
            },
            "comfort_rewatch_affinity": {
                "keywords": {"cozy": 1.0},
                "collections": {"weekend picks": 1.0},
                "studios": {"studio home": 1.0},
                "genres": {"adventure": 1.0},
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"90_109": 1.0},
                "decades": {"2020s": 1.0},
            },
            "world_rating_profile": {
                "alignment": 0.95,
                "confidence": 0.75,
                "sample_size": 8,
            },
        }
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="top-picks-low",
                title="Weekend Crowdpleaser",
                genres=["Adventure", "Family"],
                keywords=["cozy", "mystery"],
                studios=["studio home"],
                collection_name="Weekend Picks",
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                popularity=80.0,
                rating=6.4,
                rating_count=9000,
                score_breakdown={
                    "provider_rating": 6.4,
                    "provider_rating_count": 9000,
                    "days_since_activity": 0.0,
                    "rewatch_count": 1.0,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="top-picks-high",
                title="Critics' Weekend Pick",
                genres=["Adventure", "Family"],
                keywords=["cozy", "mystery"],
                studios=["studio home"],
                collection_name="Weekend Picks",
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                popularity=80.0,
                rating=8.9,
                rating_count=9000,
                score_breakdown={
                    "provider_rating": 8.9,
                    "provider_rating_count": 9000,
                    "days_since_activity": 0.0,
                    "rewatch_count": 1.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            profile_payload,
            use_movie_rewatch_model=True,
        )
        legacy_titles, current_titles, payload = self._comparison_titles(
            reranked, top_n=2
        )

        self.assertEqual(
            legacy_titles,
            ["Weekend Crowdpleaser", "Critics' Weekend Pick"],
        )
        self.assertEqual(
            current_titles,
            ["Critics' Weekend Pick", "Weekend Crowdpleaser"],
        )
        self.assertEqual(payload["comparison_summary"]["promoted_titles"], [])
        self.assertEqual(payload["comparison_summary"]["dropped_titles"], [])
        self.assertEqual(payload["comparison_summary"]["changed_rank_count"], 2)

    def test_movie_comfort_rewatches_world_quality_debug_shows_before_and_after_titles(
        self,
    ):
        profile_payload = {
            "phase_keyword_affinity": {"comfort": 1.0, "rewatch": 0.9},
            "recent_keyword_affinity": {"comfort": 1.0, "rewatch": 0.9},
            "phase_studio_affinity": {"studio home": 1.0},
            "recent_studio_affinity": {"studio home": 1.0},
            "phase_collection_affinity": {"comfort shelf": 1.0},
            "recent_collection_affinity": {"comfort shelf": 1.0},
            "phase_certification_affinity": {"PG": 1.0},
            "recent_certification_affinity": {"PG": 1.0},
            "phase_runtime_bucket_affinity": {"90_109": 1.0},
            "recent_runtime_bucket_affinity": {"90_109": 1.0},
            "phase_decade_affinity": {"2010s": 1.0},
            "recent_decade_affinity": {"2010s": 1.0},
            "phase_genre_affinity": {"animation": 1.0, "comedy": 0.8},
            "recent_genre_affinity": {"animation": 1.0, "comedy": 0.8},
            "comfort_library_affinity": {
                "keywords": {"comfort": 1.0, "rewatch": 0.9},
                "collections": {"comfort shelf": 1.0},
                "studios": {"studio home": 1.0},
                "genres": {"animation": 1.0, "comedy": 0.8},
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"90_109": 1.0},
                "decades": {"2010s": 1.0},
            },
            "comfort_rewatch_affinity": {
                "keywords": {"comfort": 1.0, "rewatch": 0.9},
                "collections": {"comfort shelf": 1.0},
                "studios": {"studio home": 1.0},
                "genres": {"animation": 1.0, "comedy": 0.8},
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"90_109": 1.0},
                "decades": {"2010s": 1.0},
            },
            "world_rating_profile": {
                "alignment": 0.9,
                "confidence": 0.8,
                "sample_size": 10,
            },
        }
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="comfort-low",
                title="Reliable Rewatch",
                genres=["Animation", "Comedy"],
                keywords=["comfort", "rewatch"],
                studios=["studio home"],
                collection_name="Comfort Shelf",
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2010s",
                popularity=75.0,
                rating=6.6,
                rating_count=8500,
                score_breakdown={
                    "provider_rating": 6.6,
                    "provider_rating_count": 8500,
                    "user_score": 8.0,
                    "days_since_activity": 220.0,
                    "rewatch_count": 2.0,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="comfort-high",
                title="Beloved Rewatch",
                genres=["Animation", "Comedy"],
                keywords=["comfort", "rewatch"],
                studios=["studio home"],
                collection_name="Comfort Shelf",
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2010s",
                popularity=75.0,
                rating=8.7,
                rating_count=8500,
                score_breakdown={
                    "provider_rating": 8.7,
                    "provider_rating_count": 8500,
                    "user_score": 8.0,
                    "days_since_activity": 220.0,
                    "rewatch_count": 2.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            profile_payload,
            use_movie_rewatch_model=True,
        )
        legacy_titles, current_titles, _payload = self._comparison_titles(
            reranked, top_n=2
        )

        self.assertEqual(
            legacy_titles,
            ["Reliable Rewatch", "Beloved Rewatch"],
        )
        self.assertEqual(
            current_titles,
            ["Beloved Rewatch", "Reliable Rewatch"],
        )

    def test_tv_clear_out_next_world_quality_debug_shows_before_and_after_titles(self):
        profile_payload = {
            "phase_keyword_affinity": {"serial mystery": 1.0, "cozy": 0.9},
            "recent_keyword_affinity": {"serial mystery": 1.0, "cozy": 0.9},
            "phase_studio_affinity": {"tv house": 1.0},
            "recent_studio_affinity": {"tv house": 1.0},
            "phase_collection_affinity": {"queue next": 1.0},
            "recent_collection_affinity": {"queue next": 1.0},
            "phase_certification_affinity": {"PG": 1.0},
            "recent_certification_affinity": {"PG": 1.0},
            "phase_runtime_bucket_affinity": {"45_59": 1.0},
            "recent_runtime_bucket_affinity": {"45_59": 1.0},
            "phase_decade_affinity": {"2020s": 1.0},
            "recent_decade_affinity": {"2020s": 1.0},
            "phase_genre_affinity": {"mystery": 1.0, "drama": 0.8},
            "recent_genre_affinity": {"mystery": 1.0, "drama": 0.8},
            "comfort_library_affinity": {
                "keywords": {"serial mystery": 1.0, "cozy": 0.9},
                "collections": {"queue next": 1.0},
                "studios": {"tv house": 1.0},
                "genres": {"mystery": 1.0, "drama": 0.8},
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"45_59": 1.0},
                "decades": {"2020s": 1.0},
            },
            "comfort_rewatch_affinity": {
                "keywords": {"serial mystery": 1.0},
                "collections": {"queue next": 1.0},
                "studios": {"tv house": 1.0},
                "genres": {"mystery": 1.0},
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"45_59": 1.0},
                "decades": {"2020s": 1.0},
            },
            "world_rating_profile": {
                "alignment": 0.92,
                "confidence": 0.7,
                "sample_size": 9,
            },
        }
        candidates = [
            CandidateItem(
                media_type=MediaTypes.TV.value,
                source=Sources.TMDB.value,
                media_id="queue-low",
                title="Queue Up First",
                genres=["Mystery", "Drama"],
                keywords=["serial mystery", "cozy"],
                studios=["tv house"],
                collection_name="Queue Next",
                certification="PG",
                runtime_bucket="45_59",
                release_decade="2020s",
                popularity=88.0,
                rating=6.7,
                rating_count=7000,
                score_breakdown={
                    "provider_rating": 6.7,
                    "provider_rating_count": 7000,
                    "days_since_activity": 40.0,
                    "rewatch_count": 1.0,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.TV.value,
                source=Sources.TMDB.value,
                media_id="queue-high",
                title="Prestige Next Episode",
                genres=["Mystery", "Drama"],
                keywords=["serial mystery", "cozy"],
                studios=["tv house"],
                collection_name="Queue Next",
                certification="PG",
                runtime_bucket="45_59",
                release_decade="2020s",
                popularity=88.0,
                rating=8.8,
                rating_count=7000,
                score_breakdown={
                    "provider_rating": 8.8,
                    "provider_rating_count": 7000,
                    "days_since_activity": 40.0,
                    "rewatch_count": 1.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            profile_payload,
            use_movie_rewatch_model=True,
        )
        legacy_titles, current_titles, _payload = self._comparison_titles(
            reranked, top_n=2
        )

        self.assertEqual(
            legacy_titles,
            ["Queue Up First", "Prestige Next Episode"],
        )
        self.assertEqual(
            current_titles,
            ["Prestige Next Episode", "Queue Up First"],
        )

    def test_behavior_first_world_quality_stays_legacy_when_evidence_is_low(self):
        profile_payload = {
            "phase_keyword_affinity": {"cozy": 1.0},
            "recent_keyword_affinity": {"cozy": 1.0},
            "phase_studio_affinity": {"studio home": 1.0},
            "recent_studio_affinity": {"studio home": 1.0},
            "phase_certification_affinity": {"PG": 1.0},
            "recent_certification_affinity": {"PG": 1.0},
            "phase_runtime_bucket_affinity": {"90_109": 1.0},
            "recent_runtime_bucket_affinity": {"90_109": 1.0},
            "phase_decade_affinity": {"2020s": 1.0},
            "recent_decade_affinity": {"2020s": 1.0},
            "phase_genre_affinity": {"adventure": 1.0},
            "recent_genre_affinity": {"adventure": 1.0},
            "comfort_library_affinity": {
                "keywords": {"cozy": 1.0},
                "collections": {},
                "studios": {"studio home": 1.0},
                "genres": {"adventure": 1.0},
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"90_109": 1.0},
                "decades": {"2020s": 1.0},
            },
            "comfort_rewatch_affinity": {
                "keywords": {"cozy": 1.0},
                "collections": {},
                "studios": {"studio home": 1.0},
                "genres": {"adventure": 1.0},
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"90_109": 1.0},
                "decades": {"2020s": 1.0},
            },
            "world_rating_profile": {
                "alignment": 0.95,
                "confidence": 0.33,
                "sample_size": 4,
            },
        }
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="low-evidence-low",
                title="Low Evidence Crowdpleaser",
                genres=["Adventure"],
                keywords=["cozy"],
                studios=["studio home"],
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                popularity=82.0,
                rating=6.1,
                rating_count=8000,
                score_breakdown={
                    "provider_rating": 6.1,
                    "provider_rating_count": 8000,
                    "days_since_activity": 0.0,
                    "rewatch_count": 1.0,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="low-evidence-high",
                title="Low Evidence Critical Darling",
                genres=["Adventure"],
                keywords=["cozy"],
                studios=["studio home"],
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                popularity=82.0,
                rating=8.9,
                rating_count=8000,
                score_breakdown={
                    "provider_rating": 8.9,
                    "provider_rating_count": 8000,
                    "days_since_activity": 0.0,
                    "rewatch_count": 1.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            profile_payload,
            use_movie_rewatch_model=True,
        )
        legacy_titles, current_titles, payload = self._comparison_titles(
            reranked, top_n=2
        )

        self.assertEqual(
            legacy_titles,
            ["Low Evidence Crowdpleaser", "Low Evidence Critical Darling"],
        )
        self.assertEqual(current_titles, legacy_titles)
        self.assertEqual(payload["comparison_summary"]["promoted_titles"], [])
        self.assertEqual(payload["comparison_summary"]["dropped_titles"], [])

    def test_behavior_first_negative_alignment_does_not_invert_toward_bad_titles(self):
        profile_payload = {
            "phase_keyword_affinity": {"comfort": 1.0, "family": 0.9},
            "recent_keyword_affinity": {"comfort": 1.0, "family": 0.9},
            "phase_studio_affinity": {"studio home": 1.0},
            "recent_studio_affinity": {"studio home": 1.0},
            "phase_collection_affinity": {"comfort shelf": 1.0},
            "recent_collection_affinity": {"comfort shelf": 1.0},
            "phase_certification_affinity": {"PG": 1.0},
            "recent_certification_affinity": {"PG": 1.0},
            "phase_runtime_bucket_affinity": {"90_109": 1.0},
            "recent_runtime_bucket_affinity": {"90_109": 1.0},
            "phase_decade_affinity": {"2020s": 1.0},
            "recent_decade_affinity": {"2020s": 1.0},
            "phase_genre_affinity": {"animation": 1.0, "family": 0.9},
            "recent_genre_affinity": {"animation": 1.0, "family": 0.9},
            "comfort_library_affinity": {
                "keywords": {"comfort": 1.0, "family": 0.9},
                "collections": {"comfort shelf": 1.0},
                "studios": {"studio home": 1.0},
                "genres": {"animation": 1.0, "family": 0.9},
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"90_109": 1.0},
                "decades": {"2020s": 1.0},
            },
            "comfort_rewatch_affinity": {
                "keywords": {"comfort": 1.0, "family": 0.9},
                "collections": {"comfort shelf": 1.0},
                "studios": {"studio home": 1.0},
                "genres": {"animation": 1.0, "family": 0.9},
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"90_109": 1.0},
                "decades": {"2020s": 1.0},
            },
            "world_rating_profile": {
                "alignment": -0.9,
                "confidence": 0.9,
                "sample_size": 10,
            },
        }
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="negative-alignment-strong",
                title="Strong Comfort Fit",
                genres=["Animation", "Family"],
                keywords=["comfort", "family"],
                studios=["studio home"],
                collection_name="Comfort Shelf",
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                popularity=70.0,
                rating=5.8,
                rating_count=6000,
                score_breakdown={
                    "provider_rating": 5.8,
                    "provider_rating_count": 6000,
                    "user_score": 8.0,
                    "days_since_activity": 240.0,
                    "rewatch_count": 2.0,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="negative-alignment-weak",
                title="High Consensus Weak Fit",
                genres=["Drama"],
                keywords=["biography"],
                studios=["other studio"],
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                popularity=70.0,
                rating=9.1,
                rating_count=6000,
                score_breakdown={
                    "provider_rating": 9.1,
                    "provider_rating_count": 6000,
                    "user_score": 8.0,
                    "days_since_activity": 240.0,
                    "rewatch_count": 2.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            profile_payload,
            use_movie_rewatch_model=True,
        )
        legacy_titles, current_titles, payload = self._comparison_titles(
            reranked, top_n=2
        )

        self.assertEqual(legacy_titles[0], "Strong Comfort Fit")
        self.assertEqual(current_titles[0], "Strong Comfort Fit")
        self.assertEqual(payload["comparison_summary"]["promoted_titles"], [])
        self.assertLess(
            reranked[0].score_breakdown["world_quality"],
            reranked[1].score_breakdown["world_quality"],
        )

    def test_clear_out_next_candidates_exclude_caught_up_anime_entries(self):
        open_item = Item.objects.create(
            media_id="clear-open-1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Open Pick",
            image="https://example.com/clear-open-1.jpg",
            genres=["Mystery"],
            provider_keywords=["Cozy"],
            provider_popularity=75.0,
            provider_rating=8.5,
        )
        caught_up_item = Item.objects.create(
            media_id="clear-caught-up-1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Caught Up Pick",
            image="https://example.com/clear-caught-up-1.jpg",
            genres=["Mystery"],
            provider_keywords=["Cozy"],
            provider_popularity=68.0,
            provider_rating=7.8,
        )

        with patch(
            "app.models.providers.services.get_media_metadata",
            return_value={"max_progress": 10},
        ):
            Anime.objects.create(
                user=self.user,
                item=open_item,
                status=Status.IN_PROGRESS.value,
                progress=3,
            )
            Anime.objects.create(
                user=self.user,
                item=caught_up_item,
                status=Status.IN_PROGRESS.value,
                progress=1,
            )

        Event.objects.create(
            item=open_item,
            content_number=5,
            datetime=timezone.now() - timedelta(days=1),
        )
        Event.objects.create(
            item=caught_up_item,
            content_number=1,
            datetime=timezone.now() - timedelta(days=1),
        )

        profile_payload = {
            "genre_affinity": {"mystery": 1.0},
            "recent_genre_affinity": {"mystery": 1.0},
        }
        candidates = _clear_out_next_candidates(
            self.user,
            MediaTypes.ANIME.value,
            "clear_out_next",
            profile_payload,
        )

        self.assertEqual([item.media_id for item in candidates], ["clear-open-1"])
        self.assertTrue(all(item.display_score is not None for item in candidates))
        candidate = candidates[0]
        self.assertGreaterEqual(candidate.display_score, 0.0)
        self.assertLessEqual(candidate.display_score, 1.0)
        self.assertIn("rewatch_bonus", candidate.score_breakdown)
        self.assertIn("inactivity_norm", candidate.score_breakdown)
        self.assertIn("tag_signal_mode", candidate.score_breakdown)

    def test_comfort_confidence_ranks_by_recent_watch_similarity(self):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="recent_match",
                title="Recent Match",
                genres=["Science Fiction", "Thriller"],
                final_score=0.55,
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 120.0,
                    "recency_bonus": 0.9,
                    "phase_genre_bonus": 0.95,
                    "recency_tag_bonus": 0.7,
                    "phase_tag_bonus": 0.8,
                    "rewatch_count": 1.0,
                    "genre_match": 0.7,
                    "tag_match": 0.5,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="high_rated",
                title="High Rated",
                genres=["Romance", "Comedy"],
                final_score=0.6,
                score_breakdown={
                    "user_score": 10.0,
                    "days_since_activity": 460.0,
                    "recency_bonus": 0.1,
                    "phase_genre_bonus": 0.2,
                    "recency_tag_bonus": 0.1,
                    "phase_tag_bonus": 0.1,
                    "rewatch_count": 1.0,
                    "genre_match": 0.2,
                    "tag_match": 0.1,
                },
            ),
        ]

        profile = {
            "phase_genre_affinity": {
                "science fiction": 1.0,
                "thriller": 0.8,
                "action": 0.5,
            },
        }

        boosted = _apply_comfort_confidence(candidates, profile)

        # Recent-match candidate should rank above high-rated candidate
        self.assertEqual(boosted[0].media_id, "recent_match")
        self.assertGreater(boosted[0].final_score, boosted[1].final_score)
        self.assertTrue(
            all(c.display_score is not None for c in boosted),
        )
        # Match genres populated from profile overlap
        self.assertIn(
            "match_genres",
            boosted[0].score_breakdown,
        )
        self.assertIn(
            "Science Fiction",
            boosted[0].score_breakdown["match_genres"],
        )

    def test_comfort_confidence_unrated_not_penalized(self):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="unrated_phase",
                title="Unrated Phase Fit",
                genres=["Animation", "Family"],
                score_breakdown={
                    "days_since_activity": 180.0,
                    "recency_bonus": 0.8,
                    "phase_genre_bonus": 1.0,
                    "recency_tag_bonus": 0.8,
                    "phase_tag_bonus": 1.0,
                    "rewatch_count": 1.0,
                    "genre_match": 0.8,
                    "tag_match": 0.7,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="rated_weak",
                title="Rated But Weak Fit",
                genres=["Crime"],
                score_breakdown={
                    "user_score": 9.5,
                    "days_since_activity": 180.0,
                    "recency_bonus": 0.2,
                    "phase_genre_bonus": 0.2,
                    "recency_tag_bonus": 0.1,
                    "phase_tag_bonus": 0.1,
                    "rewatch_count": 1.0,
                    "genre_match": 0.2,
                    "tag_match": 0.1,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            {"phase_genre_affinity": {"animation": 1.0, "family": 0.8}},
        )

        self.assertEqual(reranked[0].media_id, "unrated_phase")
        self.assertEqual(reranked[0].score_breakdown["rating_confidence"], 0.5)
        self.assertGreater(reranked[0].final_score, reranked[1].final_score)

    def test_comfort_confidence_rewatch_boost(self):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="rewatched",
                title="Rewatched",
                genres=["Comedy"],
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 220.0,
                    "recency_bonus": 0.6,
                    "phase_genre_bonus": 0.7,
                    "recency_tag_bonus": 0.4,
                    "phase_tag_bonus": 0.5,
                    "rewatch_count": 4.0,
                    "genre_match": 0.6,
                    "tag_match": 0.4,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="single_watch",
                title="Single Watch",
                genres=["Comedy"],
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 220.0,
                    "recency_bonus": 0.6,
                    "phase_genre_bonus": 0.7,
                    "recency_tag_bonus": 0.4,
                    "phase_tag_bonus": 0.5,
                    "rewatch_count": 1.0,
                    "genre_match": 0.6,
                    "tag_match": 0.4,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            {"phase_genre_affinity": {"comedy": 1.0}},
        )

        self.assertEqual(reranked[0].media_id, "rewatched")
        self.assertGreater(
            reranked[0].score_breakdown["rewatch_bonus"],
            reranked[1].score_breakdown["rewatch_bonus"],
        )

    def test_comfort_confidence_decorrelates_hot_recency_overlap(self):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="overlap",
                title="Overlap Fit",
                genres=["Drama"],
                tags=["Character Study"],
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 200.0,
                    "recency_bonus": 0.62,
                    "phase_genre_bonus": 0.6,
                    "recency_tag_bonus": 0.66,
                    "phase_tag_bonus": 0.64,
                    "rewatch_count": 1.0,
                    "genre_match": 0.6,
                    "tag_match": 0.6,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            {"phase_genre_affinity": {"drama": 1.0}},
        )
        breakdown = reranked[0].score_breakdown

        self.assertGreater(breakdown["hot_recency_base"], 0.0)
        self.assertLess(breakdown["hot_recency"], breakdown["hot_recency_base"])
        self.assertGreaterEqual(breakdown["phase_hot_overlap_ratio"], 0.0)
        self.assertLess(breakdown["phase_hot_overlap_ratio"], 1.0)
        self.assertGreaterEqual(breakdown["hot_recency_incremental"], 0.0)

    def test_comfort_confidence_uses_tag_coverage_mode_for_hot_recency(self):
        sparse_candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="sparse",
                title="Sparse",
                genres=["Drama"],
                tags=[],
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 200.0,
                    "recency_bonus": 0.95,
                    "phase_genre_bonus": 0.25,
                    "recency_tag_bonus": 0.95,
                    "phase_tag_bonus": 0.1,
                    "rewatch_count": 1.0,
                    "genre_match": 0.6,
                    "tag_match": 0.2,
                    "recent_history_tag_coverage": 0.1,
                },
            ),
        ]
        rich_candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="rich",
                title="Rich",
                genres=["Drama"],
                tags=["Cozy"],
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 200.0,
                    "recency_bonus": 0.95,
                    "phase_genre_bonus": 0.25,
                    "recency_tag_bonus": 0.95,
                    "phase_tag_bonus": 0.1,
                    "rewatch_count": 1.0,
                    "genre_match": 0.6,
                    "tag_match": 0.2,
                    "recent_history_tag_coverage": 0.8,
                },
            ),
        ]

        sparse_ranked = _apply_comfort_confidence(
            sparse_candidates,
            {"phase_genre_affinity": {"drama": 1.0}},
        )
        rich_ranked = _apply_comfort_confidence(
            rich_candidates,
            {
                "phase_genre_affinity": {"drama": 1.0},
                "phase_tag_affinity": {"cozy": 1.0},
            },
        )

        sparse = sparse_ranked[0].score_breakdown
        rich = rich_ranked[0].score_breakdown
        self.assertEqual(sparse["tag_signal_mode"], "tag_sparse")
        self.assertEqual(rich["tag_signal_mode"], "tag_rich")
        self.assertLess(sparse["hot_recency_mode_multiplier"], 1.0)
        self.assertEqual(rich["hot_recency_mode_multiplier"], 1.0)
        self.assertGreater(
            rich["hot_recency_contribution"], sparse["hot_recency_contribution"]
        )

    def test_comfort_confidence_rewatch_needs_phase_support(self):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="legacy_rewatch",
                title="Legacy Rewatch",
                genres=["Drama"],
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 220.0,
                    "recency_bonus": 0.2,
                    "phase_genre_bonus": 0.2,
                    "recency_tag_bonus": 0.2,
                    "phase_tag_bonus": 0.2,
                    "rewatch_count": 6.0,
                    "genre_match": 0.2,
                    "tag_match": 0.2,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="phase_fit",
                title="Phase Fit",
                genres=["Animation"],
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 220.0,
                    "recency_bonus": 0.55,
                    "phase_genre_bonus": 0.85,
                    "recency_tag_bonus": 0.5,
                    "phase_tag_bonus": 0.8,
                    "rewatch_count": 1.0,
                    "genre_match": 0.7,
                    "tag_match": 0.7,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            {"phase_genre_affinity": {"animation": 1.0}},
        )

        self.assertEqual(reranked[0].media_id, "phase_fit")
        legacy = next(
            candidate
            for candidate in reranked
            if candidate.media_id == "legacy_rewatch"
        )
        self.assertLess(legacy.score_breakdown["rewatch_gate"], 0.5)

    def test_comfort_confidence_prefers_strong_phase_in_opening_window(self):
        candidates = []
        for index in range(12):
            candidates.append(
                CandidateItem(
                    media_type=MediaTypes.MOVIE.value,
                    source="tmdb",
                    media_id=f"medium-{index}",
                    title=f"Medium {index}",
                    genres=["Drama"],
                    score_breakdown={
                        "phase_pool_medium": 1.0,
                    },
                    final_score=0.80 - (index * 0.01),
                ),
            )
        candidates.append(
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="strong-below",
                title="Strong Below",
                genres=["Drama"],
                score_breakdown={
                    "phase_pool_strong": 1.0,
                },
                final_score=0.78,
            ),
        )
        reranked = _prefer_strong_phase_opening_window(candidates)

        opening_ids = {candidate.media_id for candidate in reranked[:12]}
        self.assertIn("strong-below", opening_ids)
        self.assertTrue(
            any(
                float(
                    candidate.score_breakdown.get("strong_phase_promoted_opening", 0.0)
                )
                >= 1.0
                for candidate in reranked[:12]
            ),
        )

    def test_comfort_candidates_includes_unrated(self):
        rated_item = Item.objects.create(
            media_id="4001",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Rated Old Favorite",
            image="http://example.com/rated.jpg",
            genres=["Action"],
        )
        unrated_old_item = Item.objects.create(
            media_id="4002",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Unrated Dormant",
            image="http://example.com/unrated-old.jpg",
            genres=["Drama"],
        )
        unrated_recent_item = Item.objects.create(
            media_id="4003",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Unrated Recent",
            image="http://example.com/unrated-recent.jpg",
            genres=["Comedy"],
        )

        with patch(
            "app.models.providers.services.get_media_metadata",
            return_value={"max_progress": 1},
        ):
            Movie.objects.create(
                item=rated_item,
                user=self.user,
                score=9,
                status=Status.COMPLETED.value,
                end_date=timezone.now() - timedelta(days=130),
            )
            Movie.objects.create(
                item=unrated_old_item,
                user=self.user,
                score=None,
                status=Status.COMPLETED.value,
                end_date=timezone.now() - timedelta(days=140),
            )
            Movie.objects.create(
                item=unrated_recent_item,
                user=self.user,
                score=None,
                status=Status.COMPLETED.value,
                end_date=timezone.now() - timedelta(days=20),
            )

        candidates = _comfort_candidates(
            self.user,
            MediaTypes.MOVIE.value,
            row_key="comfort_rewatches",
            source_reason="Past favorite",
            older_than_days=30,
            min_score=8.0,
        )
        candidate_ids = {candidate.media_id for candidate in candidates}

        self.assertIn("4001", candidate_ids)
        self.assertIn("4002", candidate_ids)
        self.assertNotIn("4003", candidate_ids)

    def test_comfort_candidates_exclude_recent_movies_inside_30_day_floor(self):
        eligible_item = Item.objects.create(
            media_id="4101",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Eligible Comfort",
            image="http://example.com/eligible.jpg",
            genres=["Animation"],
        )
        recent_item = Item.objects.create(
            media_id="4102",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Recent Comfort",
            image="http://example.com/recent.jpg",
            genres=["Animation"],
        )

        with patch(
            "app.models.providers.services.get_media_metadata",
            return_value={"max_progress": 1},
        ):
            Movie.objects.create(
                item=eligible_item,
                user=self.user,
                score=9,
                status=Status.COMPLETED.value,
                end_date=timezone.now() - timedelta(days=31),
            )
            Movie.objects.create(
                item=recent_item,
                user=self.user,
                score=10,
                status=Status.COMPLETED.value,
                end_date=timezone.now() - timedelta(days=29),
            )

        candidates = _comfort_candidates(
            self.user,
            MediaTypes.MOVIE.value,
            row_key="comfort_rewatches",
            source_reason="Past favorite",
            older_than_days=30,
            min_score=8.0,
        )
        candidate_ids = {candidate.media_id for candidate in candidates}

        self.assertIn("4101", candidate_ids)
        self.assertNotIn("4102", candidate_ids)

    def test_comfort_candidates_biases_phase_evidence_with_limited_backfill(self):
        phase_media_ids = {"5001", "5002"}
        with patch(
            "app.models.providers.services.get_media_metadata",
            return_value={"max_progress": 1},
        ):
            for media_id in sorted(phase_media_ids):
                item = Item.objects.create(
                    media_id=media_id,
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.MOVIE.value,
                    title=f"Phase {media_id}",
                    image=f"http://example.com/{media_id}.jpg",
                    genres=["Animation"],
                )
                Movie.objects.create(
                    item=item,
                    user=self.user,
                    score=8,
                    status=Status.COMPLETED.value,
                    end_date=timezone.now() - timedelta(days=180),
                )

            for index in range(8):
                media_id = str(6000 + index)
                item = Item.objects.create(
                    media_id=media_id,
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.MOVIE.value,
                    title=f"Broad {media_id}",
                    image=f"http://example.com/{media_id}.jpg",
                    genres=["Action"],
                )
                Movie.objects.create(
                    item=item,
                    user=self.user,
                    score=10,
                    status=Status.COMPLETED.value,
                    end_date=timezone.now() - timedelta(days=180),
                )

        candidates = _comfort_candidates(
            self.user,
            MediaTypes.MOVIE.value,
            row_key="comfort_rewatches",
            source_reason="Past favorite",
            older_than_days=90,
            min_score=8.0,
            profile_payload={"phase_genre_affinity": {"animation": 1.0}},
        )
        top_two = {candidate.media_id for candidate in candidates[:2]}
        weak_count = sum(
            1
            for candidate in candidates
            if "action" in {genre.lower() for genre in (candidate.genres or [])}
        )

        self.assertEqual(top_two, phase_media_ids)
        self.assertEqual(len(candidates), 8)
        self.assertEqual(weak_count, 6)

    def test_entries_to_candidates_include_richer_movie_metadata(self):
        item = Item.objects.create(
            media_id="7001",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Rich Metadata",
            image="http://example.com/rich.jpg",
            genres=["Animation", "Mystery"],
            provider_keywords=["Whodunit", "Holiday"],
            provider_certification="PG",
            provider_collection_id="44",
            provider_collection_name="Mystery Collection",
            runtime_minutes=102,
            release_datetime=timezone.now() - timedelta(days=365),
            studios=["Pixar Animation Studios"],
        )
        director = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="director-rich",
            name="Greta Gerwig",
        )
        lead = Person.objects.create(
            source=Sources.TMDB.value,
            source_person_id="actor-rich",
            name="Amy Poehler",
        )
        studio = Studio.objects.create(
            source=Sources.TMDB.value,
            source_studio_id="studio-rich",
            name="Pixar Animation Studios",
        )
        ItemPersonCredit.objects.create(
            item=item,
            person=director,
            role_type=CreditRoleType.CREW.value,
            role="Director",
            department="Directing",
        )
        ItemPersonCredit.objects.create(
            item=item,
            person=lead,
            role_type=CreditRoleType.CAST.value,
            role="Lead",
            sort_order=0,
        )
        ItemStudioCredit.objects.create(item=item, studio=studio)

        with patch(
            "app.models.providers.services.get_media_metadata",
            return_value={"max_progress": 1},
        ):
            entry = Movie.objects.create(
                item=item,
                user=self.user,
                score=9,
                status=Status.COMPLETED.value,
                end_date=timezone.now() - timedelta(days=120),
            )

        candidates = _entries_to_candidates(
            [entry],
            user=self.user,
            row_key="comfort_rewatches",
            source_reason="Past favorite",
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.keywords, ["whodunit", "holiday"])
        self.assertEqual(candidate.studios, ["pixar"])
        self.assertEqual(candidate.directors, ["greta gerwig"])
        self.assertEqual(candidate.lead_cast, ["amy poehler"])
        self.assertEqual(candidate.collection_name, "Mystery Collection")
        self.assertEqual(candidate.certification, "PG")
        self.assertEqual(candidate.runtime_bucket, "90_109")
        self.assertEqual(candidate.release_decade, "2020s")

    def test_entries_to_candidates_include_richer_anime_metadata(self):
        item = Item.objects.create(
            media_id="anime-rich-7001",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Rich Anime Metadata",
            image="http://example.com/anime-rich.jpg",
            genres=["Animation", "Fantasy"],
            provider_keywords=["Found Family", "Holiday"],
            provider_certification="PG",
            provider_collection_id="54",
            provider_collection_name="Magic Collection",
            runtime_minutes=24,
            release_datetime=timezone.now() - timedelta(days=365),
            studios=["Studio Pierrot"],
        )
        director = Person.objects.create(
            source=Sources.MAL.value,
            source_person_id="anime-director-rich",
            name="Hayao Miyazaki",
        )
        lead = Person.objects.create(
            source=Sources.MAL.value,
            source_person_id="anime-actor-rich",
            name="Maaya Sakamoto",
        )
        studio = Studio.objects.create(
            source=Sources.MAL.value,
            source_studio_id="anime-studio-rich",
            name="Studio Pierrot",
        )
        ItemPersonCredit.objects.create(
            item=item,
            person=director,
            role_type=CreditRoleType.CREW.value,
            role="Director",
            department="Directing",
        )
        ItemPersonCredit.objects.create(
            item=item,
            person=lead,
            role_type=CreditRoleType.CAST.value,
            role="Lead",
            sort_order=0,
        )
        ItemStudioCredit.objects.create(item=item, studio=studio)

        with patch(
            "app.models.providers.services.get_media_metadata",
            return_value={"max_progress": 1},
        ):
            entry = Anime.objects.create(
                item=item,
                user=self.user,
                score=9,
                status=Status.COMPLETED.value,
                end_date=timezone.now() - timedelta(days=120),
            )

        candidates = _entries_to_candidates(
            [entry],
            user=self.user,
            row_key="comfort_rewatches",
            source_reason="Past favorite",
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.keywords, ["found family", "holiday"])
        self.assertEqual(candidate.studios, ["studio pierrot"])
        self.assertEqual(candidate.directors, ["hayao miyazaki"])
        self.assertEqual(candidate.lead_cast, ["maaya sakamoto"])
        self.assertEqual(candidate.collection_name, "Magic Collection")
        self.assertEqual(candidate.certification, "PG")
        self.assertEqual(candidate.runtime_bucket, "<90")
        self.assertEqual(candidate.release_decade, "2020s")

    def test_behavior_first_confidence_applies_to_tv(self):
        base_profile = {
            "phase_keyword_affinity": {"holiday": 1.0, "whodunit": 0.9},
            "recent_keyword_affinity": {"holiday": 0.9, "whodunit": 0.8},
            "phase_studio_affinity": {"pixar": 1.0},
            "recent_studio_affinity": {"pixar": 0.9},
            "phase_collection_affinity": {"mystery collection": 0.9},
            "recent_collection_affinity": {"mystery collection": 0.8},
            "phase_director_affinity": {"greta gerwig": 0.6},
            "recent_director_affinity": {"greta gerwig": 0.4},
            "phase_lead_cast_affinity": {"amy poehler": 0.6},
            "recent_lead_cast_affinity": {"amy poehler": 0.4},
            "phase_certification_affinity": {"pg": 1.0},
            "recent_certification_affinity": {"pg": 0.9},
            "phase_runtime_bucket_affinity": {"<90": 1.0},
            "recent_runtime_bucket_affinity": {"<90": 0.9},
            "phase_decade_affinity": {"2020s": 0.7},
            "recent_decade_affinity": {"2020s": 0.8},
            "phase_genre_affinity": {"animation": 0.8, "family": 0.7},
            "recent_genre_affinity": {"animation": 0.9, "family": 0.8},
            "comfort_library_affinity": {
                "keywords": {"holiday": 1.0, "whodunit": 0.95},
                "collections": {"mystery collection": 0.9},
                "studios": {"pixar": 1.0},
                "genres": {"animation": 0.9, "family": 0.8},
                "directors": {"greta gerwig": 0.4},
                "lead_cast": {"amy poehler": 0.3},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"<90": 1.0},
                "decades": {"2020s": 0.9},
            },
            "comfort_rewatch_affinity": {
                "keywords": {"holiday": 1.0},
                "collections": {"mystery collection": 0.9},
                "studios": {"pixar": 1.0},
                "genres": {"animation": 0.9},
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"<90": 1.0},
                "decades": {"2020s": 0.9},
            },
        }

        candidates = [
            CandidateItem(
                media_type=MediaTypes.TV.value,
                source=Sources.TMDB.value,
                media_id="tv-fit",
                title="Family Comfort",
                genres=["Animation", "Family"],
                keywords=["holiday", "whodunit"],
                studios=["pixar"],
                directors=["greta gerwig"],
                lead_cast=["amy poehler"],
                collection_name="Mystery Collection",
                certification="PG",
                runtime_bucket="<90",
                release_decade="2020s",
                popularity=90.0,
                rating_count=9000,
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 220.0,
                    "rewatch_count": 2.0,
                    "recent_history_tag_coverage": 0.0,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.TV.value,
                source=Sources.TMDB.value,
                media_id="tv-weak",
                title="Broad Fit",
                genres=["Drama"],
                certification="PG",
                runtime_bucket="<90",
                release_decade="2010s",
                popularity=70.0,
                rating_count=5000,
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 260.0,
                    "rewatch_count": 1.0,
                    "recent_history_tag_coverage": 0.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            base_profile,
            use_movie_rewatch_model=True,
        )

        self.assertEqual(reranked[0].media_id, "tv-fit")
        self.assertGreater(reranked[0].score_breakdown["library_fit"], 0.0)
        self.assertGreater(reranked[0].score_breakdown["recency_phase_fit"], 0.0)
        self.assertIn("ready_now_score", reranked[0].score_breakdown)
        self.assertTrue(
            reranked[0]
            .score_breakdown["primary_reason_bucket"]
            .startswith("keywords:"),
        )

    @patch("app.discover.movie_comfort._is_holiday_window", new=lambda today=None: False)
    @patch("app.discover.comfort_scoring._is_holiday_window", new=lambda today=None: False)
    def test_behavior_first_confidence_keeps_anime_on_generic_path(self):
        base_profile = {
            "phase_keyword_affinity": {"holiday": 1.0, "whodunit": 0.9},
            "recent_keyword_affinity": {"holiday": 0.9, "whodunit": 0.8},
            "phase_studio_affinity": {"pixar": 1.0},
            "recent_studio_affinity": {"pixar": 0.9},
            "phase_collection_affinity": {"mystery collection": 0.9},
            "recent_collection_affinity": {"mystery collection": 0.8},
            "phase_director_affinity": {"greta gerwig": 0.6},
            "recent_director_affinity": {"greta gerwig": 0.4},
            "phase_lead_cast_affinity": {"amy poehler": 0.6},
            "recent_lead_cast_affinity": {"amy poehler": 0.4},
            "phase_certification_affinity": {"pg": 1.0},
            "recent_certification_affinity": {"pg": 0.9},
            "phase_runtime_bucket_affinity": {"<90": 1.0},
            "recent_runtime_bucket_affinity": {"<90": 0.9},
            "phase_decade_affinity": {"2020s": 0.7},
            "recent_decade_affinity": {"2020s": 0.8},
            "phase_genre_affinity": {"animation": 0.8, "family": 0.7},
            "recent_genre_affinity": {"animation": 0.9, "family": 0.8},
            "comfort_library_affinity": {
                "keywords": {"holiday": 1.0, "whodunit": 0.95},
                "collections": {"mystery collection": 0.9},
                "studios": {"pixar": 1.0},
                "genres": {"animation": 0.9, "family": 0.8},
                "directors": {"greta gerwig": 0.4},
                "lead_cast": {"amy poehler": 0.3},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"<90": 1.0},
                "decades": {"2020s": 0.9},
            },
            "comfort_rewatch_affinity": {
                "keywords": {"holiday": 1.0},
                "collections": {"mystery collection": 0.9},
                "studios": {"pixar": 1.0},
                "genres": {"animation": 0.9},
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"<90": 1.0},
                "decades": {"2020s": 0.9},
            },
        }
        candidates = [
            CandidateItem(
                media_type=MediaTypes.ANIME.value,
                source=Sources.TMDB.value,
                media_id="anime-fit",
                title="Family Comfort",
                genres=["Animation", "Family"],
                keywords=["holiday", "whodunit"],
                studios=["pixar"],
                directors=["greta gerwig"],
                lead_cast=["amy poehler"],
                collection_name="Mystery Collection",
                certification="PG",
                runtime_bucket="<90",
                release_decade="2020s",
                popularity=90.0,
                rating_count=9000,
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 220.0,
                    "rewatch_count": 2.0,
                    "recent_history_tag_coverage": 0.0,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.ANIME.value,
                source=Sources.TMDB.value,
                media_id="anime-weak",
                title="Broad Fit",
                genres=["Drama"],
                certification="PG",
                runtime_bucket="<90",
                release_decade="2010s",
                popularity=70.0,
                rating_count=5000,
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 260.0,
                    "rewatch_count": 1.0,
                    "recent_history_tag_coverage": 0.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            base_profile,
            use_movie_rewatch_model=True,
        )

        self.assertEqual(reranked[0].media_id, "anime-weak")
        self.assertEqual(reranked[0].score_breakdown["tag_signal_mode"], "tag_sparse")
        self.assertNotIn("library_fit", reranked[0].score_breakdown)
        self.assertNotIn("primary_reason_bucket", reranked[0].score_breakdown)

    def test_movie_comfort_confidence_prefers_behavior_first_fits_and_filters_weak_unrated(
        self,
    ):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="family",
                title="Family Comfort",
                genres=["Animation", "Family"],
                keywords=["holiday", "whodunit"],
                studios=["pixar"],
                collection_name="Mystery Collection",
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                popularity=90.0,
                rating_count=9000,
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 220.0,
                    "rewatch_count": 2.0,
                    "recent_history_tag_coverage": 0.0,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="classic",
                title="Classic Suspense",
                genres=["Thriller"],
                directors=["alfred hitchcock"],
                certification="PG",
                runtime_bucket="90_109",
                release_decade="1950s",
                popularity=70.0,
                rating_count=5000,
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 260.0,
                    "rewatch_count": 1.0,
                    "recent_history_tag_coverage": 0.0,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="apollo",
                title="Apollo 11",
                genres=["Documentary"],
                keywords=["space program"],
                studios=["neon"],
                certification="G",
                runtime_bucket="90_109",
                release_decade="2010s",
                popularity=85.0,
                rating_count=7000,
                score_breakdown={
                    "days_since_activity": 240.0,
                    "rewatch_count": 1.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            {
                "phase_keyword_affinity": {"holiday": 1.0, "whodunit": 0.9},
                "recent_keyword_affinity": {"holiday": 0.9, "whodunit": 0.8},
                "phase_studio_affinity": {"pixar": 1.0},
                "recent_studio_affinity": {"pixar": 0.9},
                "phase_collection_affinity": {"mystery collection": 0.9},
                "recent_collection_affinity": {"mystery collection": 0.8},
                "phase_director_affinity": {"alfred hitchcock": 0.6},
                "recent_director_affinity": {"alfred hitchcock": 0.3},
                "phase_certification_affinity": {"pg": 1.0},
                "recent_certification_affinity": {"pg": 0.9},
                "phase_runtime_bucket_affinity": {"90_109": 1.0},
                "recent_runtime_bucket_affinity": {"90_109": 0.9},
                "phase_decade_affinity": {"2020s": 0.7, "1950s": 0.5},
                "recent_decade_affinity": {"2020s": 0.8},
                "phase_genre_affinity": {"animation": 0.8, "family": 0.7},
                "recent_genre_affinity": {"animation": 0.9, "family": 0.8},
                "comfort_library_affinity": {
                    "keywords": {"holiday": 1.0, "whodunit": 0.95},
                    "collections": {"mystery collection": 0.9},
                    "studios": {"pixar": 1.0},
                    "genres": {"animation": 0.9, "family": 0.8},
                    "directors": {"alfred hitchcock": 0.4},
                    "lead_cast": {},
                    "certifications": {"PG": 1.0},
                    "runtime_buckets": {"90_109": 1.0},
                    "decades": {"2020s": 0.9},
                },
                "comfort_rewatch_affinity": {
                    "keywords": {"holiday": 1.0},
                    "collections": {"mystery collection": 0.9},
                    "studios": {"pixar": 1.0},
                    "genres": {"animation": 0.9},
                    "directors": {},
                    "lead_cast": {},
                    "certifications": {"PG": 1.0},
                    "runtime_buckets": {"90_109": 1.0},
                    "decades": {"2020s": 0.9},
                },
            },
            use_movie_rewatch_model=True,
        )

        reranked_ids = [candidate.media_id for candidate in reranked]
        self.assertEqual(reranked_ids[0], "family")
        self.assertNotIn("apollo", reranked_ids)
        self.assertGreater(
            reranked[0].score_breakdown["library_fit"],
            reranked[1].score_breakdown["library_fit"],
        )
        self.assertGreater(reranked[0].score_breakdown["recency_phase_fit"], 0.0)
        self.assertTrue(
            reranked[0]
            .score_breakdown["primary_reason_bucket"]
            .startswith("keywords:"),
        )

    def test_row_match_signal_prefers_movie_reason_bucket_labels_over_runtime_and_decade(
        self,
    ):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="signal-1",
                title="Signal Movie",
                genres=["Drama"],
                keywords=["whodunit"],
                studios=["pixar"],
                runtime_bucket="90_109",
                release_decade="2010s",
                score_breakdown={
                    "phase_fit": 0.9,
                    "library_fit": 0.8,
                    "recency_phase_fit": 0.75,
                    "keyword_fit": 0.9,
                    "studio_fit": 0.8,
                    "runtime_fit": 0.7,
                    "decade_fit": 0.6,
                    "primary_reason_bucket": "keywords:whodunit",
                },
                final_score=0.85,
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="signal-2",
                title="Signal Movie Two",
                studios=["pixar"],
                runtime_bucket="90_109",
                release_decade="2010s",
                score_breakdown={
                    "phase_fit": 0.8,
                    "library_fit": 0.7,
                    "recency_phase_fit": 0.7,
                    "studio_fit": 0.85,
                    "runtime_fit": 0.65,
                    "decade_fit": 0.55,
                    "primary_reason_bucket": "studios:pixar",
                },
                final_score=0.8,
            ),
        ]

        signal, details = _row_match_signal_with_details(
            "comfort_rewatches",
            candidates,
            {},
        )

        self.assertIsNotNone(signal)
        self.assertIn("Whodunit", signal or "")
        self.assertIn("Pixar", signal or "")
        self.assertNotIn("2010s", signal or "")
        self.assertNotIn("90 109", signal or "")
        self.assertIsNotNone(details)
        details = details or {}
        self.assertEqual(details["mode"], "movie_reason_buckets")
        self.assertIn("Signal evidence:", details["explanation"])
        self.assertGreaterEqual(len(details.get("match_signal_label_sources", [])), 1)

    def test_top_picks_match_signal_formats_runtime_bucket_labels_humanly(self):
        signal, details = _row_match_signal_with_details(
            "top_picks_for_you",
            [
                CandidateItem(
                    media_type=MediaTypes.MOVIE.value,
                    source="tmdb",
                    media_id="runtime-signal",
                    title="Runtime Signal Movie",
                    runtime_bucket="90_109",
                    score_breakdown={"phase_fit": 0.9},
                    final_score=0.82,
                ),
            ],
            {
                "phase_runtime_bucket_affinity": {"90_109": 1.0},
            },
        )

        self.assertIsNotNone(signal)
        self.assertIn("90-109 Minutes", signal or "")
        self.assertNotIn("90 109", signal or "")
        self.assertIsNotNone(details)

    def test_movie_comfort_debug_payload_exposes_behavior_first_fields(self):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="family",
                title="Family Comfort",
                genres=["Animation", "Family"],
                keywords=["holiday", "whodunit"],
                studios=["pixar"],
                collection_name="Mystery Collection",
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                popularity=90.0,
                rating_count=9000,
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 220.0,
                    "rewatch_count": 2.0,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="classic",
                title="Classic Suspense",
                genres=["Thriller"],
                directors=["alfred hitchcock"],
                certification="PG",
                runtime_bucket="90_109",
                release_decade="1950s",
                popularity=70.0,
                rating_count=5000,
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 260.0,
                    "rewatch_count": 1.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            {
                "phase_keyword_affinity": {"holiday": 1.0, "whodunit": 0.9},
                "recent_keyword_affinity": {"holiday": 0.9},
                "phase_studio_affinity": {"pixar": 1.0},
                "recent_studio_affinity": {"pixar": 0.9},
                "phase_collection_affinity": {"mystery collection": 0.9},
                "phase_certification_affinity": {"pg": 1.0},
                "recent_certification_affinity": {"pg": 0.9},
                "phase_runtime_bucket_affinity": {"90_109": 1.0},
                "recent_runtime_bucket_affinity": {"90_109": 0.9},
                "phase_decade_affinity": {"2020s": 0.7},
                "recent_decade_affinity": {"2020s": 0.8},
                "phase_genre_affinity": {"animation": 0.8, "family": 0.7},
                "recent_genre_affinity": {"animation": 0.9, "family": 0.8},
                "comfort_library_affinity": {
                    "keywords": {"holiday": 1.0, "whodunit": 0.95},
                    "collections": {"mystery collection": 0.9},
                    "studios": {"pixar": 1.0},
                    "genres": {"animation": 0.9, "family": 0.8},
                    "directors": {"alfred hitchcock": 0.4},
                    "lead_cast": {},
                    "certifications": {"PG": 1.0},
                    "runtime_buckets": {"90_109": 1.0},
                    "decades": {"2020s": 0.9},
                },
                "comfort_rewatch_affinity": {
                    "keywords": {"holiday": 1.0},
                    "collections": {"mystery collection": 0.9},
                    "studios": {"pixar": 1.0},
                    "genres": {"animation": 0.9},
                    "directors": {},
                    "lead_cast": {},
                    "certifications": {"PG": 1.0},
                    "runtime_buckets": {"90_109": 1.0},
                    "decades": {"2020s": 0.9},
                },
            },
            use_movie_rewatch_model=True,
        )
        payload = _build_comfort_debug_payload(reranked, top_n=2)

        self.assertEqual(payload["score_model"], "movie_behavior_first")
        self.assertIn("library", payload["contribution_totals"])
        self.assertIn("recency_phase", payload["contribution_totals"])
        self.assertIn("behavior", payload["contribution_totals"])
        self.assertIn("profile_layer_weights", payload)
        self.assertIn("primary_reason_bucket", payload["top_candidates"][0])
        self.assertIn("library_fit", payload["top_candidates"][0])
        self.assertIn("recency_phase_fit", payload["top_candidates"][0])
        self.assertIn("ready_now", payload["contribution_totals"])
        self.assertIn("ready_now_score", payload["top_candidates"][0])
        self.assertIn("cooldown_penalty", payload["top_candidates"][0])
        self.assertIn("burst_replay_allowance", payload["top_candidates"][0])
        self.assertIn("recent_play_count_90d", payload["top_candidates"][0])
        self.assertIn("recent_play_count_180d", payload["top_candidates"][0])
        self.assertIn("recent_gap_median_days", payload["top_candidates"][0])
        self.assertIn("title_saturation_penalty", payload["top_candidates"][0])
        self.assertIn("saturation_multiplier", payload["top_candidates"][0])
        self.assertIn("active_signal_families", payload["top_candidates"][0])
        self.assertIn("suppressed_signal_families", payload["top_candidates"][0])
        self.assertIn("world_quality", payload["top_candidates"][0])
        self.assertIn("tmdb_world_quality", payload["top_candidates"][0])
        self.assertIn("trakt_world_quality", payload["top_candidates"][0])
        self.assertIn("world_source_blend", payload["top_candidates"][0])
        self.assertIn("legacy_rank", payload["top_candidates"][0])
        self.assertIn("rank_delta", payload["top_candidates"][0])
        self.assertIn("legacy_raw_final_score", payload["top_candidates"][0])
        self.assertIn("world_alignment_sample_size", payload["top_candidates"][0])
        self.assertIn("comparison_summary", payload)

    def test_movie_comfort_debug_template_uses_clarified_time_labels(self):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="render-debug",
                title="Render Debug",
                genres=["Animation"],
                keywords=["family"],
                studios=["pixar"],
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                popularity=80.0,
                rating_count=5000,
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 120.0,
                    "rewatch_count": 2.0,
                },
            ),
        ]
        reranked = _apply_comfort_confidence(
            candidates,
            {
                "phase_keyword_affinity": {"family": 1.0},
                "recent_keyword_affinity": {"family": 0.9},
                "phase_studio_affinity": {"pixar": 1.0},
                "recent_studio_affinity": {"pixar": 0.9},
                "phase_certification_affinity": {"PG": 1.0},
                "recent_certification_affinity": {"PG": 0.9},
                "phase_runtime_bucket_affinity": {"90_109": 1.0},
                "recent_runtime_bucket_affinity": {"90_109": 0.9},
                "phase_decade_affinity": {"2020s": 0.8},
                "recent_decade_affinity": {"2020s": 0.8},
                "comfort_library_affinity": {
                    "keywords": {"family": 1.0},
                    "collections": {},
                    "studios": {"pixar": 1.0},
                    "genres": {"animation": 0.9},
                    "directors": {},
                    "lead_cast": {},
                    "certifications": {"PG": 1.0},
                    "runtime_buckets": {"90_109": 1.0},
                    "decades": {"2020s": 0.8},
                },
                "comfort_rewatch_affinity": {
                    "keywords": {"family": 1.0},
                    "collections": {},
                    "studios": {"pixar": 1.0},
                    "genres": {"animation": 0.9},
                    "directors": {},
                    "lead_cast": {},
                    "certifications": {"PG": 1.0},
                    "runtime_buckets": {"90_109": 1.0},
                    "decades": {"2020s": 0.8},
                },
            },
            use_movie_rewatch_model=True,
        )
        row = RowResult(
            key="comfort_rewatches",
            title="Comfort Rewatches",
            mission="Comfort",
            why="Past favorites that feel ready to revisit again.",
            source="local",
            items=[],
            debug_payload=_build_comfort_debug_payload(reranked, top_n=1),
        )

        content = render_to_string(
            "app/components/discover_row.html",
            {
                "row": row,
                "discover_debug": True,
                "show_more": False,
                "selected_media_type": MediaTypes.MOVIE.value,
                "user": self.user,
            },
        )

        self.assertIn("last-watch-days", content)
        self.assertIn("median-repeat-gap", content)
        self.assertIn("recent-90", content)
        self.assertNotIn("title-days", content)

    @patch("app.discover.movie_comfort._is_holiday_window", new=lambda today=None: False)
    @patch("app.discover.comfort_scoring._is_holiday_window", return_value=False)
    def test_movie_behavior_first_applies_keyword_holiday_penalty_out_of_season(
        self, _mock_window
    ):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="keyword-holiday",
                title="Winter Whodunit",
                keywords=["christmas", "whodunit"],
                studios=["pixar"],
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                popularity=90.0,
                rating_count=9000,
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 220.0,
                    "rewatch_count": 1.0,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="neutral",
                title="Anytime Whodunit",
                keywords=["whodunit"],
                studios=["pixar"],
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                popularity=80.0,
                rating_count=8000,
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 220.0,
                    "rewatch_count": 1.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            {
                "phase_keyword_affinity": {"christmas": 1.0, "whodunit": 0.9},
                "recent_keyword_affinity": {"christmas": 0.9, "whodunit": 0.8},
                "phase_studio_affinity": {"pixar": 1.0},
                "recent_studio_affinity": {"pixar": 0.9},
                "phase_certification_affinity": {"pg": 1.0},
                "recent_certification_affinity": {"pg": 0.9},
                "phase_runtime_bucket_affinity": {"90_109": 1.0},
                "recent_runtime_bucket_affinity": {"90_109": 0.9},
                "phase_decade_affinity": {"2020s": 0.8},
                "recent_decade_affinity": {"2020s": 0.8},
                "comfort_library_affinity": {
                    "keywords": {"christmas": 1.0, "whodunit": 0.95},
                    "collections": {},
                    "studios": {"pixar": 1.0},
                    "genres": {},
                    "directors": {},
                    "lead_cast": {},
                    "certifications": {"PG": 1.0},
                    "runtime_buckets": {"90_109": 1.0},
                    "decades": {"2020s": 0.9},
                },
                "comfort_rewatch_affinity": {
                    "keywords": {"christmas": 1.0, "whodunit": 0.95},
                    "collections": {},
                    "studios": {"pixar": 1.0},
                    "genres": {},
                    "directors": {},
                    "lead_cast": {},
                    "certifications": {"PG": 1.0},
                    "runtime_buckets": {"90_109": 1.0},
                    "decades": {"2020s": 0.9},
                },
            },
            use_movie_rewatch_model=True,
        )

        by_id = {candidate.media_id: candidate for candidate in reranked}
        self.assertLess(
            by_id["keyword-holiday"].score_breakdown["seasonal_adjustment"], 0.0
        )
        self.assertEqual(by_id["neutral"].score_breakdown["seasonal_adjustment"], 0.0)
        self.assertLess(
            by_id["keyword-holiday"].final_score, by_id["neutral"].final_score
        )

    @patch("app.discover.movie_comfort._is_holiday_window", new=lambda today=None: False)
    @patch("app.discover.comfort_scoring._is_holiday_window", return_value=False)
    def test_movie_behavior_first_debug_payload_exposes_holiday_penalty(
        self, _mock_window
    ):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="keyword-holiday",
                title="Winter Whodunit",
                keywords=["christmas", "whodunit"],
                studios=["pixar"],
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                popularity=90.0,
                rating_count=9000,
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 220.0,
                    "rewatch_count": 1.0,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="neutral",
                title="Anytime Whodunit",
                keywords=["whodunit"],
                studios=["pixar"],
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                popularity=80.0,
                rating_count=8000,
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 220.0,
                    "rewatch_count": 1.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            {
                "phase_keyword_affinity": {"christmas": 1.0, "whodunit": 0.9},
                "recent_keyword_affinity": {"christmas": 0.9, "whodunit": 0.8},
                "phase_studio_affinity": {"pixar": 1.0},
                "recent_studio_affinity": {"pixar": 0.9},
                "phase_certification_affinity": {"pg": 1.0},
                "recent_certification_affinity": {"pg": 0.9},
                "phase_runtime_bucket_affinity": {"90_109": 1.0},
                "recent_runtime_bucket_affinity": {"90_109": 0.9},
                "phase_decade_affinity": {"2020s": 0.8},
                "recent_decade_affinity": {"2020s": 0.8},
                "comfort_library_affinity": {
                    "keywords": {"christmas": 1.0, "whodunit": 0.95},
                    "collections": {},
                    "studios": {"pixar": 1.0},
                    "genres": {},
                    "directors": {},
                    "lead_cast": {},
                    "certifications": {"PG": 1.0},
                    "runtime_buckets": {"90_109": 1.0},
                    "decades": {"2020s": 0.9},
                },
                "comfort_rewatch_affinity": {
                    "keywords": {"christmas": 1.0, "whodunit": 0.95},
                    "collections": {},
                    "studios": {"pixar": 1.0},
                    "genres": {},
                    "directors": {},
                    "lead_cast": {},
                    "certifications": {"PG": 1.0},
                    "runtime_buckets": {"90_109": 1.0},
                    "decades": {"2020s": 0.9},
                },
            },
            use_movie_rewatch_model=True,
        )
        payload = _build_comfort_debug_payload(reranked, top_n=2)
        by_id = {
            candidate_payload["media_id"]: candidate_payload
            for candidate_payload in payload["top_candidates"]
        }

        self.assertIn("holiday_strength", by_id["keyword-holiday"])
        self.assertIn("seasonal_adjustment", by_id["keyword-holiday"])
        self.assertLess(by_id["keyword-holiday"]["seasonal_adjustment"], 0.0)
        self.assertGreaterEqual(by_id["keyword-holiday"]["penalty_count"], 1)
        self.assertEqual(by_id["neutral"]["seasonal_adjustment"], 0.0)

    def test_movie_comfort_confidence_softly_cools_recent_exact_title(self):
        zootopia_item = Item.objects.create(
            media_id="cooldown-zootopia",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Zootopia",
            image="http://example.com/zootopia.jpg",
            genres=["Animation", "Adventure", "Comedy"],
            provider_keywords=["Buddy Comedy", "Animal"],
            provider_certification="PG",
            provider_collection_name="Disney Animation",
            runtime_minutes=108,
            release_datetime=timezone.now() - timedelta(days=365 * 8),
            studios=["Walt Disney Animation Studios"],
        )
        encanto_item = Item.objects.create(
            media_id="cooldown-encanto",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Encanto",
            image="http://example.com/encanto.jpg",
            genres=["Animation", "Adventure", "Comedy"],
            provider_keywords=["Family", "Musical"],
            provider_certification="PG",
            provider_collection_name="Disney Animation",
            runtime_minutes=102,
            release_datetime=timezone.now() - timedelta(days=365 * 4),
            studios=["Walt Disney Animation Studios"],
        )

        with patch(
            "app.models.providers.services.get_media_metadata",
            return_value={"max_progress": 1},
        ):
            Movie.objects.create(
                item=zootopia_item,
                user=self.user,
                score=9,
                status=Status.COMPLETED.value,
                end_date=timezone.now() - timedelta(days=4),
            )
            Movie.objects.create(
                item=zootopia_item,
                user=self.user,
                score=9,
                status=Status.COMPLETED.value,
                end_date=timezone.now() - timedelta(days=180),
            )
            Movie.objects.create(
                item=encanto_item,
                user=self.user,
                score=9,
                status=Status.COMPLETED.value,
                end_date=timezone.now() - timedelta(days=70),
            )

        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="cooldown-zootopia",
                title="Zootopia",
                genres=["Animation", "Adventure", "Comedy"],
                keywords=["buddy comedy", "animal"],
                studios=["walt disney animation studios"],
                collection_name="Disney Animation",
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2010s",
                popularity=90.0,
                rating_count=12000,
                score_breakdown={
                    "user_score": 9.0,
                    "days_since_activity": 4.0,
                    "rewatch_count": 2.0,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="cooldown-encanto",
                title="Encanto",
                genres=["Animation", "Adventure", "Comedy"],
                keywords=["family", "musical"],
                studios=["walt disney animation studios"],
                collection_name="Disney Animation",
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                popularity=88.0,
                rating_count=11000,
                score_breakdown={
                    "user_score": 9.0,
                    "days_since_activity": 70.0,
                    "rewatch_count": 1.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            {
                "phase_keyword_affinity": {
                    "family": 1.0,
                    "musical": 0.9,
                    "animal": 0.8,
                },
                "recent_keyword_affinity": {"family": 1.0, "musical": 0.9},
                "phase_studio_affinity": {"walt disney animation studios": 1.0},
                "recent_studio_affinity": {"walt disney animation studios": 1.0},
                "phase_collection_affinity": {"disney animation": 1.0},
                "recent_collection_affinity": {"disney animation": 0.9},
                "phase_certification_affinity": {"PG": 1.0},
                "recent_certification_affinity": {"PG": 1.0},
                "phase_runtime_bucket_affinity": {"90_109": 1.0},
                "recent_runtime_bucket_affinity": {"90_109": 1.0},
                "phase_decade_affinity": {"2010s": 0.8, "2020s": 0.8},
                "recent_decade_affinity": {"2020s": 0.9},
                "phase_genre_affinity": {
                    "animation": 1.0,
                    "adventure": 0.8,
                    "comedy": 0.7,
                },
                "recent_genre_affinity": {
                    "animation": 1.0,
                    "adventure": 0.9,
                    "comedy": 0.8,
                },
                "comfort_library_affinity": {
                    "keywords": {"family": 1.0, "musical": 0.9, "animal": 0.8},
                    "collections": {"disney animation": 1.0},
                    "studios": {"walt disney animation studios": 1.0},
                    "genres": {"animation": 1.0, "adventure": 0.9, "comedy": 0.8},
                    "directors": {},
                    "lead_cast": {},
                    "certifications": {"PG": 1.0},
                    "runtime_buckets": {"90_109": 1.0},
                    "decades": {"2010s": 0.8, "2020s": 0.8},
                },
                "comfort_rewatch_affinity": {
                    "keywords": {"family": 1.0, "animal": 0.9},
                    "collections": {"disney animation": 1.0},
                    "studios": {"walt disney animation studios": 1.0},
                    "genres": {"animation": 1.0, "adventure": 0.8},
                    "directors": {},
                    "lead_cast": {},
                    "certifications": {"PG": 1.0},
                    "runtime_buckets": {"90_109": 1.0},
                    "decades": {"2010s": 0.9},
                },
            },
            use_movie_rewatch_model=True,
            user=self.user,
        )

        self.assertEqual(reranked[0].media_id, "cooldown-encanto")
        zootopia = next(
            candidate
            for candidate in reranked
            if candidate.media_id == "cooldown-zootopia"
        )
        encanto = next(
            candidate
            for candidate in reranked
            if candidate.media_id == "cooldown-encanto"
        )
        self.assertGreater(zootopia.score_breakdown["cooldown_penalty"], 0.0)
        self.assertGreater(
            zootopia.score_breakdown["cooldown_penalty"],
            encanto.score_breakdown["cooldown_penalty"],
        )
        self.assertLess(
            zootopia.score_breakdown["ready_now_score"],
            encanto.score_breakdown["ready_now_score"],
        )

    def test_movie_comfort_confidence_penalizes_bursty_recent_density_once_titles_are_eligible(
        self,
    ):
        bursty_item = Item.objects.create(
            media_id="burst-title",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Bursty Favorite",
            image="http://example.com/bursty.jpg",
            genres=["Animation", "Adventure"],
            provider_keywords=["Family", "Adventure"],
            provider_certification="PG",
            provider_collection_name="Family Comfort",
            runtime_minutes=101,
            release_datetime=timezone.now() - timedelta(days=365 * 3),
            studios=["Disney"],
        )
        steady_item = Item.objects.create(
            media_id="steady-title",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Steady Favorite",
            image="http://example.com/steady.jpg",
            genres=["Animation", "Adventure"],
            provider_keywords=["Family", "Adventure"],
            provider_certification="PG",
            provider_collection_name="Family Comfort",
            runtime_minutes=101,
            release_datetime=timezone.now() - timedelta(days=365 * 3),
            studios=["Disney"],
        )

        with patch(
            "app.models.providers.services.get_media_metadata",
            return_value={"max_progress": 1},
        ):
            for days_ago in (35, 50, 70):
                Movie.objects.create(
                    item=bursty_item,
                    user=self.user,
                    score=8,
                    status=Status.COMPLETED.value,
                    end_date=timezone.now() - timedelta(days=days_ago),
                )
            for days_ago in (35, 170, 320):
                Movie.objects.create(
                    item=steady_item,
                    user=self.user,
                    score=8,
                    status=Status.COMPLETED.value,
                    end_date=timezone.now() - timedelta(days=days_ago),
                )

        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="burst-title",
                title="Bursty Favorite",
                genres=["Animation", "Adventure"],
                keywords=["family", "adventure"],
                studios=["disney"],
                collection_name="Family Comfort",
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                popularity=70.0,
                rating_count=5000,
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 35.0,
                    "rewatch_count": 3.0,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source=Sources.TMDB.value,
                media_id="steady-title",
                title="Steady Favorite",
                genres=["Animation", "Adventure"],
                keywords=["family", "adventure"],
                studios=["disney"],
                collection_name="Family Comfort",
                certification="PG",
                runtime_bucket="90_109",
                release_decade="2020s",
                popularity=70.0,
                rating_count=5000,
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 35.0,
                    "rewatch_count": 3.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            {
                "phase_keyword_affinity": {"family": 1.0, "adventure": 0.8},
                "recent_keyword_affinity": {"family": 1.0, "adventure": 0.8},
                "phase_studio_affinity": {"disney": 1.0},
                "recent_studio_affinity": {"disney": 1.0},
                "phase_collection_affinity": {"family comfort": 1.0},
                "recent_collection_affinity": {"family comfort": 0.9},
                "phase_certification_affinity": {"PG": 1.0},
                "recent_certification_affinity": {"PG": 1.0},
                "phase_runtime_bucket_affinity": {"90_109": 1.0},
                "recent_runtime_bucket_affinity": {"90_109": 1.0},
                "phase_decade_affinity": {"2020s": 1.0},
                "recent_decade_affinity": {"2020s": 1.0},
                "phase_genre_affinity": {"animation": 1.0, "adventure": 0.8},
                "recent_genre_affinity": {"animation": 1.0, "adventure": 0.8},
                "comfort_library_affinity": {
                    "keywords": {"family": 1.0, "adventure": 0.9},
                    "collections": {"family comfort": 1.0},
                    "studios": {"disney": 1.0},
                    "genres": {"animation": 1.0, "adventure": 0.8},
                    "directors": {},
                    "lead_cast": {},
                    "certifications": {"PG": 1.0},
                    "runtime_buckets": {"90_109": 1.0},
                    "decades": {"2020s": 1.0},
                },
                "comfort_rewatch_affinity": {
                    "keywords": {"family": 1.0, "adventure": 0.9},
                    "collections": {"family comfort": 1.0},
                    "studios": {"disney": 1.0},
                    "genres": {"animation": 1.0, "adventure": 0.8},
                    "directors": {},
                    "lead_cast": {},
                    "certifications": {"PG": 1.0},
                    "runtime_buckets": {"90_109": 1.0},
                    "decades": {"2020s": 1.0},
                },
            },
            use_movie_rewatch_model=True,
            user=self.user,
        )

        bursty = next(
            candidate for candidate in reranked if candidate.media_id == "burst-title"
        )
        steady = next(
            candidate for candidate in reranked if candidate.media_id == "steady-title"
        )
        self.assertGreater(
            bursty.score_breakdown["title_saturation_penalty"],
            steady.score_breakdown["title_saturation_penalty"],
        )
        self.assertLess(
            bursty.score_breakdown["saturation_multiplier"],
            steady.score_breakdown["saturation_multiplier"],
        )
        self.assertGreater(
            bursty.score_breakdown["recent_play_count_90d"],
            steady.score_breakdown["recent_play_count_90d"],
        )
        self.assertEqual(bursty.score_breakdown["burst_replay_allowance"], 0.0)
        self.assertEqual(steady.score_breakdown["burst_replay_allowance"], 0.0)
        self.assertLess(
            bursty.final_score,
            steady.final_score,
        )

    def _movie_comfort_disney_profile(self):
        return {
            "phase_keyword_affinity": {"family": 1.0, "musical": 0.9, "animal": 0.8},
            "recent_keyword_affinity": {"family": 1.0, "musical": 0.9},
            "phase_studio_affinity": {"walt disney animation studios": 1.0},
            "recent_studio_affinity": {"walt disney animation studios": 1.0},
            "phase_collection_affinity": {"disney animation": 1.0},
            "recent_collection_affinity": {"disney animation": 0.9},
            "phase_certification_affinity": {"PG": 1.0},
            "recent_certification_affinity": {"PG": 1.0},
            "phase_runtime_bucket_affinity": {"90_109": 1.0},
            "recent_runtime_bucket_affinity": {"90_109": 1.0},
            "phase_decade_affinity": {"2010s": 0.8, "2020s": 0.8},
            "recent_decade_affinity": {"2020s": 0.9},
            "phase_genre_affinity": {"animation": 1.0, "adventure": 0.8, "comedy": 0.7},
            "recent_genre_affinity": {
                "animation": 1.0,
                "adventure": 0.9,
                "comedy": 0.8,
            },
            "comfort_library_affinity": {
                "keywords": {"family": 1.0, "musical": 0.9, "animal": 0.8},
                "collections": {"disney animation": 1.0},
                "studios": {"walt disney animation studios": 1.0},
                "genres": {"animation": 1.0, "adventure": 0.9, "comedy": 0.8},
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"90_109": 1.0},
                "decades": {"2010s": 0.8, "2020s": 0.8},
            },
            "comfort_rewatch_affinity": {
                "keywords": {"family": 1.0, "animal": 0.9},
                "collections": {"disney animation": 1.0},
                "studios": {"walt disney animation studios": 1.0},
                "genres": {"animation": 1.0, "adventure": 0.8},
                "directors": {},
                "lead_cast": {},
                "certifications": {"PG": 1.0},
                "runtime_buckets": {"90_109": 1.0},
                "decades": {"2010s": 0.9},
            },
        }

    def _movie_comfort_candidate(self, media_id, title, *, row_key, **overrides):
        payload = {
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "media_id": media_id,
            "title": title,
            "genres": ["Animation", "Adventure", "Comedy"],
            "keywords": ["family", "animal"],
            "studios": ["walt disney animation studios"],
            "collection_name": "Disney Animation",
            "certification": "PG",
            "runtime_bucket": "90_109",
            "release_decade": "2010s",
            "popularity": 90.0,
            "rating_count": 12000,
            "row_key": row_key,
            "score_breakdown": {
                "user_score": 9.0,
                "days_since_activity": 100.0,
                "rewatch_count": 1.0,
            },
        }
        payload.update(overrides)
        return CandidateItem(**payload)

    def _create_movie_watches(self, item, days_ago_list, score=9):
        with patch(
            "app.models.providers.services.get_media_metadata",
            return_value={"max_progress": 1},
        ):
            for days_ago in days_ago_list:
                Movie.objects.create(
                    item=item,
                    user=self.user,
                    score=score,
                    status=Status.COMPLETED.value,
                    end_date=timezone.now() - timedelta(days=days_ago),
                )

    def _create_movie_item(self, media_id, title, **overrides):
        payload = {
            "media_id": media_id,
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "title": title,
            "image": f"http://example.com/{media_id}.jpg",
            "genres": ["Animation", "Adventure", "Comedy"],
            "provider_keywords": ["Family", "Animal"],
            "provider_certification": "PG",
            "provider_collection_name": "Disney Animation",
            "runtime_minutes": 100,
            "release_datetime": timezone.now() - timedelta(days=365 * 8),
            "studios": ["Walt Disney Animation Studios"],
        }
        payload.update(overrides)
        return Item.objects.create(**payload)

    def test_comfort_rewatches_gates_titles_watched_within_90_days(self):
        rotation_item = self._create_movie_item("gate-rotation", "Rotation Favorite")
        absent_item = self._create_movie_item("gate-absent", "Absent Favorite")
        self._create_movie_watches(rotation_item, [48, 93, 138])
        self._create_movie_watches(absent_item, [400])

        candidates = [
            self._movie_comfort_candidate(
                "gate-rotation",
                "Rotation Favorite",
                row_key="comfort_rewatches",
                score_breakdown={
                    "user_score": 9.0,
                    "days_since_activity": 48.0,
                    "rewatch_count": 3.0,
                },
            ),
            self._movie_comfort_candidate(
                "gate-absent",
                "Absent Favorite",
                row_key="comfort_rewatches",
                keywords=["family"],
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 400.0,
                    "rewatch_count": 1.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            self._movie_comfort_disney_profile(),
            use_movie_rewatch_model=True,
            user=self.user,
        )

        rotation = next(c for c in reranked if c.media_id == "gate-rotation")
        absent = next(c for c in reranked if c.media_id == "gate-absent")
        self.assertEqual(rotation.score_breakdown["absence_gate_active"], 1.0)
        self.assertEqual(absent.score_breakdown["absence_gate_active"], 0.0)
        self.assertGreater(rotation.score_breakdown["cooldown_window_days"], 90.0)
        self.assertLess(rotation.score_breakdown["ready_now_score"], 0.5)
        # The weaker-affinity but long-absent title outranks the gated
        # rotation title.
        self.assertEqual(reranked[0].media_id, "gate-absent")

    def test_comfort_rewatches_absence_boost_prefers_long_unseen_favorite(self):
        long_item = self._create_movie_item("boost-long", "Long Unseen Favorite")
        short_item = self._create_movie_item("boost-short", "Recent Favorite")
        self._create_movie_watches(long_item, [2251])
        self._create_movie_watches(short_item, [100, 250, 400])

        candidates = [
            self._movie_comfort_candidate(
                "boost-short",
                "Recent Favorite",
                row_key="comfort_rewatches",
                score_breakdown={
                    "user_score": 9.0,
                    "days_since_activity": 100.0,
                    "rewatch_count": 3.0,
                },
            ),
            self._movie_comfort_candidate(
                "boost-long",
                "Long Unseen Favorite",
                row_key="comfort_rewatches",
                score_breakdown={
                    "user_score": 9.0,
                    "days_since_activity": 2251.0,
                    "rewatch_count": 1.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            self._movie_comfort_disney_profile(),
            use_movie_rewatch_model=True,
            user=self.user,
        )

        long_unseen = next(c for c in reranked if c.media_id == "boost-long")
        recent = next(c for c in reranked if c.media_id == "boost-short")
        self.assertGreater(long_unseen.score_breakdown["absence_boost"], 0.10)
        self.assertLess(recent.score_breakdown["absence_boost"], 0.02)
        self.assertEqual(reranked[0].media_id, "boost-long")
        # Legacy (frozen) path preferred the recent favorite via its stronger
        # rewatch strength; the absence boost flips the current ordering.
        self.assertLess(
            recent.score_breakdown["legacy_rank"],
            long_unseen.score_breakdown["legacy_rank"],
        )

    def test_comfort_rewatches_absence_boost_does_not_promote_weak_fit_titles(self):
        weak_item = self._create_movie_item(
            "weak-ancient",
            "Ancient Weak Fit",
            genres=["Western"],
            provider_keywords=["Desert"],
            provider_certification="R",
            provider_collection_name="",
            studios=["Unknown Pictures"],
        )
        strong_item = self._create_movie_item(
            "strong-moderate", "Moderately Absent Favorite"
        )
        self._create_movie_watches(weak_item, [1500], score=8)
        self._create_movie_watches(strong_item, [120])

        candidates = [
            self._movie_comfort_candidate(
                "weak-ancient",
                "Ancient Weak Fit",
                row_key="comfort_rewatches",
                genres=["Western"],
                keywords=["desert"],
                studios=["unknown pictures"],
                collection_name=None,
                certification="R",
                runtime_bucket="130_plus",
                release_decade="1970s",
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 1500.0,
                    "rewatch_count": 1.0,
                },
            ),
            self._movie_comfort_candidate(
                "strong-moderate",
                "Moderately Absent Favorite",
                row_key="comfort_rewatches",
                score_breakdown={
                    "user_score": 9.0,
                    "days_since_activity": 120.0,
                    "rewatch_count": 1.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            self._movie_comfort_disney_profile(),
            use_movie_rewatch_model=True,
            user=self.user,
        )

        # Absence breaks ties; it must not outweigh a materially stronger fit.
        self.assertEqual(reranked[0].media_id, "strong-moderate")
        weak = next(c for c in reranked if c.media_id == "weak-ancient")
        strong = next(c for c in reranked if c.media_id == "strong-moderate")
        self.assertGreater(
            weak.score_breakdown["absence_norm"],
            strong.score_breakdown["absence_norm"],
        )
        self.assertLess(
            weak.score_breakdown["absence_boost"],
            0.15,
        )

    def test_comfort_rewatches_rotation_lengthens_cooldown_and_attenuates_rewatch_strength(
        self,
    ):
        bursty_item = self._create_movie_item("rotation-bursty", "Household Rotation")
        steady_item = self._create_movie_item("rotation-steady", "Occasional Favorite")
        self._create_movie_watches(bursty_item, [100, 114, 128, 142, 156])
        self._create_movie_watches(steady_item, [100, 220])

        candidates = [
            self._movie_comfort_candidate(
                "rotation-bursty",
                "Household Rotation",
                row_key="comfort_rewatches",
                score_breakdown={
                    "user_score": 9.0,
                    "days_since_activity": 100.0,
                    "rewatch_count": 5.0,
                },
            ),
            self._movie_comfort_candidate(
                "rotation-steady",
                "Occasional Favorite",
                row_key="comfort_rewatches",
                score_breakdown={
                    "user_score": 9.0,
                    "days_since_activity": 100.0,
                    "rewatch_count": 2.0,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            self._movie_comfort_disney_profile(),
            use_movie_rewatch_model=True,
            user=self.user,
        )

        bursty = next(c for c in reranked if c.media_id == "rotation-bursty")
        steady = next(c for c in reranked if c.media_id == "rotation-steady")
        self.assertGreater(bursty.score_breakdown["rotation_pressure"], 0.5)
        self.assertGreater(bursty.score_breakdown["cooldown_window_days"], 120.0)
        self.assertLess(
            bursty.score_breakdown["rewatch_strength"],
            bursty.score_breakdown["rewatch_strength_raw"],
        )
        self.assertEqual(steady.score_breakdown["rotation_pressure"], 0.0)
        self.assertEqual(
            steady.score_breakdown["rewatch_strength"],
            steady.score_breakdown["rewatch_strength_raw"],
        )

    def test_top_picks_new_titles_drop_constant_padding_and_decompress_spread(self):
        def build(row_key):
            return [
                self._movie_comfort_candidate(
                    "pick-strong",
                    "Strong Match",
                    row_key=row_key,
                    score_breakdown={"planning_entry": 1.0},
                ),
                self._movie_comfort_candidate(
                    "pick-medium",
                    "Medium Match",
                    row_key=row_key,
                    keywords=["family"],
                    collection_name=None,
                    score_breakdown={"planning_entry": 1.0},
                ),
                self._movie_comfort_candidate(
                    "pick-weak",
                    "Weak Match",
                    row_key=row_key,
                    genres=["Drama"],
                    keywords=["quiet"],
                    studios=["indie films"],
                    collection_name=None,
                    certification="R",
                    runtime_bucket="130_plus",
                    release_decade="1990s",
                    score_breakdown={"planning_entry": 1.0},
                ),
            ]

        profile = self._movie_comfort_disney_profile()
        top_picks = _apply_comfort_confidence(
            build("top_picks_for_you"),
            profile,
            use_movie_rewatch_model=True,
            user=self.user,
        )
        baseline = _apply_comfort_confidence(
            build(None),
            profile,
            use_movie_rewatch_model=True,
            user=self.user,
        )

        for candidate in top_picks:
            score = candidate.score_breakdown
            self.assertEqual(score["core_weight_profile"], "top_picks_new")
            self.assertEqual(score["ready_now_contribution"], 0.0)
            self.assertEqual(score["behavior_contribution"], 0.0)
            self.assertLessEqual(score["planning_confidence_bonus"], 0.10)

        def raw_spread(items):
            raws = [float(c.score_breakdown["raw_final_score"]) for c in items]
            return max(raws) - min(raws)

        self.assertGreater(raw_spread(top_picks), raw_spread(baseline))
        for candidate in baseline:
            self.assertEqual(
                candidate.score_breakdown["core_weight_profile"],
                "comfort_default",
            )

    def test_top_picks_upcoming_release_gated_to_zero(self):
        upcoming = self._movie_comfort_candidate(
            "pick-upcoming",
            "Upcoming Release",
            row_key="top_picks_for_you",
            release_date=(timezone.now() + timedelta(days=90)).strftime("%Y-%m-%d"),
            score_breakdown={"planning_entry": 1.0},
        )
        released = self._movie_comfort_candidate(
            "pick-released",
            "Released Title",
            row_key="top_picks_for_you",
            score_breakdown={"planning_entry": 1.0},
        )

        reranked = _apply_comfort_confidence(
            [upcoming, released],
            self._movie_comfort_disney_profile(),
            use_movie_rewatch_model=True,
            user=self.user,
        )

        upcoming_result = next(c for c in reranked if c.media_id == "pick-upcoming")
        self.assertEqual(upcoming_result.score_breakdown["release_status"], "upcoming")
        self.assertEqual(upcoming_result.score_breakdown["raw_final_score"], 0.0)
        self.assertEqual(reranked[0].media_id, "pick-released")

    def test_quality_score_uses_per_family_alignment_offsets(self):
        profile = self._movie_comfort_disney_profile()
        profile["world_rating_profile"] = {
            "alignment": 0.5,
            "confidence": 1.0,
            "sample_size": 20,
        }
        profile["world_alignment_offsets"] = {
            "genres": {"drama": -0.09, "animation": 0.07},
        }
        profile["world_alignment_offset_samples"] = {
            "genres": {"drama": 6, "animation": 9},
        }

        shared_ratings = {
            "planning_entry": 1.0,
            "provider_rating": 7.5,
            "provider_rating_count": 5000,
        }
        drama = self._movie_comfort_candidate(
            "align-drama",
            "Acclaimed Drama",
            row_key="top_picks_for_you",
            genres=["Drama"],
            score_breakdown=dict(shared_ratings),
        )
        animation = self._movie_comfort_candidate(
            "align-animation",
            "Beloved Animation",
            row_key="top_picks_for_you",
            genres=["Animation"],
            score_breakdown=dict(shared_ratings),
        )

        reranked = _apply_comfort_confidence(
            [drama, animation],
            profile,
            use_movie_rewatch_model=True,
            user=self.user,
        )

        drama_result = next(c for c in reranked if c.media_id == "align-drama")
        animation_result = next(c for c in reranked if c.media_id == "align-animation")
        self.assertLess(
            drama_result.score_breakdown["personal_alignment_offset"],
            0.0,
        )
        self.assertGreater(
            animation_result.score_breakdown["personal_alignment_offset"],
            0.0,
        )
        # Same world rating, but personal divergence shifts the quality blend.
        self.assertGreater(
            animation_result.score_breakdown["quality_score"],
            drama_result.score_breakdown["quality_score"],
        )
        self.assertTrue(
            animation_result.score_breakdown["alignment_offset_families"],
        )

    def test_unrated_rewatched_candidate_gets_implicit_rating_confidence(self):
        rewatched_item = self._create_movie_item(
            "implicit-rewatched", "Unrated Favorite"
        )
        self._create_movie_watches(rewatched_item, [200, 500, 800])
        rated_item = self._create_movie_item("explicit-rated", "Rated Favorite")
        self._create_movie_watches(rated_item, [200])

        unrated = self._movie_comfort_candidate(
            "implicit-rewatched",
            "Unrated Favorite",
            row_key="comfort_rewatches",
            score_breakdown={
                "days_since_activity": 200.0,
                "rewatch_count": 3.0,
                "fast_completion": 1.0,
            },
        )
        rated = self._movie_comfort_candidate(
            "explicit-rated",
            "Rated Favorite",
            row_key="comfort_rewatches",
            score_breakdown={
                "user_score": 9.0,
                "days_since_activity": 200.0,
                "rewatch_count": 1.0,
            },
        )

        reranked = _apply_comfort_confidence(
            [unrated, rated],
            self._movie_comfort_disney_profile(),
            use_movie_rewatch_model=True,
            user=self.user,
        )

        unrated_result = next(c for c in reranked if c.media_id == "implicit-rewatched")
        rated_result = next(c for c in reranked if c.media_id == "explicit-rated")
        self.assertEqual(
            unrated_result.score_breakdown["rating_confidence_source"],
            "implicit",
        )
        self.assertAlmostEqual(
            unrated_result.score_breakdown["rating_confidence"],
            0.65,
            places=6,
        )
        self.assertEqual(
            rated_result.score_breakdown["rating_confidence_source"],
            "explicit",
        )

    def test_avoidance_prior_is_overridden_by_strong_rewatch_evidence(self):
        profile = self._movie_comfort_disney_profile()
        profile["avoidance_offsets"] = {
            "genres": {"drama": -0.05},
        }
        profile["avoidance_baselines"] = {
            "genres": {
                "drama": {
                    "expected_share": 0.05,
                    "observed_share": 0.01,
                    "samples": 3.0,
                },
            },
        }

        weak_item = self._create_movie_item("avoid-weak", "Single Watch")
        self._create_movie_watches(weak_item, [200])
        strong_item = self._create_movie_item("avoid-strong", "Rewatched Favorite")
        self._create_movie_watches(strong_item, [200, 500, 800])

        weak = self._movie_comfort_candidate(
            "avoid-weak",
            "Single Watch",
            row_key="comfort_rewatches",
            keywords=["quiet"],
            collection_name=None,
            genres=["Drama"],
            studios=["unknown pictures"],
            score_breakdown={
                "user_score": 8.0,
                "days_since_activity": 200.0,
                "rewatch_count": 1.0,
            },
        )
        strong = self._movie_comfort_candidate(
            "avoid-strong",
            "Rewatched Favorite",
            row_key="comfort_rewatches",
            genres=["Animation", "Drama"],
            score_breakdown={
                "user_score": 9.0,
                "days_since_activity": 200.0,
                "rewatch_count": 3.0,
            },
        )

        reranked = _apply_comfort_confidence(
            [weak, strong],
            profile,
            use_movie_rewatch_model=True,
            user=self.user,
        )

        weak_result = next(c for c in reranked if c.media_id == "avoid-weak")
        strong_result = next(c for c in reranked if c.media_id == "avoid-strong")
        self.assertLess(weak_result.score_breakdown["applied_avoidance"], 0.0)
        self.assertLess(weak_result.score_breakdown["avoidance_prior"], 0.0)
        # Strong explicit evidence (rewatch strength >= 0.40) zeroes the prior.
        self.assertEqual(strong_result.score_breakdown["applied_avoidance"], 0.0)
        self.assertTrue(weak_result.score_breakdown["avoidance_families"])

    def test_movie_comfort_debug_payload_exposes_taste_signal_fields(self):
        from app.discover.comfort_scoring import _build_comfort_debug_payload

        item = self._create_movie_item("debug-taste", "Taste Signal Movie")
        self._create_movie_watches(item, [1500])
        profile = self._movie_comfort_disney_profile()
        profile["world_alignment_offsets"] = {"genres": {"animation": 0.07}}
        profile["world_alignment_offset_samples"] = {"genres": {"animation": 5}}
        profile["avoidance_offsets"] = {"studios": {"sony pictures": -0.03}}
        profile["avoidance_baselines"] = {
            "studios": {
                "sony pictures": {
                    "expected_share": 0.04,
                    "observed_share": 0.01,
                    "samples": 4.0,
                },
            },
        }

        candidates = [
            self._movie_comfort_candidate(
                "debug-taste",
                "Taste Signal Movie",
                row_key="comfort_rewatches",
                score_breakdown={
                    "user_score": 9.0,
                    "days_since_activity": 1500.0,
                    "rewatch_count": 1.0,
                },
            ),
        ]
        reranked = _apply_comfort_confidence(
            candidates,
            profile,
            use_movie_rewatch_model=True,
            user=self.user,
        )
        payload = _build_comfort_debug_payload(
            reranked,
            top_n=12,
            profile_payload=profile,
        )

        entry = payload["top_candidates"][0]
        for key in (
            "personal_alignment_offset",
            "alignment_offset_families",
            "avoidance_prior",
            "applied_avoidance",
            "avoidance_families",
            "rating_confidence_source",
            "primary_reason_kind",
        ):
            self.assertIn(key, entry)
        self.assertIn("avoidance", payload["contribution_totals"])
        self.assertIn("profile_signals", payload)
        self.assertEqual(
            payload["profile_signals"]["alignment_offset_counts"],
            {"genres": 1},
        )
        self.assertEqual(entry["primary_reason_kind"], "long_unseen_favorite")

    def _provider_candidate(self, media_id, title, *, pool_source=None, **overrides):
        payload = {
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "media_id": media_id,
            "title": title,
            "genres": ["Animation", "Comedy"],
            "release_date": "2019-06-01",
            "popularity": 70.0,
            "rating": 7.5,
            "rating_count": 4000,
            "row_key": "top_picks_for_you",
        }
        payload.update(overrides)
        candidate = CandidateItem(**payload)
        if pool_source:
            candidate.score_breakdown["pool_source"] = pool_source
        return candidate

    @patch("app.discover.service.TMDB_ADAPTER.genre_discovery")
    @patch("app.discover.service.TMDB_ADAPTER.related")
    @patch("app.discover.service._select_top_picks_anchors")
    @patch("app.discover.service._planning_candidates")
    def test_top_picks_blends_provider_sources_with_quotas(
        self,
        mock_planning,
        mock_anchors,
        mock_related,
        mock_genre_discovery,
    ):
        mock_planning.return_value = [
            self._movie_comfort_candidate(
                f"plan-{index}",
                f"Planning {index}",
                row_key="top_picks_for_you",
                score_breakdown={"planning_entry": 1.0},
            )
            for index in range(10)
        ]
        anchor_item = SimpleNamespace(media_id="anchor-1", title="Anchor Favorite")
        mock_anchors.return_value = [SimpleNamespace(item=anchor_item)]
        mock_related.return_value = [
            self._provider_candidate(f"related-{index}", f"Related {index}")
            for index in range(6)
        ]
        mock_genre_discovery.return_value = [
            self._provider_candidate(f"discovery-{index}", f"Discovery {index}")
            for index in range(6)
        ]

        profile = self._movie_comfort_disney_profile()
        candidates = _top_picks_candidates(
            self.user,
            MediaTypes.MOVIE.value,
            "top_picks_for_you",
            profile,
        )

        visible = candidates[:12]
        planning_visible = [
            c for c in visible if c.score_breakdown.get("pool_source") == "planning"
        ]
        provider_visible = [
            c for c in visible if c.score_breakdown.get("pool_source") != "planning"
        ]
        self.assertLessEqual(len(planning_visible), 6)
        self.assertGreaterEqual(len(provider_visible), 4)
        for candidate in provider_visible:
            self.assertIn(
                candidate.score_breakdown["pool_source"],
                {"similar_to_favorite", "taste_discovery"},
            )
            self.assertTrue(candidate.source_reason)
        related_visible = [
            c
            for c in candidates
            if c.score_breakdown.get("pool_source") == "similar_to_favorite"
        ]
        self.assertTrue(
            all("Anchor Favorite" in (c.source_reason or "") for c in related_visible),
        )
        self.assertTrue(
            all(c.score_breakdown.get("source_quota_action") for c in candidates),
        )

    def test_apply_top_picks_source_quotas_relaxes_when_provider_thin(self):
        candidates = [
            self._provider_candidate(
                f"plan-{index}",
                f"Plan {index}",
                pool_source="planning",
            )
            for index in range(14)
        ]
        candidates.append(
            self._provider_candidate(
                "prov-1",
                "Provider One",
                pool_source="similar_to_favorite",
            ),
        )

        _apply_top_picks_source_quotas(candidates)

        visible = candidates[:12]
        provider_visible = [
            c for c in visible if c.score_breakdown.get("pool_source") != "planning"
        ]
        self.assertEqual(len(provider_visible), 1)
        self.assertEqual(len(visible), 12)
        self.assertTrue(
            any(
                c.score_breakdown.get("source_quota_action") == "relaxed_fill"
                for c in visible
            ),
        )

    @patch("app.discover.service.TMDB_ADAPTER.genre_discovery")
    @patch("app.discover.service.TMDB_ADAPTER.related")
    @patch("app.discover.service._select_top_picks_anchors")
    @patch("app.discover.service._planning_candidates")
    def test_top_picks_excludes_watched_and_hidden_provider_candidates(
        self,
        mock_planning,
        mock_anchors,
        mock_related,
        mock_genre_discovery,
    ):
        watched_item = self._create_movie_item("prov-watched", "Watched Provider")
        self._create_movie_watches(watched_item, [200])
        hidden_item = self._create_movie_item("prov-hidden", "Hidden Provider")
        DiscoverFeedback.objects.create(
            user=self.user,
            item=hidden_item,
            feedback_type=DiscoverFeedbackType.NOT_INTERESTED.value,
        )

        mock_planning.return_value = []
        anchor_item = SimpleNamespace(media_id="anchor-1", title="Anchor Favorite")
        mock_anchors.return_value = [SimpleNamespace(item=anchor_item)]
        mock_related.return_value = [
            self._provider_candidate("prov-watched", "Watched Provider"),
            self._provider_candidate("prov-hidden", "Hidden Provider"),
            self._provider_candidate("prov-fresh", "Fresh Provider"),
        ]
        mock_genre_discovery.return_value = []

        candidates = _top_picks_candidates(
            self.user,
            MediaTypes.MOVIE.value,
            "top_picks_for_you",
            self._movie_comfort_disney_profile(),
        )

        media_ids = [candidate.media_id for candidate in candidates]
        self.assertIn("prov-fresh", media_ids)
        self.assertNotIn("prov-watched", media_ids)
        self.assertNotIn("prov-hidden", media_ids)

    def test_hydrate_movie_candidates_from_items(self):
        from app.discover.entry_candidates import (
            _hydrate_movie_candidates_from_items,
        )

        item = self._create_movie_item(
            "hydrate-known",
            "Known Movie",
            provider_keywords=["Family", "Adventure"],
            provider_certification="PG",
            provider_collection_name="Known Collection",
            runtime_minutes=104,
            provider_popularity=88.0,
            provider_rating=7.7,
            provider_rating_count=9000,
        )
        known = CandidateItem(
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
            media_id="hydrate-known",
            title="Known Movie",
            genres=["Animation"],
            row_key="top_picks_for_you",
        )
        unknown = CandidateItem(
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
            media_id="hydrate-unknown",
            title="Unknown Movie",
            genres=["Animation"],
            release_date="1994-06-01",
            row_key="top_picks_for_you",
        )

        _hydrate_movie_candidates_from_items([known, unknown])

        self.assertEqual(known.score_breakdown["hydrated_from_item"], 1.0)
        self.assertIn("family", known.keywords)
        self.assertTrue(known.studios)
        self.assertEqual(known.certification, "PG")
        self.assertEqual(known.runtime_bucket, "90_109")
        self.assertTrue(known.release_decade)
        self.assertEqual(known.collection_name, "Known Collection")
        self.assertEqual(
            known.score_breakdown["provider_rating"],
            7.7,
        )
        self.assertEqual(unknown.score_breakdown["hydrated_from_item"], 0.0)
        self.assertEqual(unknown.release_decade, "1990s")

    def test_top_picks_sparse_provider_fairness_and_genre_floor(self):
        enriched = self._movie_comfort_candidate(
            "fair-planning",
            "Enriched Planning",
            row_key="top_picks_for_you",
            score_breakdown={"planning_entry": 1.0, "pool_source": "planning"},
        )
        # Anchor-sourced ("because you watched X") candidates get NO special
        # exemption from the genre-fit floor: a weak-fit anchor guess is
        # filtered exactly like a weak-fit discovery guess.
        sparse_anchor_weak = self._provider_candidate(
            "fair-anchor-weak",
            "Sparse Weak Anchor Pick",
            pool_source="similar_to_favorite",
            genres=["Western"],
            release_decade="1970s",
        )
        sparse_anchor_fit = self._provider_candidate(
            "fair-anchor-fit",
            "Sparse Fitting Anchor Pick",
            pool_source="similar_to_favorite",
            genres=["Animation", "Comedy"],
            release_decade="2010s",
        )
        sparse_discovery_weak = self._provider_candidate(
            "fair-discovery-weak",
            "Sparse Weak Discovery",
            pool_source="taste_discovery",
            genres=["Western"],
            release_decade="1970s",
        )
        sparse_discovery_fit = self._provider_candidate(
            "fair-discovery-fit",
            "Sparse Fitting Discovery",
            pool_source="taste_discovery",
            genres=["Animation", "Comedy"],
            release_decade="2010s",
        )

        reranked = _apply_comfort_confidence(
            [
                enriched,
                sparse_anchor_weak,
                sparse_anchor_fit,
                sparse_discovery_weak,
                sparse_discovery_fit,
            ],
            self._movie_comfort_disney_profile(),
            use_movie_rewatch_model=True,
            user=self.user,
        )

        media_ids = [candidate.media_id for candidate in reranked]
        self.assertIn("fair-anchor-fit", media_ids)
        self.assertIn("fair-discovery-fit", media_ids)
        self.assertNotIn("fair-anchor-weak", media_ids)
        self.assertNotIn("fair-discovery-weak", media_ids)
        self.assertEqual(
            sparse_anchor_weak.score_breakdown.get(
                "filtered_provider_weak_genre_fit",
            ),
            1.0,
        )
        self.assertEqual(
            sparse_discovery_weak.score_breakdown.get(
                "filtered_provider_weak_genre_fit",
            ),
            1.0,
        )
        anchor_result = next(c for c in reranked if c.media_id == "fair-anchor-fit")
        enriched_result = next(c for c in reranked if c.media_id == "fair-planning")
        self.assertAlmostEqual(
            anchor_result.score_breakdown["coverage_renorm_factor"],
            1.0 / 0.55,
            places=4,
        )
        self.assertEqual(
            enriched_result.score_breakdown["coverage_renorm_factor"],
            1.0,
        )

    def test_top_picks_discovery_requires_target_genre_to_be_primary(self):
        # Comedy was the query target, but this candidate's own primary
        # (first-listed) TMDB genre is Action — Comedy only trails as a
        # footnote co-tag. Must be filtered even though "comedy" appears
        # somewhere in its genre list (this is the Naruto-class mismatch).
        secondary_mismatch = self._provider_candidate(
            "genre-mismatch",
            "Action Anime With Comedy Footnote",
            pool_source="taste_discovery",
            genres=["Action", "Adventure", "Comedy"],
            score_breakdown={
                "pool_source": "taste_discovery",
                "taste_discovery_target_genres": ["comedy", "family"],
            },
        )
        genuine_match = self._provider_candidate(
            "genre-match",
            "Genuine Comedy Pick",
            pool_source="taste_discovery",
            genres=["Comedy", "Family"],
            score_breakdown={
                "pool_source": "taste_discovery",
                "taste_discovery_target_genres": ["comedy", "family"],
            },
        )

        reranked = _apply_comfort_confidence(
            [secondary_mismatch, genuine_match],
            self._movie_comfort_disney_profile(),
            use_movie_rewatch_model=True,
            user=self.user,
        )

        media_ids = [candidate.media_id for candidate in reranked]
        self.assertNotIn("genre-mismatch", media_ids)
        self.assertIn("genre-match", media_ids)
        self.assertEqual(
            secondary_mismatch.score_breakdown.get(
                "filtered_provider_secondary_genre_mismatch",
            ),
            1.0,
        )

    def test_top_picks_debug_template_renders_pool_fields(self):
        from app.discover.comfort_scoring import _build_comfort_debug_payload

        candidates = [
            self._provider_candidate(
                "tmpl-provider",
                "Template Provider",
                pool_source="similar_to_favorite",
                source_reason="Because you watched Anchor Favorite",
            ),
        ]
        reranked = _apply_comfort_confidence(
            candidates,
            self._movie_comfort_disney_profile(),
            use_movie_rewatch_model=True,
            user=self.user,
        )
        _apply_top_picks_source_quotas(reranked)
        row = RowResult(
            key="top_picks_for_you",
            title="Top Picks For You",
            mission="Personal Taste Match",
            why="New-to-you movies tailored to your taste.",
            source="local",
            items=[],
            debug_payload=_build_comfort_debug_payload(reranked, top_n=1),
        )

        content = render_to_string(
            "app/components/discover_row.html",
            {
                "row": row,
                "discover_debug": True,
                "show_more": False,
                "selected_media_type": MediaTypes.MOVIE.value,
                "user": self.user,
            },
        )

        for label in (
            "pool similar_to_favorite",
            "hydrated",
            "coverage",
            "src-quota",
            "personal-align",
            "reason-kind",
            "rating-src",
            "absence-boost",
            "avoidance",
            "Pool mix",
        ):
            self.assertIn(label, content)
        self.assertIn("Because you watched Anchor Favorite", content)

    def test_movie_comfort_debug_payload_exposes_absence_and_rotation_fields(self):
        from app.discover.comfort_scoring import _build_movie_comfort_debug_payload

        rotation_item = self._create_movie_item("debug-rotation", "Rotation Favorite")
        self._create_movie_watches(rotation_item, [48, 93, 138])
        candidates = [
            self._movie_comfort_candidate(
                "debug-rotation",
                "Rotation Favorite",
                row_key="comfort_rewatches",
                score_breakdown={
                    "user_score": 9.0,
                    "days_since_activity": 48.0,
                    "rewatch_count": 3.0,
                },
            ),
        ]
        reranked = _apply_comfort_confidence(
            candidates,
            self._movie_comfort_disney_profile(),
            use_movie_rewatch_model=True,
            user=self.user,
        )
        payload = _build_movie_comfort_debug_payload(reranked, top_n=12)

        entry = payload["top_candidates"][0]
        for key in (
            "absence_gate_active",
            "absence_norm",
            "absence_boost",
            "rotation_pressure",
            "rewatch_strength_raw",
            "core_weight_profile",
        ):
            self.assertIn(key, entry)
        self.assertIn("absence_boost", payload["contribution_totals"])

    @patch("app.discover.movie_comfort._is_holiday_window", new=lambda today=None: False)
    @patch("app.discover.comfort_scoring._is_holiday_window", return_value=False)
    def test_comfort_confidence_applies_out_of_season_holiday_penalty(
        self, _mock_window
    ):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="holiday",
                title="The Man Who Invented Christmas",
                tags=["Christmas"],
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 220.0,
                    "recency_bonus": 0.7,
                    "phase_genre_bonus": 0.7,
                    "recency_tag_bonus": 0.7,
                    "phase_tag_bonus": 0.7,
                    "rewatch_count": 1.0,
                    "genre_match": 0.7,
                    "tag_match": 0.7,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="neutral",
                title="Anytime Comfort",
                tags=["Feel Good"],
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 220.0,
                    "recency_bonus": 0.7,
                    "phase_genre_bonus": 0.7,
                    "recency_tag_bonus": 0.7,
                    "phase_tag_bonus": 0.7,
                    "rewatch_count": 1.0,
                    "genre_match": 0.7,
                    "tag_match": 0.7,
                },
            ),
        ]

        reranked = _apply_comfort_confidence(
            candidates,
            {
                "phase_genre_affinity": {"comedy": 1.0},
                "phase_tag_affinity": {"feel good": 1.0},
            },
        )

        by_id = {candidate.media_id: candidate for candidate in reranked}
        self.assertLess(by_id["holiday"].score_breakdown["seasonal_adjustment"], 0.0)
        self.assertEqual(by_id["neutral"].score_breakdown["seasonal_adjustment"], 0.0)
        self.assertLess(by_id["holiday"].final_score, by_id["neutral"].final_score)

    def test_comfort_match_signal_prefers_phase_tags(self):
        signal = _comfort_match_signal(
            {
                "phase_tag_affinity": {"cozy": 1.0, "musical": 0.8, "family": 0.7},
                "phase_genre_affinity": {"drama": 1.0, "action": 0.8},
            },
        )
        self.assertEqual(signal, "Driven by your current Cozy, Musical, Family phase")

    def test_comfort_match_signal_formats_generic_genres_with_descriptive_labels(self):
        signal = _comfort_match_signal(
            {
                "phase_genre_affinity": {"drama": 1.0, "comedy": 0.9, "action": 0.8},
            },
        )
        self.assertEqual(
            signal,
            "Driven by your current Drama, Comedy, Action phase",
        )

    def test_row_match_signal_uses_row_candidates_not_static_phrase(self):
        profile = {
            "phase_genre_affinity": {
                "drama": 1.0,
                "comedy": 0.9,
                "action": 0.8,
                "animation": 0.7,
            },
            "phase_tag_affinity": {"cozy": 1.0, "ensemble": 0.6},
        }
        top_picks_candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="1",
                title="Top Picks Cozy",
                genres=["Animation", "Comedy"],
                tags=["Cozy"],
                score_breakdown={"phase_fit": 0.9},
                final_score=0.8,
            ),
        ]
        comfort_candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="2",
                title="Comfort Action",
                genres=["Action", "Drama"],
                tags=["Ensemble"],
                score_breakdown={"phase_fit": 0.8},
                final_score=0.75,
            ),
        ]

        top_picks_signal = _row_match_signal(
            "top_picks_for_you",
            top_picks_candidates,
            profile,
        )
        comfort_signal = _row_match_signal(
            "comfort_rewatches",
            comfort_candidates,
            profile,
        )

        self.assertIsNotNone(top_picks_signal)
        self.assertIsNotNone(comfort_signal)
        self.assertIn("Cozy", top_picks_signal)
        self.assertNotEqual(top_picks_signal, comfort_signal)

    def test_row_match_signal_details_include_signal_evidence(self):
        profile = {
            "phase_genre_affinity": {"drama": 1.0, "thriller": 0.9},
            "phase_tag_affinity": {"cozy": 0.8},
        }
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="11",
                title="Drama Evidence",
                genres=["Drama", "Thriller"],
                tags=["Cozy"],
                score_breakdown={"phase_fit": 0.9},
                final_score=0.81,
            ),
        ]

        signal, details = _row_match_signal_with_details(
            "comfort_rewatches",
            candidates,
            profile,
        )

        self.assertEqual(signal, "Driven by your current Drama, Thriller, Cozy phase")
        self.assertIsNotNone(details)
        details = details or {}
        self.assertEqual(details["mode"], "row_candidates")
        self.assertIn("Signal evidence:", details["explanation"])
        self.assertGreaterEqual(len(details["labels"]), 1)

    def test_comfort_confidence_phase_lane_quota_promotes_recent_lane(self):
        candidates: list[CandidateItem] = []
        for index in range(12):
            candidates.append(
                CandidateItem(
                    media_type=MediaTypes.MOVIE.value,
                    source="tmdb",
                    media_id=f"broad-{index}",
                    title=f"Broad {index}",
                    genres=["Action"],
                    tags=["General"],
                    score_breakdown={
                        "user_score": 9.0,
                        "days_since_activity": 300.0,
                        "recency_bonus": 0.8,
                        "phase_genre_bonus": 0.25,
                        "recency_tag_bonus": 0.2,
                        "phase_tag_bonus": 0.2,
                        "rewatch_count": 2.0,
                        "genre_match": 0.8,
                        "tag_match": 0.4,
                    },
                ),
            )

        for index in range(3):
            candidates.append(
                CandidateItem(
                    media_type=MediaTypes.MOVIE.value,
                    source="tmdb",
                    media_id=f"phase-{index}",
                    title=f"Phase {index}",
                    genres=["Animation"],
                    tags=["Cozy"],
                    score_breakdown={
                        "user_score": 7.0,
                        "days_since_activity": 120.0,
                        "recency_bonus": 0.3,
                        "phase_genre_bonus": 0.35,
                        "recency_tag_bonus": 0.3,
                        "phase_tag_bonus": 0.35,
                        "rewatch_count": 1.0,
                        "genre_match": 0.3,
                        "tag_match": 0.3,
                    },
                ),
            )

        reranked = _apply_comfort_confidence(
            candidates,
            {
                "phase_genre_affinity": {"animation": 1.0},
                "phase_tag_affinity": {"cozy": 1.0},
            },
        )
        top_twelve = reranked[:12]
        phase_count = sum(
            1
            for candidate in top_twelve
            if "animation" in {genre.lower() for genre in (candidate.genres or [])}
            or "cozy" in {tag.lower() for tag in (candidate.tags or [])}
        )
        self.assertGreaterEqual(phase_count, 3)

    def test_comfort_confidence_display_calibration_strengthens_top_band(self):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="top",
                title="Top",
                genres=["Animation"],
                tags=["Cozy"],
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 200.0,
                    "recency_bonus": 0.8,
                    "phase_genre_bonus": 0.85,
                    "recency_tag_bonus": 0.8,
                    "phase_tag_bonus": 0.85,
                    "rewatch_count": 2.0,
                    "genre_match": 0.8,
                    "tag_match": 0.8,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="mid",
                title="Mid",
                genres=["Comedy"],
                tags=["Feel Good"],
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 160.0,
                    "recency_bonus": 0.5,
                    "phase_genre_bonus": 0.55,
                    "recency_tag_bonus": 0.5,
                    "phase_tag_bonus": 0.55,
                    "rewatch_count": 1.0,
                    "genre_match": 0.5,
                    "tag_match": 0.5,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="low",
                title="Low",
                genres=["Drama"],
                tags=["Classic"],
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 140.0,
                    "recency_bonus": 0.3,
                    "phase_genre_bonus": 0.3,
                    "recency_tag_bonus": 0.2,
                    "phase_tag_bonus": 0.2,
                    "rewatch_count": 1.0,
                    "genre_match": 0.3,
                    "tag_match": 0.3,
                },
            ),
        ]
        reranked = _apply_comfort_confidence(
            candidates,
            {
                "phase_genre_affinity": {"animation": 1.0},
                "phase_tag_affinity": {"cozy": 1.0},
            },
        )
        final_scores = [float(candidate.final_score or 0.0) for candidate in reranked]
        display_scores = [
            float(candidate.display_score or 0.0) for candidate in reranked
        ]

        self.assertEqual(
            [candidate.media_id for candidate in reranked],
            ["top", "mid", "low"],
        )
        self.assertGreaterEqual(display_scores[0], 0.8)
        self.assertGreater(display_scores[0], final_scores[0])
        self.assertGreater(display_scores[1], final_scores[1])
        self.assertGreater(display_scores[2], final_scores[2])
        self.assertGreaterEqual(final_scores[0], final_scores[1])
        self.assertGreaterEqual(final_scores[1], final_scores[2])
        self.assertGreaterEqual(display_scores[0], display_scores[1])
        self.assertGreaterEqual(display_scores[1], display_scores[2])

    @patch("app.discover.movie_comfort._is_holiday_window", new=lambda today=None: False)
    @patch("app.discover.comfort_scoring._is_holiday_window", return_value=False)
    def test_comfort_debug_payload_exposes_spread_and_penalty_stack(self, _mock_window):
        candidates = [
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="holiday",
                title="The Man Who Invented Christmas",
                tags=["Christmas"],
                genres=["Drama"],
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 200.0,
                    "recency_bonus": 0.75,
                    "phase_genre_bonus": 0.75,
                    "recency_tag_bonus": 0.75,
                    "phase_tag_bonus": 0.75,
                    "rewatch_count": 2.0,
                    "genre_match": 0.75,
                    "tag_match": 0.75,
                    "phase_pool_backfill": 1.0,
                },
            ),
            CandidateItem(
                media_type=MediaTypes.MOVIE.value,
                source="tmdb",
                media_id="clean",
                title="Clean Fit",
                tags=["Cozy"],
                genres=["Animation"],
                score_breakdown={
                    "user_score": 8.0,
                    "days_since_activity": 200.0,
                    "recency_bonus": 0.6,
                    "phase_genre_bonus": 0.65,
                    "recency_tag_bonus": 0.6,
                    "phase_tag_bonus": 0.65,
                    "rewatch_count": 1.0,
                    "genre_match": 0.6,
                    "tag_match": 0.6,
                    "phase_pool_strong": 1.0,
                },
            ),
        ]
        reranked = _apply_comfort_confidence(
            candidates,
            {
                "phase_genre_affinity": {"animation": 1.0},
                "phase_tag_affinity": {"cozy": 1.0},
            },
        )
        payload = _build_comfort_debug_payload(reranked, top_n=2)

        self.assertIn("score_distribution", payload)
        self.assertIn("penalty_stack", payload)
        self.assertIn("contribution_totals", payload)
        self.assertIn("dampener_totals", payload)
        self.assertIn("tag_signal", payload)
        self.assertIn("top_candidates", payload)
        self.assertEqual(len(payload["top_candidates"]), 2)
        self.assertIn("raw_spread", payload["score_distribution"])
        self.assertIn("compressed_raw", payload["score_distribution"])
        self.assertIn("multi_penalty_count", payload["penalty_stack"])
        self.assertIn("phase_family", payload["contribution_totals"])
        self.assertIn("hot_recency", payload["contribution_totals"])
        self.assertIn("seasonality", payload["dampener_totals"])
        self.assertIn("opening_era", payload["dampener_totals"])
        self.assertIn("mode", payload["tag_signal"])
        self.assertIn("candidate_tag_coverage_top_n", payload["tag_signal"])
        self.assertIn("recent_history_tag_coverage", payload["tag_signal"])
        self.assertIn("phase_pool_source", payload["top_candidates"][0])
        self.assertIn("raw_final_score", payload["top_candidates"][0])
        self.assertIn("phase_family_contribution", payload["top_candidates"][0])
        self.assertIn("hot_recency_contribution", payload["top_candidates"][0])
        self.assertIn("seasonality_dampener_contribution", payload["top_candidates"][0])
        self.assertIn("diversity_dampener_contribution", payload["top_candidates"][0])
        self.assertIn("tag_signal_mode", payload["top_candidates"][0])
