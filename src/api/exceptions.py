from http import HTTPStatus as HTTP  # noqa: N814

from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

from api.helpers import InvalidSortError, error_response
from app.providers.services import ProviderAPIError


def _validation_data(errors):
    """Return the validation errors as `{"detail": str, "errors": {...}}`."""
    if not isinstance(errors, dict):
        errors = {"non_field_errors": errors}

    return {
        "detail": f"Invalid request: {', '.join(errors)}.",
        "errors": errors,
    }


def _django_validation_response(exc):
    """Build a django validation error response."""
    errors = exc.message_dict if hasattr(exc, "error_dict") else exc.messages
    return Response(_validation_data(errors), status=HTTP.BAD_REQUEST)


def _provider_error_response(exc):
    """Map a provider failure to 404 if the media doesn't exist, else to 500."""
    if exc.status_code == HTTP.NOT_FOUND:
        return error_response("Media not found at the provider.", HTTP.NOT_FOUND, exc)
    return error_response(
        "An error occurred while contacting the provider.",
        HTTP.INTERNAL_SERVER_ERROR,
        exc,
    )


def _handle_non_drf_exception(exc):
    """Return the response for the non DRF exceptions the views let through."""
    if isinstance(exc, ProviderAPIError):
        return _provider_error_response(exc)
    if isinstance(exc, InvalidSortError):
        return error_response("Invalid sorting", HTTP.BAD_REQUEST)
    if isinstance(exc, DjangoValidationError):
        return _django_validation_response(exc)
    if isinstance(exc, IntegrityError):
        return error_response("Conflict with existing data.", HTTP.CONFLICT, exc)
    if isinstance(exc, ObjectDoesNotExist):
        return error_response("Not found.", HTTP.NOT_FOUND, exc)
    return None


def api_exception_handler(exc, context):
    """Return handled API errors as `{"detail": str}` plus optional extra keys.

    Used for errors from the views, provider failures, invalid sorts and database level
    errors. For other exceptions `ApiJsonErrorMiddleware` is used.
    """
    response = exception_handler(exc, context)
    if response is None:
        return _handle_non_drf_exception(exc)

    if isinstance(exc, ValidationError):
        response.data = _validation_data(response.data)
    return response
