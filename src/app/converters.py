# https://docs.djangoproject.com/en/stable/topics/http/urls/#registering-custom-path-converters
from app.models import MEDIA_TYPES


class MediaTypeChecker:
    """Check if the media type is valid."""

    regex = f"({'|'.join(MEDIA_TYPES)})"

    def to_python(self, value):
        """Return the media type if it is valid."""
        return value

    def to_url(self, value):
        """Return the media type if it is valid."""
        return value
