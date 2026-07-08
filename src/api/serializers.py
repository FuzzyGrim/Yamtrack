from django.conf import settings
from django.utils.timezone import now
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

    def to_representation(self, item):  # noqa: D102
        return build_parent_id(item)


class MediaStatusChoiceField(serializers.ChoiceField):
    """Custom field for media status options."""

    def __init__(self, **kwargs):
        """Initialize with predefined status choices."""
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_null", True)
        status_choices = MEDIA_STATUS_CHOICES
        kwargs["choices"] = status_choices
        super().__init__(**kwargs)

    def to_internal_value(self, data):  # noqa: D102
        value = super().to_internal_value(data)
        return get_media_status(value, reverse=True)

    def to_representation(self, obj):  # noqa: D102
        return get_media_status(getattr(obj, "status", None))


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
    old_value = serializers.CharField(allow_null=True, required=False)
    new_value = serializers.CharField(allow_null=True, required=False)


class ChangesHistoryEntrySerializer(serializers.Serializer):
    """Serializer that builds a change-based history entry."""

    id = serializers.IntegerField(allow_null=True, required=False)
    item_id = serializers.CharField(allow_null=True, required=False)
    timestamp = serializers.DateTimeField(allow_null=True, required=False)
    changes = ChangesHistoryChangeSerializer(many=True)

    def to_representation(self, instance):
        """Build history entry with changes."""
        media_type = None
        if self.context:
            media_type = self.context.get("media_type")

        prev = getattr(instance, "prev_record", None)
        if prev is not None:
            changes = get_changes_from_diff(instance, prev, media_type)
        else:
            changes = get_changes_from_new_record(instance, media_type)

        for change in changes:
            if change.get("field") == "status":

                class TempObj:
                    def __init__(self, status_value):
                        self.status = status_value

                status_field = MediaStatusChoiceField()
                if change.get("old_value") is not None:
                    change["old_value"] = status_field.to_representation(
                        TempObj(change["old_value"]),
                    )
                if change.get("new_value") is not None:
                    change["new_value"] = status_field.to_representation(
                        TempObj(change["new_value"]),
                    )

        item_obj = getattr(instance, "item_obj", None)
        item_id = build_item_id(item_obj) if item_obj is not None else None

        return {
            "id": getattr(instance, "history_id", None),
            "item_id": item_id,
            "timestamp": getattr(instance, "history_date", None),
            "changes": changes,
        }


class ApiMessageResponseSerializer(serializers.Serializer):
    """Standard API message response serializer."""

    detail = serializers.CharField()


# TODO: errors field can be str or list, depending on the error
class ApiErrorResponseSerializer(serializers.Serializer):
    """Standard API error response serializer."""

    detail = serializers.CharField()
    errors = serializers.CharField(required=False, allow_blank=True)


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
    """Serializer for media count by type in statistics response."""

    total = serializers.IntegerField()
    tv = serializers.IntegerField()
    season = serializers.IntegerField()
    movie = serializers.IntegerField()
    anime = serializers.IntegerField()
    manga = serializers.IntegerField()
    game = serializers.IntegerField()
    book = serializers.IntegerField()
    comic = serializers.IntegerField()
    board_game = serializers.IntegerField()


class ListMinimizedSerializer(serializers.Serializer):
    """Serializer for minimized list information."""

    list_id = serializers.IntegerField()
    list_item_id = serializers.IntegerField()


