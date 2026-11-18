from django.contrib.auth import get_user_model
from django.test import TestCase

from app.stream_availability import (
    NO_CHARGE_BIT,
    PAID_BIT,
    bitmask_for_region,
    compute_stream_availability,
    configured_regions,
)
from users.models import WATCH_PROVIDER_REGION_UNSET

User = get_user_model()


class BitmaskForRegionTest(TestCase):
    """Test bitmask_for_region."""

    def test_empty(self):
        """No provider data yields a zero bitmask."""
        self.assertEqual(bitmask_for_region({}), 0)

    def test_no_charge_only(self):
        """flatrate/free/ads yield the no-charge bit."""
        for key in ("flatrate", "free", "ads"):
            with self.subTest(key=key):
                self.assertEqual(
                    bitmask_for_region({key: [{"provider_id": 1}]}),
                    NO_CHARGE_BIT,
                )

    def test_paid_only(self):
        """buy/rent yield the paid bit."""
        for key in ("buy", "rent"):
            with self.subTest(key=key):
                self.assertEqual(
                    bitmask_for_region({key: [{"provider_id": 1}]}),
                    PAID_BIT,
                )

    def test_both(self):
        """Both tiers present yield both bits set."""
        self.assertEqual(
            bitmask_for_region(
                {"flatrate": [{"provider_id": 1}], "buy": [{"provider_id": 2}]},
            ),
            NO_CHARGE_BIT | PAID_BIT,
        )

    def test_empty_lists_do_not_count(self):
        """Empty provider lists don't set any bit."""
        self.assertEqual(bitmask_for_region({"flatrate": [], "buy": []}), 0)


class ComputeStreamAvailabilityTest(TestCase):
    """Test compute_stream_availability."""

    def test_builds_map_for_requested_regions(self):
        """Only requested regions are included, with a bitmask each."""
        all_providers = {
            "US": {"flatrate": [{"provider_id": 1}]},
            "GB": {"buy": [{"provider_id": 2}]},
            "FR": {"flatrate": [{"provider_id": 1}]},
        }

        result = compute_stream_availability(all_providers, {"US", "GB"})

        self.assertEqual(result, {"US": NO_CHARGE_BIT, "GB": PAID_BIT})

    def test_missing_region_defaults_to_zero(self):
        """A requested region absent from the payload gets a zero bitmask."""
        result = compute_stream_availability({"US": {"flatrate": [{"id": 1}]}}, {"DE"})

        self.assertEqual(result, {"DE": 0})

    def test_none_payload(self):
        """A None providers payload doesn't raise."""
        result = compute_stream_availability(None, {"US"})

        self.assertEqual(result, {"US": 0})


class ConfiguredRegionsTest(TestCase):
    """Test configured_regions."""

    def test_excludes_unset_and_empty(self):
        """Only users with a real region configured contribute to the set."""
        pw = {"password": "pass12345"}
        User.objects.create_user(
            **pw,
            username="unset",
            watch_provider_region=WATCH_PROVIDER_REGION_UNSET,
        )
        User.objects.create_user(**pw, username="us", watch_provider_region="US")
        User.objects.create_user(**pw, username="gb", watch_provider_region="GB")
        User.objects.create_user(**pw, username="us2", watch_provider_region="US")

        self.assertEqual(configured_regions(), {"US", "GB"})

    def test_no_configured_regions(self):
        """No users with a region configured yields an empty set."""
        credentials = {"username": "unset", "password": "pass12345"}
        User.objects.create_user(
            **credentials,
            watch_provider_region=WATCH_PROVIDER_REGION_UNSET,
        )

        self.assertEqual(configured_regions(), set())
