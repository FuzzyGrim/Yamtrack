from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
)

from .helpers import (
    LIST_SORTS,
    MEDIA_TYPE_COMPLETE_VALID_LIST,
    MEDIA_TYPE_VALID_LIST,
    SOURCES_VALID_LIST,
)
from .serializers import ApiErrorResponseSerializer


class BearerAuthenticationScheme(OpenApiAuthenticationExtension):
    """Describe the custom bearer token auth scheme for OpenAPI generation."""

    target_class = "api.authentication.BearerAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, _auto_schema):
        """Return the OpenAPI security scheme for bearer authentication."""
        return {
            "type": "http",
            "scheme": "bearer",
        }


class ApiKeyAuthenticationScheme(OpenApiAuthenticationExtension):
    """Describe the custom API key auth scheme for OpenAPI generation."""

    target_class = "api.authentication.APIKeyAuthentication"
    name = "ApiKeyAuth"

    def get_security_definition(self, _auto_schema):
        """Return the OpenAPI security scheme for header-based API keys."""
        return {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
        }


forbidden_response = OpenApiResponse(
    ApiErrorResponseSerializer,
    description="Forbidden",
    examples=[
        OpenApiExample(
            "No authentication example",
            description="No authentication example",
            summary="No authentication example",
            value={"detail": "Authentication credentials were not provided."},
        ),
        OpenApiExample(
            "Invalid token example",
            description="Invalid token example",
            summary="Invalid token example",
            value={"detail": "Invalid token"},
        ),
    ],
)

PaginationLimitParam = OpenApiParameter(
    name="limit",
    type={"type": "integer", "minimum": 1, "default": 20},
    location=OpenApiParameter.QUERY,
    description="Maximum number of results to return (default: 20).",
)

PaginationOffsetParam = OpenApiParameter(
    name="offset",
    type={"type": "integer", "minimum": 0, "default": 0},
    location=OpenApiParameter.QUERY,
    description="Number of results to skip before returning items (default: 0).",
)

ListSortParam = OpenApiParameter(
    name="sort",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description="Sort order for results.",
    enum=[
        suffix_sort
        for sort in LIST_SORTS
        for suffix_sort in (f"{sort}_asc", f"{sort}_desc")
    ],
)

ListSearchParam = OpenApiParameter(
    name="search",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description="Case-insensitive substring search on list name or description.",
)

MediaTypeParam = OpenApiParameter(
    name="media_type",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.PATH,
    description="Type of media item.",
    required=True,
    enum=MEDIA_TYPE_VALID_LIST,
)

MediaTypeCompleteParam = OpenApiParameter(
    name="media_type",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.PATH,
    description="Type of media item.",
    required=True,
    enum=MEDIA_TYPE_COMPLETE_VALID_LIST,
)

MediaIdParam = OpenApiParameter(
    name="media_id",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.PATH,
    description="ID of the media item.",
    required=True,
)

SourceParam = OpenApiParameter(
    name="source",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.PATH,
    description="Source of media item data for import operations.",
    required=False,
    enum=SOURCES_VALID_LIST,
)

SeasonNumberParam = OpenApiParameter(
    "season_number",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.PATH,
    description="Season number for the specified media item.",
    required=True,
)

EpisodeNumberParam = OpenApiParameter(
    "episode_number",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.PATH,
    description="Episode number for the specified media item.",
    required=True,
)
