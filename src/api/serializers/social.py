from rest_framework import serializers


class LikeSerializer(serializers.Serializer):
    """Validate generic like target payloads."""

    target_type = serializers.ChoiceField(choices=["diary", "list"])
    target_id = serializers.IntegerField(min_value=1)
