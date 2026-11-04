from drf_spectacular.utils import (
    extend_schema_field,
)
from rest_framework import serializers

from api.changes_history_processor import (
    get_changes_from_diff,
    get_changes_from_new_record,
)
from app.models import (
    Episode,
    Item,
    Status,
)

from .fields import ItemIdField, MediaStatusChoiceField, UTCDateTimeField


class ItemSerializer(serializers.ModelSerializer):
    """Serializer used for item details."""

    media_id = serializers.SerializerMethodField()

    @extend_schema_field(str)
    def get_media_id(self, obj):
        """Return media_id preserving alphanumeric provider IDs."""
        media_id = getattr(obj, "media_id", None)
        if media_id is None:
            return None
        return str(media_id)

    class Meta:  # noqa: D106
        model = Item
        exclude = ("id",)


class ChangesHistoryChangeSerializer(serializers.Serializer):
    """Serializer for a single change in a history entry."""

    field = serializers.ChoiceField(
        choices=["end_date", "notes", "progress", "score", "start_date", "status"]
    )
    old_value = serializers.JSONField(allow_null=True, required=False)
    new_value = serializers.JSONField(allow_null=True, required=False)


class ChangesHistoryEntrySerializer(serializers.Serializer):
    """Serializer that builds a change-based history entry."""

    id = serializers.IntegerField(source="history_id", allow_null=True, required=False)
    item_id = ItemIdField(source="item_obj", allow_null=True, required=False)
    timestamp = UTCDateTimeField(source="history_date", allow_null=True, required=False)
    changes = serializers.SerializerMethodField()

    @extend_schema_field(ChangesHistoryChangeSerializer(many=True))
    def get_changes(self, obj):
        """Return the changes from the previous record of the history entry."""
        media_type = self.context.get("media_type")
        prev = getattr(obj, "prev_record", None)
        if prev is not None:
            changes = get_changes_from_diff(obj, prev, media_type)
        else:
            changes = get_changes_from_new_record(obj, media_type)

        status_field = MediaStatusChoiceField()
        for change in changes:
            if change.get("field") == "status":
                change["old_value"] = status_field.to_representation(
                    change.get("old_value"),
                )
                change["new_value"] = status_field.to_representation(
                    change.get("new_value"),
                )
        return ChangesHistoryChangeSerializer(changes, many=True).data


class ApiMessageResponseSerializer(serializers.Serializer):
    """Standard API message response serializer."""

    detail = serializers.CharField()


class ApiErrorResponseSerializer(serializers.Serializer):
    """Standard API error response serializer."""

    detail = serializers.CharField()
    errors = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
        help_text="Validation errors, keyed by field name.",
    )
    debug = serializers.CharField(
        required=False,
        help_text="Exception message, only returned when DEBUG is enabled.",
    )


class PaginationSerializer(serializers.Serializer):
    """Common pagination metadata serializer."""

    total = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)


class PaginatedChangesHistoryResponseSerializer(serializers.Serializer):
    """Paginated changes history serializer."""

    pagination = PaginationSerializer()
    results = ChangesHistoryEntrySerializer(many=True)


class ListMinimizedSerializer(serializers.Serializer):
    """Serializer for minimized list information."""

    list_id = serializers.IntegerField()
    list_item_id = serializers.IntegerField(allow_null=True)


class EpisodeHistory:
    """History entry of the watch of an episode."""

    score = None
    progress = 1
    status = Status.COMPLETED.value

    def __init__(self, episode):
        """Wrap the watched episode."""
        self.id = episode.id
        self.created_at = episode.created_at
        self.end_date = episode.end_date
        self.progressed_at = self.end_date
        self.start_date = None
        self.notes = episode.notes


class HistorySerializer(serializers.Serializer):
    """Serializer for watch history entries."""

    consumption_id = serializers.IntegerField(
        source="id",
        help_text=(
            "The id of the media consumption (id numbers are global for all "
            "consumptions of all medias of the same type, they aren't "
            "sequential per media)."
        ),
    )
    created = UTCDateTimeField(source="created_at", allow_null=True)
    score = serializers.FloatField(allow_null=True)
    progress = serializers.IntegerField(allow_null=True)
    progressed_at = UTCDateTimeField(allow_null=True)
    status = MediaStatusChoiceField()
    start_date = UTCDateTimeField(allow_null=True)
    end_date = UTCDateTimeField(allow_null=True)
    notes = serializers.CharField(allow_null=True)

    def to_representation(self, instance):
        """Serialize a media consumption, or the single watch of an episode."""
        if isinstance(instance, Episode):
            instance = EpisodeHistory(instance)
        return super().to_representation(instance)


class PaginatedHistoryResponseSerializer(serializers.Serializer):
    """Serializer for paginated watch history."""

    pagination = PaginationSerializer()
    results = HistorySerializer(many=True)


class UserSerializer(serializers.Serializer):
    """Serializer for user information."""

    id = serializers.IntegerField()
    username = serializers.CharField()
