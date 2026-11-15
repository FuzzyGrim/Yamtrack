import copy

from django.conf import settings
from django.utils.timezone import now
from rest_framework import serializers

from app import helpers as app_helpers
from app.artwork_sizes import card_image_url
from app.backdrops import cached_backdrop_or_warm  # FORK: horizontal artwork
from app.helpers import build_provider_ids
from app.history_entry_builders import _serialize_show
from app.models import (
    TV,
    AlbumTracker,
    Anime,
    BasicMedia,
    BoardGame,
    Book,
    Comic,
    ComicIssue,  # FORK: fork-only media type
    Episode,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    MoviePlay,
    Music,  # FORK: fork-only media type
    Podcast,  # FORK: fork-only media type
    PodcastPlay,
    PodcastShowTracker,
    Season,
    Status,
)
from app.templatetags.app_tags import media_url
from events.models import Event
from lists.models import CustomList, CustomListItem
from users.home_screen import HomeRowEntry

from .changes_history_processor import (
    get_changes_from_diff,
    get_changes_from_new_record,
)
from .helpers import (
    build_item_id,
    build_parent_id,
    filter_item_bucket,
    get_media_status,
)


class ItemIdField(serializers.Field):
    """Custom field to generate item_id string."""

    def to_representation(self, item):  # noqa: D102
        return build_item_id(item)


class ParentIdField(serializers.Field):
    """Custom field to generate parent_id string for seasons and episodes."""

    def to_representation(self, item):  # noqa: D102
        return build_parent_id(item)


class StatusField(serializers.Field):
    """Custom field to convert status string to numeric value."""

    def to_representation(self, obj):  # noqa: D102
        return get_media_status(getattr(obj, "status", None))


_EPISODIC_MEDIA_TYPES = frozenset(
    {
        MediaTypes.TV.value,
        MediaTypes.SEASON.value,
        MediaTypes.ANIME.value,
    },
)
_UNSET = object()


def _episode_remaining(media, *, total_episode_count=_UNSET):
    """Return released and provider-total episodes remaining for one media row."""
    item = getattr(media, "item", None)
    if item is None or getattr(item, "media_type", None) not in _EPISODIC_MEDIA_TYPES:
        return None, None

    progress = getattr(media, "progress", None)
    if progress is None:
        return None, None
    try:
        progress = max(0, int(progress))
    except (TypeError, ValueError):
        return None, None

    def _remaining(count):
        if count is None:
            return None
        try:
            return max(0, int(count) - progress)
        except (TypeError, ValueError):
            return None

    episodes_left = _remaining(getattr(media, "max_progress", None))
    if total_episode_count is _UNSET:
        total_episode_count = getattr(media, "total_episode_count", _UNSET)
        if total_episode_count is _UNSET:
            total_episode_count = getattr(item, "provider_episode_count", None)
    return episodes_left, _remaining(total_episode_count)


def _names_to_list(value):
    """Return provider names as a list.

    Providers send a list (Open Library), a comma-joined string (BoardGameGeek)
    or nothing at all.
    """
    if isinstance(value, str):
        return value.split(", ") if value else []
    return list(value or [])


def _has_dropped_season(media):
    """Return whether a TV-like media row has an excluded dropped season."""
    item = getattr(media, "item", None)
    if getattr(item, "media_type", None) != MediaTypes.TV.value:
        return False
    return any(
        season.status == Status.DROPPED.value
        for season in media.seasons.all()
        if getattr(getattr(season, "item", None), "season_number", None) not in (None, 0)
    )


class ItemSerializer(serializers.ModelSerializer):
    """Serializer used for item details."""

    media_id = serializers.SerializerMethodField()
    url = serializers.SerializerMethodField()
    ids = serializers.SerializerMethodField()
    card_image = serializers.SerializerMethodField()

    def get_media_id(self, obj):
        """Return media_id preserving alphanumeric provider IDs."""
        media_id = getattr(obj, "media_id", None)
        if media_id is None:
            return None
        return str(media_id)

    def get_url(self, obj):
        """Return the Floppy detail URL for this item, or None."""
        if not getattr(obj, "media_id", None):
            return None
        try:
            return media_url(obj) or None
        except Exception:
            return None

    def get_ids(self, obj):
        """Return the item's resolved external provider ids."""
        return build_provider_ids(obj)

    def get_card_image(self, obj):
        """Return ``image`` at the provider size a media card shows."""
        return card_image_url(getattr(obj, "image", "") or "")

    class Meta:  # noqa: D106
        model = Item
        exclude = ("id", "provider_episode_count")


