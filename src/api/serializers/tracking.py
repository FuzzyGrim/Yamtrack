from decimal import Decimal

from rest_framework import serializers

from app.models import Status


class TrackingWriteSerializer(serializers.Serializer):
    """Validate generic tracking writes."""

    status = serializers.ChoiceField(choices=Status.values, required=False)
    rating = serializers.DecimalField(
        max_digits=3,
        decimal_places=1,
        min_value=Decimal(0),
        max_value=Decimal(10),
        required=False,
        allow_null=True,
    )
    progress = serializers.IntegerField(min_value=0, required=False)
    start_date = serializers.DateTimeField(required=False, allow_null=True)
    end_date = serializers.DateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    season_number = serializers.IntegerField(required=False, allow_null=True)


class ConsumeSerializer(serializers.Serializer):
    """Validate consume action payloads."""

    consumed_at = serializers.DateTimeField(required=False, allow_null=True)
    create_diary_entry = serializers.BooleanField(required=False, default=False)


class EpisodeWatchSerializer(serializers.Serializer):
    """Validate watched episode payloads."""

    watched_at = serializers.DateTimeField(required=False, allow_null=True)


class BookProgressSerializer(serializers.Serializer):
    """Validate book progress payloads."""

    progress_type = serializers.ChoiceField(choices=["pages", "percentage"])
    value = serializers.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal(0))
    notes = serializers.CharField(required=False, allow_blank=True)
