"""Pytest conftest with rich mock data for Playwright integration tests.

Provides realistic search results and metadata so the browser-driven tests
can run without network access inside a NixOS VM test.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import django.conf
import pytest
from app.providers import manual as _manual_provider

# Breaking Bad episode counts per season (TMDB data)
_BB_SEASON_EPISODES = {1: 7, 2: 13, 3: 13, 4: 13, 5: 16}
_BB_TOTAL_EPISODES = sum(_BB_SEASON_EPISODES.values())  # 62


def _bb_episodes(sn):
    """Build episode list for a Breaking Bad season (raw TMDB format).

    Episodes must include still_path, name, overview, runtime, vote_count
    because tmdb.process_episodes() reads those fields.
    air_date must be a string (YYYY-MM-DD) — that's how TMDB returns it.
    """
    ep_count = _BB_SEASON_EPISODES.get(sn, 10)
    episodes = []
    for i in range(1, ep_count + 1):
        if sn == 1:
            d = datetime(2008, 1, 20 + (i - 1), tzinfo=UTC)
        else:
            d = datetime(2009, 3, i, tzinfo=UTC)
        episodes.append({
            "episode_number": i,
            "still_path": None,
            "name": f"Episode {i}",
            "overview": "",
            "runtime": 45,
            "vote_count": 100,
            "air_date": d.strftime("%Y-%m-%d"),
        })
    return episodes


def _bb_tv_metadata():
    """Full Breaking Bad TV metadata matching tmdb.process_tv() output."""
    num_seasons = 5
    result = {
        "media_id": 1396,
        "source": "tmdb",
        "media_type": "tv",
        "title": "Breaking Bad",
        "image": "http://example.com/poster.jpg",
        "max_progress": _BB_TOTAL_EPISODES,
        "synopsis": "Mock synopsis",
        "genres": [],
        "score": "8.9",
        "score_count": 1000,
        "source_url": "https://www.themoviedb.org/tv/1396",
        "details": {
            "format": "TV",
            "first_air_date": datetime(2008, 1, 20, tzinfo=UTC),
            "last_air_date": "2013-09-29",
            "status": "Ended",
            "seasons": num_seasons,
            "episodes": _BB_TOTAL_EPISODES,
            "runtime": "45m",
            "studios": "Sony Pictures Television",
            "country": "US",
            "languages": "English",
        },
        "related": {
            "seasons": [
                {
                    "media_id": 1396,
                    "source": "tmdb",
                    "media_type": "season",
                    "title": "Breaking Bad",
                    "season_number": sn,
                    "season_title": f"Season {sn}",
                    "image": "http://example.com/season.jpg",
                    "first_air_date": datetime(2008, 1, 20, tzinfo=UTC),
                    "max_progress": _BB_SEASON_EPISODES[sn],
                }
                for sn in range(1, num_seasons + 1)
            ],
            "recommendations": [],
        },
        "tvdb_id": None,
        "external_links": {},
        "last_episode_season": num_seasons,
        "next_episode_season": None,
        "providers": {},
    }
    # Attach season data for tv_with_seasons calls
    # Matches output of process_season() + enrich_season_with_tv_data()
    for sn in range(1, num_seasons + 1):
        ep_count = _BB_SEASON_EPISODES[sn]
        result[f"season/{sn}"] = {
            "source": "tmdb",
            "media_type": "season",
            "media_id": 1396,
            "title": "Breaking Bad",
            "season_title": f"Season {sn}",
            "season_number": sn,
            "max_progress": ep_count,
            "image": "http://example.com/season.jpg",
            "synopsis": "Mock synopsis",
            "score": "9.0",
            "score_count": 500,
            "source_url": f"https://www.themoviedb.org/tv/1396/season/{sn}",
            "tvdb_id": None,
            "external_links": {},
            "genres": [],
            "details": {
                "first_air_date": datetime(2008, 1, 20, tzinfo=UTC),
                "last_air_date": "2008-03-09",
                "episodes": ep_count,
                "runtime": "45m",
                "total_runtime": f"{ep_count * 45}m",
            },
            "episodes": _bb_episodes(sn),
            "providers": {},
        }
    return result


def _mock_get_media_metadata(media_type, media_id, source,
                             season_numbers=None, episode_number=None):
    """Return realistic metadata for Breaking Bad and Perfect Blue."""

    # --- manual source: delegate to real manual provider (reads from DB) ---
    if source == "manual":
        if media_type == "season":
            return _manual_provider.season(media_id, season_numbers[0])
        if media_type == "episode":
            return _manual_provider.episode(
                media_id, season_numbers[0], episode_number,
            )
        real_type = "tv" if media_type == "tv_with_seasons" else media_type
        return _manual_provider.metadata(media_id, real_type)

    # --- Breaking Bad (TMDB, id=1396) ---
    if source == "tmdb" and str(media_id) == "1396":
        full = _bb_tv_metadata()
        if media_type == "season":
            # Real services.get_media_metadata indexes tv_with_seasons[season/N]
            sn = season_numbers[0] if season_numbers else 1
            return full[f"season/{sn}"]
        return full

    # --- Perfect Blue (MAL, id=437) ---
    if source == "mal" and str(media_id) == "437":
        return {
            "media_id": 437,
            "source": "mal",
            "media_type": "anime",
            "title": "Perfect Blue",
            "image": "http://example.com/poster.jpg",
            "max_progress": 1,
            "synopsis": "Mock synopsis",
            "genres": [],
            "score": "8.5",
            "score_count": 500,
            "details": {},
            "related": {},
        }

    # --- Fallback for any other media ---
    if media_type in ("tv_with_seasons", "tv"):
        num_seasons = 10
        result = {
            "media_id": media_id,
            "source": source,
            "media_type": "tv",
            "title": "Test Show",
            "image": "http://example.com/image.jpg",
            "max_progress": num_seasons,
            "details": {"seasons": num_seasons},
            "related": {
                "seasons": [
                    {
                        "media_id": media_id,
                        "source": source,
                        "media_type": "season",
                        "title": "Test Show",
                        "season_number": sn,
                        "season_title": f"Season {sn}",
                        "image": "http://example.com/season.jpg",
                        "first_air_date": datetime(2020, 1, 1, tzinfo=UTC),
                        "max_progress": 24,
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

    if media_type == "game":
        return {
            "title": "Test Game",
            "image": "http://example.com/image.jpg",
            "max_progress": None,
        }

    return {
        "title": "Test Media",
        "image": "http://example.com/image.jpg",
        "max_progress": 26,
    }


def _mock_search(media_type, query, page, source=None):
    """Return search results for Breaking Bad and Perfect Blue."""
    q = query.lower()

    if "breaking bad" in q:
        return {
            "page": 1,
            "total_results": 1,
            "total_pages": 1,
            "results": [
                {
                    "media_id": 1396,
                    "source": "tmdb",
                    "media_type": "tv",
                    "title": "Breaking Bad",
                    "image": "http://example.com/poster.jpg",
                },
            ],
        }

    if "perfect blue" in q:
        return {
            "page": 1,
            "total_results": 1,
            "total_pages": 1,
            "results": [
                {
                    "media_id": 437,
                    "source": "mal",
                    "media_type": "anime",
                    "title": "Perfect Blue",
                    "image": "http://example.com/poster.jpg",
                },
            ],
        }

    # Default: empty results
    return {
        "page": 1,
        "total_results": 0,
        "total_pages": 1,
        "results": [],
    }


@pytest.fixture(autouse=True, scope="session")
def _disable_trusted_ip_header():
    """Remove ALLAUTH_TRUSTED_CLIENT_IP_HEADER so login works without a proxy."""
    settings = django.conf.settings
    if hasattr(settings, "ALLAUTH_TRUSTED_CLIENT_IP_HEADER"):
        delattr(settings, "ALLAUTH_TRUSTED_CLIENT_IP_HEADER")
    yield


@pytest.fixture(autouse=True)
def _mock_external_apis(request):
    """Auto-mock all external API calls for Playwright integration tests."""
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
