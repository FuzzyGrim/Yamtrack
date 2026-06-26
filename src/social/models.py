from django.conf import settings
from django.db import models
from django.db.models import Q


class FollowStatus(models.TextChoices):
    """Status values for one-way follow relationships."""

    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"


class Visibility(models.TextChoices):
    """Visibility values shared by social surfaces."""

    PUBLIC = "public", "Public"
    FOLLOWERS = "followers", "Followers"
    PRIVATE = "private", "Private"
    UNLISTED = "unlisted", "Unlisted"


class Follow(models.Model):
    """One-way follow relationship between users."""

    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="following_edges",
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="follower_edges",
    )
    status = models.CharField(
        max_length=20,
        choices=FollowStatus.choices,
        default=FollowStatus.ACCEPTED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["from_user", "to_user"],
                name="social_follow_unique_from_to",
            ),
            models.CheckConstraint(
                condition=~Q(from_user=models.F("to_user")),
                name="social_follow_no_self_follow",
            ),
        ]
        indexes = [
            models.Index(fields=["from_user", "status", "-created_at"]),
            models.Index(fields=["to_user", "status", "-created_at"]),
        ]


class Block(models.Model):
    """A user block relationship."""

    blocker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="blocks_sent",
    )
    blocked = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="blocks_received",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["blocker", "blocked"],
                name="social_block_unique_blocker_blocked",
            ),
            models.CheckConstraint(
                condition=~Q(blocker=models.F("blocked")),
                name="social_block_no_self_block",
            ),
        ]
        indexes = [
            models.Index(fields=["blocker", "-created_at"]),
            models.Index(fields=["blocked", "-created_at"]),
        ]


class ContentLike(models.Model):
    """Like for a supported social target."""

    DIARY_ENTRY = "diary"
    CUSTOM_LIST = "list"

    TARGET_CHOICES = [
        (DIARY_ENTRY, "Diary entry"),
        (CUSTOM_LIST, "Custom list"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    target_type = models.CharField(max_length=20, choices=TARGET_CHOICES)
    target_id = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "target_type", "target_id"],
                name="social_contentlike_unique_user_target",
            ),
        ]
        indexes = [
            models.Index(fields=["target_type", "target_id", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
        ]


class Activity(models.Model):
    """Materialized activity item for reverse-chronological feeds."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="activities",
    )
    verb = models.CharField(max_length=50)
    target_type = models.CharField(max_length=50)
    target_id = models.PositiveBigIntegerField()
    item = models.ForeignKey(
        "app.Item",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activities",
    )
    visibility = models.CharField(
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
    )
    snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["-created_at", "-id"]),
            models.Index(fields=["actor", "-created_at", "-id"]),
            models.Index(fields=["item", "-created_at", "-id"]),
        ]


class ProgressChange(models.Model):
    """Durable progress delta for tracked media."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="progress_changes",
    )
    item = models.ForeignKey(
        "app.Item",
        on_delete=models.CASCADE,
        related_name="progress_changes",
    )
    previous_progress = models.JSONField()
    current_progress = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["actor", "item", "-created_at"]),
            models.Index(fields=["-created_at", "id"]),
        ]


class SocialAuditLog(models.Model):
    """Audit log for important social actions."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="social_audit_actions",
    )
    action = models.CharField(max_length=80)
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="social_audit_targets",
    )
    target_type = models.CharField(max_length=50, blank=True, default="")
    target_id = models.PositiveBigIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["actor", "-created_at"]),
            models.Index(fields=["target_user", "-created_at"]),
            models.Index(fields=["action", "-created_at"]),
        ]