# One shared instance: building a ModelSerializer's fields costs ~5 ms, and
# every per-row payload used to pay it again for each item it serialized.
_ITEM_SERIALIZER = ItemSerializer()


class ChangesHistoryEntrySerializer(serializers.Serializer):
    """Serializer that builds a change-based history entry."""

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

                status_field = StatusField()
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


class CompleteEpisodeSerializer(serializers.Serializer):
    """Serializer that builds a CompleteEpisode response."""

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
        consumptions = serialize_data(
            user_medias,
            serializer_class=HistorySerializer,
            many=True,
        )

        item = instance.get("item")

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
            "episodes_left": None,
            "total_episodes_left": None,
            "image": image,
            # FORK: show-level backdrop
            "backdrop": cached_backdrop_or_warm(media_metadata),
            "synopsis": episode.get("overview"),
            "genres": media_metadata.get("genres", []),
            "score": float(episode.get("vote_average")),
            "score_count": episode.get("vote_count"),
            # FORK: IMDb rating alongside TMDB-based score
            "imdb_rating": getattr(item, "imdb_rating", None),
            "imdb_rating_count": getattr(item, "imdb_rating_count", None),
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
            "media_type_status": instance.get("media_type_status"),
        }


class CompleteMediaSerializer(serializers.Serializer):
    """Serializer that builds a CompleteMedia response."""

    def _process_seasons(
        self,
        media_metadata,
        seasons_by_number=None,
        library_media_type=None,
    ):
        """Process seasons in related data."""
        if "related" not in media_metadata or media_metadata["related"] is None:
            media_metadata["related"] = {}
        if (
            "seasons" not in media_metadata["related"]
            or media_metadata["related"]["seasons"] is None
        ):
            media_metadata["related"]["seasons"] = []

        # FORK: look up already-synced season Items (e.g. IMDb ratings) for
        # untracked seasons instead of always building an ephemeral in-memory
        # Item, one batched query for the whole show rather than per-season.
        existing_items_by_season = {
            existing_item.season_number: existing_item
            for existing_item in filter_item_bucket(
                Item.objects.filter(
                    media_id=str(media_metadata.get("media_id") or ""),
                    source=media_metadata.get("source"),
                    media_type=MediaTypes.SEASON.value,
                ),
                MediaTypes.SEASON.value,
                library_media_type=library_media_type,
            )
        }

        processed_seasons = []
        for season in media_metadata["related"]["seasons"]:
            season_number = season.get("season_number")
            tracked_season = (
                seasons_by_number.get(season_number) if seasons_by_number else None
            )

            item = getattr(tracked_season, "item", None)
            if item is None:
                item = existing_items_by_season.get(season_number)
                if item is not None and not app_helpers.has_real_image(item.image):
                    fallback_image = app_helpers.first_real_image(
                        season.get("image"),
                        default=None,
                    )
                    if fallback_image:
                        item = copy.copy(item)
                        item.image = fallback_image
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
                        "updated_at": None,
                        "score": None,
            "scored_at": None,
                        "status": None,
                        "progress": None,
                        "progressed_at": None,
                        "start_date": None,
                        "end_date": None,
                        "notes": None,
                    },
                )()

            if tracked_season is not None and season.get("max_progress") is not None:
                tracked_season.total_episode_count = season["max_progress"]
            processed_seasons.append(
                MediaSerializer().to_representation(tracked_season),
            )

        media_metadata["related"]["seasons"] = processed_seasons

    def _process_episodes(
        self,
        media_metadata,
        episodes_by_number=None,
        library_media_type=None,
    ):
        """Process episodes in media data."""
        if "related" not in media_metadata or media_metadata["related"] is None:
            media_metadata["related"] = {}
        if (
            "episodes" not in media_metadata["related"]
            or media_metadata["related"]["episodes"] is None
        ):
            media_metadata["related"]["episodes"] = []

        episodes = media_metadata.pop("episodes", [])
        # FORK: look up already-synced episode Items (e.g. IMDb ratings) for
        # untracked episodes instead of always building an ephemeral in-memory
        # Item, one batched query for the whole season rather than per-episode.
        existing_items_by_episode = {
            existing_item.episode_number: existing_item
            for existing_item in filter_item_bucket(
                Item.objects.filter(
                    media_id=str(media_metadata.get("media_id") or ""),
                    source=media_metadata.get("source"),
                    media_type=MediaTypes.EPISODE.value,
                    season_number=media_metadata.get("season_number"),
                ),
                MediaTypes.EPISODE.value,
                library_media_type=library_media_type,
            )
        }
        serializer = EpisodeSerializer(
            context={
                "source": media_metadata.get("source"),
                "tracked_episodes": episodes_by_number or {},
                "existing_items_by_episode": existing_items_by_episode,
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
        library_media_type = instance.get("library_media_type")

        if media_type == MediaTypes.TV.value:
            self._process_seasons(
                media_metadata,
                instance.get("seasons"),
                library_media_type=library_media_type,
            )
        elif media_type == MediaTypes.SEASON.value:
            self._process_episodes(
                media_metadata,
                instance.get("episodes"),
                library_media_type=library_media_type,
            )

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
        if "provider_game_lengths" in media_metadata:
            game_lengths = media_metadata.pop("provider_game_lengths")
            if isinstance(game_lengths, dict):
                for source_key in ("hltb", "igdb"):
                    if isinstance(game_lengths.get(source_key), dict):
                        game_lengths[source_key].pop("raw", None)
            details["provider_game_lengths"] = game_lengths
        if "year" in details:
            details["year"] = int(details["year"])
        if "players" in details:
            details["players"] = details["players"].strip(" players").split("-")
        if "playtime" in details:
            details["playtime"] = int(details["playtime"].strip(" min"))
        if "min_age" in details:
            details["min_age"] = int(details["min_age"].strip("+"))
        if "designers" in details:
            details["designers"] = _names_to_list(details["designers"])
        if "publishers" in details:
            details["publishers"] = _names_to_list(details["publishers"])
        related = media_metadata.get("related", {})

        consumptions_number = len(user_medias)
        consumptions = serialize_data(
            user_medias,
            serializer_class=HistorySerializer,
            many=True,
        )

        primary_media = user_medias[0] if user_medias else None
        if primary_media is None:
            episode_left_values = (None, None)
        elif hasattr(primary_media, "total_episode_count") and _has_dropped_season(
            primary_media,
        ):
            episode_left_values = _episode_remaining(
                primary_media,
                total_episode_count=primary_media.total_episode_count,
            )
        else:
            total_episode_count = media_metadata.get("max_progress")
            if total_episode_count is None:
                total_episode_count = getattr(
                    primary_media,
                    "total_episode_count",
                    None,
                )
            episode_left_values = _episode_remaining(
                primary_media,
                total_episode_count=total_episode_count,
            )

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
            "episodes_left": episode_left_values[0],
            "total_episodes_left": episode_left_values[1],
            "image": media_metadata.get("image"),
            # FORK: 16:9 artwork
            "backdrop": cached_backdrop_or_warm(media_metadata),
            "synopsis": media_metadata.get("synopsis"),
            "genres": media_metadata.get("genres"),
            "score": float(media_metadata.get("score"))
            if media_metadata.get("score") is not None
            else None,
            "score_count": int(media_metadata.get("score_count"))
            if media_metadata.get("score_count") is not None
            else None,
            # FORK: IMDb rating alongside TMDB-based score
            "imdb_rating": getattr(instance.get("item"), "imdb_rating", None),
            "imdb_rating_count": getattr(
                instance.get("item"), "imdb_rating_count", None,
            ),
            "cast": media_metadata.get("cast") or [],
            "crew": media_metadata.get("crew") or [],
            "details": details,
            # FORK: the linked studio list the web Studios card shows (details.studios is trimmed).
            "studios": [
                {"id": str(studio.get("studio_id") or studio.get("id") or ""), "name": name}
                for studio in media_metadata.get("studios_full") or []
                if (name := (studio.get("name") or "").strip())
            ],
            "related": related,
            "item_id": ItemIdField().to_representation(temp_media),
            "parent_id": ParentIdField().to_representation(temp_media),
            "tracked": consumptions_number > 0,
            "consumptions_number": consumptions_number,
            "consumptions": consumptions,
            "lists": lists,
            "media_type_status": instance.get("media_type_status"),
        }


class EpisodeSerializer(serializers.ModelSerializer):
    """Serializer used for Episode items."""

    def to_representation(self, instance):
        """Serialize an Episode with item details."""
        context = self.context or {}

        if isinstance(instance, Episode):
            item = getattr(instance, "item", None)
            lists_by_item_id = context.get("lists_by_item_id", {})
            return {
                "id": item.id if item is not None else None,
                "consumption_id": instance.id,
                "item": _ITEM_SERIALIZER.to_representation(item)
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
                "updated_at": getattr(instance, "updated_at", None),
                # FORK: episodes expose the same tracking details as other media.
                "score": float(instance.score)
                if getattr(instance, "score", None) is not None
                else None,
                "scored_at": getattr(instance, "scored_at", None),
                "status": get_media_status(instance.status),
                "progress": 1 if instance.end_date else 0,
                "progress_scope": "entry",
                "progress_unit": "episodes",
                "progressed_at": instance.end_date,
                "start_date": instance.start_date,
                "end_date": instance.end_date,
                "notes": instance.notes,
                "source": instance.entry_source,
                "lists": lists_by_item_id.get(item.id, []),
                "next_episode": None,
                "show": None,
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
            # FORK: reuse an already-synced episode Item (e.g. IMDb ratings)
            # instead of always building an ephemeral in-memory Item.
            existing_items_by_episode = context.get("existing_items_by_episode", {})
            item = existing_items_by_episode.get(episode_number)
            if item is not None and not app_helpers.has_real_image(item.image):
                fallback_image = app_helpers.first_real_image(
                    "https://image.tmdb.org/t/p/original" + instance.get("still_path")
                    if instance.get("still_path")
                    else None,
                    instance.get("image"),
                    default=None,
                )
                if fallback_image:
                    item = copy.copy(item)
                    item.image = fallback_image
            if item is None:
                image = app_helpers.first_real_image(
                    "https://image.tmdb.org/t/p/original" + instance.get("still_path")
                    if instance.get("still_path")
                    else None,
                    instance.get("image"),
                    default=None,
                )
                item = Item(
                    media_id=media_id,
                    source=context.get("source"),
                    media_type=MediaTypes.EPISODE.value,
                    title=instance.get("name") or "",
                    image=image,
                    season_number=season_number,
                    episode_number=episode_number,
                    release_datetime=app_helpers.extract_release_datetime(
                        {"release_date": instance.get("air_date")},
                    ),
                )

        if hasattr(episode, "lists"):
            lists = episode.lists
        else:
            lists = context.get("lists_by_number", {}).get(episode_number, [])
            if not lists and item is not None:
                lists_by_item_id = context.get("lists_by_item_id", {})
                lists = lists_by_item_id.get(item.id, [])

        serialized_item = _ITEM_SERIALIZER.to_representation(item) if item else None
        if serialized_item is not None:
            serialized_item["title"] = instance.get("name") or serialized_item["title"]
            if not serialized_item.get("release_datetime") and instance.get(
                "air_date",
            ):
                serialized_item["release_datetime"] = (
                    app_helpers.extract_release_datetime(
                        {"release_date": instance.get("air_date")},
                    )
                )

        return {
            "id": item.id if item is not None else None,
            "consumption_id": episode.id if episode is not None else None,
            "item": serialized_item,
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
            "scored_at": None,
            "status": 3 if tracked else None,
            "progress": 1 if tracked else None,
            "progress_scope": "entry" if tracked else None,
            "progress_unit": "episodes" if tracked else None,
            "progressed_at": episode.end_date if hasattr(episode, "end_date") else None,
            "start_date": episode.created_at
            if hasattr(episode, "created_at")
            else None,
            "end_date": episode.end_date if hasattr(episode, "end_date") else None,
            "notes": None,
            "lists": lists,
            "next_episode": None,
            "show": None,
        }


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


class HealthResponseSerializer(serializers.Serializer):
    """Serializer for health check response."""

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

    def to_representation(self, instance):
        """Transform a user media instance into a watch history entry."""
        # For Episode/MoviePlay instances (play-per-instance types with no
        # standalone status/progress fields), use simplified structure.
        if isinstance(instance, (Episode, MoviePlay)):
            # FORK: episodes and movie plays expose the same tracking details
            # as other media.
            return {
                "consumption_id": instance.id,
                "created": instance.created_at
                if hasattr(instance, "created_at")
                else None,
                "score": float(instance.score)
                if getattr(instance, "score", None) is not None
                else None,
                "scored_at": getattr(instance, "scored_at", None),
                "progress": 1 if instance.end_date else 0,
                "progressed_at": instance.end_date,
                "status": get_media_status(getattr(instance, "status", None)),
                "start_date": getattr(instance, "start_date", None),
                "end_date": instance.end_date,
                "notes": getattr(instance, "notes", ""),
                "source": getattr(instance, "entry_source", ""),
                # FORK: client-supplied play id, so a syncing client can match
                # its own event to the stored play after a restart.
                "external_id": getattr(instance, "external_id", None),
            }
        if isinstance(instance, PodcastPlay):
            tracker = instance.podcast
            return {
                "consumption_id": instance.id,
                "created": instance.created_at,
                "score": float(tracker.score) if tracker.score is not None else None,
                "scored_at": getattr(tracker, "scored_at", None),
                "progress": instance.progress,
                "played_up_to_seconds": instance.played_up_to_seconds,
                "progressed_at": instance.end_date or instance.updated_at,
                "status": get_media_status(instance.status),
                "start_date": instance.start_date,
                "end_date": instance.end_date,
                "notes": tracker.notes,
                "source": instance.entry_source,
                "external_id": instance.external_id,
            }
        status = StatusField().to_representation(instance)

        return {
            "consumption_id": instance.id,
            "created": instance.created_at
            if hasattr(instance, "created_at") and instance.created_at is not None
            else None,
            "score": float(instance.score)
            if hasattr(instance, "score") and instance.score is not None
            else None,
            "scored_at": getattr(instance, "scored_at", None),
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
            "source": instance.entry_source
            if hasattr(instance, "entry_source") and instance.entry_source is not None
            else None,
        }


class InfoSerializer(serializers.Serializer):
    """Serializer for the info endpoint."""

    def to_representation(self, instance):
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


class ListSerializer(serializers.Serializer):
    """Serializer used for custom lists."""

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
                    "results": serialize_data(
                        items_context.get("results", []),
                        many=True,
                        context=nested_context,
                        homogeneous=False,
                    ),
                }
                list_added = self.context.get("list_added_by_item_id", {})
                for entry in items["results"]:
                    entry["list_date_added"] = list_added.get(entry.get("id"))
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
            "viewers": [
                {"id": viewer.id, "username": viewer.username}
                for viewer in instance.viewers.all()
            ],
            "items_count": item_count,
            "latest_update": latest_update,
            "visibility": instance.visibility,
            "is_public": instance.is_public,
            "is_unlisted": instance.is_unlisted,
            "public_slug": instance.public_slug,
            "allow_recommendations": instance.allow_recommendations,
            "is_smart": instance.is_smart,
            "tags": list(instance.tags or []),
            "include_notes": instance.include_notes,
        }

        completed_counts = (self.context or {}).get("completed_counts")
        if completed_counts is not None:
            # Same figure the web lists page prints: titles completed / titles.
            completed = completed_counts.get(instance.id, 0)
            response["completed_count"] = completed
            response["completion_percent"] = (
                round(completed / item_count * 100) if item_count else None
            )

        breakdown = (self.context or {}).get("media_type_breakdown")
        if breakdown is not None:
            # The web's media-type breakdown popover (pie chart button).
            response["media_type_breakdown"] = [
                {**entry, "label": str(entry["label"])} for entry in breakdown
            ]

        if include_items:
            response["items"] = items

        return response


