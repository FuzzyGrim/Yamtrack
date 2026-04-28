from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
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
    response=ApiErrorResponseSerializer,
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
    description="Sorting expression in the format `<field>:asc|desc`.",
)

ListSearchParam = OpenApiParameter(
    name="search",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    description="Free-text filter for list names or item titles.",
)
