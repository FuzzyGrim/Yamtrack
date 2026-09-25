from users.models import WATCH_PROVIDER_REGION_UNSET, User

NO_CHARGE_BIT = 1
PAID_BIT = 2


def bitmask_for_region(region_providers):
    """Compute a no-charge/paid availability bitmask from one region's raw providers."""
    bitmask = 0
    if any(region_providers.get(key) for key in ("flatrate", "free", "ads")):
        bitmask |= NO_CHARGE_BIT
    if any(region_providers.get(key) for key in ("buy", "rent")):
        bitmask |= PAID_BIT
    return bitmask


def compute_stream_availability(all_providers, regions):
    """Build a {region: bitmask} map for the given regions from a raw providers dict."""
    all_providers = all_providers or {}
    return {
        region: bitmask_for_region(all_providers.get(region, {})) for region in regions
    }


def configured_regions():
    """Return the distinct watch-provider regions actually configured by users."""
    return set(
        User.objects.exclude(
            watch_provider_region=WATCH_PROVIDER_REGION_UNSET,
        )
        .exclude(watch_provider_region="")
        .values_list("watch_provider_region", flat=True)
        .distinct(),
    )
