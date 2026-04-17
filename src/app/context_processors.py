# https://docs.djangoproject.com/en/stable/ref/templates/api/#writing-your-own-context-processors

from django.conf import settings

from app.models import MediaTypes, Sources, Status, UserMessage


def export_vars(request):  # noqa: ARG001
    """Export variables to templates."""
    return {
        "REGISTRATION": settings.REGISTRATION,
        "REDIRECT_LOGIN_TO_SSO": settings.REDIRECT_LOGIN_TO_SSO,
        "IMG_NONE": settings.IMG_NONE,
        "TRACK_TIME": settings.TRACK_TIME,
    }


def media_enums(request):  # noqa: ARG001
    """Export media enums to templates."""
    return {
        "MediaTypes": MediaTypes,
        "Sources": Sources,
        "Status": Status,
    }


def persistent_messages(request):
    """Return persistent user notifications that have not been shown yet."""
    if not request.user.is_authenticated:
        return {"persistent_messages": []}

    return {
        "persistent_messages": list(
            UserMessage.objects.filter(
                user=request.user,
                shown_at__isnull=True,
            ),
        ),
    }
