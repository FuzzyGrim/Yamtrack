from django.apps import AppConfig

from app.providers import igdb


class AppConfig(AppConfig):
    """Default app config."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "app"

    def ready(self):
        """Run when the app is ready."""
        igdb.get_access_token()
