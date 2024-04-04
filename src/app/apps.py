from django.apps import AppConfig


class AppConfig(AppConfig):
    """Default app config."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "app"
