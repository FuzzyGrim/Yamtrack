from rest_framework import serializers


class ImportSerializer(serializers.Serializer):
    """Validate mobile import requests."""

    mode = serializers.ChoiceField(choices=["new", "overwrite"])
    username = serializers.CharField(required=False, allow_blank=True)
    file = serializers.FileField(required=False)
