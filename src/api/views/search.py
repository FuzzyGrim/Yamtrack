from http import HTTPStatus as HTTP  # noqa: N814

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import views as drf_views
from rest_framework.response import Response

from api.helpers import (
    SOURCES_COMPLETE_VALID_LIST,
    check_source_type,
    check_valid_type,
    fetch_search_results,
    paginate_data,
    parse_limit_offset,
)
from api.schemas import (
    BadRequestResponse,
    MediaTypeParam,
    PaginationLimitParam,
    PaginationOffsetParam,
)
from api.serializers import (
    MediaSerializer,
    SearchMediaSerializer,
    SearchResponseSerializer,
)
from app.models import Sources


# /api/v1/search/[media_type]/
class SearchProviderView(drf_views.APIView):
    """Search view."""

    serializer_class = MediaSerializer

    @extend_schema(
        operation_id="search_get",
        summary="Search for media",
        parameters=[
            MediaTypeParam,
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search query",
            ),
            OpenApiParameter(
                name="source",
                type=OpenApiTypes.STR,
                enum=SOURCES_COMPLETE_VALID_LIST,
                location=OpenApiParameter.QUERY,
                description="Source of the media",
            ),
            PaginationLimitParam,
            PaginationOffsetParam,
        ],
        responses={
            200: OpenApiResponse(
                SearchResponseSerializer,
            ),
            400: BadRequestResponse,
        },
    )
    def get(self, request, media_type):
        """Search for media using the specified provider."""
        search = request.GET.get("search", "")
        source = request.GET.get("source", None)
        limit, offset, err = parse_limit_offset(request)
        if err:
            return err

        if not check_valid_type(media_type, complete=True):
            return Response(
                {"detail": "Unsupported media type."},
                status=HTTP.BAD_REQUEST,
            )
        if media_type in ("season", "episode"):
            # Since data of seasons and episodes (title, author, description,
            # etc.) is not saved in the db but retrieved every time, it's not
            # possible to search for them
            return Response(
                {
                    "detail": f"Search for {media_type} is not supported.",
                },
                status=HTTP.BAD_REQUEST,
            )

        if source and not check_source_type(media_type, source):
            return Response(
                {
                    "detail": f"Cannot query `{source}` for `{media_type}` media type",
                },
                status=HTTP.BAD_REQUEST,
            )

        presliced = source == Sources.MANUAL.value

        try:
            results, total = fetch_search_results(
                media_type,
                search,
                source,
                limit,
                offset,
                request.user,
                presliced=presliced,
            )
        except Exception:  # noqa: BLE001
            return Response(
                {"detail": HTTP.INTERNAL_SERVER_ERROR.phrase},
                status=HTTP.INTERNAL_SERVER_ERROR,
            )

        paginated_data = paginate_data(
            request,
            results,
            limit,
            offset,
            total=total,
            sliced=presliced,
        )
        paginated_data["results"] = SearchMediaSerializer(
            paginated_data["results"],
            many=True,
        ).data
        return Response(paginated_data, status=HTTP.OK)
