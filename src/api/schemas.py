from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
)

from .helpers import (
    EPISODE_SORTS,
    LIST_SORTS,
    MEDIA_TYPE_COMPLETE_VALID_LIST,
    MEDIA_TYPE_VALID_LIST,
    SOURCES_COMPLETE_VALID_LIST,
    SOURCES_VALID_LIST,
    get_sorts,
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


def _one_of_to_any_of(node):
    """Replace `oneOf` with `anyOf` in unions without a discriminator."""
    if isinstance(node, list):
        for child in node:
            _one_of_to_any_of(child)
    elif isinstance(node, dict):
        if "oneOf" in node and "discriminator" not in node:
            node["anyOf"] = node.pop("oneOf")
        for child in node.values():
            _one_of_to_any_of(child)


def polymorphic_any_of_hook(result, generator, request, public):  # noqa: ARG001
    """Postprocessing hook for polymorphic serializers without a discriminator."""
    _one_of_to_any_of(result)
    return result


BadRequestResponse = OpenApiResponse(
    ApiErrorResponseSerializer,
    description="Bad request",
    examples=[
        OpenApiExample(
            "Invalid request example",
            description="Invalid request example",
            summary="Invalid request example",
            value={"detail": "Invalid request."},
        )
    ],
)

ForbiddenResponse = OpenApiResponse(
    ApiErrorResponseSerializer,
    description="Forbidden",
    examples=[
        OpenApiExample(
            "No permission example",
            description="No permission example",
            summary="No permission example",
            value={"detail": "Forbidden"},
        ),
    ],
)

UnauthorizedResponse = OpenApiResponse(
    ApiErrorResponseSerializer,
    description="Unauthorized",
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

ConflictResponse = OpenApiResponse(
    ApiErrorResponseSerializer,
    description="Conflict",
    examples=[
        OpenApiExample(
            "Already tracked example",
            description="Already tracked example",
            summary="Already tracked example",
            value={"detail": "Conflict. Breaking Bad is already tracked."},
        )
    ],
)

InternalServerErrorResponse = OpenApiResponse(
    ApiErrorResponseSerializer,
    description="Internal server error",
    examples=[
        OpenApiExample(
            "Internal server error example",
            description="Internal server error",
            summary="Internal server error",
            value={"detail": "Internal server error."},
        )
    ],
)

NotFoundResponse = OpenApiResponse(
    ApiErrorResponseSerializer,
    description="Not found",
    examples=[
        OpenApiExample(
            "Not found example",
            description="Not found example",
            summary="Not found example",
            value={"detail": "Not found."},
        )
    ],
)

TooManyRequestsResponse = OpenApiResponse(
    ApiErrorResponseSerializer,
    description="Too Many Requests",
    examples=[
        OpenApiExample(
            "Sync too soon example",
            description="Sync too soon example",
            summary="Sync too soon example",
            value={
                "detail": ("The data was recently synced, please wait a few seconds.")
            },
        )
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

MediaSortParam = OpenApiParameter(
    name="sort",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description=(
        "Sort field, optionally followed by `_asc` or `_desc` "
        "(default: ascending). Episodes only support a subset of the sorts: "
        f"{', '.join(EPISODE_SORTS)}."
    ),
    enum=[
        suffix_sort
        for sort in get_sorts()
        for suffix_sort in (sort, f"{sort}_asc", f"{sort}_desc")
    ],
)

ListSearchParam = OpenApiParameter(
    name="search",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description="Case-insensitive substring search on list name or description.",
)


def _path_param(name, description, *, param_type=OpenApiTypes.STR, enum=None):
    """Build a required path parameter."""
    return OpenApiParameter(
        name=name,
        type=param_type,
        location=OpenApiParameter.PATH,
        description=description,
        required=True,
        enum=enum,
    )


MediaTypeParam = _path_param(
    "media_type",
    "Type of media item.",
    enum=MEDIA_TYPE_VALID_LIST,
)

MediaTypeCompleteParam = _path_param(
    "media_type",
    "Type of media item.",
    enum=MEDIA_TYPE_COMPLETE_VALID_LIST,
)

MediaTypeCompleteQueryParam = OpenApiParameter(
    name="media_type",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description="Type of media item.",
    required=False,
    enum=MEDIA_TYPE_COMPLETE_VALID_LIST,
)

MediaIdParam = _path_param("media_id", "ID of the media item.")

SourceParam = _path_param(
    "source",
    "Source of the media item data.",
    enum=SOURCES_VALID_LIST,
)

SourceCompleteParam = _path_param(
    "source",
    "Source of the media item data.",
    enum=SOURCES_COMPLETE_VALID_LIST,
)

SeasonNumberParam = _path_param(
    "season_number",
    "Season number for the specified media item.",
    param_type=OpenApiTypes.INT,
)

EpisodeNumberParam = _path_param(
    "episode_number",
    "Episode number for the specified media item.",
    param_type=OpenApiTypes.INT,
)