class EpisodeSerializer(serializers.Serializer):
    """Serializer used for Episode items."""

    id = serializers.IntegerField(source="item.id", allow_null=True, required=False)
    consumption_id = serializers.IntegerField(
        source="id", allow_null=True, required=False
    )
    item = ItemSerializer()
    item_id = ItemIdField(source="item")
    parent_id = ParentIdField(source="item")
    tracked = serializers.BooleanField()
    created_at = serializers.DateTimeField(allow_null=True, required=False)
    score = serializers.FloatField(allow_null=True)
    status = MediaStatusChoiceField()
    progress = serializers.IntegerField(allow_null=True)
    progressed_at = serializers.DateTimeField(allow_null=True)
    start_date = serializers.DateTimeField(allow_null=True)
    end_date = serializers.DateTimeField(allow_null=True)
    notes = serializers.CharField(allow_null=True)
    lists = ListMinimizedSerializer(many=True)

    def to_representation(self, instance):
        """Serialize an Episode with item details."""
        context = self.context or {}

        if isinstance(instance, Episode):
            item = getattr(instance, "item", None)
            lists_by_item_id = context.get("lists_by_item_id", {})
            return {
                "id": item.id if item is not None else None,
                "consumption_id": instance.id,
                "item": ItemSerializer().to_representation(item)
                if item is not None
                else None,
                "item_id": ItemIdField().to_representation(item)
                if item is not None
                else None,
                "parent_id": ParentIdField().to_representation(item)
                if item is not None
                else None,
                "tracked": True,
                "created_at": instance.created_at,
                "score": None,
                "status": 3,
                "progress": 1,
                "progressed_at": instance.end_date,
                "start_date": instance.created_at,
                "end_date": instance.end_date,
                "notes": None,
                "lists": lists_by_item_id.get(item.id, []),
            }

        media_id = instance.get("show_id")
        season_number = instance.get("season_number")
        episode_number = instance.get("episode_number")

        tracked_episodes = context.get("tracked_episodes", {})
        episode = tracked_episodes.get(episode_number)
        tracked = episode is not None
        if hasattr(episode, "item"):
            item = getattr(episode, "item", None)
        else:
            image = (
                "https://image.tmdb.org/t/p/original" + instance.get("still_path")
                if instance.get("still_path")
                else None
            )
            item = Item(
                media_id=media_id,
                source=context.get("source"),
                media_type=MediaTypes.EPISODE.value,
                title=instance.get("name") or "",
                image=image,
                season_number=season_number,
                episode_number=episode_number,
            )

        if hasattr(episode, "lists"):
            lists = episode.lists
        else:
            lists = context.get("lists_by_number", {}).get(episode_number, [])
            if not lists and item is not None:
                lists_by_item_id = context.get("lists_by_item_id", {})
                lists = lists_by_item_id.get(item.id, [])

        return {
            "id": item.id if item is not None else None,
            "consumption_id": episode.id if episode is not None else None,
            "item": ItemSerializer().to_representation(item)
            if item is not None
            else None,
            "item_id": ItemIdField().to_representation(item)
            if item is not None
            else None,
            "parent_id": ParentIdField().to_representation(item)
            if item is not None
            else None,
            "tracked": tracked,
            "created_at": episode.created_at
            if hasattr(episode, "created_at")
            else None,
            "score": None,
            "status": 3 if tracked else None,
            "progress": 1 if tracked else None,
            "progressed_at": episode.end_date if hasattr(episode, "end_date") else None,
            "start_date": episode.created_at
            if hasattr(episode, "created_at")
            else None,
            "end_date": episode.end_date if hasattr(episode, "end_date") else None,
            "notes": None,
            "lists": lists,
        }


class PaginatedEpisodesSerializer(serializers.Serializer):
    """Serializer for paginated episodes."""

    pagination = PaginationSerializer()
    results = EpisodeSerializer(many=True)


class EventSerializer(serializers.ModelSerializer):
    """Serializer used for calendar events."""

    item = ItemSerializer()
    item_id = ItemIdField(source="item", read_only=True)
    parent_id = ParentIdField(source="item", read_only=True)

    class Meta:  # noqa: D106
        model = Event
        fields = "__all__"

    def to_representation(self, instance):
        """Transform item to episode when content_number is present."""
        data = super().to_representation(instance)

        if data.get("item") and instance.item is not None:
            data["item"]["media_id"] = instance.item.media_id

        if instance.content_number is not None and data.get("item"):
            item_data = data["item"]
            item_data["episode_number"] = instance.content_number
            if item_data.get("media_type") == "season":
                item_data["media_type"] = "episode"

            class TempItem:
                def __init__(self, item_dict):
                    for key, value in item_dict.items():
                        setattr(self, key, value)

            temp_item = TempItem(item_data)
            data["item_id"] = ItemIdField().to_representation(temp_item)
            data["parent_id"] = ParentIdField().to_representation(temp_item)

        return data


class PaginatedEventsSerializer(serializers.Serializer):
    """Serializer for paginated calendar events."""

    pagination = PaginationSerializer()
    results = EventSerializer(many=True)


class HealthCheckSerializer(serializers.Serializer):
    """Serializer for individual health checks."""

    status = serializers.ChoiceField(choices=["ok", "error"])
    error = serializers.CharField(allow_null=True)


