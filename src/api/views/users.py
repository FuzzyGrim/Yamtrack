from http import HTTPStatus as HTTP  # noqa: N814

from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
)
from rest_framework import views as drf_views
from rest_framework.response import Response

from api.examples import (
    ME_EXAMPLE,
    PREFERENCES_EXAMPLE,
)
from api.helpers import forbid_demo_user
from api.schemas import (
    BadRequestResponse,
    ForbiddenResponse,
)
from api.serializers import (
    PreferencesSerializer,
    UserProfileSerializer,
    UserUpdateSerializer,
)
from app.providers import tmdb


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
    """Preferences of the authenticated user."""

    serializer_class = PreferencesSerializer

    @extend_schema(
        operation_id="me_preferences_get",
        summary="Get the preferences",
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
        summary="Update the preferences",
        description=(
            "Only the sent fields are updated. Invalid values are rejected, "
            "unlike the web UI which ignores them."
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
