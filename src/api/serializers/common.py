from decimal import Decimal

from django.conf import settings
from rest_framework import serializers

from app import config
from app.models import BasicMedia, Item, MediaTypes
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


def related_sections_from_payload(related, media_type, source, request=None, user=None):
    """Normalize provider related media into mobile section cards."""
    if not related:
        return []

    if media_type == MediaTypes.BOOK.value:
        candidates = [("recommendations", "Recommendations", related.get("recommendations") or [])]
    elif media_type == MediaTypes.GAME.value:
        candidates = [("all_related", "Related", related.get("all_related") or [])]
    else:
        candidates = [
            (key, key.replace("_", " ").title(), values)
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
    if media is None:
        return {"is_tracked": False, "status": None, "rating": None, "in_lists": list_ids}
    return {
        "is_tracked": True,
        "tracking_id": media.id,
        "status": getattr(media, "status", None),
        "rating": decimal_string(getattr(media, "score", None)),
        "in_lists": list_ids,
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
