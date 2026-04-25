from django.contrib import admin
from django.utils import timezone

from events.models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    """Admin configuration for the Event model."""

    search_fields = ["item__title", "item__media_id"]
    list_display = [
        "__str__",
        "formatted_datetime",
        "content_number",
        "notification_sent",
    ]
    list_filter = [
        "notification_sent",
        "item__media_type",
        "item__source",
        ("datetime", admin.DateFieldListFilter),
    ]

    def formatted_datetime(self, obj):
        """Display datetime in a safe format, handling extreme values."""
        try:
            return timezone.localtime(obj.datetime).strftime("%Y-%m-%d %H:%M")
        except (OverflowError, ValueError):
            return "Invalid date"
