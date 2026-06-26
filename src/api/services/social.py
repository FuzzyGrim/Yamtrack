from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404

from api.permissions import can_view_user_profile
from api.serializers.common import media_summary_from_item, user_summary
from app.models import DiaryEntry
from lists.models import CustomList
from social.models import (
    Activity,
    Block,
    ContentLike,
    Follow,
    FollowStatus,
    SocialAuditLog,
)


def follow_user(actor, username):
    """Follow or request to follow a user."""
    User = get_user_model()
    target = get_object_or_404(User, username=username)
    status = FollowStatus.PENDING if target.profile_private else FollowStatus.ACCEPTED
    follow, _ = Follow.objects.update_or_create(
        from_user=actor,
        to_user=target,
        defaults={"status": status},
    )
    SocialAuditLog.objects.create(
        actor=actor,
        action="follow" if status == FollowStatus.ACCEPTED else "follow_request",
        target_user=target,
    )
    return follow


def unfollow_user(actor, username):
    """Remove a follow edge."""
    User = get_user_model()
    target = get_object_or_404(User, username=username)
    Follow.objects.filter(from_user=actor, to_user=target).delete()
    SocialAuditLog.objects.create(actor=actor, action="unfollow", target_user=target)


def block_user(actor, username):
    """Block a user and remove follow edges both ways."""
    User = get_user_model()
    target = get_object_or_404(User, username=username)
    with transaction.atomic():
        block, _ = Block.objects.get_or_create(blocker=actor, blocked=target)
        Follow.objects.filter(from_user__in=[actor, target], to_user__in=[actor, target]).delete()
        SocialAuditLog.objects.create(actor=actor, action="block", target_user=target)
    return block


def unblock_user(actor, username):
    """Remove a block."""
    User = get_user_model()
    target = get_object_or_404(User, username=username)
    Block.objects.filter(blocker=actor, blocked=target).delete()
    SocialAuditLog.objects.create(actor=actor, action="unblock", target_user=target)


def set_like(user, *, target_type, target_id, liked):
    """Create/delete a like for a supported target."""
    if target_type == ContentLike.DIARY_ENTRY:
        get_object_or_404(DiaryEntry, id=target_id)
    elif target_type == ContentLike.CUSTOM_LIST:
        get_object_or_404(CustomList, id=target_id)
    if liked:
        ContentLike.objects.get_or_create(
            user=user,
            target_type=target_type,
            target_id=target_id,
        )
        action = "like"
    else:
        ContentLike.objects.filter(
            user=user,
            target_type=target_type,
            target_id=target_id,
        ).delete()
        action = "unlike"
    SocialAuditLog.objects.create(
        actor=user,
        action=action,
        target_type=target_type,
        target_id=target_id,
    )
    return {
        "liked": liked,
        "like_count": ContentLike.objects.filter(
            target_type=target_type,
            target_id=target_id,
        ).count(),
    }


def feed_queryset(user):
    """Return activities visible in the current user's feed."""
    following = Follow.objects.filter(
        from_user=user,
        status=FollowStatus.ACCEPTED,
    ).values("to_user")
    return Activity.objects.filter(actor__in=following).select_related("actor", "item")


def user_activity_queryset(viewer, target_user):
    """Return visible activity for a profile."""
    if not can_view_user_profile(viewer, target_user):
        return Activity.objects.none()
    return Activity.objects.filter(actor=target_user).select_related("actor", "item")


def activity_payload(activity, request=None, viewer=None):
    """Serialize a materialized feed item."""
    snapshot = activity.snapshot or {}
    object_payload = {
        "type": activity.target_type,
        "id": activity.target_id,
        **snapshot,
    }
    if activity.verb == "progress_updated":
        object_payload = {
            "type": activity.target_type,
            "id": activity.target_id,
            "previous": snapshot.get("previous"),
            "current": snapshot.get("current"),
        }
    return {
        "id": activity.id,
        "type": activity.verb,
        "created_at": activity.created_at,
        "actor": user_summary(activity.actor, request=request),
        "media": media_summary_from_item(activity.item, request=request) if activity.item else None,
        "object": object_payload,
        "viewer": {
            "can_view": True,
            "has_liked": bool(
                viewer
                and viewer.is_authenticated
                and ContentLike.objects.filter(
                    user=viewer,
                    target_type=activity.target_type,
                    target_id=activity.target_id,
                ).exists()
            ),
        },
    }
