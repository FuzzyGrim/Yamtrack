"""Pytest conftest that mocks all external API calls for sandboxed nix builds.

This file is injected by the nix test derivations so tests run without
network access.  Individual tests that already carry their own @patch
decorators will override these auto‑use fixtures transparently.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest


def _mock_get_media_metadata(media_type, media_id, source,
                             season_numbers=None, episode_number=None):
    """Return plausible metadata for any media type."""
    # Manual sources have no real API provider — return minimal data
    # but include season keys so Episode.save() doesn't crash
    if source == "manual":
        sn_list = season_numbers or [1]
        result = {
            "title": "Manual Media",
            "image": "http://example.com/image.jpg",
            "max_progress": None,
            "details": {"seasons": 0},
            "related": {"seasons": []},
            "episodes": [
                {
                    "episode_number": i,
                    "image": "http://example.com/ep.jpg",
                    "air_date": None,
                }
                for i in range(1, 25)
            ],
        }
        for sn in sn_list:
            result[f"season/{sn}"] = {
                "image": "http://example.com/season.jpg",
                "season_number": sn,
                "episodes": [
                    {
                        "episode_number": i,
                        "image": "http://example.com/ep.jpg",
                        "air_date": None,
                    }
                    for i in range(1, 25)
                ],
            }
        return result

    season_numbers = season_numbers or [1]

    if media_type == "tv_with_seasons":
        result = {
            "title": "Test Show",
            "image": "http://example.com/image.jpg",
            "max_progress": 10,
            "details": {"seasons": len(season_numbers)},
            "related": {
                "seasons": [
                    {
                        "season_number": sn,
                        "image": "http://example.com/season.jpg",
                        "first_air_date": datetime(2020, 1, 1, tzinfo=UTC),
                    }
                    for sn in season_numbers
                ],
            },
        }
        for sn in season_numbers:
            result[f"season/{sn}"] = {
                "image": "http://example.com/season.jpg",
                "season_number": sn,
                "episodes": [
                    {
                        "episode_number": i,
                        "image": "http://example.com/ep.jpg",
                        "air_date": datetime(2020, 1, i, tzinfo=UTC),
                    }
                    for i in range(1, 25)
                ],
            }
        return result

    if media_type == "season":
        return {
            "title": "Test Show",
            "image": "http://example.com/season.jpg",
            "episodes": [
                {
                    "episode_number": i,
                    "image": "http://example.com/ep.jpg",
                    "air_date": datetime(2020, 1, i, tzinfo=UTC),
                }
                for i in range(1, 25)
            ],
        }

    if media_type == "tv":
        num_seasons = 10
        result = {
            "title": "Test Show",
            "image": "http://example.com/image.jpg",
            "max_progress": num_seasons,
            "details": {"seasons": num_seasons},
            "related": {
                "seasons": [
                    {
                        "season_number": sn,
                        "image": "http://example.com/season.jpg",
                        "first_air_date": datetime(2020, 1, 1, tzinfo=UTC),
                    }
                    for sn in range(1, num_seasons + 1)
                ],
            },
        }
        for sn in range(1, num_seasons + 1):
            result[f"season/{sn}"] = {
                "image": "http://example.com/season.jpg",
                "season_number": sn,
                "episodes": [
                    {
                        "episode_number": i,
                        "image": "http://example.com/ep.jpg",
                        "air_date": datetime(2020, 1, i, tzinfo=UTC),
                    }
                    for i in range(1, 25)
                ],
            }
        return result

    # Games are measured in minutes — no meaningful max_progress
    if media_type == "game":
        return {
            "title": "Test Game",
            "image": "http://example.com/image.jpg",
            "max_progress": None,
        }

    # Anime, movie, manga, book, comic, boardgame — have episode/chapter counts
    return {
        "title": "Test Media",
        "image": "http://example.com/image.jpg",
        "max_progress": 26,
    }


def _mock_search(media_type, query, page, source=None):
    """Return a plausible search result for any query.

    Import tests need search to return matching results so items can be created.
    We generate a deterministic media_id from the query to keep tests stable.
    """
    if not query or query == '=""':
        return {"results": [], "total_results": 0, "total_pages": 0}

    # Generate a stable fake ID from the query string
    media_id = str(abs(hash(query)) % 100000)
    return {
        "results": [
            {
                "media_id": media_id,
                "title": query,
                "image": "http://example.com/cover.jpg",
                "source": source or "mock",
            },
        ],
        "total_results": 1,
        "total_pages": 1,
    }


def _mock_api_request(provider, method, url, params=None, data=None,
                      headers=None, response_format="json"):
    """Mock API request that returns appropriate data based on provider."""
    # AniList GraphQL responses
    if "anilist" in url.lower() or provider == "ANILIST":
        return {"data": {"MediaListCollection": {"lists": []}}}
    # MAL responses
    if "myanimelist" in url.lower() or provider == "MAL":
        return {"data": []}
    # Simkl responses
    if "simkl" in url.lower() or provider == "SIMKL":
        if "token" in url.lower() or "oauth" in url.lower():
            return {"access_token": "mock_token"}
        if "user" in url.lower():
            return {"user": {"name": "test"}}
        return []
    # GitHub anime mapping
    if provider == "GITHUB":
        return []
    return {}


_TMDB_ID_MAP = {
    # Known IMDB → TMDB mappings used by test fixtures
    "tt0468569": (155, "movie", "The Dark Knight"),
    "tt0111161": (278, "movie", "The Shawshank Redemption"),
    "tt0944947": (1399, "tv", "Game of Thrones"),
    "tt16968450": (1096028, "movie", "The Wonderful Story of Henry Sugar"),
    "tt7366338": (87108, "tv", "Chernobyl"),
    "tt0475293": (13649, "movie", "High School Musical"),
    "tt13623136": (774752, "movie", "The Guardians of the Galaxy Holiday Special"),
    "tt1117563": (13851, "movie", "Batman: Gotham Knight"),
}


def _mock_tmdb_find(external_id, external_source):
    """Mock TMDB find that returns plausible results for any external ID."""
    if external_id in _TMDB_ID_MAP:
        tmdb_id, media_type, title = _TMDB_ID_MAP[external_id]
    else:
        tmdb_id = abs(hash(external_id)) % 100000
        media_type = "movie"
        title = f"Mock ({external_id})"

    if media_type == "tv":
        return {
            "movie_results": [],
            "tv_results": [
                {
                    "id": tmdb_id,
                    "name": title,
                    "poster_path": f"/mock_{tmdb_id}.jpg",
                    "first_air_date": "2020-01-01",
                },
            ],
        }
    return {
        "movie_results": [
            {
                "id": tmdb_id,
                "title": title,
                "poster_path": f"/mock_{tmdb_id}.jpg",
                "release_date": "2020-01-01",
            },
        ],
        "tv_results": [],
    }


def _mock_tv_with_seasons(tmdb_id, season_numbers=None):
    """Mock TMDB tv_with_seasons for import tests."""
    seasons = season_numbers or [1]
    result = {
        "title": f"Mock TV {tmdb_id}",
        "image": "http://example.com/tv.jpg",
        "max_progress": len(seasons),
        "details": {"seasons": len(seasons)},
        "related": {"seasons": [
            {"season_number": sn, "image": "http://example.com/s.jpg",
             "first_air_date": datetime(2020, 1, 1, tzinfo=UTC)}
            for sn in seasons
        ]},
    }
    for sn in seasons:
        result[f"season/{sn}"] = {
            "image": "http://example.com/s.jpg",
            "season_number": sn,
            "max_progress": 12,
            "episodes": [
                {"episode_number": i, "image": "http://example.com/ep.jpg",
                 "air_date": datetime(2020, 1, i, tzinfo=UTC),
                 "still_path": f"/ep_{sn}_{i}.jpg"}
                for i in range(1, 13)
            ],
        }
    return result


def _mock_tmdb_movie(tmdb_id):
    """Mock TMDB movie for import tests."""
    return {
        "title": f"Mock Movie {tmdb_id}",
        "image": "http://example.com/movie.jpg",
        "max_progress": 1,
    }


def _mock_mal_anime(mal_id):
    """Mock MAL anime for import tests."""
    return {
        "title": f"Mock Anime {mal_id}",
        "image": "http://example.com/anime.jpg",
        "max_progress": 24,
    }


@pytest.fixture(autouse=True)
def _mock_external_apis(request):
    """Auto-mock all external API calls to prevent network access.

    Skips auto-mocking for tests in the providers/ directory since those
    have their own fine-grained mocks.
    """
    test_path = str(request.fspath)
    if "/tests/providers/" in test_path or "/tests/calendar/" in test_path:
        yield
        return

    # Import tests: mock provider functions that hit external APIs directly.
    # Tests with @patch("requests.Session.*") handle their own HTTP mocking at
    # a lower layer — those patches won't conflict with these higher-level mocks.
    # Note: api_request is also mocked because some tests (anilist, mal, simkl)
    # patch _get_user_list but the importer still calls tmdb/mal providers via
    # api_request for metadata lookups.
    if "/tests/imports/" in test_path:
        with (
            patch(
                "app.providers.services.get_media_metadata",
                side_effect=_mock_get_media_metadata,
            ),
            patch(
                "app.providers.services.search",
                side_effect=_mock_search,
            ),
            patch(
                "app.providers.tmdb.find",
                side_effect=_mock_tmdb_find,
            ),
            patch(
                "app.providers.tmdb.get_image_url",
                side_effect=lambda path: f"http://example.com{path}" if path else "",
            ),
            patch(
                "app.providers.tmdb.tv_with_seasons",
                side_effect=_mock_tv_with_seasons,
            ),
            patch(
                "app.providers.tmdb.movie",
                side_effect=_mock_tmdb_movie,
            ),
            patch(
                "app.providers.mal.anime",
                side_effect=_mock_mal_anime,
            ),
        ):
            yield
        return

    with (
        patch(
            "app.providers.services.get_media_metadata",
            side_effect=_mock_get_media_metadata,
        ),
        patch(
            "app.providers.services.search",
            side_effect=_mock_search,
        ),
        patch(
            "app.providers.services.api_request",
            return_value={},
        ),
        patch(
            "app.providers.tmdb.watch_provider_regions",
            return_value=[],
        ),
    ):
        yield
