from django.db.models import Count, Q
from django.utils import timezone

from api.serializers.common import (
    get_or_create_item_from_metadata,
    media_summary_from_item,
    user_summary,
)
from app.models import Tag
from app.providers import services as provider_services
from app.services import create_diary_entry, update_diary_entry_tags
from social.models import Activity, ContentLike


def diary_payload(entry, request=None, viewer=None):
    """Serialize a diary entry for the API."""
    like_count = ContentLike.objects.filter(
        target_type=ContentLike.DIARY_ENTRY,
        target_id=entry.id,
    ).count()
    viewer_has_liked = (
        viewer
        and viewer.is_authenticated
        and ContentLike.objects.filter(
            user=viewer,
            target_type=ContentLike.DIARY_ENTRY,
            target_id=entry.id,
        ).exists()
    )
    return {
        "id": entry.id,
        "user": user_summary(entry.user, request=request),
        "media": media_summary_from_item(entry.item, request=request),
        "consumed_at": entry.consumed_at,
        "rating": str(entry.rating) if entry.rating is not None else None,
        "review_title": entry.review_title,
        "review": entry.review,
        "contains_spoilers": entry.contains_spoilers,
        "liked": entry.liked,
        "is_rewatch": entry.is_rewatch,
        "tags": [tag.name for tag in entry.tags.all()],
        "visibility": entry.visibility,
        "like_count": like_count,
        "viewer_has_liked": bool(viewer_has_liked),
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


def create_entry(user, data):
    """Create a diary entry from API payload."""
    ref = data["ref"]
    metadata = provider_services.get_media_metadata(
        ref["media_type"],
        ref["media_id"],
        ref["source"],
        [ref.get("season_number")] if ref.get("season_number") is not None else None,
        ref.get("episode_number"),
    )
    item = get_or_create_item_from_metadata(ref, metadata)
    entry = create_diary_entry(
        user=user,
        item=item,
        consumed_at=data.get("consumed_at") or timezone.now(),
        rating=data.get("rating"),
        review=data.get("review", ""),
        liked=data.get("liked", False),
        is_rewatch=data.get("is_rewatch", False),
        auto_mark_consumed=data.get("auto_mark_consumed", False),
        tags=data.get("tags", []),
    )
    entry.visibility = data.get("visibility", "public")
    entry.contains_spoilers = data.get("contains_spoilers", False)
    entry.review_title = data.get("review_title", "")
    entry.save(update_fields=["visibility", "contains_spoilers", "review_title", "updated_at"])
    Activity.objects.create(
        actor=user,
        verb="diary_created",
        target_type="diary",
        target_id=entry.id,
        item=item,
        visibility=entry.visibility,
        snapshot={"rating": str(entry.rating) if entry.rating is not None else None},
    )
    return entry


def update_entry(entry, data):
    """Update a diary entry from API payload."""
    for field in [
        "consumed_at",
        "rating",
        "review",
        "review_title",
        "liked",
        "is_rewatch",
        "contains_spoilers",
        "visibility",
    ]:
        if field in data:
            setattr(entry, field, data[field])
    entry.save()
    if "tags" in data:
        update_diary_entry_tags(entry, data["tags"])
    return entry


def tag_results(query, user=None):
    """Return tag search results."""
    queryset = Tag.objects.all()
    ordering = ["-usage_count", "name"]
    if user is not None:
        queryset = (
            queryset.filter(diary_entries__user=user)
            .annotate(user_usage_count=Count("diary_entries", filter=Q(diary_entries__user=user)))
            .distinct()
        )
        ordering = ["-user_usage_count", "name"]
    if query:
        queryset = queryset.filter(name__icontains=query)
    return [
        {"name": tag.name, "usage_count": getattr(tag, "user_usage_count", tag.usage_count)}
        for tag in queryset.order_by(*ordering)[:10]
    ]
