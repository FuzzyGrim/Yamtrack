"""WeTrakr: the API client, the device login and the batching adapter.

WeTrakr has not been called for real yet. Every reply below is a fixture built
from an unofficial transcription of its documentation (2026-10-01, changelog
1.0.7), so each shape here is an assumption that the first live account will
confirm or correct. The tests pin what Floppy does with those shapes, above all
that a reply it does not understand is never counted as delivered.
"""

import datetime
import logging
from decimal import Decimal
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.cache_safety import acquire_lock, release_lock
from app.models import (
    Item,
    MediaTypes,
    Movie,
    MoviePlay,
    Sources,
    Status,
    WatchStateOrigin,
    WatchStateSequence,
)
from app.services.watch_state import importing_watch_state, record_state_change
from integrations import wetrakr_api
from integrations.imports.helpers import decrypt, encrypt
from integrations.models import (
    CAPABILITY_PLAY_PUSH,
    CAPABILITY_PLAY_REMOVE,
    CAPABILITY_RATING_PUSH,
    CAPABILITY_STATUS_PUSH,
    OutboundDeliveryStatus,
    OutboundStateDelivery,
    SyncBinding,
    SyncClientKind,
    SyncDeliverySchedule,
    SyncDirection,
    WeTrakrAccount,
)
from integrations.state import outbound
from integrations.state.adapters import wetrakr as adapter_module
from integrations.state.adapters.base import (
    BATCH_DELIVERED,
    BATCH_FAILED,
    BATCH_RETRY,
    BATCH_SKIPPED,
    BatchItem,
    TerminalAdapterError,
    TransientAdapterError,
)
from integrations.state.adapters.wetrakr import WeTrakrAdapter, build_adapter
from integrations.state.identity import (
    activate_binding,
    deactivate_binding,
    get_or_create_binding,
)
from integrations.tasks._wetrakr_export import export_wetrakr_library, library_preview


def setUpModule():
    """Silence log noise for this module only."""
    logging.disable(logging.DEBUG)


def tearDownModule():
    """Restore logging so other modules' assertLogs still see records."""
    logging.disable(logging.NOTSET)


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        """Stand in for a requests response."""
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.content = b"" if body is None else b"x"

    def json(self):
        """Return the body, or fail like a reply that is not JSON."""
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _play_item(delivery_id, external_id, watched_at="2026-10-18T20:15:00+00:00"):
    return BatchItem(
        delivery_id=delivery_id,
        operation="play",
        item=None,
        external_id=external_id,
        payload={"watched_at": watched_at},
        rating=None,
    )


def _status_item(delivery_id, external_id, status):
    return BatchItem(
        delivery_id=delivery_id,
        operation="status",
        item=None,
        external_id=external_id,
        payload={"status": status},
        rating=None,
    )


def _rating_item(delivery_id, external_id, rating):
    return BatchItem(
        delivery_id=delivery_id,
        operation="rating",
        item=None,
        external_id=external_id,
        payload={},
        rating=rating,
    )


class WeTrakrTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="owner")
        self.account = WeTrakrAccount.objects.create(
            user=self.user,
            profile_key="profile-1",
            client_id="app-key",
            access_token=encrypt("access-1"),
            refresh_token=encrypt("refresh-1"),
            access_expires_at=timezone.now() + datetime.timedelta(days=1),
        )


class ErrorReadingTests(TestCase):
    def test_both_error_shapes_are_read(self):
        flat = FakeResponse(400, {"error": "BAD", "message": "no"})
        nested = FakeResponse(
            400,
            {"success": False, "error": {"code": "BAD", "message": "no"}},
        )

        self.assertEqual(wetrakr_api.read_error(flat), ("BAD", "no"))
        self.assertEqual(wetrakr_api.read_error(nested), ("BAD", "no"))

    def test_a_body_that_is_not_json_gives_no_error(self):
        self.assertEqual(wetrakr_api.read_error(FakeResponse(500)), ("", ""))

    def test_the_minute_limit_waits_for_its_reset(self):
        response = FakeResponse(429, {}, {"RateLimit-Reset": "42"})

        self.assertEqual(wetrakr_api.retry_after(response), 42)

    def test_an_epoch_reset_is_turned_into_a_wait(self):
        now = datetime.datetime(2026, 10, 19, 12, tzinfo=datetime.UTC)
        response = FakeResponse(
            429,
            {},
            {"RateLimit-Reset": str(int(now.timestamp()) + 90)},
        )

        self.assertEqual(wetrakr_api.retry_after(response, now), 90)

    def test_a_spent_daily_quota_waits_until_midnight_utc(self):
        now = datetime.datetime(2026, 10, 19, 22, tzinfo=datetime.UTC)
        response = FakeResponse(
            429,
            {},
            {"X-Quota-Remaining": "0", "RateLimit-Reset": "5"},
        )

        self.assertEqual(wetrakr_api.retry_after(response, now), 2 * 3600)

    def test_a_429_is_transient_with_the_wait(self):
        with self.assertRaises(TransientAdapterError) as caught:
            wetrakr_api.raise_for_status(
                FakeResponse(429, {"error": "X"}, {"RateLimit-Reset": "30"}),
            )

        self.assertEqual(caught.exception.retry_after, 30)

    def test_a_server_error_is_transient(self):
        with self.assertRaises(TransientAdapterError):
            wetrakr_api.raise_for_status(FakeResponse(503, {}))

    def test_a_rejected_login_is_an_auth_error(self):
        with self.assertRaises(wetrakr_api.WeTrakrAuthError):
            wetrakr_api.raise_for_status(FakeResponse(401, {"error": "INVALID_TOKEN"}))

    def test_a_rejected_app_key_is_an_auth_error_too(self):
        with self.assertRaises(wetrakr_api.WeTrakrKeyError):
            wetrakr_api.raise_for_status(
                FakeResponse(401, {"error": "MISSING_API_KEY"}),
            )

    def test_plan_limits_are_terminal_and_not_a_broken_login(self):
        for status in (420, 426, 422):
            with self.subTest(status=status):
                with self.assertRaises(TerminalAdapterError) as caught:
                    wetrakr_api.raise_for_status(
                        FakeResponse(status, {"error": "PLAN", "message": "upgrade"}),
                    )
                self.assertNotIsInstance(caught.exception, wetrakr_api.WeTrakrAuthError)


class BundledKeyTests(TestCase):
    """The app-owned WeTrakr key ships by default and can still be replaced."""

    def test_the_bundled_key_is_the_default_and_is_what_a_user_resolves(self):
        from django.conf import settings

        from app.providers import credentials

        bundled = settings.SHARED_DEFAULT_CREDENTIALS["WETRAKR_CLIENT_ID"]
        self.assertTrue(bundled)
        self.assertEqual(settings.WETRAKR_CLIENT_ID, bundled)
        user = get_user_model().objects.create_user(username="bundled", password="x")
        self.assertEqual(wetrakr_api.client_id_for(user), bundled)
        field = credentials.get_spec("wetrakr").fields[0]
        self.assertEqual(credentials.default_value(field), bundled)

    def test_an_instance_key_replaces_the_bundled_one(self):
        user = get_user_model().objects.create_user(username="own", password="x")
        with self.settings(WETRAKR_CLIENT_ID="instance-key"):
            self.assertEqual(wetrakr_api.client_id_for(user), "instance-key")


