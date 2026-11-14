"""Query-count regression pins for representative high-traffic pages.

These tests pin the number of SQL queries issued by key pages so that
N+1 regressions are caught in CI. The constants below are the budget for
each page; when an optimization lands, tighten the pin in the same commit
so the diff documents the win.

Run with: python src/manage.py test app.tests.test_query_counts -v 2
"""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.models import (
    TV,
    Anime,
    CollectionEntry,
    Episode,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Podcast,
    PodcastEpisode,
    PodcastShow,
    Season,
    Sources,
    Status,
)
from app.tv_sort import RUNTIME_UNKNOWN_AIRED
from lists.models import CustomList, CustomListItem
from users.home_screen import ensure_home_screen_rows
from users.models import HomeScreenRowTypeChoices


def setUpModule():
    """Silence log noise for this module only."""
    logging.disable(logging.DEBUG)


def tearDownModule():
    """Restore logging so other modules' assertLogs still see records."""
    logging.disable(logging.NOTSET)


SHOW_COUNT = 10
SEASONS_PER_SHOW = 4
EPISODES_PER_SEASON = 5

# Query budgets. Exact counts are pinned so regressions fail loudly;
# update deliberately when page behavior changes.
HOME_PAGE_MAX_QUERIES = 120
TV_LIST_RUNTIME_SORT_MAX_QUERIES = (
    38  # +3 from prefetch on all_media (prevents N+1 for duplicate-watch users)
)
TV_LIST_TABLE_PAGE_MAX_QUERIES = 35  # +3 from same prefetch
TV_LIST_DEFAULT_SORT_MAX_QUERIES = 24  # pinned after Fix 1+2+3 (was 2642 in production)
TV_LIST_TIME_LEFT_SORT_MAX_QUERIES = (
    26  # pinned after Fix 4 bulk runtime load (was ~400+ per-season queries)
)
TV_LIST_NEXT_EPISODE_SORT_MAX_QUERIES = (
    35  # +1 narrow representative-glyph lookup for the alphabet rail;
    # +2 fixed Item prefetches after compact Episode window selection;
    # keeps wide Item columns out of the history-sized window intermediates.
    # The untracked-season events of every show on the page are read in
    # one query; it was one per show plus one per event's item (49 for these
    # ten shows, 92 for sixty)
)
MOVIE_LIST_DEFAULT_SORT_MAX_QUERIES = 16  # +1 narrow alphabet-glyph lookup; +1 bulk tag options
ANIME_LIST_DEFAULT_SORT_MAX_QUERIES = (
    22  # +2 over the pre-credential-registry pin: resolving the MAL/TMDB
    # credentials reads the instance credential table and the viewer's personal
    # one, each once per cold cache (an hour in production, every test here).
)
ANIME_LIST_GROUPED_MAX_QUERIES = (
    29  # +1 narrow alphabet-glyph lookup;
    # +1 id-first window sort (covering-index membership scan, d95bf59e9);
    # +1 bulk Item prefetch after compact Episode window selection;
    # grouped (TV-backed) anime adds no per-show runtime queries;
    # +1 for the page COUNT: the list is paged by the library-query engine
    # instead of counting a fully loaded list (#1248);
    # +4 from the Genres/Tags column Prefetch("item__item_tags") added in #457;
    # +2 from the instance and personal provider-credential reads;
    # +1 from the sidebar's saved-views read (#413)
)
MANGA_LIST_DEFAULT_SORT_MAX_QUERIES = 14
MANGA_LIST_NO_STATUS_MAX_QUERIES = 19  # +1 from the custom-list collaborators prefetch
GAME_LIST_DEFAULT_SORT_MAX_QUERIES = 19  # +1 bulk tag options
GAME_LIST_START_DATE_SORT_LIBRARY_SIZE = 150
GAME_LIST_START_DATE_SORT_MAX_QUERIES = (
    15  # pinned after the SQL pushdown fast path (#1004) — was scanning the
    # entire status-filtered library before slicing to the page
)
HOME_ROW_FRAGMENT_MAX_QUERIES = (
    123  # +2 from the Tags column Prefetch (#457); +1 from the provider-credential read
)
CUSTOM_LIST_DETAIL_MAX_QUERIES = 44  # +1 intentional stray-podcast repair lookup (529ae8917); +1 bulk tag options; +1 viewers prefetch; +1 narrow alphabet-glyph lookup; +1 id-first window sort (d95bf59e9); +3 from prefilled release-year metadata;
# +1 from the custom-list collaborators prefetch; +1 from the sidebar's
# saved-views read (#413); +3 from the filter menu's options (member ids,
# member metadata, tag names) on a full page render (#806); +1 from the
# completed-status lookup, which runs once per media type (the Video type)
SEASON_PAGE_FIRST_VIEW_EPISODE_COUNT = 18
SEASON_PAGE_FIRST_VIEW_MAX_QUERIES = 46  # +1 from the per-item metadata language override lookup (#1009)
SESSION_HISTORY_MODAL_MAX_QUERIES = 60
PODCAST_SHOW_DETAIL_MAX_QUERIES = 17  # was 155 for twenty played episodes
PODCAST_EPISODE_PAGE_MAX_QUERIES = 14  # was 72