class HealthResponseSerializer(serializers.Serializer):
    """Serializer for health check response."""

    status = serializers.ChoiceField(choices=["ok", "unavailable"])
    timestamp = serializers.DateTimeField()
    checks = serializers.DictField(child=HealthCheckSerializer())

    def to_representation(self, instance):
        """Transform reports from health-check library to json."""
        plugins = instance.get("plugins", {})
        errors = instance.get("errors", [])

        checks = {}
        for plugin_identifier, plugin in plugins.items():
            plugin_has_errors = bool(plugin.errors)

            checks[plugin_identifier] = {
                "status": "error" if plugin_has_errors else "ok",
                "error": plugin.pretty_status() if plugin_has_errors else None,
            }

        overall_status = "unavailable" if errors else "ok"

        return {
            "status": overall_status,
            "timestamp": now().isoformat(),
            "checks": checks,
        }


class HistorySerializer(serializers.Serializer):
    """Serializer for watch history entries."""

    consumption_id = serializers.IntegerField(
        help_text=(
            "The id of the media consumption (id numbers are global for all "
            "consumptions of all medias of the same type, they aren't "
            "sequential per media)."
        )
    )
    created = serializers.DateTimeField(allow_null=True)
    score = serializers.FloatField(allow_null=True)
    progress = serializers.IntegerField(allow_null=True)
    progressed_at = serializers.DateTimeField(allow_null=True)
    status = MediaStatusChoiceField()
    start_date = serializers.DateTimeField(allow_null=True)
    end_date = serializers.DateTimeField(allow_null=True)
    notes = serializers.CharField(allow_null=True)

    def to_representation(self, instance):
        """Transform a user media instance into a watch history entry."""
        # For Episode instances, use simplified structure
        if isinstance(instance, Episode):
            return {
                "consumption_id": instance.id,
                "created": instance.created_at
                if hasattr(instance, "created_at")
                else None,
                "score": None,
                "progress": 1 if bool(instance) else 0,
                "progressed_at": instance.created_at
                if hasattr(instance, "created_at")
                else None,
                "status": 3 if bool(instance) else None,
                "start_date": instance.created_at
                if hasattr(instance, "created_at")
                else None,
                "end_date": instance.end_date
                if hasattr(instance, "end_date")
                else None,
                "notes": "",
            }
        status = MediaStatusChoiceField().to_representation(instance)

        return {
            "consumption_id": instance.id,
            "created": instance.created_at
            if hasattr(instance, "created_at") and instance.created_at is not None
            else None,
            "score": float(instance.score)
            if hasattr(instance, "score") and instance.score is not None
            else None,
            "progress": instance.progress if hasattr(instance, "progress") else None,
            "progressed_at": instance.progressed_at
            if hasattr(instance, "progressed_at") and instance.progressed_at is not None
            else None,
            "status": status,
            "start_date": instance.start_date
            if hasattr(instance, "start_date") and instance.start_date is not None
            else None,
            "end_date": instance.end_date
            if hasattr(instance, "end_date") and instance.end_date is not None
            else None,
            "notes": instance.notes
            if hasattr(instance, "notes") and instance.notes is not None
            else None,
        }


class PaginatedHistoryResponseSerializer(serializers.Serializer):
    """Serializer for paginated watch history."""

    pagination = PaginationSerializer()
    results = HistorySerializer(many=True)


class InfoSerializer(serializers.Serializer):
    """Serializer for the info endpoint."""

    version = serializers.CharField()
    debug = serializers.BooleanField()
    frontend_url = serializers.URLField()
    language = serializers.CharField()
    timezone = serializers.CharField()
    admin_enabled = serializers.BooleanField()
    track_time = serializers.BooleanField()

    def to_representation(self, instance):  # noqa: ARG002
        """Transform to representation."""
        return {
            "version": settings.VERSION,
            "debug": settings.DEBUG,
            "frontend_url": settings.BASE_URL or "http://localhost:8000",
            "language": settings.LANGUAGE_CODE,
            "timezone": settings.TIME_ZONE,
            "admin_enabled": settings.ADMIN_ENABLED,
            "track_time": settings.TRACK_TIME,
        }


class PaginatedListsMinimizedResponseSerializer(serializers.Serializer):
    """Paginated response serializer for minimized list information."""

    pagination = PaginationSerializer()
    results = ListMinimizedSerializer(many=True)


class UserSerializer(serializers.Serializer):
    """Serializer for user information."""

    id = serializers.IntegerField()
    username = serializers.CharField()