class DeviceFlowTests(TestCase):
    def _poll(self, *responses):
        with patch(
            "integrations.wetrakr_api.requests.request", side_effect=responses
        ) as call:
            result = wetrakr_api.poll_device_flow("app-key", "dev-1")
        return result, call

    def test_a_code_is_requested_with_the_users_key(self):
        reply = {
            "device_code": "dev-1",
            "user_code": "ABCD-1234",
            "verification_url": "https://wetrakr.com/device",
            "interval": 5,
            "expires_in": 600,
        }
        with patch(
            "integrations.wetrakr_api.requests.request",
            return_value=FakeResponse(200, reply),
        ) as call:
            flow = wetrakr_api.start_device_flow("app-key")

        self.assertEqual(flow["user_code"], "ABCD-1234")
        sent = call.call_args.kwargs
        self.assertEqual(sent["json"], {"client_id": "app-key"})
        self.assertEqual(sent["headers"]["wetrakr-api-key"], "app-key")
        self.assertEqual(sent["headers"]["wetrakr-api-version"], "1")
        self.assertNotIn("Authorization", sent["headers"])

    def test_a_suspended_key_is_explained(self):
        with (
            patch(
                "integrations.wetrakr_api.requests.request",
                return_value=FakeResponse(423, {"error": "SUSPENDED"}),
            ),
            self.assertRaisesMessage(wetrakr_api.DeviceFlowError, "suspended"),
        ):
            wetrakr_api.start_device_flow("app-key")

    def test_a_key_wetrakr_does_not_know_is_explained(self):
        with (
            patch(
                "integrations.wetrakr_api.requests.request",
                return_value=FakeResponse(401, {"error": "INVALID"}),
            ),
            self.assertRaisesMessage(wetrakr_api.DeviceFlowError, "app key"),
        ):
            wetrakr_api.start_device_flow("app-key")

    def test_waiting_for_approval_is_pending(self):
        result, _call = self._poll(
            FakeResponse(400, {"error": "authorization_pending"}),
        )

        self.assertEqual(result, ("pending", None))

    def test_approval_returns_the_tokens(self):
        tokens = {"access_token": "a", "refresh_token": "r", "expires_in": 3600}

        result, call = self._poll(FakeResponse(200, tokens))

        self.assertEqual(result, ("approved", tokens))
        self.assertEqual(call.call_args.kwargs["json"]["code"], "dev-1")

    def test_the_other_field_name_is_tried_once_if_the_first_is_invalid(self):
        tokens = {"access_token": "a", "refresh_token": "r"}

        result, call = self._poll(
            FakeResponse(400, {"error": "VALIDATION_ERROR"}),
            FakeResponse(200, tokens),
        )

        self.assertEqual(result[0], "approved")
        bodies = [c.kwargs["json"] for c in call.call_args_list]
        self.assertIn("code", bodies[0])
        self.assertIn("device_code", bodies[1])
        self.assertEqual(len(bodies), 2)

    def test_polling_too_fast_asks_for_a_slower_pace(self):
        result, _call = self._poll(
            FakeResponse(429, {}, {"RateLimit-Reset": "15"}),
        )

        self.assertEqual(result, ("pending", {"interval": 15}))

    def test_expired_and_denied_codes_stop_the_flow(self):
        for status in (410, 418, 409, 404):
            with self.subTest(status=status):
                result, _call = self._poll(FakeResponse(status, {"error": "X"}))
                self.assertEqual(result[0], "failed")

    def test_an_approval_without_tokens_is_not_accepted(self):
        result, _call = self._poll(FakeResponse(200, {"access_token": "a"}))

        self.assertEqual(result[0], "failed")

    def test_tokens_are_stored_encrypted_with_an_expiry(self):
        now = datetime.datetime(2026, 10, 19, tzinfo=datetime.UTC)

        fields = wetrakr_api.token_fields(
            {"access_token": "a", "refresh_token": "r", "expires_in": 3600},
            now,
        )

        self.assertEqual(decrypt(fields["access_token"]), "a")
        self.assertEqual(decrypt(fields["refresh_token"]), "r")
        self.assertEqual(
            fields["access_expires_at"],
            now + datetime.timedelta(hours=1),
        )


class ClientTests(WeTrakrTestCase):
    def _client(self):
        return wetrakr_api.WeTrakrClient(self.account)

    def test_a_request_carries_the_key_the_login_and_the_idempotency_key(self):
        with patch(
            "integrations.wetrakr_api.requests.request",
            return_value=FakeResponse(200, {"added": {}}),
        ) as call:
            self._client().request(
                "POST",
                "/sync/tracking",
                json={"movies": []},
                idempotency_key="key-1",
            )

        headers = call.call_args.kwargs["headers"]
        self.assertEqual(headers["wetrakr-api-key"], "app-key")
        self.assertEqual(headers["Authorization"], "Bearer access-1")
        self.assertEqual(headers["Idempotency-Key"], "key-1")
        self.assertEqual(
            call.call_args.args[1], "https://api.wetrakr.com/sync/tracking"
        )

    def test_an_expired_login_is_refreshed_and_the_new_tokens_kept(self):
        self.account.access_expires_at = timezone.now() - datetime.timedelta(minutes=1)
        self.account.save()
        refreshed = FakeResponse(
            200,
            {
                "access_token": "access-2",
                "refresh_token": "refresh-2",
                "expires_in": 3600,
            },
        )
        with patch(
            "integrations.wetrakr_api.requests.request",
            side_effect=[refreshed, FakeResponse(200, {"added": {}})],
        ) as call:
            self._client().request("POST", "/sync/tracking", json={})

        self.assertEqual(
            call.call_args_list[0].args[1].rsplit("/", 2)[-2:], ["token", "refresh"]
        )
        self.assertEqual(
            call.call_args_list[0].kwargs["json"],
            {"refresh_token": "refresh-1"},
        )
        self.assertEqual(
            call.call_args_list[1].kwargs["headers"]["Authorization"],
            "Bearer access-2",
        )
        self.account.refresh_from_db()
        self.assertEqual(decrypt(self.account.refresh_token), "refresh-2")

    def test_a_stale_access_token_is_refreshed_once_and_the_call_retried(self):
        refreshed = FakeResponse(
            200,
            {"access_token": "access-2", "refresh_token": "refresh-2"},
        )
        with patch(
            "integrations.wetrakr_api.requests.request",
            side_effect=[
                FakeResponse(401, {"error": "INVALID_TOKEN"}),
                refreshed,
                FakeResponse(200, {"added": {}}),
            ],
        ) as call:
            self._client().request("POST", "/sync/tracking", json={})

        self.assertEqual(call.call_count, 3)

    def test_a_refresh_another_worker_already_did_is_not_repeated(self):
        """The rotated token is used instead of spending the old one twice."""
        stale = wetrakr_api.WeTrakrClient(
            WeTrakrAccount.objects.get(pk=self.account.pk)
        )
        WeTrakrAccount.objects.filter(pk=self.account.pk).update(
            refresh_token=encrypt("refresh-2"),
            access_token=encrypt("access-2"),
            access_expires_at=timezone.now() + datetime.timedelta(days=7),
        )

        with patch("integrations.wetrakr_api.requests.request") as call:
            stale.refresh(force=True)

        call.assert_not_called()
        self.assertEqual(decrypt(stale.account.refresh_token), "refresh-2")

    def test_a_refresh_waits_when_another_worker_holds_the_lock(self):
        lock = f"wetrakr_refresh:{self.account.pk}"
        self.assertTrue(acquire_lock(lock, timeout=30))
        self.addCleanup(release_lock, lock)

        with (
            patch.object(wetrakr_api, "REFRESH_WAIT_SECONDS", 0),
            patch("integrations.wetrakr_api.requests.request") as call,
            self.assertRaises(TransientAdapterError),
        ):
            self._client().refresh(force=True)

        call.assert_not_called()

    def test_a_refresh_does_not_run_when_the_lock_cannot_be_checked(self):
        with (
            patch("app.cache_safety.cache.add", side_effect=ConnectionError("down")),
            patch.object(wetrakr_api, "REFRESH_WAIT_SECONDS", 0),
            patch("integrations.wetrakr_api.requests.request") as call,
            self.assertRaises(TransientAdapterError),
        ):
            self._client().refresh(force=True)

        call.assert_not_called()

    def test_a_refresh_wetrakr_rejects_marks_the_connection_broken(self):
        self.account.access_expires_at = timezone.now() - datetime.timedelta(minutes=1)
        self.account.save()
        with (
            patch(
                "integrations.wetrakr_api.requests.request",
                return_value=FakeResponse(400, {"error": "invalid_grant"}),
            ),
            self.assertRaises(wetrakr_api.WeTrakrAuthError),
        ):
            self._client().request("POST", "/sync/tracking", json={})

        self.account.refresh_from_db()
        self.assertTrue(self.account.connection_broken)

    def test_an_outage_never_marks_the_connection_broken(self):
        with (
            patch(
                "integrations.wetrakr_api.requests.request",
                side_effect=requests.ConnectionError("down"),
            ),
            self.assertRaises(TransientAdapterError),
        ):
            self._client().request("POST", "/sync/tracking", json={})

        self.account.refresh_from_db()
        self.assertFalse(self.account.connection_broken)
        self.assertTrue(self.account.last_error_message)

    def test_a_good_call_clears_an_earlier_error(self):
        self.account.last_error_message = "WeTrakr could not be reached."
        self.account.save()

        with patch(
            "integrations.wetrakr_api.requests.request",
            return_value=FakeResponse(200, {"added": {}}),
        ):
            self._client().request("POST", "/sync/tracking", json={})

        self.account.refresh_from_db()
        self.assertEqual(self.account.last_error_message, "")


