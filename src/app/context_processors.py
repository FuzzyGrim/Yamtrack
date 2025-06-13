# https://docs.djangoproject.com/en/stable/ref/templates/api/#writing-your-own-context-processors

from django.conf import settings


def export_vars(request):  # noqa: ARG001
    """Export variables to templates."""
    return {
        "REGISTRATION": settings.REGISTRATION,
        "REDIRECT_LOGIN_TO_SSO": settings.REDIRECT_LOGIN_TO_SSO,
        "IMG_NONE": settings.IMG_NONE,
    }


def media_enums(request): # noqa: ARG001
    """Export media enums to templates."""
    from app.models import MediaTypes, Sources, Status

    return {
        "MediaTypes": MediaTypes,
        "Sources": Sources,
        "Status": Status,
    }
