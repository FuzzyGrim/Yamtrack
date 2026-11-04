from datetime import UTC

from django.db import models
from django.utils import timezone
from rest_framework import serializers

from api.helpers import (
    MEDIA_STATUS_CHOICES,
    MEDIA_TYPE_COMPLETE_VALID_LIST,
    MEDIA_TYPE_VALID_LIST,
    SOURCES_COMPLETE_VALID_LIST,
    SOURCES_VALID_LIST,
    build_item_id,
    build_parent_id,
    get_media_status,
)


class ItemIdField(serializers.CharField):
    """Custom field to generate item_id string."""

    def to_representation(self, item):  # noqa: D102
        return build_item_id(item)


class ParentIdField(serializers.CharField):
    """Custom field to generate parent_id string for seasons and episodes."""

    def __init__(self, **kwargs):
        """Initialize the field, it's null for medias without a parent."""
        kwargs.setdefault("allow_null", True)
        super().__init__(**kwargs)

    def to_representation(self, item):  # noqa: D102
        return build_parent_id(item)


class UTCDateTimeField(serializers.DateTimeField):
    """DateTimeField that returns the datetimes in UTC."""

    def __init__(self, **kwargs):
        """Initialize the field with the UTC timezone."""
        kwargs.setdefault("default_timezone", UTC)
        super().__init__(**kwargs)

    def to_internal_value(self, value):
        """Parse datetimes in the server timezone, then normalize to UTC."""
        field_timezone = getattr(self, "timezone", None)
        has_field_timezone = hasattr(self, "timezone")
        self.timezone = timezone.get_current_timezone()
        try:
            value = super().to_internal_value(value)
        finally:
            if has_field_timezone:
                self.timezone = field_timezone
            else:
                del self.timezone
        return value.astimezone(field_timezone) if field_timezone is not None else value


class UTCModelSerializer(serializers.ModelSerializer):
    """ModelSerializer that returns the datetime model fields in UTC."""

    serializer_field_mapping = {
        **serializers.ModelSerializer.serializer_field_mapping,
        models.DateTimeField: UTCDateTimeField,
    }


class MediaStatusChoiceField(serializers.ChoiceField):
    """Custom field for media status options."""

    def __init__(self, **kwargs):
        """Initialize with predefined status choices."""
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_null", True)
        status_choices = MEDIA_STATUS_CHOICES
        kwargs["choices"] = status_choices
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        """Convert the API integer status to the model status."""
        value = super().to_internal_value(data)
        return get_media_status(value, reverse=True)

    def to_representation(self, value):
        """Convert the model status to the API integer status."""
        return get_media_status(value)


class MediaSourceChoiceField(serializers.ChoiceField):
    """Custom field for media source options."""

    def __init__(self, **kwargs):
        """Initialize with predefined source choices."""
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_null", True)
        kwargs["choices"] = SOURCES_VALID_LIST
        super().__init__(**kwargs)


class MediaSourceCompleteChoiceField(serializers.ChoiceField):
    """Custom field for media source options including manual."""

    def __init__(self, **kwargs):
        """Initialize with predefined source choices including manual."""
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_null", True)
        kwargs["choices"] = SOURCES_COMPLETE_VALID_LIST
        super().__init__(**kwargs)


class MediaTypeChoiceField(serializers.ChoiceField):
    """Custom field for media type options."""

    def __init__(self, **kwargs):
        """Initialize with predefined media type choices."""
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_null", True)
        kwargs["choices"] = MEDIA_TYPE_VALID_LIST
        super().__init__(**kwargs)


class MediaTypeCompleteChoiceField(serializers.ChoiceField):
    """Custom field for complete media type options."""

    def __init__(self, **kwargs):
        """Initialize with predefined complete media type choices."""
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_null", True)
        kwargs["choices"] = MEDIA_TYPE_COMPLETE_VALID_LIST
        super().__init__(**kwargs)