class AdapterIdentityTests(WeTrakrTestCase):
    def _item(self, **kwargs):
        defaults = {
            "media_id": "155",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "title": "Film",
        }
        defaults.update(kwargs)
        item, _ = Item.objects.get_or_create(**defaults)
        return item

    def test_native_tmdb_items_resolve_with_their_type(self):
        adapter = WeTrakrAdapter(self.account)
        episode = self._item(
            media_id="1399",
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
        )

        self.assertEqual(adapter.resolve_external_id(self._item()), "movie:155")
        self.assertEqual(
            adapter.resolve_external_id(
                self._item(media_id="1399", media_type=MediaTypes.TV.value),
            ),
            "show:1399",
        )
        self.assertEqual(adapter.resolve_external_id(episode), "episode:1399:1:2")

    def test_other_sources_and_seasons_do_not_resolve(self):
        adapter = WeTrakrAdapter(self.account)

        self.assertIsNone(
            adapter.resolve_external_id(self._item(source=Sources.MAL.value)),
        )
        self.assertIsNone(
            adapter.resolve_external_id(
                self._item(
                    media_id="1399",
                    media_type=MediaTypes.SEASON.value,
                    season_number=1,
                ),
            ),
        )

    def test_ratings_use_one_decimal_on_a_ten_point_scale(self):
        adapter = WeTrakrAdapter(self.account)

        self.assertEqual(adapter.normalize_rating(Decimal("8.46")), Decimal("8.5"))
        self.assertEqual(adapter.normalize_rating(Decimal(11)), Decimal(10))
        self.assertIsNone(adapter.normalize_rating(None))

    def test_the_adapter_declines_imports_and_batches(self):
        self.assertFalse(WeTrakrAdapter.ACCEPTS_IMPORTED_CHANGES)
        self.assertGreater(WeTrakrAdapter.BATCH_SIZE, 0)
        self.assertLessEqual(WeTrakrAdapter.BATCH_SIZE, 5000)

    def test_only_a_connected_account_yields_an_adapter(self):
        binding, _ = get_or_create_binding(
            self.user,
            SyncClientKind.WETRAKR.value,
            instance_key="wetrakr.com",
            profile_key="profile-1",
        )
        self.assertIsInstance(build_adapter(binding), WeTrakrAdapter)

        self.account.refresh_token = ""
        self.account.save()
        self.assertIsNone(build_adapter(binding))


class AdapterRequestTests(WeTrakrTestCase):
    def _send(self, items, *replies):
        adapter = WeTrakrAdapter(self.account)
        responses = [FakeResponse(200, reply) for reply in replies]
        with patch(
            "integrations.wetrakr_api.requests.request",
            side_effect=responses,
        ) as call:
            results = adapter.write_batch(items, idempotency_key="batch-1")
        return results, call

    def _bodies(self, call):
        return {
            c.args[1].rsplit(".com", 1)[1]: c.kwargs["json"]
            for c in call.call_args_list
        }

    def test_a_movie_play_carries_its_own_date(self):
        _results, call = self._send(
            [_play_item(1, "movie:155")],
            {"added": {"plays": []}},
        )

        body = self._bodies(call)["/sync/tracking"]
        self.assertEqual(
            body["movies"],
            [
                {
                    "ids": {"tmdb": 155},
                    "status": "watched",
                    "tracked_at": "2026-10-18T20:15:00+00:00",
                },
            ],
        )

    def test_episode_plays_are_grouped_and_never_mark_the_whole_show(self):
        _results, call = self._send(
            [
                _play_item(1, "episode:1399:1:1", "2026-10-18T20:00:00+00:00"),
                _play_item(2, "episode:1399:1:2", "2026-10-18T21:00:00+00:00"),
                _play_item(3, "episode:1399:2:1", "2026-10-19T20:00:00+00:00"),
            ],
            {"added": {}},
        )

        shows = self._bodies(call)["/sync/tracking"]["shows"]
        self.assertEqual(len(shows), 1)
        self.assertNotIn("status", shows[0])
        self.assertEqual(
            shows[0]["seasons"],
            [
                {
                    "number": 1,
                    "episodes": [
                        {
                            "number": 1,
                            "status": "watched",
                            "tracked_at": "2026-10-18T20:00:00+00:00",
                        },
                        {
                            "number": 2,
                            "status": "watched",
                            "tracked_at": "2026-10-18T21:00:00+00:00",
                        },
                    ],
                },
                {
                    "number": 2,
                    "episodes": [
                        {
                            "number": 1,
                            "status": "watched",
                            "tracked_at": "2026-10-19T20:00:00+00:00",
                        },
                    ],
                },
            ],
        )

    def test_only_the_statuses_wetrakr_wants_are_sent(self):
        results, call = self._send(
            [
                _status_item(1, "movie:155", Status.PLANNING.value),
                _status_item(2, "movie:156", Status.COMPLETED.value),
                _status_item(3, "show:1399", Status.PAUSED.value),
                _status_item(4, "show:1400", Status.IN_PROGRESS.value),
                _status_item(5, "show:1401", Status.COMPLETED.value),
            ],
            {"added": {}},
        )

        body = self._bodies(call)["/sync/tracking"]
        self.assertEqual(
            body["movies"],
            [{"ids": {"tmdb": 155}, "status": "planning"}],
        )
        self.assertEqual(
            body["shows"],
            [{"ids": {"tmdb": 1399}, "status": "paused"}],
        )
        self.assertEqual(
            [r.outcome for r in results],
            [
                BATCH_DELIVERED,
                BATCH_SKIPPED,
                BATCH_DELIVERED,
                BATCH_SKIPPED,
                BATCH_SKIPPED,
            ],
        )

    def test_a_status_is_never_combined_with_nested_episodes(self):
        _results, call = self._send(
            [
                _status_item(1, "show:1399", Status.DROPPED.value),
                _play_item(2, "episode:1399:1:1"),
            ],
            {"added": {}},
        )

        shows = self._bodies(call)["/sync/tracking"]["shows"]
        self.assertEqual(len(shows), 2)
        with_status = [s for s in shows if "status" in s]
        self.assertEqual(len(with_status), 1)
        self.assertNotIn("seasons", with_status[0])

    def test_ratings_are_set_and_cleared_on_their_own_routes(self):
        results, call = self._send(
            [
                _rating_item(1, "movie:155", Decimal("8.5")),
                _rating_item(2, "show:1399", None),
                _rating_item(3, "episode:1399:1:1", Decimal(7)),
            ],
            {"added": {}},
            {"added": {}},
        )

        bodies = self._bodies(call)
        self.assertEqual(
            bodies["/sync/ratings"]["movies"],
            [{"ids": {"tmdb": 155}, "rating": 8.5}],
        )
        self.assertEqual(
            bodies["/sync/ratings/remove"]["shows"],
            [{"ids": {"tmdb": 1399}}],
        )
        self.assertEqual(
            [r.outcome for r in results],
            [BATCH_DELIVERED, BATCH_DELIVERED, BATCH_SKIPPED],
        )

    def test_each_request_gets_its_own_stable_idempotency_key(self):
        _results, call = self._send(
            [_play_item(1, "movie:155"), _rating_item(2, "movie:155", Decimal(8))],
            {"added": {}},
            {"added": {}},
        )

        keys = [c.kwargs["headers"]["Idempotency-Key"] for c in call.call_args_list]
        self.assertEqual(len(set(keys)), 2)
        self.assertTrue(all(key.startswith("batch-1") for key in keys))

    def test_a_play_without_a_date_is_failed_and_not_sent(self):
        item = _play_item(1, "movie:155")
        item.payload = {}

        results, _call = self._send([item, _play_item(2, "movie:156")], {"added": {}})

        self.assertEqual(results[0].outcome, BATCH_FAILED)
        self.assertEqual(results[1].outcome, BATCH_DELIVERED)


