import csv
import json
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from threading import Barrier, local
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from app import stage
from app.models import (
    Item,
    MediaTypes,
    Movie,
    Sources,
    Stage,
    StageRedirect,
)
from app.providers import wikidata
from lists.models import CustomList


class MediaSearchViewTests(TestCase):
    """Test the media search view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    @patch("app.providers.services.search")
    def test_media_search_view(self, mock_search):
        """Test the media search view."""
        mock_search.return_value = {
            "page": 1,
            "total_results": 1,
            "total_pages": 1,
            "results": [
                {
                    "media_id": "238",
                    "title": "Test Movie",
                    "media_type": MediaTypes.MOVIE.value,
                    "source": Sources.TMDB.value,
                    "image": "http://example.com/image.jpg",
                },
            ],
        }

        response = self.client.get(
            reverse("search") + "?media_type=movie&q=test",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/search.html")

        self.user.refresh_from_db()
        self.assertEqual(self.user.last_search_type, MediaTypes.MOVIE.value)

        mock_search.assert_called_once_with(
            MediaTypes.MOVIE.value,
            "test",
            1,
            Sources.TMDB.value,
        )


class StageRedirectConcurrencyTests(TransactionTestCase):
    """Exercise concurrent HTTP observations against a locking database backend."""

    @skipUnlessDBFeature("has_select_for_update")
    def test_competing_redirects_preserve_one_consistent_identity(self):
        """Concurrent conflicting responses cannot split evidence from attendance."""
        user = get_user_model().objects.create_user(username="concurrent-attendee")
        old = Item.objects.create(
            media_id="Q998",
            source="wikidata",
            media_type="stage",
            title="Saved work",
            image="",
            stage_forms=["play"],
        )
        attendance = Stage.objects.create(item=old, user=user, notes="Keep my visit")
        barrier = Barrier(2)
        thread_state = local()
        fixture = json.loads(
            (Path(__file__).parents[1] / "mock_data/stage_artwork.json").read_text()
        )
        fixture["work"]["claims"].pop("P18")

        def source_response(url, **_kwargs):
            if url == "https://commons.wikimedia.org/w/api.php":
                payload = {"query": {"search": []}}
            else:
                barrier.wait(timeout=15)
                payload = {
                    "entities": {
                        "Q998": {
                            **fixture["work"],
                            "id": thread_state.target,
                            "redirects": {"from": "Q998", "to": thread_state.target},
                        }
                    }
                }
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(payload).encode()
            return response

        def request_target(target):
            try:
                thread_state.target = target
                client = Client()
                client.force_login(user)
                return client.get(
                    reverse(
                        "media_details",
                        args=["wikidata", "stage", "Q998", "saved-work"],
                    )
                ).status_code
            finally:
                connections.close_all()

        cache.clear()
        self.addCleanup(cache.clear)
        with (
            patch("app.providers.services.session.get", side_effect=source_response),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            responses = list(executor.map(request_target, ["Q822850", "Q19320959"]))
        self.assertEqual(sorted(responses), [200, 500])
        attendance.refresh_from_db()
        self.assertEqual(
            attendance.item.media_id,
            StageRedirect.objects.get(alias_id="Q998").canonical_id,
        )
        self.assertEqual(attendance.notes, "Keep my visit")
        self.assertEqual(Item.objects.filter(media_type="stage").count(), 1)


class StageDiscoveryTests(TestCase):
    """Exercise discovery and tracking with deterministic external HTTP."""

    @staticmethod
    def entity(identifier, claims):
        """Build small source claims without repeating the Wikidata envelope."""
        return {
            "id": identifier,
            "lastrevid": 1,
            "labels": {"en": {"value": identifier}},
            "claims": {
                prop: [
                    {"mainsnak": {"datavalue": {"value": {"id": value}}}}
                    for value in values
                ]
                for prop, values in claims.items()
            },
        }

    def test_work_classification(self):
        """Recognize forms without admitting productions or screen adaptations."""
        cases = [
            ({"P31": ["Q25379"]}, ["play"]),
            ({"P31": ["Q2743"]}, ["musical"]),
            ({"P31": ["Q785522"]}, ["opera"]),
            ({"P31": ["Q15079786"]}, ["ballet"]),
            ({"P31": ["Q918727"]}, ["other"]),
            ({"P31": ["Q58483083"], "P136": ["Q2743"]}, ["musical"]),
            ({"P31": ["Q7725634"], "P7937": ["Q25379"]}, ["play"]),
            ({"P31": ["Q58483083"]}, []),
            ({"P31": ["Q2743", "Q7777570"]}, ["musical"]),
            ({"P31": ["Q7777570"], "P7937": ["Q2743"]}, []),
            ({"P31": ["Q11424"], "P7937": ["Q2743"]}, []),
            ({"P31": ["Q2743"], "P7937": ["Q25379"]}, ["play", "musical"]),
        ]
        production_credits = {prop: ["Q123"] for prop in ("P272", "P161", "P57")}
        cases += [
            ({**production_credits, "P31": ["Q2743"]}, ["musical"]),
            ({**production_credits, "P7937": ["Q2743"]}, []),
        ]
        for claims, expected in cases:
            with self.subTest(claims=claims):
                self.assertEqual(wikidata.forms(self.entity("Q100", claims)), expected)
        for description in ("theatre building", "theatrical production of Hamlet"):
            work = self.entity("Q100", {"P31": ["Q25379"]})
            work["descriptions"] = {"en": {"value": description}}
            self.assertEqual(wikidata.forms(work), [])

    def test_title_fallback_recovers_and_retries_optional_failures(self):
        """Empty title searches may shorten The; optional failures stay retryable."""
        original = self.source_response
        for fail in (True, False):
            calls = []

            def response(url, params, *, calls=calls, fail=fail, **kwargs):
                if params.get("list") == "search":
                    calls.append(params["srsearch"])
                    shortened = params["srsearch"] == 'inlabel:"Hamilton@*"'
                    if shortened and fail:
                        raise requests.Timeout
                    self.search_ids = ["Q19320959"] if shortened else []
                return original(url, params, **kwargs)

            with patch("app.providers.services.session.get", side_effect=response):
                result = wikidata.search("The Hamilton", 1)
            self.assertEqual(len(calls), 3)
            self.assertEqual(result["limited"], fail)
            self.assertEqual(len(result["results"]), 0 if fail else 1)

    def test_subtypes_cycles_and_cross_medium_genres(self):
        """Bound class traversal and distinguish genres from a work's medium."""
        self.entities.update(
            {
                "Q900": self.entity("Q900", {"P279": ["Q2743"]}),
                "Q901": self.entity("Q901", {"P279": ["Q901"]}),
                "Q902": self.entity("Q902", {"P279": ["Q2743", "Q11424"]}),
            }
        )
        cases = [
            ({"P31": ["Q900"]}, ["musical"]),
            ({"P31": ["Q901"]}, []),
            ({"P31": ["Q99999"]}, []),
            ({"P31": ["Q2743"], "P136": ["Q902"]}, ["musical"]),
            ({"P31": ["Q902"]}, []),
        ]
        for claims, expected in cases:
            with self.subTest(claims=claims):
                work = self.entity("Q100", claims)
                wikidata.classify_entities({"Q100": work}, {})
                self.assertEqual(wikidata.forms(work), expected)

    def setUp(self):
        """Provide work, adaptation, alias and non-work source responses."""
        cache.clear()
        self.user = get_user_model().objects.create_user(username="stage-reader")
        self.client.force_login(self.user)
        self.entities = {
            "Q19320959": self.entity(
                "Q19320959",
                {
                    "P31": ["Q58483083"],
                    "P7937": ["Q2743"],
                    "P86": ["Q1646482"],
                },
            ),
            "Q1646482": {
                "id": "Q1646482",
                "labels": {"en": {"value": "Lin-Manuel Miranda"}},
            },
            "Q999": self.entity("Q999", {"P31": ["Q11424"]}),
        }
        self.entities["Q19320959"]["labels"]["en"]["value"] = "Hamilton"
        self.entities["Q999"]["labels"]["en"]["value"] = "Hamilton film"
        self.entities["Q998"] = {
            **self.entities["Q19320959"],
            "redirects": {"from": "Q998", "to": "Q19320959"},
        }
        self.search_ids = ["Q999", "Q998", "Q19320959"]
        self.http = patch(
            "app.providers.services.session.get", side_effect=self.source_response
        )
        self.http.start()
        self.addCleanup(self.http.stop)
        self.addCleanup(cache.clear)

    def source_response(self, url, params, **_kwargs):
        """Return fixed Action API responses at the external boundary."""
        if url == "https://commons.wikimedia.org/w/api.php":
            response = requests.Response()
            response.status_code = 200
            response._content = b'{"query":{"search":[]}}'
            return response
        self.assertEqual(url, "https://www.wikidata.org/w/api.php")
        if params["action"] == "query":
            data = {
                "query": {
                    "search": [{"title": identifier} for identifier in self.search_ids],
                }
            }
        else:
            data = {
                "entities": {
                    identifier: self.entities.get(
                        identifier, {"id": identifier, "missing": ""}
                    )
                    for identifier in params["ids"].split("|")
                }
            }
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(data).encode()
        return response

    def test_label_fallback_failure_preserves_work_and_recovers_without_cache_delay(
        self,
    ):
        """Optional labels cannot erase works or change their provider identities."""
        self.entities["Q19320959"]["labels"] = {}
        self.search_ids = ["Q19320959"]
        label_payload = None

        def source_response(url, params, **kwargs):
            if (
                url == "https://www.wikidata.org/w/api.php"
                and params.get("props") == "labels"
                and "languages" not in params
            ):
                if label_payload == "timeout":
                    raise requests.Timeout
                response = requests.Response()
                response.status_code = 200
                response._content = json.dumps(label_payload).encode()
                return response
            return self.source_response(url, params, **kwargs)

        for payload in (
            "timeout",
            None,
            False,
            42,
            [],
            {"error": []},
            {"error": None},
            {
                "entities": {
                    "Q19320959": {"id": "Q19320959", "labels": {"nl": {"value": None}}}
                }
            },
            {"entities": {"Q19320959": []}},
            {
                "entities": {
                    "Q19320959": {
                        "id": "Q999",
                        "labels": {"en": {"value": "Wrong work"}},
                    }
                }
            },
        ):
            with self.subTest(payload=payload):
                cache.clear()
                label_payload = payload
                with patch(
                    "app.providers.services.session.get", side_effect=source_response
                ):
                    response = self.client.get(
                        reverse("search"), {"media_type": "stage", "q": "Hamilton"}
                    )
                    self.assertEqual(response.status_code, 200)
                    selected = response.context["data"]["results"][0]["item"]
                    self.assertEqual(selected["media_id"], "Q19320959")
                    self.assertEqual(selected["title"], "Q19320959")
                    label_payload = {
                        "entities": {
                            "Q19320959": {
                                "id": "Q19320959",
                                "labels": {"fr": {"value": "Hamilton"}},
                            }
                        }
                    }
                    recovered = self.client.get(
                        reverse("search"), {"media_type": "stage", "q": "Hamilton"}
                    )
                    self.assertEqual(
                        recovered.context["data"]["results"][0]["item"]["title"],
                        "Hamilton",
                    )
        self.assertFalse(StageRedirect.objects.exists())

    def test_search_details_and_tracking_use_one_work_identity(self):
        """Ordinary title search excludes film and resolves redirects on save."""
        listed = self.client.get(
            reverse("lists_modal", args=["wikidata", "stage", "Q998"])
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(Item.objects.get().media_id, "Q19320959")
        self.assertEqual(Item.objects.get().stage_forms, ["musical"])
        response = self.client.get(
            reverse("search"), {"media_type": "stage", "q": "Hamilton"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [work["item"]["media_id"] for work in response.context["data"]["results"]],
            ["Q19320959"],
        )
        self.assertContains(response, "Musical")
        self.assertContains(response, "Lin-Manuel Miranda")
        self.assertNotContains(response, "Hamilton film")
        details = self.client.get(
            reverse("media_details", args=["wikidata", "stage", "Q998", "hamilton"])
        )
        self.assertEqual(details.context["media"]["media_id"], "Q19320959")
        for identifier in ["Q998", "Q19320959"]:
            self.client.post(
                reverse("media_save"),
                {
                    "media_type": "stage",
                    "source": "wikidata",
                    "media_id": identifier,
                    "status": "Completed",
                    "venue": "Local Theatre",
                },
            )
        self.assertEqual(Item.objects.filter(media_type="stage").count(), 1)
        self.assertEqual(Stage.objects.filter(item__media_id="Q19320959").count(), 2)
        self.assertEqual(Item.objects.get(media_type="stage").stage_forms, ["musical"])

    def test_saved_redirect_preserves_attendance_history_and_memberships(self):
        """Verified redirects reconcile saved works without merging attendances."""
        old = Item.objects.create(
            media_id="Q998",
            source="wikidata",
            media_type="stage",
            title="Old Hamilton",
            image="",
            stage_forms=["musical"],
        )
        target = Item.objects.create(
            media_id="Q19320959",
            source="wikidata",
            media_type="stage",
            title="Hamilton",
            image="",
            stage_forms=["musical"],
        )
        first = Stage.objects.create(
            item=old, user=self.user, notes="First visit", venue="First Theatre"
        )
        second = Stage.objects.create(item=target, user=self.user, notes="Second visit")
        other = get_user_model().objects.create_user(username="other-attendee")
        private = Stage.objects.create(item=old, user=other, notes="Private visit")
        history_ids = list(first.history.values_list("history_id", flat=True))
        custom_list = CustomList.objects.create(name="Stage works", owner=self.user)
        custom_list.items.add(old, target)
        other.notification_excluded_items.add(old)
        old_export = b"".join(self.client.get(reverse("export_csv")).streaming_content)

        response = self.client.get(
            reverse("media_details", args=["wikidata", "stage", "Q998", "hamilton"])
        )
        self.assertContains(response, "First visit")
        self.assertContains(response, "Second visit")
        self.assertNotContains(response, "Private visit")
        for attendance in (first, second, private):
            attendance.refresh_from_db()
            self.assertEqual(attendance.item_id, target.pk)
        self.assertEqual(
            list(first.history.values_list("history_id", flat=True)), history_ids
        )
        self.assertEqual(
            list(custom_list.items.values_list("pk", flat=True)), [target.pk]
        )
        self.assertEqual(
            list(other.notification_excluded_items.values_list("pk", flat=True)),
            [target.pk],
        )
        self.assertEqual(Item.objects.filter(media_type="stage").count(), 1)
        reader = get_user_model().objects.create_user(username="restore-attendee")
        self.client.force_login(reader)
        with patch("app.providers.services.session.get", side_effect=requests.Timeout):
            self.client.post(
                reverse("import_yamtrack"),
                {
                    "mode": "new",
                    "yamtrack_csv": SimpleUploadedFile("old.csv", old_export),
                },
            )
            listing = self.client.get(
                reverse("medialist", args=[reader.username, "stage"])
            )
            self.assertEqual(listing.status_code, 200)
            modal = self.client.get(
                reverse("lists_modal", args=["wikidata", "stage", "Q998"])
            )
            self.assertEqual(modal.status_code, 200)
        self.assertEqual(
            set(Stage.objects.filter(user=reader).values_list("item_id", flat=True)),
            {target.pk},
        )
        self.assertEqual(Stage.objects.filter(user=reader).count(), 2)
        new_export = b"".join(self.client.get(reverse("export_csv")).streaming_content)
        self.assertEqual(
            {row["media_id"] for row in csv.DictReader(StringIO(new_export.decode()))},
            {"Q19320959"},
        )

    def test_unexpected_reference_aborts_redirect_without_data_loss(self):
        """Unexpected catalog references cannot be silently cascade-deleted."""
        old = Item.objects.create(
            media_id="Q998",
            source="wikidata",
            media_type="stage",
            title="Old Hamilton",
            image="",
            stage_forms=["musical"],
        )
        Item.objects.create(
            media_id="Q19320959",
            source="wikidata",
            media_type="stage",
            title="Hamilton",
            image="",
            stage_forms=["musical"],
        )
        attendance = Stage.objects.create(
            item=old, user=self.user, notes="Keep my history"
        )
        unexpected = Movie.objects.create(item=old, user=self.user, status="Planning")
        response = self.client.get(
            reverse("media_details", args=["wikidata", "stage", "Q998", "hamilton"])
        )
        self.assertContains(response, "saved records were not changed", status_code=500)
        attendance.refresh_from_db()
        self.assertEqual(attendance.item_id, old.pk)
        self.assertTrue(Movie.objects.filter(pk=unexpected.pk).exists())
        self.assertFalse(StageRedirect.objects.filter(alias_id="Q998").exists())

    def test_intermediate_redirect_returns_terminal_metadata_before_saving(self):
        """A provider's intermediate redirect cannot recreate a retired work ID."""
        StageRedirect.objects.create(
            alias_id="Q19320959", canonical_id="Q997", revision=100
        )
        self.entities["Q997"] = {
            **self.entities["Q19320959"],
            "id": "Q997",
            "labels": {"en": {"value": "Canonical Hamilton"}},
        }
        old = Item.objects.create(
            media_id="Q998",
            source="wikidata",
            media_type="stage",
            title="Saved Hamilton",
            image="",
            stage_forms=["musical"],
        )
        first = Stage.objects.create(item=old, user=self.user, notes="First visit")
        details = self.client.get(
            reverse("media_details", args=["wikidata", "stage", "Q998", "hamilton"])
        )
        self.assertContains(details, "First visit")
        self.assertContains(details, "Canonical Hamilton")
        self.entities["Q996"] = {
            **self.entities["Q19320959"],
            "redirects": {"from": "Q996", "to": "Q19320959"},
        }
        cache.clear()
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "Q996",
                "source": "wikidata",
                "media_type": "stage",
                "status": "Planning",
                "notes": "Second visit",
            },
        )
        self.assertEqual(
            set(
                Item.objects.filter(media_type="stage").values_list(
                    "media_id", flat=True
                )
            ),
            {"Q997"},
        )
        first.refresh_from_db()
        self.assertEqual(first.item.media_id, "Q997")
        self.assertEqual(
            set(Stage.objects.filter(item=first.item).values_list("notes", flat=True)),
            {"First visit", "Second visit"},
        )
        self.assertEqual(
            StageRedirect.objects.get(alias_id="Q998").canonical_id, "Q19320959"
        )
        response = self.client.get(
            reverse("search"), {"media_type": "stage", "q": "Hamilton"}
        )
        self.assertEqual(
            [
                result["item"]["media_id"]
                for result in response.context["data"]["results"]
            ],
            ["Q997"],
        )

    def test_conflicting_saved_redirect_leaves_records_untouched(self):
        """Contradictory provider identity cannot silently reassign saved work."""
        self.client.get(
            reverse("media_details", args=["wikidata", "stage", "Q998", "hamilton"])
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "Q19320959",
                "media_type": "stage",
                "source": "wikidata",
                "status": "Completed",
                "notes": "Keep this visit",
            },
        )
        self.entities["Q998"] = {
            **self.entities["Q19320959"],
            "id": "Q997",
            "redirects": {"from": "Q998", "to": "Q997"},
        }
        cache.clear()
        response = self.client.get(
            reverse("search"), {"media_type": "stage", "q": "Hamilton"}
        )
        self.assertContains(response, "Conflicting Stage identity", status_code=500)
        self.assertEqual(Stage.objects.get().item.media_id, "Q19320959")
        self.assertEqual(Stage.objects.get().notes, "Keep this visit")

    def test_redirect_moves_saved_image_with_its_source(self):
        """Canonicalization keeps an existing image and its original evidence ID."""
        old = Item.objects.create(
            media_id="Q998",
            source="wikidata",
            media_type="stage",
            title="Old",
            image="https://thumb.wikimedia.org/saved.jpg",
            stage_artwork={
                "work_id": "Q998",
                "source_url": "https://commons.wikimedia.org/wiki/File:Saved.jpg",
            },
        )
        target = Item.objects.create(
            media_id="Q19320959",
            source="wikidata",
            media_type="stage",
            title="Canonical",
            image="",
        )
        stage.record_redirect("Q998", self.entities["Q998"])
        target.refresh_from_db()
        self.assertEqual(target.image, old.image)
        self.assertEqual(
            target.stage_artwork["source_url"], old.stage_artwork["source_url"]
        )
        self.assertEqual(target.stage_artwork["evidence_work_id"], "Q998")
        self.assertEqual(target.stage_artwork["work_id"], target.media_id)

    def test_outage_preserves_local_attendance_and_manual_creation(self):
        """Provider downtime cannot prevent editing an existing attendance."""
        item = Item.objects.create(
            media_id="Q19320959",
            source="wikidata",
            media_type="stage",
            title="Hamilton",
            image="",
            stage_forms=["musical"],
        )
        attendance = Stage.objects.create(item=item, user=self.user, status="Planning")
        self.client.post(
            reverse("media_save"),
            {
                "instance_id": attendance.pk,
                "media_id": item.media_id,
                "source": "wikidata",
                "media_type": "stage",
                "status": "In progress",
            },
        )
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)
        with patch("app.providers.services.session.get", side_effect=requests.Timeout):
            response = self.client.get(
                reverse("search"), {"media_type": "stage", "q": "Hamilton"}
            )
            self.assertEqual(response.status_code, 500)
            self.assertContains(response, "Wikidata", status_code=500)
            self.client.post(
                reverse("media_save"),
                {
                    "instance_id": attendance.pk,
                    "media_id": item.media_id,
                    "source": "wikidata",
                    "media_type": "stage",
                    "status": "Completed",
                    "venue": "Local Theatre",
                },
            )
            attendance.refresh_from_db()
            self.assertEqual(attendance.status, "Completed")
            self.assertEqual(attendance.venue, "Local Theatre")
            self.client.post(
                reverse("create_entry"),
                {
                    "title": "Uncataloged",
                    "media_type": "stage",
                    "stage_forms": ["other"],
                    "status": "Completed",
                },
            )
            self.assertTrue(Item.objects.filter(title="Uncataloged").exists())

    def test_pagination_retains_imageless_distinct_works(self):
        """Pagination counts validated unique works, never raw provider hits."""
        for number in range(1000, 1023):
            identifier = f"Q{number}"
            self.entities[identifier] = {
                **self.entities["Q19320959"],
                "id": identifier,
                "labels": {"en": {"value": f"Stage Work {number}"}},
            }
            self.search_ids.append(identifier)
        all_ids, offsets = self.search_ids, []

        def continued(url, params, **kwargs):
            offset = params.get("sroffset", 0)
            self.search_ids = all_ids[offset : offset + 9]
            response = self.source_response(url, params, **kwargs)
            if params.get("list") == "search":
                offsets.append(offset)
                data = json.loads(response.content)
                data["continue"] = {"sroffset": offset + 9}
                response._content = json.dumps(data).encode()
            return response

        with patch("app.providers.services.session.get", side_effect=continued):
            first = self.client.get(
                reverse("search"), {"media_type": "stage", "q": "Stage Work"}
            )
        self.assertEqual(offsets, [0, 9, 18])
        self.assertTrue(first.context["data"]["limited"])
        second = self.client.get(
            reverse("search"), {"media_type": "stage", "q": "Stage Work", "page": 2}
        )
        first_ids = [
            work["item"]["media_id"] for work in first.context["data"]["results"]
        ]
        second_ids = [
            work["item"]["media_id"] for work in second.context["data"]["results"]
        ]
        self.assertEqual(len(first_ids), 20)
        self.assertEqual(len(second_ids), 4)
        self.assertFalse(set(first_ids).intersection(second_ids))
        self.assertEqual(second.context["data"]["total_results"], 24)

    def test_sync_refreshes_metadata_without_creating_alias_items(self):
        """Sync bypasses cached labels and leaves attendance and identity intact."""
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "Q998",
                "media_type": "stage",
                "source": "wikidata",
                "status": "Planning",
                "venue": "Saved Venue",
            },
        )
        self.entities["Q19320959"]["labels"] = {"en": {"value": "Hamilton Updated"}}
        response = self.client.post(
            reverse("sync_metadata", args=["wikidata", "stage", "Q998"]),
            {"next": "/"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Item.objects.get().title, "Hamilton Updated")
        self.assertEqual(Stage.objects.get().venue, "Saved Venue")
        modal = self.client.get(
            reverse("track_modal", args=["wikidata", "stage", "Q998"]),
            {"return_url": "/"},
        )
        self.assertContains(modal, "Saved Venue")
        self.assertEqual(Item.objects.count(), 1)

    def test_rate_limit_and_api_errors_are_visible_not_empty_results(self):
        """Back-pressure terminates the request and does not cache empty results."""
        for status, body in [
            (429, {}),
            (200, {"error": {"code": "maxlag", "info": "Replica lag"}}),
        ]:
            with self.subTest(status=status):
                response = requests.Response()
                response.status_code = status
                response._content = json.dumps(body).encode()
                response.headers["Retry-After"] = "60"
                with patch("app.providers.services.session.get", return_value=response):
                    result = self.client.get(
                        reverse("search"), {"media_type": "stage", "q": "Hamilton"}
                    )
                self.assertContains(result, "Wikidata", status_code=500)
        recovered = self.client.get(
            reverse("search"), {"media_type": "stage", "q": "Hamilton"}
        )
        self.assertContains(recovered, "Lin-Manuel Miranda")
