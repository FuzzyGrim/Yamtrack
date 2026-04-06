from django.contrib import admin

from lists.models import CustomList, CustomListItem


@admin.register(CustomList)
class CustomListAdmin(admin.ModelAdmin):
    """Admin configuration for CustomList model."""

    search_fields = ["name", "description", "owner__username"]
    list_display = ["name", "owner", "item_count", "get_last_update"]
    list_filter = ["owner"]
    raw_id_fields = ["owner"]
    autocomplete_fields = ["collaborators"]
    filter_horizontal = ["collaborators"]

    @admin.display(description="Number of items")
    def item_count(self, obj):
        """Return the number of items in the list."""
        return obj.items.count()

    @admin.display(description="Last updated")
    def get_last_update(self, obj):
        """Return the date of the last item added."""
        last_update = CustomListItem.objects.get_last_added_date(obj)
        return last_update or "-"


@admin.register(CustomListItem)
class CustomListItemAdmin(admin.ModelAdmin):
    """Admin configuration for CustomListItem model."""

    search_fields = ["item__title", "custom_list__name", "item__media_id"]
    list_display = ["item", "custom_list", "date_added", "get_media_type"]
    list_filter = ["custom_list", "item__media_type", "custom_list__owner"]
    raw_id_fields = ["item", "custom_list"]
    autocomplete_fields = ["item", "custom_list"]
    readonly_fields = ["date_added"]

    @admin.display(description="Media Type")
    def get_media_type(self, obj):
        """Return the media type of the item."""
        return obj.item.get_media_type_display()