class AdapterReplyTests(WeTrakrTestCase):
    def _send(self, items, reply):
        adapter = WeTrakrAdapter(self.account)
        with patch(
            "integrations.wetrakr_api.requests.request",
            return_value=FakeResponse(200, reply),
        ):
            return adapter.write_batch(items, idempotency_key="batch-1")

    def test_the_play_id_wetrakr_returns_is_kept_as_the_receipt(self):
        reply = {
            "added": {
                "plays": [
                    {
                        "play_id": 9001,
                        "ids": {"tmdb": 155},
                        "tracked_at": "2026-10-18T20:15:00+00:00",
                    },
                ],
            },
        }

        results = self._send([_play_item(1, "movie:155")], reply)

        self.assertEqual(results[0].outcome, BATCH_DELIVERED)
        self.assertEqual(results[0].receipt, "9001")

    def test_a_receipt_is_matched_when_wetrakr_writes_the_date_differently(self):
        reply = {
            "added": {
                "plays": [
                    {
                        "play_id": 9001,
                        "ids": {"tmdb": 155},
                        "tracked_at": "2026-10-18T20:15:00.000Z",
                    },
                ],
            },
        }

        results = self._send([_play_item(1, "movie:155")], reply)

        self.assertEqual(results[0].receipt, "9001")

    def test_two_plays_at_the_same_moment_each_keep_their_own_receipt(self):
        when = "2026-10-18T20:15:00+00:00"
        reply = {
            "added": {
                "plays": [
                    {"play_id": 9001, "ids": {"tmdb": 155}, "tracked_at": when},
                    {"play_id": 9002, "ids": {"tmdb": 155}, "tracked_at": when},
                ],
            },
        }

        results = self._send(
            [_play_item(1, "movie:155", when), _play_item(2, "movie:155", when)],
            reply,
        )

        self.assertEqual([r.receipt for r in results], ["9001", "9002"])

    def test_a_receipt_without_a_date_is_used_when_only_one_play_was_sent(self):
        reply = {"added": {"plays": [{"play_id": 9001, "ids": {"tmdb": 155}}]}}

        results = self._send([_play_item(1, "movie:155")], reply)

        self.assertEqual(results[0].receipt, "9001")

    def test_receipts_without_dates_are_not_guessed_for_two_plays_of_one_title(self):
        reply = {
            "added": {
                "plays": [
                    {"play_id": 9001, "ids": {"tmdb": 155}},
                    {"play_id": 9002, "ids": {"tmdb": 155}},
                ],
            },
        }

        results = self._send(
            [
                _play_item(1, "movie:155", "2026-10-01T20:00:00+00:00"),
                _play_item(2, "movie:155", "2026-10-02T20:00:00+00:00"),
            ],
            reply,
        )

        self.assertEqual([r.receipt for r in results], ["", ""])
        self.assertEqual([r.outcome for r in results], [BATCH_DELIVERED] * 2)

    def test_an_item_wetrakr_cannot_find_is_skipped_and_the_rest_delivered(self):
        reply = {
            "added": {},
            "notFound": {"movies": [{"ids": {"tmdb": 156}}]},
        }

        results = self._send(
            [_play_item(1, "movie:155"), _play_item(2, "movie:156")],
            reply,
        )

        self.assertEqual([r.outcome for r in results], [BATCH_DELIVERED, BATCH_SKIPPED])

    def test_an_errored_episode_is_failed_with_its_reason(self):
        reply = {
            "added": {},
            "errored": {
                "shows": [
                    {
                        "ids": {"tmdb": 1399},
                        "seasons": [
                            {
                                "number": 1,
                                "episodes": [{"number": 2, "error": "bad date"}],
                            },
                        ],
                    },
                ],
            },
        }

        results = self._send(
            [_play_item(1, "episode:1399:1:1"), _play_item(2, "episode:1399:1:2")],
            reply,
        )

        self.assertEqual([r.outcome for r in results], [BATCH_DELIVERED, BATCH_FAILED])
        self.assertIn("bad date", results[1].error)

    def test_a_count_of_rejections_without_names_sends_everything_again(self):
        results = self._send(
            [_play_item(1, "movie:155"), _play_item(2, "movie:156")],
            {"added": {}, "errored": {"movies": 1}},
        )

        self.assertEqual([r.outcome for r in results], [BATCH_RETRY, BATCH_RETRY])

    def test_a_reply_it_does_not_understand_is_never_counted_as_delivered(self):
        results = self._send([_play_item(1, "movie:155")], {"ok": True})

        self.assertEqual(results[0].outcome, BATCH_RETRY)

    def test_a_request_that_fails_outright_is_raised_for_the_engine_to_retry(self):
        adapter = WeTrakrAdapter(self.account)
        with (
            patch(
                "integrations.wetrakr_api.requests.request",
                return_value=FakeResponse(429, {}, {"RateLimit-Reset": "30"}),
            ),
            self.assertRaises(TransientAdapterError) as caught,
        ):
            adapter.write_batch([_play_item(1, "movie:155")], idempotency_key="k")

        self.assertEqual(caught.exception.retry_after, 30)

    def test_one_failing_request_does_not_hide_the_one_that_worked(self):
        adapter = WeTrakrAdapter(self.account)
        with patch(
            "integrations.wetrakr_api.requests.request",
            side_effect=[
                FakeResponse(200, {"added": {}}),
                FakeResponse(503, {}),
            ],
        ):
            results = adapter.write_batch(
                [_play_item(1, "movie:155"), _rating_item(2, "movie:155", Decimal(8))],
                idempotency_key="k",
            )

        self.assertEqual([r.outcome for r in results], [BATCH_DELIVERED, BATCH_RETRY])


