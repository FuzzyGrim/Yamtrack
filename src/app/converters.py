# https://docs.djangoproject.com/en/stable/topics/http/urls/#registering-custom-path-converters


class MediaTypeChecker:
    """Check if the media type is valid."""

    regex = "(anime|manga|tv|season|movie)"

    def to_python(self, value):
        """Return the media type if it is valid."""
        return value

    def to_url(self, value):
        """Return the media type if it is valid."""
        return value
