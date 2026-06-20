from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class AuthRateThrottle(AnonRateThrottle):
    """Tighter throttle for login/register/password endpoints."""

    scope = "auth"


class SearchRateThrottle(UserRateThrottle):
    """Throttle provider-backed search/detail endpoints."""

    scope = "search"