def _music_identity(instance):
    """Return the artist, album and track ids of a music row, else nothing."""
    if getattr(instance, "item", None) is None or instance.item.media_type != "music":
        return {}
    return {
        "artist_id": getattr(instance, "artist_id", None),
        "album_id": getattr(instance, "album_id", None),
        "track_id": getattr(instance, "track_id", None),
    }


class MediaSerializer(serializers.ModelSerializer):
    """Serializer used for media items."""

    entry_id = serializers.IntegerField(read_only=True, required=False, help_text="Tracker row identity when separate_entries=1.")

    # Declared so OpenAPI generation documents the real wire format (see
    # api.schema.StatusFieldExtension) instead of the model field's string-label
    # choices, which `to_representation` below never actually returns.
    status = StatusField()

    class Meta:  # noqa: D106
        model = BasicMedia
        exclude = ("user",)

    def to_representation(self, instance):
        """Serialize media."""
        item = getattr(instance, "item", None)
        episodes_left, total_episodes_left = _episode_remaining(instance)
        next_episode_by_item_id = (self.context or {}).get(
            "next_episode_by_item_id",
            {},
        )
        tags_by_item_id = (self.context or {}).get("tags_by_item_id")
        if item is not None and item.id in next_episode_by_item_id:
            next_episode = next_episode_by_item_id[item.id]
        elif item is not None:
            from app.media_list_filters import next_episode_for_media

            next_episode = next_episode_for_media(instance)
        else:
            next_episode = None

        if hasattr(instance, "lists"):
            lists = instance.lists
        else:
            lists = []
            if self.context and item is not None:
                lists_by_item_id = self.context.get("lists_by_item_id", {})
                lists = lists_by_item_id.get(item.id, [])

        show = None
        if isinstance(instance, Podcast):
            show = (
                instance.episode.show
                if instance.episode and instance.episode.show
                else instance.show
            )

        return {
            "id": item.id if item is not None else None,
            "consumption_id": None if isinstance(instance, (PodcastShowTracker, AlbumTracker)) else instance.id,
            "item": _ITEM_SERIALIZER.to_representation(item)
            if item is not None
            else None,
            "item_id": ItemIdField().to_representation(item)
            if item is not None
            else None,
            "parent_id": ParentIdField().to_representation(item)
            if item is not None
            else None,
            "tracked": getattr(instance, "id", None) is not None,
            "created_at": instance.created_at,
            "updated_at": getattr(instance, "updated_at", None),
            "score": float(instance.score)
            if hasattr(instance, "score") and instance.score is not None
            else None,
            "scored_at": getattr(instance, "scored_at", None),
            "status": StatusField().to_representation(instance),
            "progress": instance.progress if hasattr(instance, "progress") else None,
            "episodes_left": episodes_left,
            "total_episodes_left": total_episodes_left,
            # `progress` is always this single entry's own value (this play,
            # session, or re-watch), never a sum across a user's entries for the
            # item; `progress_unit` names what it counts so clients don't have to
            # infer it from media_type (e.g. minutes for games, episodes for TV,
            # plays for boardgame/music, percentage/pages/chapters for
            # book/manga/comic depending on preference and format).
            "progress_scope": "entry"
            if getattr(instance, "progress", None) is not None
            else None,
            "progress_unit": instance.progress_unit
            if hasattr(instance, "progress_unit")
            else None,
            "progressed_at": instance.progressed_at
            if hasattr(instance, "progressed_at")
            else None,
            "start_date": instance.start_date
            if hasattr(instance, "start_date")
            else None,
            "end_date": instance.end_date if hasattr(instance, "end_date") else None,
            "notes": instance.notes if hasattr(instance, "notes") else None,
            "source": instance.entry_source if hasattr(instance, "entry_source") else None,
            "lists": lists,
            "next_episode": next_episode,
            "show": _serialize_show(show),
            # FORK: lets a native client open the artist/album/track page of
            # a music row without a second lookup.
            **_music_identity(instance),
            # FORK: the viewer's tag names, only where the list view batched them.
            **(
                {"tags": tags_by_item_id.get(item.id, [])}
                if tags_by_item_id is not None and item is not None
                else {}
            ),
        }


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
            "item": _ITEM_SERIALIZER.to_representation(instance),
            "item_id": ItemIdField().to_representation(instance),
            "parent_id": ParentIdField().to_representation(instance),
            "tracked": False,
            "created_at": None,
            "updated_at": None,
            "score": None,
            "scored_at": None,
            "status": None,
            "progress": None,
            "episodes_left": None,
            "total_episodes_left": None,
            "progress_scope": None,
            "progress_unit": None,
            "progressed_at": None,
            "start_date": None,
            "end_date": None,
            "notes": None,
            "lists": lists,
            "next_episode": None,
            "show": None,
        }