class MediaSerializer(serializers.ModelSerializer):
    """Serializer used for media items."""

    id = serializers.IntegerField(source="item.id", allow_null=True, required=False)
    consumption_id = serializers.IntegerField(
        source="id", allow_null=True, required=False
    )
    item = ItemSerializer()
    item_id = ItemIdField(source="item", allow_null=True, required=False)
    parent_id = ParentIdField(source="item", allow_null=True, required=False)
    tracked = serializers.BooleanField()
    created_at = serializers.DateTimeField(allow_null=True, required=False)
    score = serializers.FloatField(allow_null=True)
    status = MediaStatusChoiceField()
    progress = serializers.IntegerField(allow_null=True)
    progressed_at = serializers.DateTimeField(allow_null=True)
    start_date = serializers.DateTimeField(allow_null=True)
    end_date = serializers.DateTimeField(allow_null=True)
    notes = serializers.CharField(allow_null=True)
    lists = ListMinimizedSerializer(many=True)

    class Meta:  # noqa: D106
        model = BasicMedia
        exclude = ("user",)

    def to_representation(self, instance):
        """Serialize media."""
        item = getattr(instance, "item", None)

        if hasattr(instance, "lists"):
            lists = instance.lists
        else:
            lists = []
            if self.context and item is not None:
                lists_by_item_id = self.context.get("lists_by_item_id", {})
                lists = lists_by_item_id.get(item.id, [])

        return {
            "id": item.id if item is not None else None,
            "consumption_id": instance.id,
            "item": ItemSerializer(item).data if item is not None else None,
            "item_id": ItemIdField().to_representation(item)
            if item is not None
            else None,
            "parent_id": ParentIdField().to_representation(item)
            if item is not None
            else None,
            "tracked": getattr(instance, "id", None) is not None,
            "created_at": instance.created_at,
            "score": float(instance.score)
            if hasattr(instance, "score") and instance.score is not None
            else None,
            "status": MediaStatusChoiceField().to_representation(instance),
            "progress": instance.progress if hasattr(instance, "progress") else None,
            "progressed_at": instance.progressed_at
            if hasattr(instance, "progressed_at")
            else None,
            "start_date": instance.start_date
            if hasattr(instance, "start_date")
            else None,
            "end_date": instance.end_date if hasattr(instance, "end_date") else None,
            "notes": instance.notes if hasattr(instance, "notes") else None,
            "lists": lists,
        }


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
    crew = serializers.ListField(child=serializers.DictField(), required=False)
    guest_stars = serializers.ListField(child=serializers.DictField(), required=False)


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
        help_text=(
            "The id of the item in the database (not to be confused with the item_id,"
            "which is media provider specific)"
        )
    )
    media_id = serializers.CharField()
    source = MediaSourceCompleteChoiceField()
    source_url = serializers.URLField(allow_null=True)
    media_type = MediaTypeCompleteChoiceField()
    title = serializers.CharField()
    max_progress = serializers.IntegerField()
    image = serializers.URLField(allow_null=True)
    synopsis = serializers.CharField(allow_null=True)
    genres = serializers.ListField(child=serializers.CharField(), allow_empty=True)
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
        """Transform episode data into CompleteEpisode response."""
        media_metadata = instance.get("media_metadata", {})
        episode = instance.get("episode", {})
        user_medias = instance.get("user_medias", [])
        lists = instance.get("lists", [])
        media_type = media_metadata.get("media_type")

        temp_episode = type("TempEpisode", (), {})()
        temp_episode.media_type = "episode"
        temp_episode.source = media_metadata.get("source")
        temp_episode.media_id = media_metadata.get("media_id")
        temp_episode.season_number = media_metadata.get("season_number")
        temp_episode.episode_number = episode.get("episode_number")

        season_source_url = media_metadata.get("source_url")
        source_url = ""
        if season_source_url:
            source_url = f"{season_source_url}/episode/{episode.get('episode_number')}"

        # TODO: move still_path slug to global configs
        image = (
            "https://image.tmdb.org/t/p/original" + episode.get("still_path")
            if episode.get("still_path")
            else None
        )

        consumptions_number = len(user_medias)
        consumptions = HistorySerializer(user_medias, many=True).data

        return {
            "id": user_medias[0].item_id if user_medias else None,
            "media_id": (
                str(media_metadata.get("media_id"))
                if media_metadata.get("media_id") is not None
                else None
            ),
            "source": media_metadata.get("source"),
            "source_url": source_url,
            "media_type": media_type,
            "title": episode.get("name"),
            "max_progress": 1,
            "image": image,
            "synopsis": episode.get("overview"),
            "genres": media_metadata.get("genres", []),
            "score": float(episode.get("vote_average")),
            "score_count": episode.get("vote_count"),
            "details": {
                "air_date": episode.get("air_date"),
                "episode_number": episode.get("episode_number"),
                "season_number": episode.get("season_number"),
                "runtime": episode.get("runtime"),
                "episode_type": episode.get("episode_type"),
                "crew": episode.get("crew", []),
                "guest_stars": episode.get("guest_stars", []),
            },
            "related": {},
            "item_id": ItemIdField().to_representation(temp_episode),
            "parent_id": ParentIdField().to_representation(temp_episode),
            "tracked": consumptions_number > 0,
            "consumptions_number": consumptions_number,
            "consumptions": consumptions,
            "lists": lists,
        }


