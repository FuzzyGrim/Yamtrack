import contextlib

from django.apps import apps
from django.contrib import admin
from django.contrib.admin.sites import AlreadyRegistered

from app.models import (
    Tag,
    TaggedMedia,
    Episode,
    Item,
    UserMessage,
    CustomLink,
    CategoryLink,
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




@admin.register(CustomLink)
class CustomLinkAdmin(admin.ModelAdmin):
    """Admin for user custom links."""

    search_fields = ["label", "url", "user__username"]
    list_display = ["label", "url", "user", "content_type", "object_id"]
    list_filter = ["content_type"]


@admin.register(CategoryLink)
class CategoryLinkAdmin(admin.ModelAdmin):
    """Admin for per-user category links."""

    search_fields = ["label", "url", "user__username"]
    list_display = ["label", "url", "user", "media_type"]
    list_filter = ["media_type"]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    """Admin for user tags."""

    search_fields = ["name", "normalized_name", "user__username"]
    list_display = ["name", "user", "created_at"]


@admin.register(TaggedMedia)
class TaggedMediaAdmin(admin.ModelAdmin):
    """Admin for tagged media relations."""

    search_fields = ["tag__name", "user__username"]
    list_display = ["tag", "user", "content_type", "object_id", "created_at"]
    list_filter = ["content_type"]

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
    "CustomLink",
    "CategoryLink",
    "Tag",
    "TaggedMedia",
    "ExperienceVisit",
]
for model in app_models:
    if (
        not model.__name__.startswith("Historical")
        and model.__name__ not in SpecialModels
    ):
        with contextlib.suppress(AlreadyRegistered):
            admin.site.register(model, MediaAdmin)
