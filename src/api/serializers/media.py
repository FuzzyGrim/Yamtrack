from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema_field,
    extend_schema_serializer,
)
from rest_framework import serializers

from app.models import (
    TV,
    MediaTypes,
    Season,
    Sources,
)

from .common import ItemSerializer, ListMinimizedSerializer, PaginationSerializer
from .fields import (
    ItemIdField,
    MediaSourceChoiceField,
    MediaSourceCompleteChoiceField,
    MediaStatusChoiceField,
    MediaTypeChoiceField,
    ParentIdField,
    UTCDateTimeField,
)


class UntrackedMedia:
    """Media-like wrapper of an Item that the user doesn't track."""

    id = None
    created_at = None
    score = None
    status = None
    progress = None
    progressed_at = None
    start_date = None
    end_date = None
    notes = None

    def __init__(self, item):
        """Wrap the untracked item."""
        self.item = item


class MediaSerializer(serializers.Serializer):
    """Serializer used for media items."""

    id = serializers.IntegerField(source="item.id", allow_null=True, required=False)
    consumption_id = serializers.IntegerField(
        source="id", allow_null=True, required=False
    )
    item = ItemSerializer()
    item_id = ItemIdField(source="item", allow_null=True, required=False)
    parent_id = ParentIdField(source="item", allow_null=True, required=False)
    tracked = serializers.SerializerMethodField()
    created_at = UTCDateTimeField(allow_null=True, required=False)
    score = serializers.FloatField(allow_null=True)
    status = MediaStatusChoiceField()
    progress = serializers.IntegerField(allow_null=True)
    progressed_at = UTCDateTimeField(allow_null=True)
    start_date = UTCDateTimeField(allow_null=True)
    end_date = UTCDateTimeField(allow_null=True)
    notes = serializers.CharField(allow_null=True)
    lists = serializers.SerializerMethodField()

    def get_tracked(self, obj) -> bool:
        """Return whether the user tracks the media."""
        return obj.id is not None

    @extend_schema_field(ListMinimizedSerializer(many=True))
    def get_lists(self, obj):
        """Return the lists of the media, from the instance or the context."""
        if hasattr(obj, "lists"):
            return obj.lists
        if obj.item is None:
            return []
        lists_by_item_id = self.context.get("lists_by_item_id", {})
        return lists_by_item_id.get(obj.item.id, [])


class PaginatedMediaResponseSerializer(serializers.Serializer):
    """Paginated response serializer for media items."""

    pagination = PaginationSerializer()
    results = MediaSerializer(many=True)


class PaginatedMediaSerializer(serializers.Serializer):
    """Paginated response serializer for media items."""

    pagination = PaginationSerializer()
    results = MediaSerializer(many=True)


class RelatedResponseSerializer(serializers.Serializer):
    """Serializer for related media endpoint."""

    source = MediaSourceChoiceField()
    media_type = MediaTypeChoiceField()
    image = serializers.URLField(allow_null=True)
    media_id = serializers.CharField()
    title = serializers.CharField()


class BaseUpdateMediaSerializer(serializers.Serializer):
    """Base serializer for updating media items with common fields."""

    score = serializers.FloatField(required=False, allow_null=True)
    status = MediaStatusChoiceField()
    progress = serializers.IntegerField(required=False, allow_null=True)
    start_date = UTCDateTimeField(required=False, allow_null=True)
    end_date = UTCDateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    def validate_score(self, value):
        """Validate that the score is between 0 and 10 if provided."""
        if value is not None and not (0 <= value <= 10):  # noqa: PLR2004
            msg = "Score must be between 0 and 10."
            raise serializers.ValidationError(msg)
        return value

    def validate_progress(self, value):
        """Validate that the progress is not negative."""
        if value is not None and value < 0:
            msg = "Progress cannot be negative."
            raise serializers.ValidationError(msg)
        return value

    def validate(self, data):  # noqa: D102
        start_date = data.get("start_date", getattr(self.instance, "start_date", None))
        end_date = data.get("end_date", getattr(self.instance, "end_date", None))
        if start_date and end_date and start_date > end_date:
            raise serializers.ValidationError(
                {"end_date": "End date cannot be before start date."}
            )
        return data


class UpdateAnimeSerializer(BaseUpdateMediaSerializer):
    """Serializer for updating anime media items."""


class UpdateBoardGameSerializer(BaseUpdateMediaSerializer):
    """Serializer for updating board game media items."""


class UpdateBookSerializer(BaseUpdateMediaSerializer):
    """Serializer for updating book media items."""


