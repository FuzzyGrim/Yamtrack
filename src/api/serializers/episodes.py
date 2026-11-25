from django.conf import settings
from rest_framework import serializers

from app.models import (
    Episode,
    Item,
    MediaTypes,
    Status,
)

from .common import HistorySerializer, ListMinimizedSerializer, PaginationSerializer
from .fields import (
    ItemIdField,
    MediaSourceCompleteChoiceField,
    MediaTypeCompleteChoiceField,
    ParentIdField,
)
from .media import MediaSerializer


class EpisodeMedia:
    """Media-like wrapper of an episode, tracked or only in the provider metadata."""

    score = None
    notes = None

    def __init__(self, item, episode=None):
        """Wrap the episode item and its tracked episode, if any."""
        self.item = item
        self.id = getattr(episode, "id", None)
        self.created_at = getattr(episode, "created_at", None)
        self.start_date = None
        self.end_date = getattr(episode, "end_date", None)
        self.progressed_at = self.end_date
        self.notes = getattr(episode, "notes", None)
        tracked = episode is not None
        self.status = Status.COMPLETED.value if tracked else None
        self.progress = 1 if tracked else None


class EpisodeSerializer(MediaSerializer):
    """Serializer used for tracked episodes and episodes from the provider metadata."""

    def to_representation(self, instance):
        """Serialize an Episode or an episode of the provider metadata."""
        if isinstance(instance, Episode):
            return super().to_representation(EpisodeMedia(instance.item, instance))

        data = super().to_representation(self._provider_episode_media(instance))
        # Tracked episodes are saved with the show title, use the episode name
        # like untracked episodes and the episode detail endpoint
        if data.get("item") is not None and instance.get("name"):
            data["item"]["title"] = instance["name"]
        return data

    def _provider_episode_media(self, metadata):
        """Return the media of an episode from the provider metadata."""
        episode_number = metadata.get("episode_number")
        episode = self.context.get("tracked_episodes", {}).get(episode_number)

        if hasattr(episode, "item"):
            item = episode.item
        else:
            still_path = metadata.get("still_path")
            item = Item(
                media_id=metadata.get("show_id"),
                source=self.context.get("source"),
                media_type=MediaTypes.EPISODE.value,
                title=metadata.get("name") or "",
                image="https://image.tmdb.org/t/p/original" + still_path
                if still_path
                else settings.IMG_NONE,
                season_number=metadata.get("season_number"),
                episode_number=episode_number,
            )

        media = EpisodeMedia(item, episode)
        if hasattr(episode, "lists"):
            media.lists = episode.lists
        else:
            lists_by_number = self.context.get("lists_by_number", {})
            lists_by_item_id = self.context.get("lists_by_item_id", {})
            media.lists = lists_by_number.get(
                episode_number, []
            ) or lists_by_item_id.get(item.id, [])
        return media


class PaginatedEpisodesSerializer(serializers.Serializer):
    """Serializer for paginated episodes."""

    pagination = PaginationSerializer()
    results = EpisodeSerializer(many=True)


class EpisodeDetailsSerializer(serializers.Serializer):
    """Schema serializer for episode details."""

    air_date = serializers.DateField(required=False, allow_null=True)
    episode_number = serializers.IntegerField(required=False, allow_null=True)
    season_number = serializers.IntegerField(required=False, allow_null=True)
    runtime = serializers.IntegerField(required=False, allow_null=True)
    episode_type = serializers.CharField(required=False, allow_null=True)
    crew = serializers.ListField(child=serializers.DictField(), default=list)
    guest_stars = serializers.ListField(child=serializers.DictField(), default=list)


class CompleteEpisodeSerializer(serializers.Serializer):
    """Serializer that builds a CompleteEpisode response."""

    id = serializers.IntegerField(
        allow_null=True,
        help_text=(
            "The id of the item in the database (not to be confused with the item_id, "
            "which is media provider specific), null if the item is not saved"
        ),
    )
    media_id = serializers.CharField()
    source = MediaSourceCompleteChoiceField()
    source_url = serializers.URLField(allow_null=True)
    media_type = MediaTypeCompleteChoiceField()
    title = serializers.CharField()
    max_progress = serializers.IntegerField()
    image = serializers.URLField(allow_null=True)
    synopsis = serializers.CharField(allow_null=True)
    genres = serializers.ListField(
        child=serializers.CharField(),
        allow_empty=True,
        allow_null=True,
    )
    score = serializers.FloatField(allow_null=True)
    score_count = serializers.IntegerField(allow_null=True)
    details = EpisodeDetailsSerializer()
    related = serializers.DictField()
    item_id = ItemIdField()
    parent_id = ParentIdField()
    tracked = serializers.BooleanField()
    consumptions_number = serializers.IntegerField()
    consumptions = HistorySerializer(many=True)
    lists = ListMinimizedSerializer(many=True)

    def to_representation(self, instance):
        """Serialize the episode metadata with the user consumptions and lists."""
        media_metadata = instance.get("media_metadata", {})
        episode = instance.get("episode", {})
        user_medias = instance.get("user_medias", [])
        episode_number = episode.get("episode_number")

        episode_item = Item(
            media_id=media_metadata.get("media_id"),
            source=media_metadata.get("source"),
            media_type=MediaTypes.EPISODE.value,
            season_number=media_metadata.get("season_number"),
            episode_number=episode_number,
        )

        season_source_url = media_metadata.get("source_url")
        source_url = ""
        if season_source_url:
            source_url = f"{season_source_url}/episode/{episode_number}"

        image = (
            "https://image.tmdb.org/t/p/original" + episode.get("still_path")
            if episode.get("still_path")
            else None
        )

        return super().to_representation(
            {
                "id": user_medias[0].item_id if user_medias else None,
                "media_id": media_metadata.get("media_id"),
                "source": media_metadata.get("source"),
                "source_url": source_url,
                "media_type": MediaTypes.EPISODE.value,
                "title": episode.get("name"),
                "max_progress": 1,
                "image": image,
                "synopsis": episode.get("overview"),
                "genres": media_metadata.get("genres", []),
                "score": episode.get("vote_average"),
                "score_count": episode.get("vote_count"),
                "details": episode,
                "related": {},
                "item_id": episode_item,
                "parent_id": episode_item,
                "tracked": bool(user_medias),
                "consumptions_number": len(user_medias),
                "consumptions": user_medias,
                "lists": instance.get("lists", []),
            },
        )