class EndToEndBase(WeTrakrTestCase):
    """The real engine and adapter, a mocked WeTrakr, and a Floppy movie."""

    def setUp(self):
        super().setUp()
        self.item, _ = Item.objects.get_or_create(
            media_id="155",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            defaults={"title": "Film"},
        )
        self.binding, _ = get_or_create_binding(
            self.user,
            SyncClientKind.WETRAKR.value,
            instance_key="wetrakr.com",
            profile_key="profile-1",
        )
        activate_binding(
            self.binding,
            capabilities=[
                CAPABILITY_PLAY_PUSH,
                CAPABILITY_STATUS_PUSH,
                CAPABILITY_RATING_PUSH,
            ],
            directions=[SyncDirection.OUTBOUND.value],
        )

    def _watch(self):
        return Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=datetime.datetime(2026, 10, 18, 20, 15, tzinfo=datetime.UTC),
        )


class EndToEndTests(EndToEndBase):
    def test_a_movie_watched_in_floppy_reaches_wetrakr_with_its_date(self):
        self._watch()
        OutboundStateDelivery.objects.update(next_attempt_at=None)
        reply = FakeResponse(
            200,
            {
                "added": {
                    "plays": [{"play_id": 77, "ids": {"tmdb": 155}}],
                },
            },
        )

        with patch(
            "integrations.wetrakr_api.requests.request",
            return_value=reply,
        ) as call:
            outbound.flush_binding(self.binding.pk)

        body = call.call_args.kwargs["json"]
        self.assertEqual(body["movies"][0]["ids"], {"tmdb": 155})
        self.assertEqual(body["movies"][0]["status"], "watched")
        self.assertTrue(body["movies"][0]["tracked_at"].startswith("2026-10-18T20:15"))
        delivery = OutboundStateDelivery.objects.get(operation="play")
        self.assertEqual(delivery.status, OutboundDeliveryStatus.DELIVERED.value)

    def test_the_same_movie_imported_is_never_sent(self):
        with importing_watch_state():
            self._watch()

        self.assertFalse(OutboundStateDelivery.objects.exists())

    def test_a_completed_movie_does_not_also_send_a_watched_status(self):
        self._watch()

        self.assertEqual(
            set(OutboundStateDelivery.objects.values_list("operation", flat=True)),
            {"play", "status"},
        )
        OutboundStateDelivery.objects.update(next_attempt_at=None)
        with patch(
            "integrations.wetrakr_api.requests.request",
            return_value=FakeResponse(200, {"added": {}}),
        ) as call:
            outbound.flush_binding(self.binding.pk)

        entries = call.call_args.kwargs["json"]["movies"]
        self.assertEqual(len(entries), 1)
        self.assertIn("tracked_at", entries[0])
        status_delivery = OutboundStateDelivery.objects.get(operation="status")
        self.assertEqual(status_delivery.status, OutboundDeliveryStatus.SKIPPED.value)

    def test_a_play_made_by_wetrakr_itself_is_not_echoed_back(self):
        record_state_change(
            self.user,
            self.item,
            watched=True,
            play_count=1,
            watched_at=datetime.datetime(2026, 10, 18, tzinfo=datetime.UTC),
            origin_kind=WatchStateOrigin.WEBHOOK.value,
            origin_key=self.binding.origin_key,
        )

        self.assertFalse(OutboundStateDelivery.objects.exists())

    def test_an_adapter_module_is_what_the_registry_loads(self):
        self.assertIs(
            adapter_module.build_adapter(self.binding).__class__,
            WeTrakrAdapter,
        )


