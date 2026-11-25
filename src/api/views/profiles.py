from http import HTTPStatus as HTTP  # noqa: N814

from django.contrib.auth import get_user_model
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
)
from rest_framework import views as drf_views
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from api.examples import MEDIA_LIST_EXAMPLE, PUBLIC_PROFILE_EXAMPLE
from api.helpers import (
    fetch_results_for_type,
    paginate_data,
    parse_limit_offset,
    parse_sort_filter,
    parse_status_param,
    validate_media_path,
)
from api.schemas import (
    BadRequestResponse,
    MediaSortParam,
    MediaTypeCompleteParam,
    NotFoundResponse,
    PaginationLimitParam,
    PaginationOffsetParam,
    UsernameParam,
)
from api.serializers import (
    MixedMediaSerializer,
    PaginatedMediaSerializer,
    PublicProfileSerializer,
)

User = get_user_model()


def get_visible_user(request, username):
    """Return the user if the requester can see their profile."""
    try:
        target_user = User.objects.get(username=username)
    except User.DoesNotExist as e:
        msg = "User not found."
        raise NotFound(msg) from e

    if target_user.profile_private and request.user != target_user:
        msg = "User not found."
        raise NotFound(msg)
    return target_user


# /api/v1/users/{username}/
class UserProfileView(drf_views.APIView):
    """Public profile of a user."""

    permission_classes = []
    serializer_class = PublicProfileSerializer

    @extend_schema(
        operation_id="user_profile_get",
        summary="Get a public profile",
        description="Private profiles answer 404, as if the user didn't exist.",
        parameters=[UsernameParam],
        responses={
            200: OpenApiResponse(
                PublicProfileSerializer,
                description="Successful response",
                examples=[PUBLIC_PROFILE_EXAMPLE],
            ),
            404: NotFoundResponse,
        },
    )
    def get(self, request, username):
        """Return the public information of a user."""
        target_user = get_visible_user(request, username)
        return Response(
            PublicProfileSerializer(
                {
                    "username": target_user.username,
                    "enabled_media_types": target_user.get_enabled_media_types(),
                },
            ).data,
            status=HTTP.OK,
        )


# /api/v1/users/{username}/media/{media_type}/
class UserMediaTypeListView(drf_views.APIView):
    """Media of a user with a public profile."""

    permission_classes = []
    serializer_class = PaginatedMediaSerializer

    @extend_schema(
        operation_id="user_media_type_list_get",
        summary="List the media of a public profile",
        description=(
            "Private profiles and media types the user disabled answer 404. "
            "The lists the media belongs to aren't exposed."
        ),
        parameters=[
            UsernameParam,
            MediaTypeCompleteParam,
            MediaSortParam,
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                PaginatedMediaSerializer,
                description="Successful response",
                examples=[MEDIA_LIST_EXAMPLE],
            ),
            400: BadRequestResponse,
            404: NotFoundResponse,
        },
    )
    def get(self, request, username, media_type):
        """Retrieve the media of a specific type of a public profile."""
        target_user = get_visible_user(request, username)

        status = request.GET.get("status", "")
        search = request.GET.get("search", "")
        sort_filter = request.GET.get("sort", "")
        limit, offset = parse_limit_offset(request)

        status = parse_status_param(status)
        sort, sort_order = parse_sort_filter(sort_filter)
        validate_media_path(media_type, complete=True)

        if media_type not in target_user.get_enabled_media_types():
            msg = "Media type not found."
            raise NotFound(msg)

        results = fetch_results_for_type(
            target_user,
            media_type,
            status,
            sort,
            sort_order,
            search,
        )

        paginated_data = paginate_data(request, results, limit, offset)
        paginated_data["results"] = MixedMediaSerializer(
            paginated_data["results"],
            context={"request": request, "lists_by_item_id": {}},
            many=True,
        ).data
        return Response(paginated_data, status=HTTP.OK)
