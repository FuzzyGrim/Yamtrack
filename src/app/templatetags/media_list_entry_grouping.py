"""Template state for the media-list entry-grouping control."""

from django import template

from app.media_list_entry_grouping import (
    preference_field,
    show_separate_entries,
)

register = template.Library()


@register.simple_tag
def media_list_entry_grouping_control(request):
    """Return the supported state for the current media-list route."""
    resolver_match = request.resolver_match
    media_type = (
        resolver_match.kwargs.get("media_type")
        if resolver_match is not None
        else None
    )
    supported = preference_field(media_type) is not None
    enabled = supported and show_separate_entries(request.user, media_type)
    return {
        "enabled": enabled,
        "next_value": "false" if enabled else "true",
        "state_label": "On" if enabled else "Off",
        "supported": supported,
    }


@register.simple_tag
def library_settings_menu_props(request, entry_grouping, enable_bulk_select):
    """Props for the React settings menu: its labels and the entry-grouping form."""
    from django.middleware.csrf import get_token
    from django.urls import reverse
    from django.utils.translation import gettext as _

    resolver_match = request.resolver_match
    media_type = (
        resolver_match.kwargs.get("media_type")
        if resolver_match is not None
        else None
    )
    props = {
        "labels": {
            "settings": _("View settings"),
            "grid": _("Grid View"),
            "table": _("Table View"),
            "save": _("Save View"),
            "select": _("Select Items"),
            "cancelSelect": _("Cancel Select"),
            "share": _("Share View"),
            "sharing": _("Sharing…"),
            "entryGrouping": _("Show matching entries separately"),
            "close": _("Close menu"),
        },
        "bulkSelect": bool(enable_bulk_select),
        "entryGrouping": None,
    }
    if entry_grouping["supported"]:
        props["entryGrouping"] = {
            "action": reverse("medialist_entry_grouping", args=[media_type]),
            "nextValue": entry_grouping["next_value"],
            "stateLabel": _(entry_grouping["state_label"]),
            "csrfToken": get_token(request),
            "next": request.get_full_path(),
        }
    return props
