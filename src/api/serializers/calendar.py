from drf_spectacular.utils import (
    extend_schema_field,
)
from rest_framework import serializers

from api.helpers import (
    build_item_id,
    build_parent_id,
)
from app.models import (
    Item,
    MediaTypes,
)
from events.models import Event

from .common import ItemSerializer, PaginationSerializer
from .fields import UTCModelSerializer


class EventSerializer(UTCModelSerializer):
    """Serializer used for calendar events."""

    item = serializers.SerializerMethodField()
    item_id = serializers.SerializerMethodField()
    parent_id = serializers.SerializerMethodField()

    class Meta:  # noqa: D106
        model = Event
        fields = "__all__"

    def _released_item(self, event):
        """Return the released item, the episode for the events with a number."""
        item = event.item
        if event.content_number is None:
            return item
        return Item(
            media_id=item.media_id,
            source=item.source,
            media_type=MediaTypes.EPISODE.value
            if item.media_type == MediaTypes.SEASON.value
            else item.media_type,
            title=item.title,
            image=item.image,
            season_number=item.season_number,
            episode_number=event.content_number,
        )

    @extend_schema_field(ItemSerializer)
    def get_item(self, obj):
        """Return the released item."""
        return ItemSerializer(self._released_item(obj)).data

    def get_item_id(self, obj) -> str:
        """Return the item_id of the released item."""
        return build_item_id(self._released_item(obj))

    def get_parent_id(self, obj) -> str | None:
        """Return the parent_id of the released item."""
        return build_parent_id(self._released_item(obj))


class PaginatedEventsSerializer(serializers.Serializer):
    """Serializer for paginated calendar events."""

    pagination = PaginationSerializer()
    results = EventSerializer(many=True)
