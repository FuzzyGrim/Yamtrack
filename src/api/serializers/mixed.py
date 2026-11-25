from rest_framework import serializers

from app.models import (
    TV,
    Anime,
    BasicMedia,
    BoardGame,
    Book,
    Comic,
    Episode,
    Game,
    Item,
    Manga,
    Movie,
    Season,
)
from events.models import Event
from lists.models import CustomList

from .calendar import EventSerializer
from .common import ItemSerializer
from .episodes import EpisodeSerializer
from .lists import ListSerializer
from .media import MediaSerializer, UntrackedMedia

serializer_map = {
    Anime: MediaSerializer,
    BasicMedia: MediaSerializer,
    BoardGame: MediaSerializer,
    Book: MediaSerializer,
    Comic: MediaSerializer,
    CustomList: ListSerializer,
    Episode: EpisodeSerializer,
    Event: EventSerializer,
    Game: MediaSerializer,
    Item: ItemSerializer,
    Manga: MediaSerializer,
    Movie: MediaSerializer,
    Season: MediaSerializer,
    TV: MediaSerializer,
}


class MixedMediaSerializer(serializers.Serializer):
    """Serializer that handles mixed media types by checking every item."""

    def to_representation(self, instance):
        """Detect instance type and use appropriate serializer."""
        if isinstance(instance, Item) and self.context.get("serialize_items_as_media"):
            return MediaSerializer(UntrackedMedia(instance), context=self.context).data

        instance_type = type(instance)
        serializer_class = serializer_map.get(instance_type)

        if serializer_class is None:
            msg = (
                f"No serializer found for type {instance_type}. "
                f"Supported types: {list(serializer_map.keys())}."
            )
            raise ValueError(msg)

        context = self.context or {}
        serializer = serializer_class(instance, context=context)
        return serializer.data
