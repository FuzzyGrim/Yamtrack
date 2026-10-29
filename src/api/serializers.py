from datetime import UTC

from django.conf import settings
from django.db import models
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    PolymorphicProxySerializer,
    extend_schema_field,
    extend_schema_serializer,
)
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
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from events.models import Event
from lists.models import CustomList, CustomListItem

from .changes_history_processor import (
    get_changes_from_diff,
    get_changes_from_new_record,
)
from .helpers import (
    MEDIA_STATUS_CHOICES,
    MEDIA_TYPE_COMPLETE_VALID_LIST,
    MEDIA_TYPE_VALID_LIST,
    SOURCES_COMPLETE_VALID_LIST,
    SOURCES_VALID_LIST,
    build_item_id,
    build_parent_id,
    get_media_status,
)

# TODO: Sort serializers


class ItemIdField(serializers.CharField):
    """Custom field to generate item_id string."""

    def to_representation(self, item):  # noqa: D102
        return build_item_id(item)


class ParentIdField(serializers.CharField):
    """Custom field to generate parent_id string for seasons and episodes."""

    def __init__(self, **kwargs):
        """Initialize the field, it's null for medias without a parent."""
        kwargs.setdefault("allow_null", True)
        super().__init__(**kwargs)

    def to_representation(self, item):  # noqa: D102
        return build_parent_id(item)


class UTCDateTimeField(serializers.DateTimeField):
    """DateTimeField that returns the datetimes in UTC."""

    def __init__(self, **kwargs):
        """Initialize the field with the UTC timezone."""
        kwargs.setdefault("default_timezone", UTC)
        super().__init__(**kwargs)


class UTCModelSerializer(serializers.ModelSerializer):
    """ModelSerializer that returns the datetime model fields in UTC."""

    serializer_field_mapping = {
        **serializers.ModelSerializer.serializer_field_mapping,
        models.DateTimeField: UTCDateTimeField,
    }


class MediaStatusChoiceField(serializers.ChoiceField):
    """Custom field for media status options."""

    def __init__(self, **kwargs):
        """Initialize with predefined status choices."""
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_null", True)
        status_choices = MEDIA_STATUS_CHOICES
        kwargs["choices"] = status_choices
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        """Convert the API integer status to the model status."""
        value = super().to_internal_value(data)
        return get_media_status(value, reverse=True)

    def to_representation(self, value):
        """Convert the model status to the API integer status."""
        return get_media_status(value)


class MediaSourceChoiceField(serializers.ChoiceField):
    """Custom field for media source options."""

    def __init__(self, **kwargs):
        """Initialize with predefined source choices."""
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_null", True)
        kwargs["choices"] = SOURCES_VALID_LIST
        super().__init__(**kwargs)


class MediaSourceCompleteChoiceField(serializers.ChoiceField):
    """Custom field for media source options including manual."""

    def __init__(self, **kwargs):
        """Initialize with predefined source choices including manual."""
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_null", True)
        kwargs["choices"] = SOURCES_COMPLETE_VALID_LIST
        super().__init__(**kwargs)


class MediaTypeChoiceField(serializers.ChoiceField):
    """Custom field for media type options."""

    def __init__(self, **kwargs):
        """Initialize with predefined media type choices."""
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_null", True)
        kwargs["choices"] = MEDIA_TYPE_VALID_LIST
        super().__init__(**kwargs)


class MediaTypeCompleteChoiceField(serializers.ChoiceField):
    """Custom field for complete media type options."""

    def __init__(self, **kwargs):
        """Initialize with predefined complete media type choices."""
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_null", True)
        kwargs["choices"] = MEDIA_TYPE_COMPLETE_VALID_LIST
        super().__init__(**kwargs)


class ItemSerializer(serializers.ModelSerializer):
    """Serializer used for item details."""

    media_id = serializers.SerializerMethodField()

    @extend_schema_field(str)
    def get_media_id(self, obj):
        """Return media_id preserving alphanumeric provider IDs."""
        media_id = getattr(obj, "media_id", None)
        if media_id is None:
            return None
        return str(media_id)

    class Meta:  # noqa: D106
        model = Item
        exclude = ("id",)


class ChangesHistoryChangeSerializer(serializers.Serializer):
    """Serializer for a single change in a history entry."""

    field = serializers.ChoiceField(
        choices=["end_date", "notes", "progress", "score", "start_date", "status"]
    )
    old_value = serializers.JSONField(allow_null=True, required=False)
    new_value = serializers.JSONField(allow_null=True, required=False)


