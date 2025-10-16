import contextlib

from django.apps import apps
from django.contrib import admin
from django.contrib.admin.sites import AlreadyRegistered

from app.models import (
    Episode,
    Item,
    CustomPosterPreference,
    DiaryEntry,
    Tag,
    DiaryEntryTag,
)


# Custom ModelAdmin classes with search functionality
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


class EpisodeAdmin(admin.ModelAdmin):
    """Custom admin for Episode model with search and filter options."""

    search_fields = ["item__title", "related_season__item__title"]
    list_display = ["__str__", "end_date"]


class MediaAdmin(admin.ModelAdmin):
    """Custom admin for regular media model with search and filter options."""

    search_fields = ["item__title", "user__username", "notes"]
    list_display = ["__str__", "status", "score", "user"]
    list_filter = ["status"]


class CustomPosterPreferenceAdmin(admin.ModelAdmin):
    """Custom admin for CustomPosterPreference model."""
    
    search_fields = ["item__title", "user__username"]
    list_display = ["__str__", "user", "item", "updated_at"]
    list_filter = ["user"]


class DiaryEntryAdmin(admin.ModelAdmin):
    """Custom admin for DiaryEntry model with search and filter options."""
    
    search_fields = ["item__title", "user__username", "review"]
    list_display = ["__str__", "user", "consumed_at", "rating"]
    list_filter = ["user", "consumed_at"]


class TagAdmin(admin.ModelAdmin):
    """Custom admin for Tag model with search and filter options."""
    
    search_fields = ["name"]
    list_display = ["name", "usage_count", "created_at"]
    list_filter = ["created_at"]
    ordering = ["-usage_count", "name"]


class DiaryEntryTagAdmin(admin.ModelAdmin):
    """Custom admin for DiaryEntryTag model with search and filter options."""
    
    search_fields = ["diary_entry__item__title", "tag__name", "diary_entry__user__username"]
    list_display = ["__str__", "diary_entry", "tag", "created_at"]
    list_filter = ["tag", "created_at"]


# Register models with custom admin classes
admin.site.register(Item, ItemAdmin)
admin.site.register(Episode, EpisodeAdmin)
admin.site.register(CustomPosterPreference, CustomPosterPreferenceAdmin)
admin.site.register(DiaryEntry, DiaryEntryAdmin)
admin.site.register(Tag, TagAdmin)
admin.site.register(DiaryEntryTag, DiaryEntryTagAdmin)


# Auto-register remaining models
app_models = apps.get_app_config("app").get_models()
SpecialModels = ["Item", "Episode", "BasicMedia", "CustomPosterPreference", "DiaryEntry", "Tag", "DiaryEntryTag"]
for model in app_models:
    if (
        not model.__name__.startswith("Historical")
        and model.__name__ not in SpecialModels
    ):
        with contextlib.suppress(AlreadyRegistered):
            admin.site.register(model, MediaAdmin)