class CompleteMediaSerializer(serializers.Serializer):
    """Serializer that builds a CompleteMedia response."""

    id = serializers.IntegerField(
        help_text=(
            "The id of the item in the database (not to be confused with the item_id,"
            "which is media provider specific)"
        )
    )
    media_id = serializers.CharField()
    source = MediaSourceCompleteChoiceField()
    source_url = serializers.URLField(allow_null=True)
    media_type = MediaTypeCompleteChoiceField()
    title = serializers.CharField()
    max_progress = serializers.IntegerField()
    image = serializers.URLField(allow_null=True)
    synopsis = serializers.CharField(allow_null=True)
    genres = serializers.ListField(child=serializers.CharField(), allow_empty=True)
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

            item = getattr(tracked_season, "item", None)
            if item is None:
                item = Item(
                    media_id=str(
                        season.get("media_id") or media_metadata.get("media_id") or "",
                    ),
                    source=season.get("source") or media_metadata.get("source"),
                    media_type=MediaTypes.SEASON.value,
                    title=season.get("season_title") or season.get("title") or "",
                    image=season.get("image") or settings.IMG_NONE,
                    season_number=season_number,
                )

            if tracked_season is None:
                tracked_season = type(
                    "TempMedia",
                    (),
                    {
                        "id": None,
                        "item": item,
                        "created_at": None,
                        "score": None,
                        "status": None,
                        "progress": None,
                        "progressed_at": None,
                        "start_date": None,
                        "end_date": None,
                        "notes": None,
                    },
                )()

            processed_seasons.append(
                MediaSerializer().to_representation(tracked_season),
            )

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
        """Transform media_metadata and user data into CompleteMedia response."""
        media_metadata = instance.get("media_metadata", {})
        user_medias = instance.get("user_medias")
        lists = instance.get("lists", [])
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
        related = media_metadata.get("related", {})

        consumptions_number = len(user_medias)
        consumptions = HistorySerializer(user_medias, many=True).data

        # TODO: Check why some informations take a while to update after a change

        return {
            "id": user_medias[0].item_id if user_medias else None,
            "media_id": (
                str(media_metadata.get("media_id"))
                if media_metadata.get("media_id") is not None
                else None
            ),
            "source": media_metadata.get("source"),
            "source_url": media_metadata.get("source_url"),
            "media_type": media_metadata.get("media_type"),
            "title": media_metadata.pop("season_title", None)
            or media_metadata.get("title"),
            "max_progress": int(media_metadata.get("max_progress"))
            if media_metadata.get("max_progress") is not None
            else 1,
            "image": media_metadata.get("image"),
            "synopsis": media_metadata.get("synopsis"),
            "genres": media_metadata.get("genres"),
            "score": float(media_metadata.get("score"))
            if media_metadata.get("score") is not None
            else None,
            "score_count": int(media_metadata.get("score_count"))
            if media_metadata.get("score_count") is not None
            else None,
            "details": details,
            "related": related,
            "item_id": ItemIdField().to_representation(temp_media),
            "parent_id": ParentIdField().to_representation(temp_media),
            "tracked": consumptions_number > 0,
            "consumptions_number": consumptions_number,
            "consumptions": consumptions,
            "lists": lists,
        }


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
    items_count = serializers.IntegerField()
    latest_update = serializers.DateTimeField()
    items = PaginatedMediaResponseSerializer(required=False, allow_null=True)

    def to_representation(self, instance):
        """Serialize a CustomList."""
        item_count = getattr(instance, "items_count", None)
        if item_count is None:
            item_count = instance.items.count()

        latest_update = getattr(instance, "latest_update", None)
        if latest_update is None:
            latest_update = CustomListItem.objects.get_last_added_date(instance)

        include_items = True
        if self.context and "include_items" in self.context:
            include_items = self.context["include_items"]

        items = []

        if self.context and self.context.get("paginated_items") is not None:
            items_context = self.context["paginated_items"]
            nested_context = {
                **self.context,
                "serialize_items_as_media": True,
            }

            if isinstance(items_context, dict) and "results" in items_context:
                items = {
                    "pagination": items_context.get("pagination", {}),
                    "results": MixedMediaSerializer(
                        items_context.get("results", []),
                        many=True,
                        context=nested_context,
                    ).data,
                }
            else:
                items = items_context

        response = {
            "id": instance.id,
            "name": instance.name,
            "description": instance.description,
            "image": instance.image,
            "owner": {
                "id": instance.owner.id,
                "username": instance.owner.username,
            },
            "collaborators": [
                {"id": collaborator.id, "username": collaborator.username}
                for collaborator in instance.collaborators.all()
            ],
            "items_count": item_count,
            "latest_update": latest_update,
        }

        if include_items:
            response["items"] = items

        return response