class ChangesHistoryEntrySerializer(serializers.Serializer):
    """Serializer that builds a change-based history entry."""

    id = serializers.IntegerField(source="history_id", allow_null=True, required=False)
    item_id = ItemIdField(source="item_obj", allow_null=True, required=False)
    timestamp = UTCDateTimeField(source="history_date", allow_null=True, required=False)
    changes = serializers.SerializerMethodField()

    @extend_schema_field(ChangesHistoryChangeSerializer(many=True))
    def get_changes(self, obj):
        """Return the changes from the previous record of the history entry."""
        media_type = self.context.get("media_type")
        prev = getattr(obj, "prev_record", None)
        if prev is not None:
            changes = get_changes_from_diff(obj, prev, media_type)
        else:
            changes = get_changes_from_new_record(obj, media_type)

        status_field = MediaStatusChoiceField()
        for change in changes:
            if change.get("field") == "status":
                change["old_value"] = status_field.to_representation(
                    change.get("old_value"),
                )
                change["new_value"] = status_field.to_representation(
                    change.get("new_value"),
                )
        return ChangesHistoryChangeSerializer(changes, many=True).data


class ApiMessageResponseSerializer(serializers.Serializer):
    """Standard API message response serializer."""

    detail = serializers.CharField()


class ApiErrorResponseSerializer(serializers.Serializer):
    """Standard API error response serializer."""

    detail = serializers.CharField()
    errors = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
        help_text="Validation errors, keyed by field name.",
    )
    debug = serializers.CharField(
        required=False,
        help_text="Exception message, only returned when DEBUG is enabled.",
    )


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


class PaginationSerializer(serializers.Serializer):
    """Common pagination metadata serializer."""

    total = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)


class PaginatedChangesHistoryResponseSerializer(serializers.Serializer):
    """Paginated changes history serializer."""

    pagination = PaginationSerializer()
    results = ChangesHistoryEntrySerializer(many=True)


class StatisticsMediaCountSerializer(serializers.Serializer):
    """Serializer for media count by enabled types in statistics response."""

    total = serializers.IntegerField()
    tv = serializers.IntegerField(required=False)
    season = serializers.IntegerField(required=False)
    movie = serializers.IntegerField(required=False)
    anime = serializers.IntegerField(required=False)
    manga = serializers.IntegerField(required=False)
    game = serializers.IntegerField(required=False)
    book = serializers.IntegerField(required=False)
    comic = serializers.IntegerField(required=False)
    boardgame = serializers.IntegerField(required=False)


class ListMinimizedSerializer(serializers.Serializer):
    """Serializer for minimized list information."""

    list_id = serializers.IntegerField()
    list_item_id = serializers.IntegerField(allow_null=True)


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


class HealthCheckSerializer(serializers.Serializer):
    """Serializer for individual health checks."""

    status = serializers.SerializerMethodField()
    error = serializers.SerializerMethodField()

    @extend_schema_field(serializers.ChoiceField(choices=["ok", "error"]))
    def get_status(self, result):
        """Return the status of the health check result."""
        return "error" if result.error else "ok"

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_error(self, result):
        """Return the error of the health check, detailed only in debug mode."""
        if not result.error:
            return None
        return str(result.error) if settings.DEBUG else "Check failed"


class HealthResponseSerializer(serializers.Serializer):
    """Serializer for health check response."""

    status = serializers.SerializerMethodField()
    timestamp = UTCDateTimeField()
    checks = serializers.DictField(child=HealthCheckSerializer())

    @extend_schema_field(serializers.ChoiceField(choices=["ok", "unavailable"]))
    def get_status(self, health):
        """Return the overall status, unavailable if any check failed."""
        if any(result.error for result in health["checks"].values()):
            return "unavailable"
        return "ok"


class EpisodeHistory:
    """History entry of the watch of an episode."""

    score = None
    progress = 1
    status = Status.COMPLETED.value
    notes = ""

    def __init__(self, episode):
        """Wrap the watched episode."""
        self.id = episode.id
        self.created_at = episode.created_at
        self.progressed_at = episode.created_at
        self.start_date = episode.created_at
        self.end_date = episode.end_date


