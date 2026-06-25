import contextlib

from django.apps import apps
from django.contrib import admin
from django.contrib.admin.sites import AlreadyRegistered

from app.models import (
    AnimeFranchiseDiscoveredEntry,
    AnimeFranchiseDiscoveryState,
    AnimeFranchiseMaintenanceScanState,
    AnimeImportScanState,
    AnimeSeriesViewMembership,
    Episode,
    Item,
    UserMessage,
)


# Custom ModelAdmin classes with search functionality
@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    """Custom admin for Item model with search and filter options."""

    search_fields = ["title", "media_id", "source"]
    list_display = [
        "title",
        "media_id",
        "season_number",
        "episode_number",
        "media_type",
        "source",
    ]
    list_filter = ["media_type", "source"]


@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    """Custom admin for Episode model with search and filter options."""

    search_fields = ["item__title", "related_season__item__title"]
    list_display = ["__str__", "end_date"]


@admin.register(UserMessage)
class UserMessageAdmin(admin.ModelAdmin):
    """Custom admin for persistent user messages."""

    search_fields = ["user__username", "message"]
    list_display = ["message", "level", "user", "created_at", "shown_at"]
    list_filter = ["level", "shown_at"]


@admin.register(AnimeFranchiseDiscoveryState)
class AnimeFranchiseDiscoveryStateAdmin(admin.ModelAdmin):
    """Admin config for MAL anime franchise discovery state rows."""

    search_fields = ["user__username", "component_root_mal_id"]
    list_display = [
        "user",
        "component_root_mal_id",
        "baseline_completed_at",
        "last_scanned_at",
        "last_seen_count",
    ]


@admin.register(AnimeFranchiseDiscoveredEntry)
class AnimeFranchiseDiscoveredEntryAdmin(admin.ModelAdmin):
    """Admin config for discovered MAL anime franchise entries."""

    search_fields = ["user__username", "discovered_media_id", "title", "root_title"]
    list_display = [
        "title",
        "user",
        "component_root_mal_id",
        "discovered_media_id",
        "section_key",
        "notified_at",
    ]
    list_filter = ["section_key", "notification_suppressed_reason"]


@admin.register(AnimeFranchiseMaintenanceScanState)
class AnimeFranchiseMaintenanceScanStateAdmin(admin.ModelAdmin):
    search_fields = ["user__username", "seed_mal_id", "component_root_mal_id"]
    list_display = [
        "user",
        "seed_mal_id",
        "component_root_mal_id",
        "next_scan_at",
        "last_scanned_at",
        "last_success_at",
        "last_error_at",
    ]
    list_filter = ["last_error_at"]


@admin.register(AnimeImportScanState)
class AnimeImportScanStateAdmin(admin.ModelAdmin):
    """Admin config for anime import scan state rows."""

    search_fields = ["user__username", "seed_mal_id"]
    list_display = [
        "user",
        "seed_mal_id",
        "profile_key",
        "next_scan_at",
        "last_scanned_at",
        "last_success_at",
        "last_error_at",
    ]
    list_filter = ["profile_key"]


@admin.register(AnimeSeriesViewMembership)
class AnimeSeriesViewMembershipAdmin(admin.ModelAdmin):
    """Admin config for Anime Series View read-model rows."""

    search_fields = [
        "user__username",
        "media_id",
        "root_media_id",
        "display_media_id",
        "display_title",
    ]
    list_display = [
        "user",
        "media_id",
        "root_media_id",
        "group_kind",
        "projection_version",
        "updated_at",
    ]
    list_filter = ["group_kind", "projection_version"]


class MediaAdmin(admin.ModelAdmin):
    """Custom admin for regular media model with search and filter options."""

    search_fields = ["item__title", "user__username", "notes"]
    list_display = ["__str__", "status", "score", "user"]
    list_filter = ["status"]


# Register models with custom admin classes


# Auto-register remaining models
app_models = apps.get_app_config("app").get_models()
SpecialModels = [
    "Item",
    "Episode",
    "BasicMedia",
    "UserMessage",
    "AnimeImportScanState",
    "AnimeFranchiseDiscoveryState",
    "AnimeFranchiseDiscoveredEntry",
    "AnimeFranchiseMaintenanceScanState",
    "AnimeSeriesViewMembership",
]
for model in app_models:
    if (
        not model.__name__.startswith("Historical")
        and model.__name__ not in SpecialModels
    ):
        with contextlib.suppress(AlreadyRegistered):
            admin.site.register(model, MediaAdmin)
