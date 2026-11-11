from rest_framework import serializers


class WatchProviderSerializer(serializers.Serializer):
    """Serializer for a single watch provider."""

    provider_id = serializers.IntegerField()
    provider_name = serializers.CharField()
    image = serializers.URLField(allow_null=True)
    display_priority = serializers.IntegerField(allow_null=True)


class WatchProvidersResponseSerializer(serializers.Serializer):
    """Serializer for watch providers endpoint."""

    region = serializers.CharField()
    link = serializers.URLField(allow_null=True)
    no_charge = WatchProviderSerializer(many=True)
    paid = WatchProviderSerializer(many=True)