class HistorySerializer(serializers.Serializer):
    """Serializer for watch history entries."""

    consumption_id = serializers.IntegerField(
        source="id",
        help_text=(
            "The id of the media consumption (id numbers are global for all "
            "consumptions of all medias of the same type, they aren't "
            "sequential per media)."
        ),
    )
    created = UTCDateTimeField(source="created_at", allow_null=True)
    score = serializers.FloatField(allow_null=True)
    progress = serializers.IntegerField(allow_null=True)
    progressed_at = UTCDateTimeField(allow_null=True)
    status = MediaStatusChoiceField()
    start_date = UTCDateTimeField(allow_null=True)
    end_date = UTCDateTimeField(allow_null=True)
    notes = serializers.CharField(allow_null=True)

    def to_representation(self, instance):
        """Serialize a media consumption, or the single watch of an episode."""
        if isinstance(instance, Episode):
            instance = EpisodeHistory(instance)
        return super().to_representation(instance)


class PaginatedHistoryResponseSerializer(serializers.Serializer):
    """Serializer for paginated watch history."""

    pagination = PaginationSerializer()
    results = HistorySerializer(many=True)


class InfoSerializer(serializers.Serializer):
    """Serializer for the info endpoint."""

    version = serializers.CharField(source="VERSION")
    debug = serializers.BooleanField(source="DEBUG")
    frontend_url = serializers.SerializerMethodField()
    language = serializers.CharField(source="LANGUAGE_CODE")
    timezone = serializers.CharField(source="TIME_ZONE")
    admin_enabled = serializers.BooleanField(source="ADMIN_ENABLED")
    track_time = serializers.BooleanField(source="TRACK_TIME")

    @extend_schema_field(serializers.URLField())
    def get_frontend_url(self, obj):
        """Return the base URL of the app, or the local one if it isn't set."""
        return obj.BASE_URL or "http://localhost:8000"


class PaginatedListsMinimizedResponseSerializer(serializers.Serializer):
    """Paginated response serializer for minimized list information."""

    pagination = PaginationSerializer()
    results = ListMinimizedSerializer(many=True)


class UserSerializer(serializers.Serializer):
    """Serializer for user information."""

    id = serializers.IntegerField()
    username = serializers.CharField()


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


class EpisodeMedia:
    """Media-like wrapper of an episode, tracked or only in the provider metadata."""

    score = None
    notes = None

    def __init__(self, item, episode=None):
        """Wrap the episode item and its tracked episode, if any."""
        self.item = item
        self.id = getattr(episode, "id", None)
        self.created_at = getattr(episode, "created_at", None)
        self.start_date = self.created_at
        self.end_date = getattr(episode, "end_date", None)
        self.progressed_at = self.end_date
        tracked = episode is not None
        self.status = Status.COMPLETED.value if tracked else None
        self.progress = 1 if tracked else None


class EpisodeSerializer(MediaSerializer):
    """Serializer used for tracked episodes and episodes from the provider metadata."""

    def to_representation(self, instance):
        """Serialize an Episode or an episode of the provider metadata."""
        if isinstance(instance, Episode):
            media = EpisodeMedia(instance.item, instance)
        else:
            media = self._provider_episode_media(instance)
        return super().to_representation(media)

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
                else None,
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


class AnimeDetailsSerializer(serializers.Serializer):
    """Schema serializer for anime details."""


class BoardGameDetailsSerializer(serializers.Serializer):
    """Schema serializer for board game details."""

    year = serializers.IntegerField(required=False, allow_null=True)
    players = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_empty=True,
    )
    playtime = serializers.IntegerField(required=False, allow_null=True)
    min_age = serializers.IntegerField(required=False, allow_null=True)
    designers = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    publishers = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )


class BookDetailsSerializer(serializers.Serializer):
    """Schema serializer for book details."""

    format = serializers.CharField(required=False, allow_null=True)
    number_of_pages = serializers.IntegerField(required=False, allow_null=True)
    publish_date = serializers.DateField(required=False, allow_null=True)
    author = serializers.CharField(required=False, allow_null=True)
    publisher = serializers.CharField(required=False, allow_null=True)
    isbn = serializers.CharField(required=False, allow_null=True)


class ComicDetailsSerializer(serializers.Serializer):
    """Schema serializer for comic details."""

    start_date = serializers.CharField(required=False, allow_null=True)
    publisher = serializers.CharField(required=False, allow_null=True)
    issues_count = serializers.IntegerField(required=False, allow_null=True)
    last_issue_name = serializers.CharField(required=False, allow_null=True)
    last_issue_number = serializers.IntegerField(required=False, allow_null=True)
    people = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    last_updated = serializers.DateField(required=False, allow_null=True)
    last_issue_id = serializers.IntegerField(required=False, allow_null=True)