def seed_tv_library(
    user,
    show_count=SHOW_COUNT,
    media_id_prefix="qc_show",
    library_media_type="",
):
    """Create TV shows with seasons and episode items carrying runtimes."""
    air_date = datetime(2020, 1, 1, tzinfo=UTC)
    for show_index in range(show_count):
        media_id = f"{media_id_prefix}_{show_index}"
        tv_item = Item.objects.create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            library_media_type=library_media_type,
            title=f"Query Count Show {show_index}",
            image="https://example.com/show.jpg",
            release_datetime=air_date,
        )
        tv = TV.objects.create(
            item=tv_item,
            user=user,
            status=Status.IN_PROGRESS.value,
        )
        for season_number in range(1, SEASONS_PER_SHOW + 1):
            season_item = Item.objects.create(
                media_id=media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                library_media_type=library_media_type,
                season_number=season_number,
                title=f"Query Count Show {show_index}",
                image="https://example.com/season.jpg",
                release_datetime=air_date,
            )
            season = Season.objects.create(
                item=season_item,
                related_tv=tv,
                user=user,
                status=Status.IN_PROGRESS.value,
            )
            episodes = []
            for episode_number in range(1, EPISODES_PER_SEASON + 1):
                episode_item = Item.objects.create(
                    media_id=media_id,
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    library_media_type=library_media_type,
                    season_number=season_number,
                    episode_number=episode_number,
                    title=f"Query Count Show {show_index}",
                    image="https://example.com/episode.jpg",
                    release_datetime=air_date
                    + timedelta(days=season_number * 30 + episode_number),
                    runtime_minutes=25 + episode_number,
                )
                episodes.append(
                    Episode(
                        item=episode_item,
                        related_season=season,
                        end_date=air_date,
                    ),
                )
            Episode.objects.bulk_create(episodes)


def seed_movie_library(user, count=8):
    release_date = datetime(2021, 1, 1, tzinfo=UTC)
    for movie_index in range(count):
        item = Item.objects.create(
            media_id=f"qc_movie_{movie_index}",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title=f"Query Count Movie {movie_index}",
            image="https://example.com/movie.jpg",
            release_datetime=release_date + timedelta(days=movie_index),
            runtime_minutes=100 + movie_index,
        )
        Movie.objects.create(
            item=item,
            user=user,
            status=Status.IN_PROGRESS.value,
            progress=movie_index % 2,
        )


def seed_anime_library(user, count=6):
    release_date = datetime(2022, 1, 1, tzinfo=UTC)
    for anime_index in range(count):
        item = Item.objects.create(
            media_id=f"qc_anime_{anime_index}",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title=f"Query Count Anime {anime_index}",
            image="https://example.com/anime.jpg",
            release_datetime=release_date + timedelta(days=anime_index),
        )
        Anime.objects.create(
            item=item,
            user=user,
            status=Status.IN_PROGRESS.value,
            progress=max(anime_index - 1, 0),
        )


def seed_game_library(user, count=6):
    for game_index in range(count):
        item = Item.objects.create(
            media_id=f"qc_game_{game_index}",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title=f"Query Count Game {game_index}",
            image="https://example.com/game.jpg",
            provider_game_lengths={
                "active_source": "hltb",
                "hltb": {
                    "summary": {"all_styles_minutes": 600 + game_index * 30},
                    "counts": {"all_styles": 10},
                },
            },
            provider_game_lengths_source="hltb",
            provider_game_lengths_match="exact_title_year",
        )
        Game.objects.create(
            item=item,
            user=user,
            status=Status.IN_PROGRESS.value,
            progress=10 + game_index,
        )
        CollectionEntry.objects.create(
            user=user,
            item=item,
            resolution=f"Platform {game_index}",
        )


