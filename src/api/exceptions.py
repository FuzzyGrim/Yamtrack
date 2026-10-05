from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    """Return handled API errors as `{"detail": str, "errors": {...}}`."""
    response = exception_handler(exc, context)
    if response is None or not isinstance(exc, ValidationError):
        return response

    errors = response.data
    if not isinstance(errors, dict):
        errors = {"non_field_errors": errors}

    response.data = {
        "detail": f"Invalid request: {', '.join(errors)}.",
        "errors": errors,
    }
    return response
