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
    """Return an empty search result."""
    return []


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
            "app.providers.tmdb.watch_provider_regions",
            return_value=[],
        ),
    ):
        yield