def seed_untracked_manga_collection(user, count=5):
    for manga_index in range(count):
        item = Item.objects.create(
            media_id=f"qc_manga_{manga_index}",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title=f"Query Count Manga {manga_index}",
            image="https://example.com/manga.jpg",
        )
        CollectionEntry.objects.create(user=user, item=item, media_type="paperback")


class QueryCountTests(TestCase):
    """Pin query counts for representative pages."""

    @classmethod
    def setUpTestData(cls):
        """Seed a TV library shared by all tests."""
        cls.user = get_user_model().objects.create_user(
            username="querycount",
            password="12345",
        )
        cls.user.tv_enabled = True
        cls.user.save()
        seed_tv_library(cls.user)
        seed_movie_library(cls.user)
        seed_anime_library(cls.user)
        seed_game_library(cls.user)
        seed_untracked_manga_collection(cls.user)
        cls.custom_list = CustomList.objects.create(
            name="Query Count Benchmark List",
            owner=cls.user,
        )
        CustomListItem.objects.bulk_create(
            [
                CustomListItem(
                    custom_list=cls.custom_list,
                    item=item,
                    added_by=cls.user,
                )
                for item in Item.objects.filter(
                    media_type__in=[
                        MediaTypes.TV.value,
                        MediaTypes.MOVIE.value,
                        MediaTypes.ANIME.value,
                    ],
                ).order_by("id")[:10]
            ],
        )
        ensure_home_screen_rows(cls.user)
        cls.tv_home_row = (
            cls.user.home_screen_rows.filter(
                media_type=MediaTypes.TV.value,
                row_type=HomeScreenRowTypeChoices.LIBRARY_QUERY,
            )
            .order_by("position", "id")
            .first()
        )

    def setUp(self):
        """Reset cache state and log in."""
        cache.clear()
        self._warm_instance_caches(self.user)
        self.client.force_login(self.user)

    @staticmethod
    def _warm_instance_caches(user):
        """Populate the instance-wide caches a running deployment already has.

        These budgets guard how list rendering scales with the library, so the
        constant per-instance lookups must not be counted. ``cache.clear()``
        above drops all three, and the image-caching one additionally creates
        its singleton row on first read, adding a savepoint and an insert.
        """
        from app import image_cache
        from app.providers import credentials, services

        image_cache.is_enabled()
        # Credentials are cached per member as well as instance-wide, and a
        # request runs as the logged-in user, so both maps have to be warm.
        credentials.is_configured("tmdb")
        credentials.is_configured("tmdb", user)
        services._get_tmdb_proxy_url()  # no public warm entry point

    def _assert_query_budget(self, url, budget, label):
        with CaptureQueriesContext(connection) as context:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        count = len(context.captured_queries)
        self.assertLessEqual(
            count,
            budget,
            f"{label} issued {count} queries, budget is {budget}. "
            "If this increase is intentional, update the pin deliberately.",
        )

    def _assert_query_budget_with_kwargs(self, url, budget, label, **request_kwargs):
        with CaptureQueriesContext(connection) as context:
            response = self.client.get(url, **request_kwargs)
        self.assertEqual(response.status_code, 200)
        count = len(context.captured_queries)
        self.assertLessEqual(
            count,
            budget,
            f"{label} issued {count} queries, budget is {budget}. "
            "If this increase is intentional, update the pin deliberately.",
        )

    def test_home_page_query_budget(self):
        """Home page stays within its query budget."""
        self._assert_query_budget("/", HOME_PAGE_MAX_QUERIES, "home page")

    def test_home_row_fragment_query_budget(self):
        """Home-row HTMX fragment stays within its query budget."""
        self._assert_query_budget_with_kwargs(
            f"{reverse('home')}?load_row={self.tv_home_row.id}&offset=0",
            HOME_ROW_FRAGMENT_MAX_QUERIES,
            "home row fragment",
            HTTP_HX_REQUEST="true",
        )

    def test_tv_list_runtime_sort_query_budget(self):
        """Runtime-sorted TV list stays within its query budget."""
        self._assert_query_budget(
            "/medialist/tv?sort=runtime",
            TV_LIST_RUNTIME_SORT_MAX_QUERIES,
            "TV list sorted by runtime",
        )

    def test_tv_list_table_page_query_budget(self):
        """TV table layout stays within its query budget."""
        self._assert_query_budget(
            "/medialist/tv?layout=table",
            TV_LIST_TABLE_PAGE_MAX_QUERIES,
            "TV list table layout",
        )

    def test_tv_list_default_sort_query_budget(self):
        """Default TV list (no sort param) stays within budget — catches N+1 regressions."""
        self._assert_query_budget(
            "/medialist/tv",
            TV_LIST_DEFAULT_SORT_MAX_QUERIES,
            "TV list default sort",
        )

    def test_tv_list_time_left_sort_query_budget(self):
        """Time-left sort uses bulk episode runtime query, not per-season queries."""
        self._assert_query_budget(
            "/medialist/tv?sort=time_left",
            TV_LIST_TIME_LEFT_SORT_MAX_QUERIES,
            "TV list time_left sort",
        )

    def test_tv_list_next_episode_air_date_sort_query_budget(self):
        """Next-episode sort reads season events in bulk, not per show."""
        self._assert_query_budget(
            "/medialist/tv?sort=next_episode_air_date",
            TV_LIST_NEXT_EPISODE_SORT_MAX_QUERIES,
            "TV list next_episode_air_date sort",
        )

    def test_tv_list_cache_hit_query_budget(self):
        """Second TV list request hits the media-list cache, skipping the expensive build phase.

        Cache hit budget is higher than 0 because annotate_max_progress,
        prefill_episode_runtime_index, and auth/session queries still run
        post-pagination (they're cheap and page-specific).
        """
        self.client.get("/medialist/tv")  # warm the cache
        self._assert_query_budget("/medialist/tv", 20, "TV list cache hit")

    def test_tv_list_cache_hit_page_two_query_budget(self):
        """A cache-hit page-2 request stays within budget (issue #865).

        Before the fix, a cache hit still unpickled the ENTIRE per-user
        library just to slice out page 2 — invisible to a query-count pin
        but very visible in wall time. The fix hydrates only the 32 rows on
        the requested page, so this budget should track the plain cache-hit
        budget above, not the library size.
        """
        self.client.get("/medialist/tv")  # warm the order + filter_data caches
        self._assert_query_budget(
            "/medialist/tv?page=2", 20, "TV list cache hit page 2"
        )

    def test_movie_list_cache_hit_query_budget(self):
        """Second movie list request hits the media-list cache (issue #865)."""
        self.client.get("/medialist/movie")  # warm the cache
        self._assert_query_budget(
            "/medialist/movie", 13, "movie list cache hit"
        )

    def test_movie_list_cache_hit_page_two_query_budget(self):
        """A cache-hit page-2 movie request hydrates only its own page."""
        self.client.get("/medialist/movie")  # warm the order + filter_data caches
        self._assert_query_budget(
            # +1 from the custom-list collaborators prefetch
            "/medialist/movie?page=2",
            13,
            "movie list cache hit page 2",
        )

    def test_movie_list_default_sort_query_budget(self):
        self._assert_query_budget(
            "/medialist/movie",
            MOVIE_LIST_DEFAULT_SORT_MAX_QUERIES,
            "movie list default sort",
        )

    def test_anime_list_default_sort_query_budget(self):
        self._assert_query_budget(
            "/medialist/anime",
            ANIME_LIST_DEFAULT_SORT_MAX_QUERIES,
            "anime list default sort",
        )

    def test_anime_list_grouped_anime_query_budget(self):
        """Grouped (TV-backed) anime entries render without per-show runtime queries.

        Regression pin: prefill_episode_runtime_index must hydrate the wrapped
        media object, not the MediaListEntry wrapper — otherwise every grouped
        show issues its own episode-runtime query at template render time.
        """
        seed_tv_library(
            self.user,
            show_count=6,
            media_id_prefix="qc_grouped_anime",
            library_media_type=MediaTypes.ANIME.value,
        )
        self._assert_query_budget(
            "/medialist/anime",
            ANIME_LIST_GROUPED_MAX_QUERIES,
            "anime list with grouped anime",
        )

    def test_manga_list_default_sort_query_budget(self):
        self._assert_query_budget(
            "/medialist/manga",
            MANGA_LIST_DEFAULT_SORT_MAX_QUERIES,
            "manga list default sort",
        )

    def test_manga_list_no_status_query_budget(self):
        self._assert_query_budget(
            "/medialist/manga?status=no_status",
            MANGA_LIST_NO_STATUS_MAX_QUERIES,
            "manga list no status",
        )

    def test_game_list_default_sort_query_budget(self):
        self._assert_query_budget(
            "/medialist/game",
            GAME_LIST_DEFAULT_SORT_MAX_QUERIES,
            "game list default sort",
        )

    def test_api_game_list_start_date_sort_query_budget(self):
        """Pin the reporter's exact repro from issue #1004.

        `GET /api/v1/media/game/?status=1&limit=10&sort=start_date&direction=asc`
        took 778ms/75 queries in production against ~2,500 games in one
        status — the library-query engine (app.library_query) must keep
        this flat regardless of library size, not scan every matching row
        to serve a 10-item page.
        """
        for index in range(GAME_LIST_START_DATE_SORT_LIBRARY_SIZE):
            item = Item.objects.create(
                media_id=f"qc_game_start_date_{index}",
                source=Sources.IGDB.value,
                media_type=MediaTypes.GAME.value,
                title=f"Query Count Start Date Game {index}",
            )
            Game.objects.create(item=item, user=self.user, status=Status.IN_PROGRESS.value)

        with CaptureQueriesContext(connection) as context:
            response = self.client.get(
                "/api/v1/media/game/",
                {"status": "1", "limit": 10, "sort": "start_date", "direction": "asc"},
                HTTP_X_API_KEY=self.user.token,
            )
        self.assertEqual(response.status_code, 200)
        # >= not ==: setUpTestData's seed_game_library also seeds
        # IN_PROGRESS games shared by every test in this class.
        self.assertGreaterEqual(
            response.json()["pagination"]["total"], GAME_LIST_START_DATE_SORT_LIBRARY_SIZE,
        )
        count = len(context.captured_queries)
        self.assertLessEqual(
            count,
            GAME_LIST_START_DATE_SORT_MAX_QUERIES,
            f"game list API start_date sort issued {count} queries against "
            f"{GAME_LIST_START_DATE_SORT_LIBRARY_SIZE} games, budget is "
            f"{GAME_LIST_START_DATE_SORT_MAX_QUERIES}. If this increase is "
            "intentional, update the pin deliberately.",
        )

    def test_custom_list_detail_query_budget(self):
        self._assert_query_budget(
            reverse("list_detail", args=[self.custom_list.public_reference]),
            CUSTOM_LIST_DETAIL_MAX_QUERIES,
            "custom list detail",
        )

    def test_session_history_modal_query_budget(self):
        """Session history stays within budget on a cold cache."""
        self._assert_query_budget(
            f"{reverse('activity_sessions_modal')}?media_type=tv&media_id=qc_show_0"
            "&source=tmdb&season_number=1",
            SESSION_HISTORY_MODAL_MAX_QUERIES,
            "session history modal",
        )

    @patch("app.providers.trakt.is_configured", return_value=False)
    @patch("app.providers.tmdb.process_episodes")
    @patch("app.providers.services.get_media_metadata")
    def test_season_page_first_view_query_budget(
        self,
        mock_get_metadata,
        mock_process_episodes,
        mock_trakt_configured,
    ):
        """First-ever view of a season with many untracked episodes stays within budget.

        Regression pin: creating N never-before-seen episode Items used to fire
        N independent Item post_save signals (schedule_runtime_backfill_on_item_save),
        each re-running the same season-scoped backfill/cache-invalidation queries
        that are identical across every episode in the season — an N+1 that only
        shows up on a season's first view, when every episode is freshly created.
        """
        media_id = "qc_season_first_view"
        season_number = 2
        air_date = datetime(1998, 4, 1, tzinfo=UTC)
        episodes = [
            {
                "episode_number": n,
                "name": f"Episode {n}",
                "air_date": (air_date + timedelta(days=n * 7)).strftime("%Y-%m-%d"),
                "runtime": 23,
                "vote_average": 7.5,
                "vote_count": 100,
            }
            for n in range(1, SEASON_PAGE_FIRST_VIEW_EPISODE_COUNT + 1)
        ]
        mock_get_metadata.return_value = {
            "title": "Query Count Season Show",
            "media_id": media_id,
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": "https://example.com/show.jpg",
            f"season/{season_number}": {
                "title": "Query Count Season Show",
                "season_title": f"Season {season_number}",
                "media_id": media_id,
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "season_number": season_number,
                "image": "https://example.com/season.jpg",
                "episodes": episodes,
            },
        }
        mock_process_episodes.return_value = [
            {
                "media_id": media_id,
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": season_number,
                "episode_number": ep["episode_number"],
                "title": ep["name"],
                "air_date": ep["air_date"],
                "actions_enabled": False,
            }
            for ep in episodes
        ]

        url = reverse(
            "season_details",
            kwargs={
                "source": Sources.TMDB.value,
                "media_id": media_id,
                "title": "query-count-season-show",
                "season_number": season_number,
            },
        )
        self._assert_query_budget_with_kwargs(
            f"{url}?fragment=secondary",
            SEASON_PAGE_FIRST_VIEW_MAX_QUERIES,
            "season page first view with many new episodes",
        )


