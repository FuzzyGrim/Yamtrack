# https://docs.djangoproject.com/en/4.2/topics/http/urls/#registering-custom-path-converters

class MediaTypeChecker:
    """Check if the media type is valid."""

    regex = "(anime|manga|tv|movie)"

    def to_python(self: "MediaTypeChecker", value: str) -> str:
        """Return the media type if it is valid."""

        return value

    def to_url(self: "MediaTypeChecker", value: str) -> str:
        """Return the media type if it is valid."""

        return value


class StatusChecker:
    """Check if the status is valid."""

    regex = "(completed|watching|paused|dropped|planning)"

    def to_python(self: "StatusChecker", value: str) -> str:
        """Return the status if it is valid."""

        return value

    def to_url(self: "StatusChecker", value: str) -> str:
        """Return the status if it is valid."""
        return value
