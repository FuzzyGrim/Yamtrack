# https://docs.djangoproject.com/en/stable/ref/templates/api/#writing-your-own-context-processors

from django.conf import settings
from django.http import HttpRequest


def export_vars(request: HttpRequest) -> dict:  # noqa: ARG001
    """Export variables to templates."""
    return {
        "REGISTRATION": settings.REGISTRATION,
        "IMG_NONE": settings.IMG_NONE,
    }
