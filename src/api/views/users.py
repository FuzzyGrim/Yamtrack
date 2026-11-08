import logging
from http import HTTPStatus as HTTP  # noqa: N814

from django.core.cache import cache
from django.db import IntegrityError, transaction
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
)
from rest_framework import views as drf_views
from rest_framework.response import Response

from api.examples import (
    ME_EXAMPLE,
    PASSWORD_CHANGED_EXAMPLE,
    PREFERENCES_EXAMPLE,
    SEARCH_CACHE_CLEARED_EXAMPLE,
    TOKEN_EXAMPLE,
    WEBUI_PREFERENCES_EXAMPLE,
)
from api.helpers import forbid_demo_user, revoke_user_sessions
from api.schemas import (
    BadRequestResponse,
    ForbiddenResponse,
)
from api.serializers import (
    ApiMessageResponseSerializer,
    PasswordChangeSerializer,
    PreferencesSerializer,
    SearchCacheResponseSerializer,
    TokenResponseSerializer,
    UserProfileSerializer,
    UserUpdateSerializer,
    WebUIPreferencesSerializer,
)
from app.providers import tmdb

logger = logging.getLogger(__name__)


# /api/v1/me/
class MeView(drf_views.APIView):
    """Profile of the authenticated user."""

    serializer_class = UserProfileSerializer

    @extend_schema(
        operation_id="me_get",
        summary="Get the authenticated user",
        responses={
            200: OpenApiResponse(
                UserProfileSerializer,
                description="Successful response",
                examples=[ME_EXAMPLE],
            ),
        },
    )
    def get(self, request):
        """Return the profile and the preferences of the authenticated user."""
        return Response(UserProfileSerializer(request.user).data, status=HTTP.OK)

    @extend_schema(
        operation_id="me_patch",
        summary="Update the username or the profile visibility",
        request=UserUpdateSerializer,
        responses={
            200: OpenApiResponse(
                UserProfileSerializer,
                description="User updated",
                examples=[ME_EXAMPLE],
            ),
            400: BadRequestResponse,
            403: ForbiddenResponse,
        },
    )
    def patch(self, request):
        """Update the username and/or the profile visibility."""
        forbid_demo_user(request.user)

        serializer = UserUpdateSerializer(
            request.user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserProfileSerializer(user).data, status=HTTP.OK)


