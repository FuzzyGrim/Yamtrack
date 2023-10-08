# https://docs.djangoproject.com/en/4.2/ref/templates/api/#writing-your-own-context-processors

from decouple import config
from django.conf import settings
from django.http import HttpRequest


def export_vars(request: HttpRequest) -> dict:  # noqa: ARG001
    """Export variables to templates."""
    return {
        "REGISTRATION": config("REGISTRATION", default=True, cast=bool),
        "IMG_NONE": settings.IMG_NONE,
    }
