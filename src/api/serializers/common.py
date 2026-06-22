from decimal import Decimal

from django.conf import settings
from django.utils.text import slugify
from rest_framework import serializers

from app import config
from app.models import BasicMedia, DiaryEntry, Item, MediaTypes
from lists.models import CustomList


class MediaRefSerializer(serializers.Serializer):
    """Stable public media identity."""

    item_id = serializers.IntegerField(required=False, allow_null=True)
    source = serializers.CharField()
    media_type = serializers.CharField()
    media_id = serializers.CharField()
    season_number = serializers.IntegerField(required=False, allow_null=True)
    episode_number = serializers.IntegerField(required=False, allow_null=True)


def absolute_url(request, url):
    """Return an absolute URL for relative uploaded media paths."""
    if not url:
        return None
    value = str(url)
    if value.startswith(("http://", "https://")):
        return value
    if request is not None:
        return request.build_absolute_uri(value)
    return value


def image_url(request, value):
    """Normalize image values from model fields or provider payloads."""
    if not value:
        return absolute_url(request, settings.IMG_NONE)
    return absolute_url(request, getattr(value, "url", value))


def media_ref_from_item(item):
    """Serialize an Item as a stable media ref."""
    if item is None:
        return None
    return {
        "item_id": item.id,
        "source": item.source,
        "media_type": item.media_type,
        "media_id": item.media_id,
        "season_number": item.season_number,
        "episode_number": item.episode_number,
    }


def find_item(ref):
    """Find an Item for a media ref if it has already been materialized."""
    return Item.objects.filter(
        source=ref["source"],
        media_type=ref["media_type"],
        media_id=ref["media_id"],
        season_number=ref.get("season_number"),
        episode_number=ref.get("episode_number"),
    ).first()


def get_or_create_item_from_metadata(ref, metadata):
    """Get or create an Item using provider metadata."""
    defaults = {
        "title": metadata.get("title") or metadata.get("name") or ref["media_id"],
        "image": metadata.get("image") or settings.IMG_NONE,
    }
    if ref["media_type"] == MediaTypes.BOOK.value:
        total_pages = metadata.get("total_pages") or metadata.get("max_progress")
        if total_pages:
            defaults["total_pages"] = total_pages
    item, _ = Item.objects.get_or_create(
        source=ref["source"],
        media_type=ref["media_type"],
        media_id=ref["media_id"],
        season_number=ref.get("season_number"),
        episode_number=ref.get("episode_number"),
        defaults=defaults,
    )
    if defaults.get("total_pages") and item.total_pages != defaults["total_pages"]:
        item.total_pages = defaults["total_pages"]
        item.save(update_fields=["total_pages"])
    return item


def media_summary_from_item(item, request=None, user=None):
    """Serialize an Item into the common media summary shape."""
    return {
        "ref": media_ref_from_item(item),
        "title": item.title,
        "subtitle": None,
        "overview": None,
        "image_url": image_url(request, item.image),
        "poster_accent_color": item.poster_accent_color or None,
        "release_date": None,
        "default_source": item.source,
        "user_state": user_state_for_item(user, item) if user else None,
    }