class HomeRowEntrySerializer(serializers.Serializer):
    """Serialize a users.home_screen.HomeRowEntry (a Home row card)."""

    def to_representation(self, instance):
        """Delegate to MediaSerializer/UntrackedMediaSerializer based on tracking."""
        context = self.context or {}
        if instance.media is not None:
            data = MediaSerializer(instance.media, context=context).data
        else:
            data = UntrackedMediaSerializer(instance.item, context=context).data
        data["show_progress_controls"] = instance.show_progress_controls
        if instance.subtitle_override:
            data["subtitle_override"] = instance.subtitle_override
        if instance.use_podcast_show and instance.podcast_show is not None:
            data["podcast_show"] = {
                "id": getattr(instance.podcast_show, "id", None),
                "title": getattr(instance.podcast_show, "title", None),
            }
        return data


class TimelineItemSerializer(serializers.ModelSerializer):
    """Compact serializer used for timeline entries to reduce payload size."""

    item_id = ItemIdField(source="item", read_only=True)
    parent_id = ParentIdField(source="item", read_only=True)
    title = serializers.CharField(source="item.title", read_only=True, allow_null=True)
    image = serializers.CharField(source="item.image", read_only=True, allow_null=True)
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


serializer_map = {
    Anime: MediaSerializer,
    BasicMedia: MediaSerializer,
    BoardGame: MediaSerializer,
    Book: MediaSerializer,
    Comic: MediaSerializer,
    # FORK: fork-only media types — registered so incidental serialization
    # (list contents, history, timelines) does not 500. Dedicated /media/
    # endpoints for these types come in a later phase.
    ComicIssue: MediaSerializer,
    CustomList: ListSerializer,
    Episode: EpisodeSerializer,
    Event: EventSerializer,
    Game: MediaSerializer,
    HomeRowEntry: HomeRowEntrySerializer,
    Item: ItemSerializer,
    Manga: MediaSerializer,
    Movie: MediaSerializer,
    Music: MediaSerializer,  # FORK
    Podcast: MediaSerializer,  # FORK
    PodcastShowTracker: MediaSerializer,
    AlbumTracker: MediaSerializer,
    Season: MediaSerializer,
    TV: MediaSerializer,
}


