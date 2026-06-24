from rest_framework import serializers

from api.serializers.common import MediaRefSerializer


class CustomListWriteSerializer(serializers.Serializer):
    """Validate custom list writes."""

    name = serializers.CharField(max_length=255, required=False)
    slug = serializers.SlugField(max_length=255, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    visibility = serializers.ChoiceField(
        choices=["public", "unlisted", "private"],
        required=False,
    )
    is_ranked = serializers.BooleanField(required=False)
    collaborator_usernames = serializers.ListField(
        child=serializers.CharField(max_length=150),
        required=False,
    )


class ListItemWriteSerializer(serializers.Serializer):
    """Validate list item writes."""

    ref = MediaRefSerializer()


class ListItemsReorderSerializer(serializers.Serializer):
    """Validate list item reorder writes."""

    item_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=True,
    )


class CollaboratorSerializer(serializers.Serializer):
    """Validate collaborator writes."""

    username = serializers.CharField(max_length=150)
