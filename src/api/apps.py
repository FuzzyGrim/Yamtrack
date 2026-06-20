from django.apps import AppConfig


class ApiConfig(AppConfig):
    """REST API app for native clients."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "api"