class ConnectViewTests(TestCase):
    """The card's buttons: connect, wait for approval, change settings, disconnect."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="owner")
        self.client.force_login(self.user)

    def _flow(self):
        return {
            "device_code": "dev-1",
            "user_code": "ABCD-1234",
            "verification_url": "https://wetrakr.com/device",
            "interval": 5,
            "expires_in": 600,
        }

    def _start(self):
        with (
            patch(
                "integrations.wetrakr_views.wetrakr_api.client_id_for",
                return_value="app-key",
            ),
            patch(
                "integrations.wetrakr_views.wetrakr_api.start_device_flow",
                return_value=self._flow(),
            ),
        ):
            return self.client.post(reverse("wetrakr_connect"))

    def _approve(self):
        tokens = {
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "expires_in": 3600,
        }
        with patch(
            "integrations.wetrakr_views.wetrakr_api.poll_device_flow",
            return_value=("approved", tokens),
        ):
            return self.client.post(reverse("wetrakr_poll"))

    def _connect(self):
        self._start()
        self._approve()
        return WeTrakrAccount.objects.get(user=self.user)

    def test_connecting_without_an_app_key_says_the_instance_has_none(self):
        with patch(
            "integrations.wetrakr_views.wetrakr_api.client_id_for", return_value=None
        ):
            response = self.client.post(reverse("wetrakr_connect"), follow=True)

        self.assertContains(response, "app key")
        self.assertFalse(WeTrakrAccount.objects.exists())

    def test_connect_stores_the_code_encrypted_and_shows_it(self):
        self._start()

        account = WeTrakrAccount.objects.get(user=self.user)
        self.assertNotEqual(account.device_code, "dev-1")
        self.assertEqual(decrypt(account.device_code), "dev-1")
        self.assertEqual(account.user_code, "ABCD-1234")
        self.assertTrue(account.is_pending)
        self.assertContains(self.client.get(reverse("provider_detail", args=["wetrakr"]), {"tab": "connection"}), "ABCD-1234")

    def test_polling_while_unapproved_stays_pending(self):
        self._start()

        with patch(
            "integrations.wetrakr_views.wetrakr_api.poll_device_flow",
            return_value=("pending", None),
        ):
            response = self.client.post(reverse("wetrakr_poll"))

        self.assertEqual(response.json()["state"], "pending")
        self.assertTrue(WeTrakrAccount.objects.get(user=self.user).is_pending)

    def test_polling_faster_than_asked_does_not_call_wetrakr_again(self):
        self._start()

        with patch(
            "integrations.wetrakr_views.wetrakr_api.poll_device_flow",
            return_value=("pending", None),
        ) as call:
            self.client.post(reverse("wetrakr_poll"))
            self.client.post(reverse("wetrakr_poll"))

        self.assertEqual(call.call_count, 1)

    def test_a_long_wait_from_wetrakr_is_not_shortened(self):
        self._start()

        with patch(
            "integrations.wetrakr_views.wetrakr_api.poll_device_flow",
            return_value=("pending", {"interval": 7200}),
        ):
            response = self.client.post(reverse("wetrakr_poll"))

        self.assertEqual(response.json()["interval"], 7200)
        self.assertEqual(WeTrakrAccount.objects.get(user=self.user).device_interval, 7200)

    def test_approval_connects_with_everything_still_switched_off(self):
        account = self._connect()

        self.assertTrue(account.is_connected)
        self.assertFalse(account.is_pending)
        self.assertEqual(decrypt(account.refresh_token), "refresh-1")
        binding = SyncBinding.objects.get(user=self.user, client_kind="wetrakr")
        self.assertEqual(binding.approved_capabilities, [])
        self.assertFalse(OutboundStateDelivery.objects.exists())

    def test_an_expired_code_is_cleared(self):
        self._start()
        WeTrakrAccount.objects.update(
            device_expires_at=timezone.now() - datetime.timedelta(seconds=1),
        )

        response = self.client.post(reverse("wetrakr_poll"))

        self.assertEqual(response.json()["state"], "failed")
        self.assertFalse(WeTrakrAccount.objects.exists())

    def test_a_denied_login_is_reported_and_cleared(self):
        self._start()

        with patch(
            "integrations.wetrakr_views.wetrakr_api.poll_device_flow",
            return_value=("failed", "The login was denied on WeTrakr."),
        ):
            response = self.client.post(reverse("wetrakr_poll"))

        self.assertEqual(response.json()["state"], "failed")
        self.assertFalse(WeTrakrAccount.objects.exists())

    def test_cancel_forgets_an_unapproved_code(self):
        self._start()

        self.client.post(reverse("wetrakr_cancel"))

        self.assertFalse(WeTrakrAccount.objects.exists())

    def test_switches_approve_only_what_was_ticked(self):
        self._connect()

        self.client.post(
            reverse("wetrakr_settings"),
            {"plays": "on", "schedule": SyncDeliverySchedule.DAILY.value},
        )

        binding = SyncBinding.objects.get(user=self.user, client_kind="wetrakr")
        self.assertEqual(binding.approved_capabilities, [CAPABILITY_PLAY_PUSH])
        self.assertEqual(binding.delivery_schedule, SyncDeliverySchedule.DAILY.value)

    def test_removing_plays_is_its_own_switch_and_starts_off(self):
        self._connect()
        self.client.post(reverse("wetrakr_settings"), {"plays": "on"})
        binding = SyncBinding.objects.get(user=self.user, client_kind="wetrakr")
        self.assertFalse(
            binding.allows(SyncDirection.OUTBOUND.value, CAPABILITY_PLAY_REMOVE),
        )

        self.client.post(reverse("wetrakr_settings"), {"plays": "on", "removals": "on"})

        binding.refresh_from_db()
        self.assertTrue(
            binding.allows(SyncDirection.OUTBOUND.value, CAPABILITY_PLAY_REMOVE),
        )

    def test_switching_everything_off_stops_all_sending(self):
        self._connect()
        self.client.post(reverse("wetrakr_settings"), {"plays": "on", "ratings": "on"})

        self.client.post(reverse("wetrakr_settings"), {})

        binding = SyncBinding.objects.get(user=self.user, client_kind="wetrakr")
        for capability in (CAPABILITY_PLAY_PUSH, CAPABILITY_RATING_PUSH):
            self.assertFalse(binding.allows(SyncDirection.OUTBOUND.value, capability))

    def test_switching_plays_on_starts_recording_changes_to_send(self):
        self._connect()
        self.assertFalse(
            WatchStateSequence.objects.filter(
                user=self.user, emit_changes=True
            ).exists(),
        )

        self.client.post(reverse("wetrakr_settings"), {"plays": "on"})

        self.assertTrue(WatchStateSequence.objects.get(user=self.user).emit_changes)

    def test_switching_back_on_resumes_sending(self):
        self._connect()
        self.client.post(reverse("wetrakr_settings"), {"plays": "on"})
        self.client.post(reverse("wetrakr_settings"), {})

        self.client.post(reverse("wetrakr_settings"), {"plays": "on"})

        binding = SyncBinding.objects.get(user=self.user, client_kind="wetrakr")
        self.assertTrue(
            binding.allows(SyncDirection.OUTBOUND.value, CAPABILITY_PLAY_PUSH)
        )

    def test_settings_need_a_connected_account(self):
        response = self.client.post(
            reverse("wetrakr_settings"), {"plays": "on"}, follow=True
        )

        self.assertContains(response, "Connect your WeTrakr account first")
        self.assertFalse(SyncBinding.objects.exists())

    def test_disconnect_logs_out_with_the_stored_tokens_and_forgets_them(self):
        self._connect()

        with patch("integrations.wetrakr_views.wetrakr_api.logout") as logout:
            self.client.post(reverse("wetrakr_disconnect"))

        logout.assert_called_once_with("app-key", "access-1", "refresh-1")
        self.assertFalse(WeTrakrAccount.objects.exists())

    def test_disconnect_still_works_when_wetrakr_is_unreachable(self):
        self._connect()

        with patch(
            "integrations.wetrakr_views.wetrakr_api.logout",
            side_effect=TransientAdapterError("down"),
        ):
            response = self.client.post(reverse("wetrakr_disconnect"), follow=True)

        self.assertFalse(WeTrakrAccount.objects.exists())
        self.assertContains(response, "could not confirm the logout")

    def test_directory_links_to_both_outbound_providers(self):
        directory = self.client.get(reverse("providers"))
        for slug in ("tmdb", "wetrakr"):
            self.assertContains(directory, reverse("provider_detail", args=[slug]))

    def test_each_service_has_its_own_provider_settings(self):
        for slug in ("tmdb", "wetrakr"):
            with self.subTest(slug=slug):
                page = self.client.get(
                    reverse("provider_detail", args=[slug]), {"tab": "connection"},
                )
                slugs = [service["slug"] for service in page.context["outbound_services"]]
                self.assertEqual(slugs, [slug])
                self.assertNotContains(page, 'id="mal-mapping-tab"')
                other_slug = "wetrakr" if slug == "tmdb" else "tmdb"
                self.assertNotContains(page, reverse(f"{other_slug}_connect"))

    def test_the_row_badge_follows_the_connection(self):
        states = lambda: {  # noqa: E731
            service["slug"]: service["state"]
            for service in self.client.get(reverse("provider_detail", args=["wetrakr"]), {"tab": "connection"}).context[
                "outbound_services"
            ]
        }
        self.assertEqual(states()["wetrakr"], "off")

        self._connect()
        self.assertEqual(states()["wetrakr"], "connected")

        WeTrakrAccount.objects.filter(user=self.user).update(connection_broken=True)
        self.assertEqual(states()["wetrakr"], "attention")

    def test_the_connection_tab_shows_pending_approval_inline(self):
        page = self.client.get(
            reverse("provider_detail", args=["wetrakr"]), {"tab": "connection"},
        )
        self.assertNotContains(page, "ABCD-1234")

        self._start()

        page = self.client.get(
            reverse("provider_detail", args=["wetrakr"]), {"tab": "connection"},
        )
        self.assertContains(page, "ABCD-1234")
        self.assertContains(page, "Enter this code on the service to approve Floppy.")
        self.assertNotContains(page, "activeModal === 'send-wetrakr'")

    def test_a_connected_card_lists_the_switches(self):
        self._connect()
        self.client.post(reverse("wetrakr_settings"), {"plays": "on"})

        page = self.client.get(reverse("provider_detail", args=["wetrakr"]), {"tab": "sync"})

        self.assertContains(page, "Send existing library")
        self.assertContains(page, 'name="plays"')

    def test_export_refuses_until_sync_is_switched_on(self):
        self._connect()

        with patch("integrations.tasks.export_wetrakr_library.delay") as queued:
            self.client.post(reverse("wetrakr_export"))

        queued.assert_not_called()

    def test_export_queues_once_sync_is_switched_on(self):
        self._connect()
        self.client.post(reverse("wetrakr_settings"), {"plays": "on"})

        with patch("integrations.tasks.export_wetrakr_library.delay") as queued:
            self.client.post(reverse("wetrakr_export"))

        queued.assert_called_once_with(self.user.pk)

    def test_the_buttons_need_a_login(self):
        self.client.logout()

        for name in (
            "wetrakr_connect",
            "wetrakr_poll",
            "wetrakr_settings",
            "wetrakr_export",
        ):
            self.assertEqual(self.client.post(reverse(name)).status_code, 302, name)
        self.assertFalse(WeTrakrAccount.objects.exists())


class ExportTests(WeTrakrTestCase):
    def setUp(self):
        super().setUp()
        self.binding, _ = get_or_create_binding(
            self.user,
            SyncClientKind.WETRAKR.value,
            instance_key="wetrakr.com",
            profile_key="profile-1",
        )
        activate_binding(
            self.binding,
            capabilities=[CAPABILITY_PLAY_PUSH, CAPABILITY_STATUS_PUSH],
            directions=[SyncDirection.OUTBOUND.value],
        )
        self.film = self._item("155", "Film")
        self.other = self._item("156", "Other")

    def _item(self, media_id, title):
        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            defaults={"title": title},
        )
        return item

    def _movie(self, item, status, end_date=None):
        with importing_watch_state():
            return Movie.objects.create(
                item=item,
                user=self.user,
                status=status,
                end_date=end_date,
            )

    def _export(self):
        with patch("integrations.state.outbound.kick_flush"):
            return export_wetrakr_library(self.user.pk)

    def test_dated_plays_are_queued_with_their_own_date(self):
        when = datetime.datetime(2026, 10, 1, 19, 0, tzinfo=datetime.UTC)
        self._movie(self.film, Status.COMPLETED.value, when)

        self.assertEqual(self._export(), 1)

        delivery = OutboundStateDelivery.objects.get(operation="play")
        self.assertEqual(delivery.payload["watched_at"], when.isoformat())
        self.assertEqual(delivery.item_id, self.film.pk)

    def test_every_recorded_watch_of_a_movie_is_queued(self):
        first = datetime.datetime(2025, 3, 1, 19, 0, tzinfo=datetime.UTC)
        second = datetime.datetime(2026, 10, 1, 19, 0, tzinfo=datetime.UTC)
        movie = self._movie(self.film, Status.COMPLETED.value, second)
        MoviePlay.objects.create(movie=movie, end_date=first)
        MoviePlay.objects.create(movie=movie, end_date=second)

        self.assertEqual(library_preview(self.user)["plays"], 2)
        # The newest play was already queued live when it was recorded, so the
        # export adds only the one WeTrakr has not been given.
        self.assertEqual(self._export(), 1)

        sent = set(
            OutboundStateDelivery.objects.filter(operation="play").values_list(
                "payload__watched_at", flat=True
            )
        )
        self.assertEqual(sent, {first.isoformat(), second.isoformat()})

    def test_a_completed_movie_with_no_plays_still_counts_once(self):
        when = datetime.datetime(2026, 10, 1, 19, 0, tzinfo=datetime.UTC)
        self._movie(self.film, Status.COMPLETED.value, when)
        self._movie(self.film, Status.COMPLETED.value, when + datetime.timedelta(days=1))

        self.assertEqual(self._export(), 2)

    def test_a_movie_with_a_dated_play_is_not_counted_as_undated(self):
        movie = self._movie(self.film, Status.COMPLETED.value)
        MoviePlay.objects.create(
            movie=movie,
            end_date=datetime.datetime(2026, 10, 1, 19, 0, tzinfo=datetime.UTC),
        )

        self.assertEqual(library_preview(self.user)["undated"], 0)

    def test_the_export_uses_the_binding_of_the_current_connection(self):
        deactivate_binding(self.binding)
        self.account.profile_key = "profile-2"
        self.account.save(update_fields=["profile_key"])
        current, _ = get_or_create_binding(
            self.user,
            SyncClientKind.WETRAKR.value,
            instance_key="wetrakr.com",
            profile_key="profile-2",
        )
        activate_binding(
            current,
            capabilities=[CAPABILITY_PLAY_PUSH],
            directions=[SyncDirection.OUTBOUND.value],
        )
        when = datetime.datetime(2026, 10, 1, 19, 0, tzinfo=datetime.UTC)
        self._movie(self.film, Status.COMPLETED.value, when)

        self.assertEqual(self._export(), 1)
        self.assertEqual(
            OutboundStateDelivery.objects.get(operation="play").binding_id,
            current.pk,
        )

    def test_a_completed_movie_without_a_date_is_left_out(self):
        self._movie(self.film, Status.COMPLETED.value)

        self.assertEqual(self._export(), 0)
        self.assertFalse(OutboundStateDelivery.objects.exists())
        self.assertEqual(library_preview(self.user)["undated"], 1)

    def test_running_it_twice_does_not_send_the_same_play_twice(self):
        when = datetime.datetime(2026, 10, 1, 19, 0, tzinfo=datetime.UTC)
        self._movie(self.film, Status.COMPLETED.value, when)

        self._export()
        again = self._export()

        self.assertEqual(again, 0)
        self.assertEqual(
            OutboundStateDelivery.objects.filter(operation="play").count(), 1
        )

    def test_planning_and_dropped_are_queued_but_watching_is_not(self):
        self._movie(self.film, Status.PLANNING.value)
        self._movie(self.other, Status.IN_PROGRESS.value)

        self._export()

        sent = OutboundStateDelivery.objects.get(operation="status")
        self.assertEqual(sent.item_id, self.film.pk)

    def test_nothing_is_queued_for_a_capability_that_is_switched_off(self):
        activate_binding(
            self.binding,
            capabilities=[CAPABILITY_STATUS_PUSH],
            directions=[SyncDirection.OUTBOUND.value],
        )
        when = datetime.datetime(2026, 10, 1, 19, 0, tzinfo=datetime.UTC)
        self._movie(self.film, Status.COMPLETED.value, when)

        self._export()

        self.assertFalse(
            OutboundStateDelivery.objects.filter(operation="play").exists()
        )

    def test_the_preview_counts_what_would_be_sent(self):
        when = datetime.datetime(2026, 10, 1, 19, 0, tzinfo=datetime.UTC)
        self._movie(self.film, Status.COMPLETED.value, when)
        self._movie(self.other, Status.PLANNING.value)

        preview = library_preview(self.user)

        self.assertEqual(preview["plays"], 1)
        self.assertEqual(preview["statuses"], 1)

    def test_the_preview_counts_without_reading_the_history(self):
        when = datetime.datetime(2026, 10, 1, 19, 0, tzinfo=datetime.UTC)
        self._movie(self.film, Status.COMPLETED.value, when)
        self._movie(self.other, Status.PLANNING.value)

        with (
            patch(
                "integrations.tasks._wetrakr_export._movie_plays",
                side_effect=AssertionError("preview read every play"),
            ),
            patch(
                "integrations.tasks._wetrakr_export._statuses",
                side_effect=AssertionError("preview read every status"),
            ),
        ):
            preview = library_preview(self.user)

        self.assertEqual((preview["plays"], preview["statuses"]), (1, 1))

    def test_a_user_without_a_binding_exports_nothing(self):
        self.binding.delete()

        self.assertEqual(export_wetrakr_library(self.user.pk), 0)


def _removal_item(
    delivery_id, external_id, *, receipt="", watched_at="2026-10-18T20:15:00+00:00"
):
    return BatchItem(
        delivery_id=delivery_id,
        operation="play_remove",
        item=None,
        external_id=external_id,
        payload={"watched_at": watched_at, "receipt": receipt, "play_delivery": 1},
        rating=None,
    )


class AdapterRemovalTests(WeTrakrTestCase):
    def _remove(self, item, *replies):
        adapter = WeTrakrAdapter(self.account)
        with patch(
            "integrations.wetrakr_api.requests.request",
            side_effect=list(replies),
        ) as call:
            results = adapter.write_batch([item], idempotency_key="batch-1")
        return results, call

    def test_a_play_with_a_receipt_is_deleted_by_that_id(self):
        results, call = self._remove(
            _removal_item(1, "movie:155", receipt="9001"),
            FakeResponse(200, {}),
        )

        self.assertEqual(results[0].outcome, BATCH_DELIVERED)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[0], "DELETE")
        self.assertTrue(call.call_args.args[1].endswith("/sync/tracklogs/9001"))

    def test_a_play_wetrakr_no_longer_has_counts_as_removed(self):
        results, _call = self._remove(
            _removal_item(1, "movie:155", receipt="9001"),
            FakeResponse(404, {"error": "NOT_FOUND"}),
        )

        self.assertEqual(results[0].outcome, BATCH_DELIVERED)

    def test_without_a_receipt_the_exact_date_is_removed(self):
        results, call = self._remove(
            _removal_item(1, "movie:155"),
            FakeResponse(200, {"removed": {}}),
        )

        self.assertEqual(results[0].outcome, BATCH_DELIVERED)
        self.assertTrue(call.call_args.args[1].endswith("/sync/tracking/remove"))
        self.assertEqual(
            call.call_args.kwargs["json"],
            {
                "movies": [
                    {
                        "ids": {"tmdb": 155},
                        "status": "watched",
                        "tracked_at": "2026-10-18T20:15:00+00:00",
                    },
                ],
            },
        )

    def test_an_episode_is_removed_by_its_exact_date_too(self):
        _results, call = self._remove(
            _removal_item(1, "episode:1399:1:2"),
            FakeResponse(200, {}),
        )

        episode = call.call_args.kwargs["json"]["shows"][0]["seasons"][0]["episodes"][0]
        self.assertEqual(episode["number"], 2)
        self.assertEqual(episode["tracked_at"], "2026-10-18T20:15:00+00:00")

    def test_the_remove_all_and_remove_last_routes_are_never_called(self):
        for item in (
            _removal_item(1, "movie:155"),
            _removal_item(2, "movie:155", receipt="9001"),
        ):
            _results, call = self._remove(item, FakeResponse(200, {}))
            for request in call.call_args_list:
                self.assertNotIn("/all", request.args[1])
                self.assertNotIn("/last", request.args[1])

    def test_a_date_wetrakr_cannot_find_is_skipped(self):
        results, _call = self._remove(
            _removal_item(1, "movie:155"),
            FakeResponse(200, {"notFound": {"movies": [{"ids": {"tmdb": 155}}]}}),
        )

        self.assertEqual(results[0].outcome, BATCH_SKIPPED)

    def test_an_outage_retries_the_whole_request(self):
        with self.assertRaises(TransientAdapterError):
            self._remove(
                _removal_item(1, "movie:155", receipt="9001"), FakeResponse(503, {})
            )


class PlayRemovalTests(EndToEndBase):
    """Removing a play in Floppy removes that exact play from WeTrakr."""

    def setUp(self):
        super().setUp()
        activate_binding(
            self.binding,
            capabilities=[
                CAPABILITY_PLAY_PUSH,
                CAPABILITY_PLAY_REMOVE,
                CAPABILITY_STATUS_PUSH,
            ],
            directions=[SyncDirection.OUTBOUND.value],
        )

    def _play(self, day, receipt):
        """Watch the film on one day and let WeTrakr accept it with a receipt."""
        movie = Movie.objects.create(
            item=self.item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=datetime.datetime(2026, 10, day, 20, 15, tzinfo=datetime.UTC),
        )
        OutboundStateDelivery.objects.update(next_attempt_at=None)
        reply = FakeResponse(
            200,
            {"added": {"plays": [{"play_id": receipt, "ids": {"tmdb": 155}}]}},
        )
        with patch("integrations.wetrakr_api.requests.request", return_value=reply):
            outbound.flush_binding(self.binding.pk)
        return movie

    def _removals(self):
        return list(OutboundStateDelivery.objects.filter(operation="play_remove"))

    def test_deleting_the_only_play_queues_its_removal_with_the_receipt(self):
        movie = self._play(18, 9001)

        movie.delete()

        (removal,) = self._removals()
        self.assertEqual(removal.payload["receipt"], "9001")
        self.assertEqual(removal.payload["watched_at"], "2026-10-18T20:15:00+00:00")

    def test_deleting_one_of_two_plays_removes_only_that_one(self):
        first = self._play(10, 9001)
        self._play(18, 9002)

        first.delete()

        (removal,) = self._removals()
        self.assertEqual(removal.payload["receipt"], "9001")

    def test_the_removal_is_sent_as_a_delete_of_that_play(self):
        self._play(18, 9001).delete()
        OutboundStateDelivery.objects.update(next_attempt_at=None)

        with patch(
            "integrations.wetrakr_api.requests.request",
            return_value=FakeResponse(200, {}),
        ) as call:
            outbound.flush_binding(self.binding.pk)

        self.assertEqual(call.call_args.args[0], "DELETE")
        self.assertTrue(call.call_args.args[1].endswith("/sync/tracklogs/9001"))
        self.assertEqual(
            self._removals()[0].status, OutboundDeliveryStatus.DELIVERED.value
        )

    def test_a_play_that_was_never_sent_is_not_sent_after_it_is_deleted(self):
        movie = self._watch()

        movie.delete()

        self.assertFalse(self._removals())
        play = OutboundStateDelivery.objects.get(operation="play")
        self.assertEqual(play.status, OutboundDeliveryStatus.SUPERSEDED.value)

    def test_nothing_is_removed_unless_removal_is_switched_on(self):
        activate_binding(
            self.binding,
            capabilities=[CAPABILITY_PLAY_PUSH],
            directions=[SyncDirection.OUTBOUND.value],
        )
        movie = self._play(18, 9001)

        movie.delete()

        self.assertFalse(self._removals())

    def test_editing_a_date_does_not_remove_anything(self):
        movie = self._play(18, 9001)

        movie.end_date = datetime.datetime(2026, 10, 19, 20, 15, tzinfo=datetime.UTC)
        movie.save()

        self.assertFalse(self._removals())

    def test_a_removal_is_queued_once(self):
        movie = self._play(18, 9001)
        movie.delete()
        self._play(19, 9002).delete()

        receipts = sorted(r.payload["receipt"] for r in self._removals())

        self.assertEqual(receipts, ["9001", "9002"])

    def test_a_removal_made_by_an_import_is_not_sent(self):
        movie = self._play(18, 9001)

        with importing_watch_state():
            movie.delete()

        self.assertFalse(self._removals())
