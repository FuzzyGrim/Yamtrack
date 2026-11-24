import logging
from http import HTTPStatus as HTTP  # noqa: N814

from django.conf import settings
from django.http import JsonResponse
from django.template.response import ContentNotRenderedError, TemplateResponse

logger = logging.getLogger(__name__)


class ApiJsonErrorMiddleware:
    """Convert HTML error responses for API paths into JSON responses."""

    def __init__(self, get_response):  # noqa: D107
        self.get_response = get_response

    def __call__(self, request):  # noqa: D102
        response = self.get_response(request)
        path = self._get_request_path(request)

        if path.startswith("/api/") and response is not None:
            response = self._handle_template_response(response, path)
            status = getattr(response, "status_code", HTTP.OK)
            content_type = self._get_content_type(response)

            if self._should_convert_to_json(status, content_type):
                return self._build_json_error_response(response, status)

        return response

    def _get_request_path(self, request):
        """Safely extract the request path."""
        try:
            return request.path or ""
        except Exception:  # noqa: BLE001
            return ""

    def _handle_template_response(self, response, path):
        """Render TemplateResponse if needed."""
        if isinstance(response, TemplateResponse):
            try:
                response = response.render()
            except ContentNotRenderedError:
                logger.exception(
                    "TemplateResponse could not be rendered for %s",
                    path,
                )
            except Exception:
                logger.exception(
                    "Error while rendering TemplateResponse for %s",
                    path,
                )
        return response

    def _get_content_type(self, response):
        """Extract content type from response."""
        if hasattr(response, "headers"):
            return response.headers.get("Content-Type", "")

        if hasattr(response, "get"):
            try:
                return response.get("Content-Type", "")
            except Exception:  # noqa: BLE001
                return getattr(response, "content_type", "")

        return getattr(response, "content_type", "")

    def _should_convert_to_json(self, status, content_type):
        """Determine if response should be converted to JSON."""
        if content_type and "application/json" in content_type:
            return False
        return status >= HTTP.BAD_REQUEST and (
            not content_type or "html" in content_type.lower()
        )

    def _build_json_error_response(self, response, status):
        """Build JSON error response."""
        try:
            detail = HTTP(status).phrase
        except ValueError:
            detail = "Unknown status"

        payload = {"detail": detail}

        if settings.DEBUG and hasattr(response, "content"):
            detail = self._extract_debug_detail(response)
            if detail:
                payload["debug_html_snippet"] = detail[:2000]

        return JsonResponse(payload, status=status)

    def _extract_debug_detail(self, response):
        """Extract debug detail from response content."""
        try:
            return response.content.decode(errors="ignore")
        except Exception:  # noqa: BLE001
            return None

    def process_exception(self, request, exception):
        """Intercept unhandled exceptions for API paths and return JSON.

        This prevents Django from rendering the HTML technical 500 page
        (when DEBUG=True) for API calls.
        """
        path = self._get_request_path(request)

        if not path.startswith("/api/"):
            return None

        logger.error(
            "Unhandled exception during API request: %s",
            path,
            exc_info=exception,
        )

        detail = str(exception) if settings.DEBUG else "Internal server error."

        return JsonResponse({"detail": detail}, status=HTTP.INTERNAL_SERVER_ERROR)
