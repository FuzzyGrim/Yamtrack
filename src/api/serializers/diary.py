from rest_framework import serializers

from api.serializers.common import MediaRefSerializer


class DiaryEntryWriteSerializer(serializers.Serializer):
    """Validate diary entry create/update payloads."""

    ref = MediaRefSerializer(required=False)
    consumed_at = serializers.DateTimeField(required=False)
    rating = serializers.DecimalField(
        max_digits=3,
        decimal_places=1,
        min_value=0,
        max_value=10,
        required=False,
        allow_null=True,
    )
    review = serializers.CharField(required=False, allow_blank=True)
    review_title = serializers.CharField(required=False, allow_blank=True, max_length=255)
    liked = serializers.BooleanField(required=False, default=False)
    is_rewatch = serializers.BooleanField(required=False, default=False)
    auto_mark_consumed = serializers.BooleanField(required=False, default=False)
    contains_spoilers = serializers.BooleanField(required=False, default=False)
    visibility = serializers.ChoiceField(
        choices=["public", "followers", "private"],
        required=False,
        default="public",
    )
    tags = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        default=list,
    )