class UpdateComicSerializer(BaseUpdateMediaSerializer):
    """Serializer for updating comic media items."""


# UpdateEpisodeSerializer is not a subclass of BaseUpdateMediaSerializer because only
# the end_date field is modifiable. In the future Episodes should be treated as Media
class UpdateEpisodeSerializer(serializers.Serializer):
    """Serializer for updating episode media items."""

    end_date = UTCDateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class UpdateGameSerializer(BaseUpdateMediaSerializer):
    """Serializer for updating game media items."""


class UpdateMangaSerializer(BaseUpdateMediaSerializer):
    """Serializer for updating manga media items."""


class UpdateMovieSerializer(BaseUpdateMediaSerializer):
    """Serializer for updating movie media items."""

    progress = None


class UpdateSeasonSerializer(BaseUpdateMediaSerializer):
    """Serializer for updating season media items."""

    progress = None
    start_date = None
    end_date = None


class UpdateTVSerializer(BaseUpdateMediaSerializer):
    """Serializer for updating TV media items."""

    progress = None
    start_date = None
    end_date = None


UPDATE_MEDIA_SERIALIZERS = {
    MediaTypes.ANIME.value: UpdateAnimeSerializer,
    MediaTypes.BOARDGAME.value: UpdateBoardGameSerializer,
    MediaTypes.BOOK.value: UpdateBookSerializer,
    MediaTypes.COMIC.value: UpdateComicSerializer,
    MediaTypes.GAME.value: UpdateGameSerializer,
    MediaTypes.MANGA.value: UpdateMangaSerializer,
    MediaTypes.MOVIE.value: UpdateMovieSerializer,
    MediaTypes.TV.value: UpdateTVSerializer,
}


@extend_schema_field(OpenApiTypes.INT)
class UserManualParentField(serializers.PrimaryKeyRelatedField):
    """Primary key of a manual media of the request user, used as parent."""

    def __init__(self, model, media_type, **kwargs):  # noqa: D107
        self.model = model
        self.media_type = media_type
        super().__init__(**kwargs)

    def get_queryset(self):
        """Return the manual media of the given type owned by the request user."""
        request = self.context.get("request")
        if request is None:
            return self.model.objects.none()
        return self.model.objects.filter(
            user=request.user,
            item__source=Sources.MANUAL.value,
            item__media_type=self.media_type,
        )


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Track new media item with provider source",
            description="Track a new media item using a provider's metadata.",
            summary="Track media with provider source",
            value={
                "source": "tmdb",
                "media_id": "12345",
            },
        ),
        OpenApiExample(
            "Track new manual media item",
            description=(
                "Track a new media item with custom metadata. Omit `media_id` and use "
                "`source: manual` to create a manual media item."
            ),
            summary="Track media with manual source",
            value={
                "source": "manual",
                "title": "My Custom Movie",
                "image": "https://example.com/poster.jpg",
                "score": 8.5,
                "status": 3,
                "progress": 1,
                "start_date": "2023-10-01T00:00:00Z",
                "end_date": "2023-10-01T00:00:00Z",
                "notes": "Watched at the festival",
            },
        ),
    ]
)
class TrackMediaSerializer(serializers.Serializer):
    """
    Serializer for tracking a new media item.

    Unified payload for creating media items. Use `source: manual` for manual items
    (omit `media_id`) or set a provider `source` and `media_id` to create from a
    provider. When using a media provider, all media data will be overwritten.
    """

    source = MediaSourceCompleteChoiceField()
    media_id = serializers.CharField(required=False, allow_null=True)
    title = serializers.CharField(required=False, allow_null=True)
    image = serializers.URLField(required=False, allow_null=True)
    score = serializers.FloatField(required=False, allow_null=True)
    status = MediaStatusChoiceField(required=False)
    progress = serializers.IntegerField(required=False, allow_null=True)
    start_date = UTCDateTimeField(required=False, allow_null=True)
    end_date = UTCDateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_null=True)
    season_number = serializers.IntegerField(required=False, allow_null=True)
    episode_number = serializers.IntegerField(required=False, allow_null=True)
    parent_tv = UserManualParentField(
        TV,
        MediaTypes.TV.value,
        required=False,
        allow_null=True,
        help_text=(
            "`consumption_id` of the manual TV show the season belongs to, "
            "required for manual seasons."
        ),
    )
    parent_season = UserManualParentField(
        Season,
        MediaTypes.SEASON.value,
        required=False,
        allow_null=True,
        help_text=(
            "`consumption_id` of the manual season the episode belongs to, "
            "required for manual episodes."
        ),
    )