# /api/v1/me/preferences/
class MePreferencesView(drf_views.APIView):
    """General preferences of the authenticated user."""

    serializer_class = PreferencesSerializer

    @extend_schema(
        operation_id="me_preferences_get",
        summary="Get the general preferences",
        responses={
            200: OpenApiResponse(
                PreferencesSerializer,
                description="Successful response",
                examples=[PREFERENCES_EXAMPLE],
            ),
        },
    )
    def get(self, request):
        """Return the preferences of the authenticated user."""
        return Response(PreferencesSerializer(request.user).data, status=HTTP.OK)

    @extend_schema(
        operation_id="me_preferences_patch",
        summary="Update the general preferences",
        description=(
            "Preferences that apply to the whole application. Only the sent "
            "fields are updated. Invalid values are rejected, unlike the web UI "
            "which ignores them."
        ),
        request=PreferencesSerializer,
        responses={
            200: OpenApiResponse(
                PreferencesSerializer,
                description="Preferences updated",
                examples=[PREFERENCES_EXAMPLE],
            ),
            400: BadRequestResponse,
            403: ForbiddenResponse,
        },
    )
    def patch(self, request):
        """Update the sent preferences."""
        forbid_demo_user(request.user)

        context = {}
        if "watch_provider_region" in request.data:
            context["watch_provider_regions"] = tmdb.watch_provider_regions()

        serializer = PreferencesSerializer(
            request.user,
            data=request.data,
            partial=True,
            context=context,
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(PreferencesSerializer(user).data, status=HTTP.OK)


# /api/v1/me/preferences/webui/
class MeWebUIPreferencesView(drf_views.APIView):
    """Web UI only preferences of the authenticated user."""

    serializer_class = WebUIPreferencesSerializer

    @extend_schema(
        operation_id="me_preferences_webui_get",
        summary="Get the web UI preferences",
        description="Preferences that only change how the web UI looks or behaves.",
        responses={
            200: OpenApiResponse(
                WebUIPreferencesSerializer,
                description="Successful response",
                examples=[WEBUI_PREFERENCES_EXAMPLE],
            ),
        },
    )
    def get(self, request):
        """Return the web UI preferences of the authenticated user."""
        return Response(WebUIPreferencesSerializer(request.user).data, status=HTTP.OK)

    @extend_schema(
        operation_id="me_preferences_webui_patch",
        summary="Update the web UI preferences",
        description=(
            "Only the sent fields are updated. Invalid values are rejected, "
            "unlike the web UI which ignores them."
        ),
        request=WebUIPreferencesSerializer,
        responses={
            200: OpenApiResponse(
                WebUIPreferencesSerializer,
                description="Preferences updated",
                examples=[WEBUI_PREFERENCES_EXAMPLE],
            ),
            400: BadRequestResponse,
            403: ForbiddenResponse,
        },
    )
    def patch(self, request):
        """Update the sent web UI preferences."""
        forbid_demo_user(request.user)

        serializer = WebUIPreferencesSerializer(
            request.user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(WebUIPreferencesSerializer(user).data, status=HTTP.OK)


# /api/v1/me/token/
class MeTokenView(drf_views.APIView):
    """API token of the authenticated user."""

    serializer_class = TokenResponseSerializer

    @extend_schema(
        operation_id="me_token_post",
        summary="Regenerate the API token",
        description=(
            "The previous token stops working immediately, so the following "
            "requests have to use the returned one."
        ),
        request=None,
        responses={
            200: OpenApiResponse(
                TokenResponseSerializer,
                description="Token regenerated",
                examples=[TOKEN_EXAMPLE],
            ),
            403: ForbiddenResponse,
        },
    )
    def post(self, request):
        """Generate a new token for the authenticated user."""
        forbid_demo_user(request.user)

        while True:
            try:
                request.user.regenerate_token()
                break
            except IntegrityError:
                continue

        return Response({"token": request.user.token}, status=HTTP.OK)


# /api/v1/me/password/
class MePasswordView(drf_views.APIView):
    """Password of the authenticated user."""

    serializer_class = PasswordChangeSerializer

    @extend_schema(
        operation_id="me_password_post",
        summary="Change the password",
        description=(
            "The API token isn't changed, but all the web login sessions are "
            "signed out."
        ),
        request=PasswordChangeSerializer,
        responses={
            200: OpenApiResponse(
                ApiMessageResponseSerializer,
                description="Password changed",
                examples=[PASSWORD_CHANGED_EXAMPLE],
            ),
            400: BadRequestResponse,
            403: ForbiddenResponse,
        },
    )
    def post(self, request):
        """Change the password and sign out the web sessions."""
        forbid_demo_user(request.user)

        serializer = PasswordChangeSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        user = request.user
        with transaction.atomic():
            user.set_password(serializer.validated_data["new_password"])
            user.save(update_fields=["password"])
            revoke_user_sessions(user)

        return Response(
            {"detail": "Password changed. Web sessions were signed out."},
            status=HTTP.OK,
        )


# /api/v1/me/search-cache/
class MeSearchCacheView(drf_views.APIView):
    """Cache of the provider searches."""

    serializer_class = SearchCacheResponseSerializer

    @extend_schema(
        operation_id="me_search_cache_delete",
        summary="Clear the search cache",
        responses={
            200: OpenApiResponse(
                SearchCacheResponseSerializer,
                description="Search cache cleared",
                examples=[SEARCH_CACHE_CLEARED_EXAMPLE],
            ),
        },
    )
    def delete(self, request):  # noqa: ARG002
        """Delete all the cached search entries."""
        deleted = cache.delete_pattern("search_*")
        logger.info("Successfully cleared %s search entries", deleted)
        return Response({"deleted": deleted}, status=HTTP.OK)