def synopsis_from_payload(payload):
    """Return provider synopsis text for API responses."""
    placeholder = "No synopsis available."
    for key in ("overview", "synopsis", "description"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text != placeholder:
            return text
    return None


def media_summary_from_provider(payload, media_type, source, request=None, user=None):
    """Serialize provider search/detail payload into the common summary shape."""
    media_id = str(payload.get("media_id") or payload.get("id") or "")
    season_number = payload.get("season_number")
    episode_number = payload.get("episode_number")
    item = Item.objects.filter(
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
        episode_number=episode_number,
    ).first()
    return {
        "ref": {
            "item_id": item.id if item else None,
            "source": source,
            "media_type": media_type,
            "media_id": media_id,
            "season_number": season_number,
            "episode_number": episode_number,
        },
        "title": payload.get("title") or payload.get("name") or "",
        "subtitle": payload.get("year") or payload.get("subtitle"),
        "overview": synopsis_from_payload(payload),
        "image_url": image_url(request, payload.get("image")),
        "poster_accent_color": getattr(item, "poster_accent_color", None) or None,
        "release_date": (
            payload.get("release_date")
            or payload.get("first_air_date")
            or payload.get("publish_date")
            or payload.get("end_date")
        ),
        "default_source": source,
        "user_state": user_state_for_item(user, item) if user and item else None,
    }


def details_for_api(metadata):
    """Return provider details with common top-level fields merged in."""
    details = dict(metadata.get("details") or {})
    genres = metadata.get("genres")
    if genres and "genres" not in details:
        names = []
        for genre in genres:
            name = genre.get("name") if isinstance(genre, dict) else genre
            if name:
                names.append(str(name))
        details["genres"] = names
    if metadata.get("time_to_beat") and "time_to_beat" not in details:
        details["time_to_beat"] = metadata["time_to_beat"]
    if metadata.get("revenue") and "revenue" not in details:
        details["revenue"] = metadata["revenue"]
    return details


def _credit_id(person):
    value = person.get("person_id") or person.get("id")
    return str(value or slugify(person.get("name") or "person"))


def _credit_image(request, person):
    value = person.get("image") or person.get("image_url") or person.get("profile_path")
    return image_url(request, value) if value else None


def cast_from_metadata(metadata, request=None):
    """Normalize provider cast into the native credit shape."""
    people = metadata.get("cast") or []
    return [
        {
            "id": _credit_id(person),
            "name": person.get("name"),
            "role": None,
            "character": person.get("character"),
            "image_url": _credit_image(request, person),
        }
        for person in people
        if isinstance(person, dict) and person.get("name")
    ]


def crew_from_metadata(metadata, request=None):
    """Normalize provider crew into the native credit shape."""
    people = metadata.get("crew") or []
    return [
        {
            "id": _credit_id(person),
            "name": person.get("name"),
            "role": (person.get("roles") or [None])[0] if person.get("roles") else person.get("job") or person.get("role"),
            "character": person.get("character"),
            "image_url": _credit_image(request, person),
        }
        for person in people
        if isinstance(person, dict) and person.get("name")
    ]


def seasons_from_metadata(metadata, request=None):
    """Normalize TV seasons into the native season summary shape."""
    seasons = (metadata.get("related") or {}).get("seasons") or []
    return [
        {
            "season_number": season.get("season_number"),
            "title": season.get("season_title") or season.get("title") or season.get("name") or "",
            "episode_count": season.get("episode_count") or season.get("episodes") or season.get("max_progress"),
            "image_url": image_url(request, season.get("image") or season.get("poster_path"))
            if (season.get("image") or season.get("poster_path"))
            else None,
            "release_date": season.get("first_air_date") or season.get("air_date") or season.get("release_date"),
        }
        for season in seasons
        if isinstance(season, dict)
    ]


def episodes_from_metadata(metadata, request=None):
    """Normalize season episodes into the native episode summary shape."""
    episodes = metadata.get("episodes") or []
    return [
        {
            "episode_number": episode.get("episode_number"),
            "title": episode.get("title") or episode.get("name") or "",
            "overview": episode.get("overview"),
            "air_date": episode.get("air_date"),
            "runtime": episode.get("runtime"),
            "image_url": image_url(request, episode.get("image") or episode.get("still_path"))
            if (episode.get("image") or episode.get("still_path"))
            else None,
            "rating": str(episode.get("vote_average")) if episode.get("vote_average") is not None else episode.get("rating"),
        }
        for episode in episodes
        if isinstance(episode, dict)
    ]


def custom_poster_url_for_user(user, ref, request=None):
    """Return a viewer's custom poster for an existing Item."""
    if not user or not user.is_authenticated:
        return None
    from app.models import CustomPosterPreference

    item = find_item(ref)
    if item is None:
        return None
    preference = CustomPosterPreference.objects.filter(user=user, item=item).first()
    return absolute_url(request, preference.custom_image_url) if preference else None


def custom_backdrop_url_for_user(user, ref, request=None):
    """Return a viewer's custom backdrop for an existing Item."""
    if not user or not user.is_authenticated:
        return None
    from app.models import CustomBackdropPreference

    item = find_item(ref)
    if item is None:
        return None
    preference = CustomBackdropPreference.objects.filter(user=user, item=item).first()
    return absolute_url(request, preference.custom_image_url) if preference else None


def related_sections_from_payload(related, media_type, source, request=None, user=None):
    """Normalize provider related media into mobile section cards."""
    if not related:
        return []

    if media_type == MediaTypes.BOOK.value:
        candidates = [
            ("other_editions", "Other Editions", related.get("other_editions") or []),
            ("recommendations", "Recommendations", related.get("recommendations") or []),
        ]
    elif media_type == MediaTypes.GAME.value:
        candidates = [
            (key, key.replace("_", " ").title(), related.get(key) or [])
            for key in (
                "dlcs",
                "expansions",
                "standalone_expansions",
                "remasters",
                "remakes",
                "expanded_games",
                "recommendations",
                "all_related",
            )
        ]
    else:
        candidates = [
            ("collection" if media_type == MediaTypes.MOVIE.value and key not in {"recommendations", "similar"} else key, key.replace("_", " ").title(), values)
            for key, values in related.items()
            if key not in {"seasons", "all_related"} and values
        ]

    sections = []
    for key, title, values in candidates:
        items = []
        for value in values[:7]:
            payload = value.get("item", value) if isinstance(value, dict) else value
            if not isinstance(payload, dict):
                continue
            item_media_type = payload.get("media_type", media_type)
            item_source = payload.get("source", source)
            summary = media_summary_from_provider(
                payload,
                item_media_type,
                item_source,
                request=request,
                user=user,
            )
            if (
                user
                and getattr(user, "hide_completed_recommendations", False)
                and key == "recommendations"
                and summary.get("user_state", {}).get("status") == "Completed"
            ):
                continue
            items.append(summary)
        if items:
            sections.append({"id": key, "title": title, "items": items})
    return sections


def user_state_for_item(user, item):
    """Return compact viewer-specific state for an item."""
    if not user or not user.is_authenticated or item is None:
        return None
    media_type = item.media_type
    queryset = BasicMedia.objects.filter_media(
        user,
        item.media_id,
        media_type,
        item.source,
        item.season_number,
        item.episode_number,
    )
    media = queryset.first()
    list_ids = list(
        CustomList.objects.filter(
            owner=user,
            items=item,
        ).values_list("id", flat=True),
    )
    latest_diary = DiaryEntry.objects.filter(user=user, item=item).order_by("-consumed_at").first()
    diary_state = {
        "diary_rating": decimal_string(latest_diary.rating) if latest_diary else None,
        "diary_consumed_at": latest_diary.consumed_at if latest_diary else None,
    }
    if media is None:
        return {"is_tracked": False, "status": None, "rating": None, "in_lists": list_ids, **diary_state}
    return {
        "is_tracked": True,
        "tracking_id": media.id,
        "status": getattr(media, "status", None),
        "rating": decimal_string(getattr(media, "score", None)),
        "in_lists": list_ids,
        **diary_state,
    }


def decimal_string(value):
    """Serialize decimals as stable strings."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def progress_for_media(media):
    """Return typed progress for a media instance."""
    media_type = media.item.media_type
    max_progress = getattr(media, "max_progress", None)
    value = getattr(media, "progress", 0)
    if media_type == MediaTypes.MOVIE.value:
        return {"kind": "binary", "value": 1 if media.end_date else 0, "max": 1, "unit": "movie"}
    if media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value):
        return {"kind": "episodes", "value": value, "max": max_progress, "unit": "episode"}
    if media_type == MediaTypes.GAME.value:
        return {"kind": "minutes", "value": value, "max": max_progress, "unit": "minute"}
    if media_type == MediaTypes.BOOK.value:
        snapshot = getattr(media, "progress_snapshot", None)
        if snapshot and snapshot.has_percentage and not snapshot.has_pages:
            return {"kind": "percentage", "value": snapshot.percentage, "max": 100, "unit": "percent"}
        return {
            "kind": "pages",
            "value": snapshot.pages if snapshot and snapshot.has_pages else value,
            "max": media.item.total_pages,
            "unit": "page",
        }
    return {
        "kind": "count",
        "value": value,
        "max": max_progress,
        "unit": config.get_unit(media_type, short=False).lower()
        if config.get_config(media_type) and config.get_config(media_type).get("unit")
        else media_type,
    }


def tracking_state(media):
    """Serialize any tracked media model into TrackingState."""
    return {
        "tracking_id": media.id,
        "status": getattr(media, "status", None),
        "rating": decimal_string(getattr(media, "score", None)),
        "progress": progress_for_media(media),
        "repeats": getattr(media, "repeats", 1),
        "start_date": getattr(media, "start_date", None),
        "end_date": getattr(media, "end_date", None),
        "notes": getattr(media, "notes", ""),
        "updated_at": getattr(media, "progressed_at", None) or getattr(media, "created_at", None),
    }


class UserSummarySerializer(serializers.Serializer):
    """Compact public user summary."""

    id = serializers.IntegerField()
    username = serializers.CharField()
    display_name = serializers.CharField()
    avatar_url = serializers.CharField(allow_null=True)


def user_summary(user, request=None):
    """Serialize a user for nested responses."""
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name or user.username,
        "avatar_url": image_url(request, user.profile_picture) if user.profile_picture else None,
    }
