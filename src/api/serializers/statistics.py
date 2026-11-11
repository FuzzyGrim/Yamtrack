from rest_framework import serializers

from .fields import (
    ItemIdField,
    MediaSourceCompleteChoiceField,
    MediaStatusChoiceField,
    MediaTypeCompleteChoiceField,
    ParentIdField,
    UTCDateTimeField,
)
from .media import MediaSerializer


class StatisticsMediaCountSerializer(serializers.Serializer):
    """Serializer for media count by enabled types in statistics response."""

    total = serializers.IntegerField()
    tv = serializers.IntegerField(required=False)
    season = serializers.IntegerField(required=False)
    movie = serializers.IntegerField(required=False)
    anime = serializers.IntegerField(required=False)
    manga = serializers.IntegerField(required=False)
    game = serializers.IntegerField(required=False)
    book = serializers.IntegerField(required=False)
    comic = serializers.IntegerField(required=False)
    boardgame = serializers.IntegerField(required=False)


class TimelineItemSerializer(serializers.Serializer):
    """Compact serializer used for timeline entries to reduce payload size."""

    id = serializers.IntegerField()
    item_id = ItemIdField(source="item")
    parent_id = ParentIdField(source="item")
    title = serializers.CharField(source="item.title")
    image = serializers.URLField(source="item.image", allow_null=True)
    media_type = MediaTypeCompleteChoiceField(source="item.media_type")
    source = MediaSourceCompleteChoiceField(source="item.source")
    created_at = UTCDateTimeField()
    score = serializers.FloatField(allow_null=True)
    progress = serializers.IntegerField()
    progressed_at = UTCDateTimeField(allow_null=True)
    status = MediaStatusChoiceField()
    start_date = UTCDateTimeField(allow_null=True)
    end_date = UTCDateTimeField(allow_null=True)
    notes = serializers.CharField(allow_blank=True)


class StatisticsResponseSerializer(serializers.Serializer):
    """Serializer for statistics endpoint payload."""

    start_date = serializers.DateField(allow_null=True, required=False)
    end_date = serializers.DateField(allow_null=True, required=False)
    media_count = StatisticsMediaCountSerializer()
    activity_data = serializers.JSONField()
    media_type_distribution = serializers.DictField()
    score_distribution = serializers.DictField()
    top_rated = MediaSerializer(many=True)
    status_distribution = serializers.DictField()
    status_pie_chart_data = serializers.JSONField()
    timeline = serializers.DictField(child=TimelineItemSerializer(many=True))
