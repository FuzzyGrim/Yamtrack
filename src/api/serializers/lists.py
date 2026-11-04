from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema_field,
    extend_schema_serializer,
)
from rest_framework import serializers

from lists.models import CustomListItem

from .common import ListMinimizedSerializer, PaginationSerializer, UserSerializer
from .fields import UTCDateTimeField
from .media import PaginatedMediaResponseSerializer


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Basic list creation",
            description="Basic list creation example.",
            summary="Basic list creation example",
            value={"name": "Favourites"},
        ),
        OpenApiExample(
            "Complete list creation",
            description="Complete list creation example.",
            summary="Complete list creation example",
            value={
                "name": "Favourites",
                "description": "My favourite items.",
                "collaborators": [1, 2, 3],
            },
        ),
    ]
)
class ListCreateRequestSerializer(serializers.Serializer):
    """Request serializer for custom list creation."""

    name = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True)
    collaborators = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        help_text="List of username ids allowed to collaborate on this list.",
    )


class ListUpdateRequestSerializer(serializers.Serializer):
    """Request serializer for partial custom list updates."""

    name = serializers.CharField(required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    collaborators = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        help_text="List of username ids allowed to collaborate on this list.",
    )


class PaginatedListsMinimizedResponseSerializer(serializers.Serializer):
    """Paginated response serializer for minimized list information."""

    pagination = PaginationSerializer()
    results = ListMinimizedSerializer(many=True)


class ListSerializer(serializers.Serializer):
    """Serializer used for custom lists."""

    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField()
    image = serializers.URLField(allow_null=True)
    owner = UserSerializer()
    collaborators = UserSerializer(many=True)
    items_count = serializers.SerializerMethodField()
    latest_update = serializers.SerializerMethodField()
    items = serializers.SerializerMethodField()

    def get_items_count(self, obj) -> int:
        """Return the number of items, annotated or counted."""
        items_count = getattr(obj, "items_count", None)
        if items_count is None:
            return obj.items.count()
        return items_count

    @extend_schema_field(UTCDateTimeField(allow_null=True))
    def get_latest_update(self, obj):
        """Return when the last item was added, annotated or queried."""
        latest_update = getattr(obj, "latest_update", None)
        if latest_update is None:
            latest_update = CustomListItem.objects.get_last_added_date(obj)
        return UTCDateTimeField().to_representation(latest_update)

    @extend_schema_field(PaginatedMediaResponseSerializer)
    def get_items(self, _obj):
        """Return the paginated items supplied in the serializer context."""
        from .mixed import MixedMediaSerializer  # noqa: PLC0415

        items = self.context.get("paginated_items")
        if items is None:
            return None

        if isinstance(items, dict) and "results" in items:
            return {
                "pagination": items.get("pagination", {}),
                "results": MixedMediaSerializer(
                    items.get("results", []),
                    many=True,
                    context={**self.context, "serialize_items_as_media": True},
                ).data,
            }
        return items


class ListSummarySerializer(ListSerializer):
    """Serializer for list responses that do not include paginated items."""

    items = None


class PaginatedListsResponseSerializer(serializers.Serializer):
    """Paginated custom lists serializer."""

    pagination = PaginationSerializer()
    results = ListSummarySerializer(many=True)
