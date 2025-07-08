from unittest.mock import MagicMock

from django.apps import AppConfig
from django.conf import settings


class EventsConfig(AppConfig):
    """Events app configuration."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "events"

    def ready(self):
        """Run when the app is ready."""
        # Disable the reload_calendar task when testing
        if settings.TESTING:
            from events.tasks import reload_calendar  # noqa: PLC0415

            reload_calendar.delay = MagicMock()
