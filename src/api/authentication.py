from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from users.models import User

AUTHORIZATION_HEADER = "Authorization"
API_KEY_HEADER = "X-API-Key"


class BearerAuthentication(BaseAuthentication):
    """Bearer Authentication."""

    keyword = "Bearer"
    header_name = AUTHORIZATION_HEADER

    def authenticate(self, request):
        """Authenticate the user with Bearer token."""
        auth = request.headers.get(self.header_name)
        if not auth:
            return None
        parts = auth.split()
        if len(parts) != 2 or parts[0] != self.keyword:  # noqa: PLR2004
            return None
        token = parts[1]
        try:
            user = User.objects.get(token=token)
        except User.DoesNotExist:
            msg = "Invalid token"
            raise AuthenticationFailed(msg) from None
        return (user, None)


class APIKeyAuthentication(BaseAuthentication):
    """API Key Authentication."""

    header_name = API_KEY_HEADER

    def authenticate(self, request):
        """Authenticate the user with API Key."""
        auth = request.headers.get(self.header_name)
        if not auth:
            return None
        try:
            user = User.objects.get(token=auth)
        except User.DoesNotExist:
            msg = "Invalid token"
            raise AuthenticationFailed(msg) from None
        return (user, None)