class EpisodeDetailsSerializer(serializers.Serializer):
    """Schema serializer for episode details."""

    air_date = serializers.DateField(required=False, allow_null=True)
    episode_number = serializers.IntegerField(required=False, allow_null=True)
    season_number = serializers.IntegerField(required=False, allow_null=True)
    runtime = serializers.IntegerField(required=False, allow_null=True)
    episode_type = serializers.CharField(required=False, allow_null=True)
    crew = serializers.ListField(child=serializers.DictField(), default=list)
    guest_stars = serializers.ListField(child=serializers.DictField(), default=list)


class GameDetailsSerializer(serializers.Serializer):
    """Schema serializer for game details."""

    format = serializers.ChoiceField(
        choices=[
            "Main game",
            "DLC",
            "Expansion",
            "Bundle",
            "Standalone expansion",
            "Mod",
            "Episode",
            "Season",
            "Remake",
            "Remaster",
            "Expanded game",
            "Port",
            "Fork",
            "Pack",
            "Update",
        ],
        required=False,
        allow_null=True,
    )
    release_date = serializers.DateField(required=False, allow_null=True)
    themes = serializers.CharField(required=False, allow_null=True)
    platforms = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )


class MangaDetailsSerializer(serializers.Serializer):
    """Schema serializer for manga details."""


class MovieDetailsSerializer(serializers.Serializer):
    """Schema serializer for movie details."""

    format = serializers.CharField(required=False, allow_null=True)
    release_date = serializers.DateField(required=False, allow_null=True)
    status = serializers.CharField(required=False, allow_null=True)
    runtime = serializers.CharField(required=False, allow_null=True)
    studios = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    country = serializers.CharField(required=False, allow_null=True)
    languages = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )


class SeasonDetailsSerializer(serializers.Serializer):
    """Schema serializer for season details."""

    first_air_date = serializers.DateField(required=False, allow_null=True)
    last_air_date = serializers.DateField(required=False, allow_null=True)
    episodes = serializers.IntegerField(required=False, allow_null=True)
    runtime = serializers.CharField(required=False, allow_null=True)
    total_runtime = serializers.CharField(required=False, allow_null=True)
    tvdb_id = serializers.IntegerField(required=False, allow_null=True)


class TVDetailsSerializer(serializers.Serializer):
    """Schema serializer for TV details."""

    format = serializers.CharField(required=False, allow_null=True)
    first_air_date = serializers.DateField(required=False, allow_null=True)
    last_air_date = serializers.DateField(required=False, allow_null=True)
    status = serializers.CharField(required=False, allow_null=True)
    seasons = serializers.IntegerField(required=False, allow_null=True)
    episodes = serializers.IntegerField(required=False, allow_null=True)
    runtime = serializers.CharField(required=False, allow_null=True)
    studios = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    country = serializers.CharField(required=False, allow_null=True)
    languages = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    tvdb_id = serializers.IntegerField(required=False, allow_null=True)
    last_episode_season = serializers.IntegerField(required=False, allow_null=True)
    next_episode_season = serializers.IntegerField(required=False, allow_null=True)


class AnimeRelatedSerializer(serializers.Serializer):
    """Schema serializer for anime related data."""


class BoardGameRelatedSerializer(serializers.Serializer):
    """Schema serializer for board game related data."""


class BookRelatedSerializer(serializers.Serializer):
    """Schema serializer for book related data."""


class ComicRelatedSerializer(serializers.Serializer):
    """Schema serializer for comic related data."""

    from_the_same_publisher = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        allow_empty=True,
    )


class EpisodeRelatedSerializer(serializers.Serializer):
    """Schema serializer for episode related data."""


class GameRelatedSerializer(serializers.Serializer):
    """Schema serializer for game related data."""

    parent_game = serializers.ListField(child=serializers.CharField(), required=False)
    remasters = serializers.ListField(child=serializers.CharField(), required=False)
    remakes = serializers.ListField(child=serializers.CharField(), required=False)
    expansions = serializers.ListField(child=serializers.CharField(), required=False)
    standalone_expansions = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )
    expanded_games = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )


class MangaRelatedSerializer(serializers.Serializer):
    """Schema serializer for manga related data."""


class MovieRelatedSerializer(serializers.Serializer):
    """Schema serializer for movie related data."""


