from django.conf import settings
from drf_spectacular.utils import (
    extend_schema_field,
)
from rest_framework import serializers

from .fields import UTCDateTimeField


class HealthCheckSerializer(serializers.Serializer):
    """Serializer for individual health checks."""

    status = serializers.SerializerMethodField()
    error = serializers.SerializerMethodField()

    @extend_schema_field(serializers.ChoiceField(choices=["ok", "error"]))
    def get_status(self, result):
        """Return the status of the health check result."""
        return "error" if result.error else "ok"

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_error(self, result):
        """Return the error of the health check, detailed only in debug mode."""
        if not result.error:
            return None
        return str(result.error) if settings.DEBUG else "Check failed"


class HealthResponseSerializer(serializers.Serializer):
    """Serializer for health check response."""

    status = serializers.SerializerMethodField()
    timestamp = UTCDateTimeField()
    checks = serializers.DictField(child=HealthCheckSerializer())

    @extend_schema_field(serializers.ChoiceField(choices=["ok", "unavailable"]))
    def get_status(self, health):
        """Return the overall status, unavailable if any check failed."""
        if any(result.error for result in health["checks"].values()):
            return "unavailable"
        return "ok"


class InfoSerializer(serializers.Serializer):
    """Serializer for the info endpoint."""

    version = serializers.CharField(source="VERSION")
    debug = serializers.BooleanField(source="DEBUG")
    frontend_url = serializers.SerializerMethodField()
    language = serializers.CharField(source="LANGUAGE_CODE")
    timezone = serializers.CharField(source="TIME_ZONE")
    admin_enabled = serializers.BooleanField(source="ADMIN_ENABLED")
    track_time = serializers.BooleanField(source="TRACK_TIME")

    @extend_schema_field(serializers.URLField())
    def get_frontend_url(self, obj):
        """Return the base URL of the app, or the local one if it isn't set."""
        return obj.BASE_URL or "http://localhost:8000"
