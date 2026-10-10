import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from app import helpers
from app.providers import commons, services, wikidata, wikipedia


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class StageArtworkTests(SimpleTestCase):
    """Keep both image sources without policy interpretation or recovery loops."""

    def setUp(self):
        """Load shared provider fixtures with an isolated cache."""
        cache.clear()
        self.addCleanup(cache.clear)
        self.fixture = json.loads(
            (Path(__file__).parent / "mock_data/stage_artwork.json").read_text(),
        )
        self.page = self.fixture["commons"]["query"]["pages"]["123"]
        self.work = {
            "media_id": "Q822850",
            "work_revision": 100,
            "artwork_candidates": ["Test stage photograph.jpg"],
        }

    def test_batch_deduplicates_files_and_preserves_work_identity(self):
        """Shared files require one request and retain independent work IDs."""
        other = {**self.work, "media_id": "Q12345"}
        with patch(
            "app.providers.services.api_request",
            return_value=self.fixture["commons"],
        ) as fetch:
            result = commons.artworks([self.work, other])
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(result["Q822850"]["work_id"], "Q822850")
            self.assertEqual(result["Q12345"]["work_id"], "Q12345")
            self.assertEqual(result["Q822850"]["image"], result["Q12345"]["image"])
            self.assertEqual(commons.artworks([self.work, other]), result)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(fetch.call_args.kwargs["params"]["prop"], "imageinfo")

    def test_candidate_order_and_filename_aliases(self):
        """Use the first usable direct claim, following only returned aliases."""
        self.work["artwork_candidates"] = ["Missing.jpg", "Original.jpg"]
        response = deepcopy(self.fixture["commons"])
        response["query"]["pages"]["-1"] = {"title": "File:Missing.jpg", "missing": ""}
        response["query"].update(
            normalized=[{"from": "File:Original.jpg", "to": "File:Normalized.jpg"}],
            redirects=[{"from": "File:Normalized.jpg", "to": self.page["title"]}],
        )
        with patch(
            "app.providers.services.api_request", return_value=response
        ) as fetch:
            artwork = commons.artworks([self.work])["Q822850"]
        self.assertEqual(artwork["artist"], "Test Photographer")
        self.assertEqual(fetch.call_count, 1)

    def test_no_license_allowlist_or_template_interpretation(self):
        """Use source-reported grants without requiring authors or policy evidence."""
        for license_name in ("Fair use", "Public domain", "CC BY-NC 4.0", ""):
            with self.subTest(license=license_name):
                page = deepcopy(self.page)
                page["templates"] = []
                page["imageinfo"][0]["extmetadata"] = {
                    "LicenseShortName": {"value": license_name},
                    "UsageTerms": {"value": "Source supplied terms"},
                }
                artwork = commons.from_file(
                    page, "Q822850", source_host="commons.wikimedia.org"
                )
                self.assertTrue(artwork["image"])
                self.assertEqual(artwork["notices"], "Source supplied terms")
                self.assertEqual(
                    commons.restored_artwork(artwork, "Q822850", artwork["image"]),
                    artwork,
                )

    def test_bad_files_and_unsafe_urls_are_not_displayed(self):
        """Retain basic rendering safeguards without assessing copyright."""
        for changes in (
            {"badfile": ""},
            {"thumburl": "javascript:alert(1)"},
            {"descriptionurl": "https://commons.wikimedia.org.evil.example/file"},
            {"mime": "text/html"},
            {"extmetadata": {"DeletionReason": {"value": "Removed"}}},
        ):
            with self.subTest(changes=changes):
                page = deepcopy(self.page)
                page["imageinfo"][0].update(changes)
                self.assertEqual(
                    commons.from_file(
                        page, "Q822850", source_host="commons.wikimedia.org"
                    ),
                    {},
                )

    def test_batch_failure_is_not_retried_or_cached_as_absence(self):
        """An outage is retryable on a later request, without a recovery cascade."""
        with patch(
            "app.providers.services.api_request", side_effect=requests.Timeout
        ) as fetch:
            self.assertIsNone(commons.artworks([self.work])["Q822850"])
            self.assertEqual(fetch.call_count, 1)
        with patch(
            "app.providers.services.api_request", return_value=self.fixture["commons"]
        ):
            self.assertTrue(commons.artworks([self.work])["Q822850"])

    def test_rate_limit_stops_later_requests(self):
        """Respect provider back-pressure with a shared cooldown."""
        response = requests.Response()
        response.status_code = 429
        with patch(
            "app.providers.services.api_request",
            side_effect=requests.HTTPError(response=response),
        ) as fetch:
            self.assertIsNone(commons.artworks([self.work])["Q822850"])
            self.assertIsNone(commons.artworks([self.work])["Q822850"])
            self.assertEqual(fetch.call_count, 1)

    def test_five_candidates_and_fifty_files_per_request(self):
        """A displayed page requires at most two Commons batches."""
        works = [
            {
                **self.work,
                "media_id": f"Q{index + 1}",
                "artwork_candidates": [
                    f"{index}-{candidate}.jpg" for candidate in range(7)
                ],
            }
            for index in range(20)
        ]
        with patch(
            "app.providers.services.api_request", return_value={"query": {"pages": {}}}
        ) as fetch:
            commons.artworks(works)
        self.assertEqual(fetch.call_count, 2)
        for call in fetch.call_args_list:
            self.assertEqual(len(call.kwargs["params"]["titles"].split("|")), 50)

    def test_wikipedia_priority_and_commons_fallback(self):
        """Keep Commons-only coverage and avoid fallback for Wikipedia successes."""
        article = self.fixture["wikipedia"]["article"]
        file_page = self.fixture["wikipedia"]["file"]
        artwork = wikipedia.checked_artwork("Q19320959", "en", article, file_page)
        self.assertEqual(artwork["license"], "Fair use")
        with patch(
            "app.providers.services.api_request", return_value=self.fixture["commons"]
        ) as fetch:
            result = wikidata.illustrate_page(
                [{**self.work, "media_id": "Q19320959"}, self.work],
                {"Q19320959": artwork, "Q822850": None},
            )
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result[0]["image"], artwork["image"])
        self.assertTrue(result[1]["stage_artwork"])
        self.assertTrue(result[1]["artwork_unavailable"])
        wrong_article = {**article, "pageprops": {"wikibase_item": "Q999"}}
        self.assertEqual(
            wikipedia.checked_artwork("Q19320959", "en", wrong_article, file_page), {}
        )

    def test_empty_candidates_need_no_request(self):
        """Missing direct images do not trigger discovery."""
        with patch("app.providers.services.api_request") as fetch:
            self.assertEqual(
                commons.artworks([{**self.work, "artwork_candidates": []}]),
                {"Q822850": {}},
            )
        fetch.assert_not_called()

    def test_wikipedia_lookup_outcomes_and_cache(self):
        """Distinguish missing imagery from failed lookups without live requests."""
        fixture = self.fixture["wikipedia"]
        work = {
            **self.work,
            "media_id": "Q19320959",
            "wikipedia_sitelinks": {"en": fixture["article"]["title"]},
        }
        for change, expected in (
            ({}, True),
            ({"missing": True}, False),
            ({"pageimage": ""}, False),
            ({"pageprops": {"wikibase_item": "Q999"}}, False),
            ({"pageprops": None}, None),
            ({"pageimage": []}, None),
        ):
            with self.subTest(change=change):
                cache.clear()
                responses = [
                    {"query": {"pages": [{**fixture["article"], **change}]}},
                    {"query": {"pages": [fixture["file"]]}},
                ]
                with patch(
                    "app.providers.services.api_request", side_effect=responses
                ) as fetch:
                    result = wikipedia.artworks([work])["Q19320959"]
                    self.assertEqual(
                        fetch.call_args_list[0].kwargs["params"]["pilicense"], "any"
                    )
                    self.assertEqual(None if result is None else bool(result), expected)
                    if expected is not None:
                        count = fetch.call_count
                        self.assertEqual(
                            wikipedia.artworks([work])["Q19320959"], result
                        )
                        self.assertEqual(fetch.call_count, count)
        for failure in (
            requests.Timeout(),
            {"error": {}},
            {"query": {"pages": [None]}},
        ):
            cache.clear()
            with patch("app.providers.services.api_request", side_effect=[failure]):
                self.assertIsNone(wikipedia.artworks([work])["Q19320959"])
        cache.clear()
        with patch(
            "app.providers.services.api_request",
            side_effect=[
                {"query": {"pages": [fixture["article"]]}},
                requests.Timeout(),
            ],
        ):
            self.assertIsNone(wikipedia.artworks([work])["Q19320959"])

    def test_wikipedia_multilingual_partial_lookup_and_aliases(self):
        """Language fallback keeps images retryable after a preferred-source outage."""
        fixture = self.fixture["wikipedia"]
        work = {
            **self.work,
            "media_id": "Q19320959",
            "wikipedia_sitelinks": {"en": "Original", "fr": "Original"},
        }
        article = {
            "query": {
                "pages": [fixture["article"]],
                "normalized": [
                    {"from": "Original", "to": fixture["article"]["title"]},
                ],
            }
        }
        file_page = deepcopy(fixture["file"])
        file_page["imagerepository"] = "shared"
        file_page["imageinfo"][0]["descriptionurl"] = (
            "https://commons.wikimedia.org/wiki/File:Hamilton-poster.jpg"
        )
        with patch(
            "app.providers.services.api_request",
            side_effect=[
                requests.Timeout(),
                article,
                {"query": {"pages": [file_page]}},
            ],
        ):
            result = wikipedia.artworks([work])["Q19320959"]
        self.assertTrue(result["image"])
        self.assertEqual(result["lookup_incomplete"], "yes")
        self.assertIsNone(cache.get(wikipedia.cache_key("Q19320959")))
        response = requests.Response()
        response.status_code = 429
        with patch(
            "app.providers.services.api_request",
            side_effect=requests.HTTPError(response=response),
        ) as fetch:
            self.assertIsNone(wikipedia.artworks([work])["Q19320959"])
            self.assertEqual(fetch.call_count, 1)

    def test_partial_lookup_preserves_saved_poster(self):
        """Display fallback images without replacing saved posters during outages."""
        saved = commons.from_file(
            self.page, "Q822850", source_host="commons.wikimedia.org"
        )
        saved["image"] = "https://thumb.wikimedia.org/saved-poster.png"
        metadata = wikidata.illustrate(
            {**self.work, "media_type": "stage"},
            commons.from_file(
                self.page, "Q822850", source_host="commons.wikimedia.org"
            ),
            unavailable=True,
        )
        helpers.preserve_stage_artwork(
            metadata,
            SimpleNamespace(
                media_type="stage",
                stage_artwork=saved,
                image=saved["image"],
            ),
        )
        self.assertEqual(metadata["image"], saved["image"])
        with self.assertRaises(services.ProviderAPIError):
            commons.require_available(metadata)

    def test_credits_have_readable_labels_and_entities(self):
        """Source links remain visible and HTML entities display as plain text."""
        self.assertEqual(commons.credit_text("Alice &amp; Bob"), "Alice & Bob")
        artwork = commons.from_file(
            self.page, "Q822850", source_host="commons.wikimedia.org"
        )
        for field in ("title", "license"):
            incomplete = {key: value for key, value in artwork.items() if key != field}
            self.assertEqual(
                commons.restored_artwork(incomplete, "Q822850", artwork["image"]), {}
            )
        self.assertEqual(
            commons.restored_artwork(
                {**artwork, "title": 123}, "Q822850", artwork["image"]
            ),
            {},
        )
