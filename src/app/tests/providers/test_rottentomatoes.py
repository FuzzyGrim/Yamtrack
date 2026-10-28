from pathlib import Path
from unittest.mock import patch

import requests
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings

from app.providers import rottentomatoes

mock_path = Path(__file__).resolve().parent.parent / "mock_data"

# Trimmed search page: movie rows use hyphenated attributes, TV rows do not
SEARCH_HTML = (mock_path / "rottentomatoes_search.html").read_text()
PAGE_HTML = (mock_path / "rottentomatoes_page.html").read_text()


@override_settings(ROTTEN_TOMATOES=True, RT_TITLE_MATCH_THRESHOLD=0.75)
class RottenTomatoesScores(TestCase):
    """Test fetching Tomatometer and Popcornmeter from rottentomatoes.com."""

    def setUp(self):
        """Clear the cache between tests."""
        cache.clear()

    @patch("app.providers.rottentomatoes.services.api_request")
    def test_search(self, mock_api_request):
        """Test parsing search results of both attribute spellings."""
        mock_api_request.return_value = SEARCH_HTML

        movies = rottentomatoes.search("dune", "movie")
        self.assertEqual(len(movies), 4)
        self.assertEqual(movies[0]["title"], "Dune: Part Two")
        self.assertEqual(movies[0]["year"], "2024")
        self.assertEqual(movies[1]["url"], "https://www.rottentomatoes.com/m/dune_2021")
        self.assertEqual(movies[3]["title"], "Jodorowsky's Dune")

        series = rottentomatoes.search("dune", "tvSeries")
        self.assertEqual(
            series,
            [
                {
                    "title": "Dune: Prophecy",
                    "url": "https://www.rottentomatoes.com/tv/dune_prophecy",
                    "year": "2024",
                },
            ],
        )

        self.assertEqual(
            mock_api_request.call_args.kwargs["params"],
            {"search": "dune"},
        )
        self.assertEqual(mock_api_request.call_args.kwargs["response_format"], "text")

    @patch("app.providers.rottentomatoes.services.api_request")
    def test_scores(self, mock_api_request):
        """Test that the result matching title and year is used."""
        mock_api_request.side_effect = [SEARCH_HTML, PAGE_HTML]

        response = rottentomatoes.scores(["Dune"], "movie", "2021")

        self.assertEqual(response["title"], "Dune")
        self.assertEqual(response["year"], "2021")
        self.assertEqual(response["url"], "https://www.rottentomatoes.com/m/dune_2021")
        self.assertTrue(response["exact"])
        self.assertEqual(
            response["tomatometer"],
            {"value": "83", "suffix": "%", "count": "480 reviews", "certified": True},
        )
        self.assertEqual(
            response["popcornmeter"],
            {
                "value": "90",
                "suffix": "%",
                "count": "10,000+ Ratings",
                "certified": False,
            },
        )
        self.assertEqual(mock_api_request.call_args.args[2], response["url"])

        # second call is served from cache
        rottentomatoes.scores(["Dune"], "movie", "2021")
        self.assertEqual(mock_api_request.call_count, 2)

    @patch("app.providers.rottentomatoes.services.api_request")
    def test_scores_year_disambiguates(self, mock_api_request):
        """Test that the same title from another year is chosen by year."""
        mock_api_request.side_effect = [SEARCH_HTML, PAGE_HTML]

        response = rottentomatoes.scores(["Dune"], "movie", "1984")

        self.assertEqual(response["url"], "https://www.rottentomatoes.com/m/dune_1984")

    @patch("app.providers.rottentomatoes.services.api_request")
    def test_scores_longer_title(self, mock_api_request):
        """Test that a longer title with a subtitle matches but is not exact."""
        mock_api_request.side_effect = [SEARCH_HTML, PAGE_HTML]

        response = rottentomatoes.scores(["Dune"], "movie", "2024")

        self.assertEqual(response["title"], "Dune: Part Two")
        self.assertFalse(response["exact"])

    @patch("app.providers.rottentomatoes.services.api_request")
    def test_scores_below_threshold(self, mock_api_request):
        """Test that results not similar enough are rejected and cached."""
        mock_api_request.return_value = SEARCH_HTML

        self.assertEqual(rottentomatoes.scores(["Blade Runner"], "movie", "1984"), {})
        self.assertEqual(rottentomatoes.scores(["Blade Runner"], "movie", "1984"), {})
        # only the search is requested, and only once
        mock_api_request.assert_called_once()

    @patch("app.providers.rottentomatoes.services.api_request")
    def test_scores_no_scorecard(self, mock_api_request):
        """Test a title page without scores."""
        mock_api_request.side_effect = [SEARCH_HTML, "<html></html>"]

        self.assertEqual(rottentomatoes.scores(["Dune"], "movie", "2021"), {})

    @patch("app.providers.rottentomatoes.services.api_request")
    def test_scores_hidden_audience_score(self, mock_api_request):
        """Test that a hidden or empty score is left out."""
        mock_api_request.side_effect = [
            SEARCH_HTML,
            PAGE_HTML.replace(
                '"hideAudienceScore": false', '"hideAudienceScore": true'
            ).replace('"score": "83"', '"score": ""'),
        ]

        response = rottentomatoes.scores(["Dune"], "movie", "2021")

        self.assertIsNone(response["tomatometer"])
        self.assertIsNone(response["popcornmeter"])

    @patch("app.providers.rottentomatoes.services.api_request")
    def test_scores_page_not_found(self, mock_api_request):
        """Test that a removed title page is cached as having no scores."""
        error = requests.exceptions.HTTPError(
            response=type("Response", (), {"status_code": 404})(),
        )
        mock_api_request.side_effect = [SEARCH_HTML, error]

        self.assertEqual(rottentomatoes.scores(["Dune"], "movie", "2021"), {})
        self.assertEqual(rottentomatoes.scores(["Dune"], "movie", "2021"), {})
        self.assertEqual(mock_api_request.call_count, 2)

    @patch("app.providers.rottentomatoes.services.api_request")
    def test_scores_request_error(self, mock_api_request):
        """Test that request failures return no scores and are not cached."""
        mock_api_request.side_effect = requests.exceptions.ConnectionError("down")

        self.assertEqual(rottentomatoes.scores(["Dune"], "movie", "2021"), {})

        mock_api_request.side_effect = [SEARCH_HTML, PAGE_HTML]
        self.assertEqual(
            rottentomatoes.scores(["Dune"], "movie", "2021")["tomatometer"]["value"],
            "83",
        )

    @override_settings(ROTTEN_TOMATOES=False)
    @patch("app.providers.rottentomatoes.services.api_request")
    def test_scores_disabled(self, mock_api_request):
        """Test that nothing is requested when disabled."""
        self.assertEqual(rottentomatoes.scores(["Dune"], "movie", "2021"), {})
        mock_api_request.assert_not_called()

    @patch("app.providers.rottentomatoes.scores")
    def test_media_scores(self, mock_scores):
        """Test how titles, type and year are derived from media metadata."""
        mock_scores.return_value = {"title": "Dune", "exact": True}

        response = rottentomatoes.media_scores(
            {
                "media_type": "movie",
                "title": "Dune",
                "details": {"release_date": "2021-10-22"},
            },
        )
        mock_scores.assert_called_with(["Dune"], "movie", "2021")
        self.assertTrue(response["exact"])

        rottentomatoes.media_scores(
            {
                "media_type": "tv",
                "title": "Breaking Bad",
                "details": {"first_air_date": "2008-01-20"},
            },
        )
        mock_scores.assert_called_with(["Breaking Bad"], "tvSeries", "2008")

        # anime is searched by its english title, falling back to the romaji one,
        # and the match is not exact when it differs from the displayed title
        mock_scores.return_value = {"title": "Attack on Titan", "exact": True}
        response = rottentomatoes.media_scores(
            {
                "media_type": "anime",
                "title": "Shingeki no Kyojin",
                "title_english": "Attack on Titan",
                "details": {"format": "Anime", "start_date": "2013-04-07"},
            },
        )
        mock_scores.assert_called_with(
            ["Attack on Titan", "Shingeki no Kyojin"], "tvSeries", "2013"
        )
        self.assertFalse(response["exact"])

        rottentomatoes.media_scores(
            {
                "media_type": "anime",
                "title": "Kimi no Na wa.",
                "title_english": None,
                "details": {"format": "Movie", "start_date": None},
            },
        )
        mock_scores.assert_called_with(["Kimi no Na wa."], "movie", None)

        mock_scores.reset_mock()
        self.assertEqual(
            rottentomatoes.media_scores({"media_type": "game", "title": "Doom"}),
            {},
        )
        mock_scores.assert_not_called()

    @patch("app.providers.rottentomatoes.services.api_request")
    def test_season_scores(self, mock_api_request):
        """Test that a season uses the page below the matched show."""
        mock_api_request.side_effect = [SEARCH_HTML, PAGE_HTML, PAGE_HTML]
        tv_metadata = {
            "media_type": "tv",
            "title": "Dune: Prophecy",
            "details": {"first_air_date": "2024-11-17"},
        }

        response = rottentomatoes.season_scores(tv_metadata, 2)

        self.assertEqual(
            response["url"], "https://www.rottentomatoes.com/tv/dune_prophecy/s02"
        )
        self.assertEqual(response["title"], "Dune: Prophecy")
        self.assertEqual(response["season"], 2)
        self.assertIsNone(response["year"])
        self.assertTrue(response["exact"])
        self.assertEqual(response["tomatometer"]["value"], "83")
        self.assertEqual(response["popcornmeter"]["value"], "90")
        self.assertEqual(mock_api_request.call_args.args[2], response["url"])

        # the show and season are both cached
        rottentomatoes.season_scores(tv_metadata, 2)
        self.assertEqual(mock_api_request.call_count, 3)

    @patch("app.providers.rottentomatoes.services.api_request")
    def test_season_scores_not_found(self, mock_api_request):
        """Test that a season missing on Rotten Tomatoes is cached as no scores."""
        error = requests.exceptions.HTTPError(
            response=type("Response", (), {"status_code": 404})(),
        )
        mock_api_request.side_effect = [SEARCH_HTML, PAGE_HTML, error]
        tv_metadata = {"media_type": "tv", "title": "Dune: Prophecy", "details": {}}

        self.assertEqual(rottentomatoes.season_scores(tv_metadata, 9), {})
        self.assertEqual(rottentomatoes.season_scores(tv_metadata, 9), {})
        self.assertEqual(mock_api_request.call_count, 3)

    @patch("app.providers.rottentomatoes.services.api_request")
    def test_season_scores_without_show(self, mock_api_request):
        """Test that specials and unmatched shows request no season page."""
        tv_metadata = {"media_type": "tv", "title": "Blade Runner", "details": {}}

        self.assertEqual(rottentomatoes.season_scores(tv_metadata, 0), {})
        mock_api_request.assert_not_called()

        mock_api_request.return_value = SEARCH_HTML
        self.assertEqual(rottentomatoes.season_scores(tv_metadata, 1), {})
        # only the show search was requested
        mock_api_request.assert_called_once()

    def test_title_similarity(self):
        """Test title comparison and the longer title rule."""
        self.assertEqual(
            rottentomatoes.title_similarity("Steins;Gate", "Steins Gate"), 1
        )
        self.assertEqual(rottentomatoes.title_similarity("The Office", "Office"), 1)
        self.assertEqual(rottentomatoes.title_similarity("Amélie", "Amelie"), 1)
        self.assertEqual(
            rottentomatoes.title_similarity("Tom & Jerry", "Tom and Jerry"), 1
        )
        self.assertGreaterEqual(
            rottentomatoes.title_similarity("Dune", "Dune: Part Two"),
            rottentomatoes.PREFIX_SIMILARITY,
        )
        # a word boundary is required for the longer title rule
        self.assertLess(
            rottentomatoes.title_similarity("Dune", "Duneland Adventures"), 0.5
        )
        self.assertLess(
            rottentomatoes.title_similarity("Dune", "Blade Runner"),
            settings.RT_TITLE_MATCH_THRESHOLD,
        )
        self.assertEqual(rottentomatoes.title_similarity("", "Dune"), 0)

    def test_best_match_prefers_exact_and_year(self):
        """Test that an exact title from the right year beats a longer title."""
        candidates = [
            {"title": "Dune: Part Two", "url": "u1", "year": "2024"},
            {"title": "Dune", "url": "u2", "year": "2021"},
            {"title": "Dune", "url": "u3", "year": None},
        ]

        match = rottentomatoes.best_match(candidates, ["Dune"], "2021")
        self.assertEqual(match["url"], "u2")
        self.assertTrue(match["exact"])

        # unknown candidate years are allowed, known mismatches are not
        match = rottentomatoes.best_match(candidates, ["Dune"], "2000")
        self.assertEqual(match["url"], "u3")

        self.assertIsNone(rottentomatoes.best_match(candidates, ["Alien"], "2021"))
        self.assertIsNone(rottentomatoes.best_match([], ["Dune"], "2021"))
