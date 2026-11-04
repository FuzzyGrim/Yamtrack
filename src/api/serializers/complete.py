from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    extend_schema_field,
)
from rest_framework import serializers

from app.models import (
    Item,
    MediaTypes,
)

from .common import HistorySerializer, ListMinimizedSerializer
from .episodes import EpisodeSerializer
from .fields import (
    ItemIdField,
    MediaSourceCompleteChoiceField,
    MediaTypeCompleteChoiceField,
    ParentIdField,
)
from .media import MediaSerializer, UntrackedMedia


class CompleteMediaSerializer(serializers.Serializer):
    """Serializer that builds a CompleteMedia response."""

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
    details = serializers.SerializerMethodField()
    related = serializers.SerializerMethodField()
    item_id = ItemIdField()
    parent_id = ParentIdField()
    tracked = serializers.BooleanField()
    consumptions_number = serializers.IntegerField()
    consumptions = HistorySerializer(many=True)
    lists = ListMinimizedSerializer(many=True)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_details(self, obj):
        """Return the raw provider details payload."""
        if isinstance(obj, dict):
            return obj.get("details", {})
        return getattr(obj, "details", {})

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_related(self, obj):
        """Return the raw provider related payload."""
        if isinstance(obj, dict):
            return obj.get("related", {})
        return getattr(obj, "related", {})

    def _process_seasons(self, media_metadata, seasons_by_number=None):
        """Process seasons in related data."""
        if "related" not in media_metadata or media_metadata["related"] is None:
            media_metadata["related"] = {}
        if (
            "seasons" not in media_metadata["related"]
            or media_metadata["related"]["seasons"] is None
        ):
            media_metadata["related"]["seasons"] = []

        processed_seasons = []
        for season in media_metadata["related"]["seasons"]:
            season_number = season.get("season_number")
            tracked_season = (
                seasons_by_number.get(season_number) if seasons_by_number else None
            )

            if tracked_season is None:
                tracked_season = UntrackedMedia(
                    Item(
                        media_id=str(
                            season.get("media_id")
                            or media_metadata.get("media_id")
                            or "",
                        ),
                        source=season.get("source") or media_metadata.get("source"),
                        media_type=MediaTypes.SEASON.value,
                        title=season.get("season_title") or season.get("title") or "",
                        image=season.get("image") or settings.IMG_NONE,
                        season_number=season_number,
                    ),
                )

            processed_seasons.append(MediaSerializer(tracked_season).data)

        media_metadata["related"]["seasons"] = processed_seasons

    def _process_episodes(self, media_metadata, episodes_by_number=None):
        """Process episodes in media data."""
        if "related" not in media_metadata or media_metadata["related"] is None:
            media_metadata["related"] = {}
        if (
            "episodes" not in media_metadata["related"]
            or media_metadata["related"]["episodes"] is None
        ):
            media_metadata["related"]["episodes"] = []

        episodes = media_metadata.pop("episodes", [])
        serializer = EpisodeSerializer(
            context={
                "source": media_metadata.get("source"),
                "tracked_episodes": episodes_by_number or {},
            },
        )
        processed_episodes = [
            serializer.to_representation(episode) for episode in episodes
        ]

        media_metadata["related"]["episodes"] = processed_episodes

    def to_representation(self, instance):
        """Serialize the media metadata with the user consumptions and lists."""
        media_metadata = instance.get("media_metadata", {})
        user_medias = instance.get("user_medias")
        media_type = media_metadata.get("media_type")

        if media_type == MediaTypes.TV.value:
            self._process_seasons(media_metadata, instance.get("seasons"))
        elif media_type == MediaTypes.SEASON.value:
            self._process_episodes(media_metadata, instance.get("episodes"))

        temp_media = type("TempMedia", (), media_metadata)()

        details = media_metadata.get("details", {})
        if "tvdb_id" in media_metadata:
            details["tvdb_id"] = media_metadata.pop("tvdb_id")
        if "last_episode_season" in media_metadata:
            details["last_episode_season"] = media_metadata.pop("last_episode_season")
        if "next_episode_season" in media_metadata:
            details["next_episode_season"] = media_metadata.pop("next_episode_season")
        if "last_issue_id" in media_metadata:
            details["last_issue_id"] = media_metadata.pop("last_issue_id")
        if "year" in details:
            details["year"] = int(details["year"])
        if "players" in details:
            details["players"] = details["players"].strip(" players").split("-")
        if "playtime" in details:
            details["playtime"] = int(details["playtime"].strip(" min"))
        if "min_age" in details:
            details["min_age"] = int(details["min_age"].strip("+"))
        if "designers" in details:
            details["designers"] = details["designers"].split(", ")
        if "publishers" in details:
            details["publishers"] = details["publishers"].split(", ")
        max_progress = media_metadata.get("max_progress")

        return super().to_representation(
            {
                "id": user_medias[0].item_id if user_medias else None,
                "media_id": media_metadata.get("media_id"),
                "source": media_metadata.get("source"),
                "source_url": media_metadata.get("source_url"),
                "media_type": media_type,
                "title": media_metadata.pop("season_title", None)
                or media_metadata.get("title"),
                "max_progress": 1 if max_progress is None else max_progress,
                "image": media_metadata.get("image"),
                "synopsis": media_metadata.get("synopsis"),
                "genres": media_metadata.get("genres"),
                "score": media_metadata.get("score"),
                "score_count": media_metadata.get("score_count"),
                "details": details,
                "related": media_metadata.get("related", {}),
                "item_id": temp_media,
                "parent_id": temp_media,
                "tracked": bool(user_medias),
                "consumptions_number": len(user_medias),
                "consumptions": user_medias,
                "lists": instance.get("lists", []),
            },
        )
