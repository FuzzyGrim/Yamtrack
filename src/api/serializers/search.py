from rest_framework import serializers

from .common import PaginationSerializer


class SearchMediaSerializer(serializers.Serializer):
    """Serializer for individual media items in search results."""

    media_id = serializers.CharField()
    source = serializers.CharField()
    media_type = serializers.CharField()
    title = serializers.CharField()
    image = serializers.URLField()


class SearchResponseSerializer(serializers.Serializer):
    """Serializer for search endpoint results."""

    pagination = PaginationSerializer()
    results = SearchMediaSerializer(many=True)