class SeasonRuntimeCacheReadBudgetTests(TestCase):
    """Pin cache round trips for the TV time-left sort.

    CaptureQueriesContext counts SQL only, so a cache round trip is invisible
    to every budget above. The time-left sort used to read up to five season
    keys for each show, one request after another, which made an interactive
    page wait for N x 5 round trips on a large library (#725).

    The shared seed gives every episode a real runtime, so the season metadata
    is never reached. This seed uses the "aired, runtime unknown" marker
    instead, which is what a library with incomplete metadata looks like.
    """

    SHOW_COUNT = 12

    @classmethod
    def setUpTestData(cls):
        """Seed shows whose episodes have no usable runtime."""
        cls.user = get_user_model().objects.create_user(
            username="seasonruntimebudget",
            password="12345",
        )
        cls.user.tv_enabled = True
        cls.user.tv_sort = "time_left"
        cls.user.save()

        air_date = datetime(2020, 1, 1, tzinfo=UTC)
        for show_index in range(cls.SHOW_COUNT):
            media_id = f"srb_show_{show_index}"
            tv_item = Item.objects.create(
                media_id=media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
                title=f"Season Runtime Show {show_index}",
                image="https://example.com/show.jpg",
                release_datetime=air_date,
                # No usable show runtime, so the season metadata is reached.
                runtime_minutes=RUNTIME_UNKNOWN_AIRED,
            )
            tv = TV.objects.create(
                item=tv_item,
                user=cls.user,
                status=Status.IN_PROGRESS.value,
            )
            season_item = Item.objects.create(
                media_id=media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                season_number=1,
                title=f"Season Runtime Show {show_index}",
                image="https://example.com/season.jpg",
                release_datetime=air_date,
            )
            season = Season.objects.create(
                item=season_item,
                related_tv=tv,
                user=cls.user,
                status=Status.IN_PROGRESS.value,
            )
            # Six aired episodes, only two watched. The show must have
            # episodes left, or the sort never asks for its runtime.
            episode_items = []
            for episode_number in range(1, 7):
                episode_items.append(
                    Item.objects.create(
                        media_id=media_id,
                        source=Sources.TMDB.value,
                        media_type=MediaTypes.EPISODE.value,
                        season_number=1,
                        episode_number=episode_number,
                        title=f"Season Runtime Show {show_index}",
                        image="https://example.com/episode.jpg",
                        release_datetime=air_date + timedelta(days=episode_number),
                        # Aired, runtime unknown. Excluded from the episode index.
                        runtime_minutes=RUNTIME_UNKNOWN_AIRED,
                    ),
                )
            Episode.objects.bulk_create(
                [
                    Episode(
                        item=episode_item,
                        related_season=season,
                        end_date=air_date,
                    )
                    for episode_item in episode_items[:2]
                ],
            )

    def setUp(self):
        cache.clear()
        self.client.force_login(self.user)

    def _count_season_cache_reads(self, url):
        """Return (single reads, grouped reads) of season keys for one request."""
        single_reads = []
        grouped_reads = []
        real_get = cache.get
        real_get_many = cache.get_many

        def counting_get(key, *args, **kwargs):
            if isinstance(key, str) and key.startswith("tmdb_season_"):
                single_reads.append(key)
            return real_get(key, *args, **kwargs)

        def counting_get_many(keys, *args, **kwargs):
            keys = list(keys)
            if any(str(key).startswith("tmdb_season_") for key in keys):
                grouped_reads.append(keys)
            return real_get_many(keys, *args, **kwargs)

        with (
            patch.object(cache, "get", counting_get),
            patch.object(cache, "get_many", counting_get_many),
        ):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return single_reads, grouped_reads

    def test_time_left_sort_reads_season_runtimes_in_groups(self):
        """The sort must not read one season key at a time for each show."""
        single_reads, grouped_reads = self._count_season_cache_reads(
            "/medialist/tv?sort=time_left",
        )

        self.assertEqual(
            single_reads,
            [],
            f"time_left sort read {len(single_reads)} season keys one at a "
            f"time; they must be read in groups. Sample: {single_reads[:5]}",
        )
        self.assertLessEqual(
            len(grouped_reads),
            2,
            f"time_left sort issued {len(grouped_reads)} grouped season reads; "
            "one page must need at most one group per chunk.",
        )

    def test_time_left_sort_survives_an_unavailable_cache(self):
        """A broken cache must degrade to the default runtime, not an error."""

        def raise_on_get_many(*_args, **_kwargs):
            message = "redis is down"
            raise RuntimeError(message)

        with patch.object(cache, "get_many", raise_on_get_many):
            response = self.client.get("/medialist/tv?sort=time_left")

        self.assertEqual(response.status_code, 200)