class PaginatedListsResponseSerializer(serializers.Serializer):
    """Paginated custom lists serializer."""

    pagination = PaginationSerializer()
    results = ListSerializer(many=True, context={"include_items": False})


# TODO: Complete the mapping of statistics response fields
class StatisticsResponseSerializer(serializers.Serializer):
    """Serializer for statistics endpoint payload."""

    start_date = serializers.DateTimeField(allow_null=True, required=False)
    end_date = serializers.DateTimeField(allow_null=True, required=False)
    media_count = StatisticsMediaCountSerializer()
    activity_data = serializers.JSONField()
    media_type_distribution = serializers.DictField()
    score_distribution = serializers.DictField()
    top_rated = MediaSerializer(many=True)
    status_distribution = serializers.DictField()
    status_pie_chart_data = serializers.JSONField()
    timeline = serializers.DictField(
        child=serializers.ListField(child=serializers.DictField()),
    )


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
            serializer = UntrackedMediaSerializer(instance, context=self.context)
            return serializer.data

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


class UntrackedMediaSerializer(serializers.Serializer):
    """Serialize an untracked Item with a Media-like response shape."""

    def to_representation(self, instance):
        """Return media-compatible payload for an untracked Item."""
        lists = []
        if self.context:
            lists_by_item_id = self.context.get("lists_by_item_id", {})
            lists = lists_by_item_id.get(instance.id, [])

        return {
            "id": instance.id,
            "consumption_id": None,
            "item": ItemSerializer().to_representation(instance),
            "item_id": ItemIdField().to_representation(instance),
            "parent_id": ParentIdField().to_representation(instance),
            "tracked": False,
            "created_at": None,
            "score": None,
            "status": None,
            "progress": None,
            "progressed_at": None,
            "start_date": None,
            "end_date": None,
            "notes": None,
            "lists": lists,
        }


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


class TimelineItemSerializer(serializers.ModelSerializer):
    """Compact serializer used for timeline entries to reduce payload size."""

    item_id = ItemIdField(source="item", read_only=True)
    parent_id = ParentIdField(source="item", read_only=True)
    title = serializers.CharField(source="item.title", read_only=True, allow_null=True)
    image = serializers.URLField(source="item.image", read_only=True, allow_null=True)
    media_type = serializers.CharField(
        source="item.media_type",
        read_only=True,
        allow_null=True,
    )
    source = serializers.CharField(
        source="item.source",
        read_only=True,
        allow_null=True,
    )

    class Meta:  # noqa: D106
        model = BasicMedia
        exclude = ("user",)


class BaseUpdateMediaSerializer(serializers.Serializer):
    """Base serializer for updating media items with common fields."""

    score = serializers.FloatField(required=False, allow_null=True)
    status = MediaStatusChoiceField()
    progress = serializers.IntegerField(required=False, allow_null=True)
    start_date = serializers.DateTimeField(required=False, allow_null=True)
    end_date = serializers.DateTimeField(required=False, allow_null=True)
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

    end_date = serializers.DateTimeField(required=False, allow_null=True)


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
    start_date = serializers.DateTimeField(required=False, allow_null=True)
    end_date = serializers.DateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_null=True)
    season_number = serializers.IntegerField(required=False, allow_null=True)
    episode_number = serializers.IntegerField(required=False, allow_null=True)


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