def serialize_data(
    data,
    *,
    many=False,
    context=None,
    serializer_class=None,
    homogeneous=True,
):
    """Serialize data using the appropriate serializer class."""
    # If serializer class is explicitly provided, use it
    if serializer_class is not None:
        kwargs = {"many": many}
        if context is not None:
            kwargs["context"] = context
        serializer = serializer_class(data, **kwargs)
        return serializer.data

    # Auto-detect serializer based on data type
    if many:
        data_list = list(data) if not isinstance(data, list) else data
        if not data_list:
            return []

        # Check if data is homogeneous (all same type)
        first_type = type(data_list[0])
        if homogeneous:
            detected_serializer_class = serializer_map.get(first_type)

            if detected_serializer_class is None:
                msg = (
                    f"No serializer found for data type {first_type}. "
                    f"Supported types: {list(serializer_map.keys())}. "
                    f"Pass serializer_class explicitly if needed."
                )
                raise ValueError(msg)

            kwargs = {"many": True}
            if context is not None:
                kwargs["context"] = context
            serializer = detected_serializer_class(data_list, **kwargs)
            return serializer.data
        kwargs = {"many": True}
        if context is not None:
            kwargs["context"] = context
        serializer = MixedMediaSerializer(data_list, **kwargs)
        return serializer.data
    sample_item = data

    data_type = type(sample_item)
    detected_serializer_class = serializer_map.get(data_type)

    if detected_serializer_class is None:
        msg = (
            f"No serializer found for data type {data_type}. "
            f"Supported types: {list(serializer_map.keys())}. "
            f"Pass serializer_class explicitly if needed."
        )
        raise ValueError(msg)

    kwargs = {"many": False}
    if context is not None:
        kwargs["context"] = context
    serializer = detected_serializer_class(sample_item, **kwargs)
    return serializer.data
