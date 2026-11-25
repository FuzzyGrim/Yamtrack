from rest_framework import serializers

from .common import PaginationSerializer


class PersonImageSerializer(serializers.Serializer):
    """Serializer for a single person image."""

    image = serializers.URLField()
    original = serializers.URLField()
    width = serializers.IntegerField(allow_null=True)
    height = serializers.IntegerField(allow_null=True)
    aspect_ratio = serializers.FloatField(allow_null=True)
    vote_average = serializers.FloatField(allow_null=True)
    vote_count = serializers.IntegerField(allow_null=True)


class PaginatedPersonImagesSerializer(serializers.Serializer):
    """Paginated response serializer for person images."""

    pagination = PaginationSerializer()
    results = PersonImageSerializer(many=True)


class MediaImageSerializer(PersonImageSerializer):
    """Serializer for a single media image."""

    language = serializers.CharField(allow_null=True)
    source = serializers.CharField(
        help_text=(
            "Where the image comes from: the media source, or an extra artwork "
            "source (fanart, anilist, mangadex)."
        ),
    )


class MediaImagesResponseSerializer(serializers.Serializer):
    """Serializer for media images endpoint."""

    posters = MediaImageSerializer(many=True)
    backdrops = MediaImageSerializer(many=True)
    screenshots = MediaImageSerializer(many=True)
    logos = MediaImageSerializer(many=True)


class CoverSerializer(serializers.Serializer):
    """Serializer for the cover of a media."""

    image = serializers.URLField(
        allow_null=True,
        help_text="Cover shown for the media, the custom one if set.",
    )
    custom_image = serializers.URLField(
        allow_null=True,
        help_text="Custom cover chosen for the media, null if none.",
    )


class CoverUpdateSerializer(serializers.Serializer):
    """Serializer for setting a custom cover."""

    image = serializers.URLField(
        help_text=(
            "URL of one of the images returned by the media images endpoint, "
            "either its `image` or `original` URL."
        ),
    )