class PodcastShowQueryCountTests(TestCase):
    """A podcast show page reads its plays in bulk, not per episode.

    A show page with tracked episodes issued 386 queries in the production
    log: each of the 20 episodes on the first page looked up its own play rows
    and their history. The pins hold with twenty episodes, every one played
    twice (a repeat listen).
    """

    EPISODES_ON_PAGE = 20

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="podcastquery",
            password="12345",
        )
        cls.show = PodcastShow.objects.create(
            podcast_uuid="qc-show",
            source=Sources.POCKETCASTS.value,
            title="Query Count Podcast",
            image="https://example.com/podcast.jpg",
        )
        published = datetime(2024, 1, 1, tzinfo=UTC)
        for index in range(cls.EPISODES_ON_PAGE + 5):
            episode = PodcastEpisode.objects.create(
                show=cls.show,
                episode_uuid=f"qc-episode-{index}",
                title=f"Episode {index}",
                published=published + timedelta(days=index),
                duration=1800,
                episode_number=index,
            )
            item = Item.objects.create(
                media_id=episode.episode_uuid,
                source=Sources.POCKETCASTS.value,
                media_type=MediaTypes.PODCAST.value,
                title=episode.title,
                image=cls.show.image,
            )
            for play in range(2):
                Podcast.objects.create(
                    item=item,
                    user=cls.user,
                    show=cls.show,
                    episode=episode,
                    status=Status.COMPLETED.value,
                    progress=30,
                    end_date=published + timedelta(days=index, hours=play),
                )

    def setUp(self):
        """Reset cache state and log in."""
        cache.clear()
        QueryCountTests._warm_instance_caches(self.user)
        self.client.force_login(self.user)

    def _assert_budget(self, url, budget, label):
        self.client.get(url)  # first view creates episode Items and warms caches
        with CaptureQueriesContext(connection) as context:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(
            len(context.captured_queries),
            budget,
            f"{label} issued {len(context.captured_queries)} queries, budget is "
            f"{budget}. If this increase is intentional, update the pin deliberately.",
        )

    def test_show_detail_page_query_budget(self):
        """The show page's first 20 episodes cost a fixed number of queries."""
        self._assert_budget(
            reverse(
                "media_details",
                args=[
                    Sources.POCKETCASTS.value,
                    MediaTypes.PODCAST.value,
                    self.show.podcast_uuid,
                    "query-count-podcast",
                ],
            ),
            PODCAST_SHOW_DETAIL_MAX_QUERIES,
            "podcast show detail",
        )

    def test_episode_page_fragment_query_budget(self):
        """The infinite-scroll episode fragment costs a fixed number of queries."""
        self._assert_budget(
            reverse("podcast_episodes_api", args=[self.show.id])
            + "?format=html&page=1&page_size=20",
            PODCAST_EPISODE_PAGE_MAX_QUERIES,
            "podcast episode fragment",
        )