class SeasonRelatedSerializer(serializers.Serializer):
    """Schema serializer for season related data."""

    episodes = serializers.ListField(child=serializers.DictField(), required=False)


class TVRelatedSerializer(serializers.Serializer):
    """Schema serializer for TV related data."""

    seasons = serializers.ListField(child=serializers.DictField(), required=False)


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
    related = EpisodeRelatedSerializer()
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

        # TODO: move still_path slug to global configs
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

    @extend_schema_field(
        PolymorphicProxySerializer(
            component_name="CompleteMediaDetails",
            serializers=[
                AnimeDetailsSerializer,
                BoardGameDetailsSerializer,
                BookDetailsSerializer,
                ComicDetailsSerializer,
                GameDetailsSerializer,
                MangaDetailsSerializer,
                MovieDetailsSerializer,
                SeasonDetailsSerializer,
                TVDetailsSerializer,
            ],
            resource_type_field_name=None,
        ),
    )
    def get_details(self, obj):
        """Return polymorphic details payload for schema generation."""
        if isinstance(obj, dict):
            return obj.get("details", {})
        return getattr(obj, "details", {})

    @extend_schema_field(
        PolymorphicProxySerializer(
            component_name="CompleteMediaRelated",
            serializers=[
                AnimeRelatedSerializer,
                BoardGameRelatedSerializer,
                BookRelatedSerializer,
                ComicRelatedSerializer,
                GameRelatedSerializer,
                MangaRelatedSerializer,
                MovieRelatedSerializer,
                SeasonRelatedSerializer,
                TVRelatedSerializer,
            ],
            resource_type_field_name=None,
        ),
    )
    def get_related(self, obj):
        """Return polymorphic related payload for schema generation."""
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

        # TODO: Check why some informations take a while to update after a change

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


class PaginatedMediaResponseSerializer(serializers.Serializer):
    """Paginated response serializer for media items."""

    pagination = PaginationSerializer()
    results = MediaSerializer(many=True)


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
    items = PaginatedMediaResponseSerializer(source="paginated_items", required=False)

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

    def to_representation(self, instance):
        """Serialize a list, with its items if they are in the context."""
        data = super().to_representation(instance)
        items = self.context.get("paginated_items")
        if items is None or not self.context.get("include_items", True):
            return data

        if isinstance(items, dict) and "results" in items:
            items = {
                "pagination": items.get("pagination", {}),
                "results": MixedMediaSerializer(
                    items.get("results", []),
                    many=True,
                    context={**self.context, "serialize_items_as_media": True},
                ).data,
            }
        data["items"] = items
        return data


class PaginatedListsResponseSerializer(serializers.Serializer):
    """Paginated custom lists serializer."""

    pagination = PaginationSerializer()
    results = ListSerializer(many=True, context={"include_items": False})


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


class TimelineItemSerializer(serializers.Serializer):
    """Compact serializer used for timeline entries to reduce payload size."""

    id = serializers.IntegerField()
    item_id = ItemIdField(source="item")
    parent_id = ParentIdField(source="item")
    title = serializers.CharField(source="item.title")
    image = serializers.URLField(source="item.image", allow_null=True)
    media_type = MediaTypeCompleteChoiceField(source="item.media_type")
    source = MediaSourceCompleteChoiceField(source="item.source")
    created_at = UTCDateTimeField()
    score = serializers.FloatField(allow_null=True)
    progress = serializers.IntegerField()
    progressed_at = UTCDateTimeField(allow_null=True)
    status = MediaStatusChoiceField()
    start_date = UTCDateTimeField(allow_null=True)
    end_date = UTCDateTimeField(allow_null=True)
    notes = serializers.CharField(allow_blank=True)


# TODO: Complete the mapping of statistics response fields
class StatisticsResponseSerializer(serializers.Serializer):
    """Serializer for statistics endpoint payload."""

    start_date = serializers.DateField(allow_null=True, required=False)
    end_date = serializers.DateField(allow_null=True, required=False)
    media_count = StatisticsMediaCountSerializer()
    activity_data = serializers.JSONField()
    media_type_distribution = serializers.DictField()
    score_distribution = serializers.DictField()
    top_rated = MediaSerializer(many=True)
    status_distribution = serializers.DictField()
    status_pie_chart_data = serializers.JSONField()
    timeline = serializers.DictField(child=TimelineItemSerializer(many=True))


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
