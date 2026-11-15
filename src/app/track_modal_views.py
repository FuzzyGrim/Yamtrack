import json
import logging
from contextlib import suppress
from datetime import UTC, date
from urllib.parse import urlencode
from uuid import uuid4

from django import forms
from django.apps import apps
from django.conf import settings
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.timezone import datetime
from django.utils.translation import gettext, gettext_noop, ngettext, pgettext
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from app import cover_views, custom_metadata, helpers, history_processor
from app import statistics as stats
from app.collection_views import build_collection_modal_context
from app.discover_views import _build_track_modal_discover_tab_context
from app.forms import BulkEpisodeTrackForm, get_form_class
from app.icon_specs import file_icon
from app.models import (
    Anime,
    BasicMedia,
    HardcoverEditionPreference,
    Item,
    MediaTypes,
    MetadataProviderPreference,
    Sources,
    Status,
)
from app.providers import hardcover, services, tmdb
from app.services import bulk_episode_tracking, library_migration, metadata_resolution
from app.services.metadata_fallback import stored_metadata_fallback
from users.templatetags.user_tags import user_datetime_format

RUNTIME_UNKNOWN_AIRED = 999998  # aired but runtime unknown
logger = logging.getLogger(__name__)


class _EmptyHistoryProxy:
    """Minimal queryset-like history object for empty podcast wrappers."""

    def all(self):
        return []

    def count(self):
        return 0

    def filter(self, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self


class _DummyPodcastWrapper:
    """Template-compatible podcast wrapper when no plays exist yet."""

    def __init__(self, item):
        self.item = item
        self.id = 0
        self.in_progress_instance_id = None
        self.history = _EmptyHistoryProxy()

    @property
    def completed_play_count(self):
        return 0

    @property
    def has_in_progress_entry(self):
        return False


def _bulk_episode_form_initial_data(return_url, domain):
    """Return initial form values for the bulk episode-play tab."""
    now = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
    date_initial = now if settings.TRACK_TIME else now.date()

    return {
        "media_id": domain["tracking_media_id"],
        "source": domain["tracking_source"],
        "media_type": domain["route_media_type"],
        "identity_media_type": domain.get("identity_media_type") or "",
        "library_media_type": domain.get("library_media_type") or "",
        "instance_id": "",
        "return_url": return_url,
        "context_kind": domain.get("context_kind") or "",
        "context_id": domain.get("context_id") or "",
        "first_season_number": domain["default_first"]["season_number"],
        "first_episode_number": domain["default_first"]["episode_number"],
        "last_season_number": domain["default_last"]["season_number"],
        "last_episode_number": domain["default_last"]["episode_number"],
        "write_mode": BulkEpisodeTrackForm.WRITE_MODE_ADD,
        "distribution_mode": BulkEpisodeTrackForm.DISTRIBUTION_MODE_AIR_DATE,
        "start_date": date_initial,
        "end_date": date_initial,
    }


def _episode_domain_template_payload(domain):
    """Return the JSON-friendly episode selector payload for Alpine."""
    if not domain:
        return None

    season_episode_map = {}
    for season_number, episodes in domain["season_episode_map"].items():
        season_episode_map[str(season_number)] = [
            {
                "order": episode["order"],
                "season_number": episode["season_number"],
                "episode_number": episode["episode_number"],
                "episode_title": episode["episode_title"],
                "selector_label": episode.get("selector_label", ""),
                "existing_play_count": episode["existing_play_count"],
                "runtime_minutes": episode.get("runtime_minutes") or "",
                "air_date": episode["air_date"].isoformat()
                if episode["air_date"]
                else "",
            }
            for episode in episodes
        ]

    return {
        "seasons": domain["seasons"],
        "seasonEpisodeMap": season_episode_map,
        "defaultFirst": domain["default_first"],
        "defaultLast": domain["default_last"],
        "lockedSeasonNumber": domain["locked_season_number"],
        "hideSeasonSelectors": domain.get("hide_season_selectors", False),
        "firstSelectionTitle": domain.get("first_selection_title", ""),
        "lastSelectionTitle": domain.get("last_selection_title", ""),
        "seasonFieldLabel": domain.get("season_field_label", ""),
        "episodeFieldLabel": domain.get("episode_field_label", ""),
        "selectionNoun": domain.get("selection_noun", ""),
        "selectionNounPlural": domain.get("selection_noun_plural", ""),
        "distributionTargetLabel": domain.get("distribution_target_label", ""),
        "missingTargetDateFallbackDistribution": domain.get(
            "missing_target_date_fallback_distribution",
            "",
        ),
        "dateShortcutLabel": domain.get("date_shortcut_label", ""),
        "modeNotice": domain.get("mode_notice", ""),
    }


def _bulk_episode_form_labels():
    """Return the translated labels used by the shared bulk-play form."""
    return {
        "startDate": str(gettext("Start date")),
        "endDate": str(gettext("End date")),
        "playHandling": str(gettext("Play handling")),
        "distribution": str(gettext("Distribution")),
        "save": str(gettext("Save plays")),
        "saving": str(gettext("Saving")),
        "justFinished": str(pgettext("date picker shortcut", "Just Finished")),
        "chooseRange": str(
            gettext("Choose the first and last item to log a bulk play range."),
        ),
        "invalidRange": str(gettext("Choose a valid ordered range to log plays.")),
        "lockedRange": str(
            gettext("This range is locked to one mapped season."),
        ),
        "targetDates": str(
            gettext("the target dates within the selected date range"),
        ),
        "evenDistribution": str(
            gettext("an even distribution across the date range"),
        ),
        "fallbackEven": str(
            gettext("Saving will fall back to even distribution."),
        ),
        "noExisting": str(
            gettext("No existing plays are currently logged in the selected range."),
        ),
        "noTargetDateOne": str(
            ngettext(
                "%(count)s selected item has no target date.",
                "%(count)s selected items have no target date.",
                1,
            ),
        ),
        "noTargetDateMany": str(
            ngettext(
                "%(count)s selected item has no target date.",
                "%(count)s selected items have no target date.",
                2,
            ),
        ),
        "addOne": str(
            ngettext(
                "This will add plays for %(count)s ordered item using %(distribution)s.",
                "This will add plays for %(count)s ordered items using %(distribution)s.",
                1,
            ),
        ),
        "addMany": str(
            ngettext(
                "This will add plays for %(count)s ordered item using %(distribution)s.",
                "This will add plays for %(count)s ordered items using %(distribution)s.",
                2,
            ),
        ),
        "replaceOne": str(
            ngettext(
                "This will replace plays for %(count)s ordered item using %(distribution)s.",
                "This will replace plays for %(count)s ordered items using %(distribution)s.",
                1,
            ),
        ),
        "replaceMany": str(
            ngettext(
                "This will replace plays for %(count)s ordered item using %(distribution)s.",
                "This will replace plays for %(count)s ordered items using %(distribution)s.",
                2,
            ),
        ),
        "existingReplaceOne": str(
            ngettext(
                "This will delete %(count)s existing play in the selected range before adding the new ordered pass.",
                "This will delete %(count)s existing plays in the selected range before adding the new ordered pass.",
                1,
            ),
        ),
        "existingReplaceMany": str(
            ngettext(
                "This will delete %(count)s existing play in the selected range before adding the new ordered pass.",
                "This will delete %(count)s existing plays in the selected range before adding the new ordered pass.",
                2,
            ),
        ),
        "existingAppendOne": str(
            ngettext(
                "This range already has %(count)s logged play. New plays will be appended in order.",
                "This range already has %(count)s logged plays. New plays will be appended in order.",
                1,
            ),
        ),
        "existingAppendMany": str(
            ngettext(
                "This range already has %(count)s logged play. New plays will be appended in order.",
                "This range already has %(count)s logged plays. New plays will be appended in order.",
                2,
            ),
        ),
    }


def _bulk_episode_presentation(
    request,
    form,
    domain,
    *,
    track_form_id,
    formaction,
    return_url,
    submit_label,
):
    """Serialize the bulk episode-play range form into shared presentation."""
    if form is None or not domain:
        return None
    track_time = bool(settings.TRACK_TIME)
    hidden_names = (
        "media_id",
        "source",
        "media_type",
        "identity_media_type",
        "library_media_type",
        "instance_id",
        "return_url",
        "context_kind",
        "context_id",
    )
    hidden = {
        name: str(form[name].value() or "")
        for name in hidden_names
        if name in form.fields
    }
    values = {
        "first_season_number": str(form["first_season_number"].value() or ""),
        "first_episode_number": str(form["first_episode_number"].value() or ""),
        "last_season_number": str(form["last_season_number"].value() or ""),
        "last_episode_number": str(form["last_episode_number"].value() or ""),
        "write_mode": str(form["write_mode"].value() or ""),
        "distribution_mode": str(form["distribution_mode"].value() or ""),
        "start_date": _track_form_field_value(form["start_date"], track_time),
        "end_date": _track_form_field_value(form["end_date"], track_time),
        "episode_count": "",
    }
    action = formaction
    if return_url:
        action = f"{action}?next={return_url}"
    errors = {
        name: " ".join(str(message) for message in messages)
        for name, messages in form.errors.items()
        if name != "__all__"
    }
    non_field_errors = [
        str(message) for message in form.non_field_errors()
    ]
    if non_field_errors:
        errors["non_field"] = " ".join(non_field_errors)
    return {
        "formId": f"{track_form_id}-episode-plays-form",
        "action": action,
        "csrfToken": get_token(request),
        "trackTime": track_time,
        "hidden": hidden,
        "values": values,
        "domain": _episode_domain_template_payload(domain) or {},
        "seasonOptions": [
            {
                "value": str(season["season_number"]),
                "label": str(season["season_title"]),
            }
            for season in domain.get("seasons", [])
        ],
        "writeModeOptions": [
            {"value": str(value), "label": str(label)}
            for value, label in form.fields["write_mode"].choices
        ],
        "distributionModeOptions": [
            {"value": str(value), "label": str(label)}
            for value, label in form.fields["distribution_mode"].choices
        ],
        "submitLabel": str(submit_label),
        "notice": str(domain.get("mode_notice") or ""),
        "errors": errors,
        "labels": _bulk_episode_form_labels(),
    }


def _track_modal_field_groups(form, *, hidden_field_names, metadata_field_names=None):
    """Split a track form into hidden, general, and metadata field groups."""
    metadata_field_names = metadata_field_names or set()
    ordered_general_field_names = [
        field_name
        for field_name in (
            "score",
            "status",
            "progress",
            "reading_time",
            "start_date",
            "end_date",
        )
        if field_name in form.fields
    ]
    remaining_general_field_names = [
        field_name
        for field_name in form.fields
        if field_name not in hidden_field_names
        and field_name not in metadata_field_names
        and field_name != "notes"
        and field_name not in ordered_general_field_names
    ]
    return {
        "general_fields": [
            form[field_name]
            for field_name in ordered_general_field_names
            + remaining_general_field_names
        ],
        "metadata_fields": [
            form[field_name]
            for field_name in form.fields
            if field_name in metadata_field_names
        ],
        "hidden_fields": [
            form[field_name]
            for field_name in form.fields
            if field_name in hidden_field_names
        ],
    }


def _track_form_date_value(value, track_time):
    """Return a date or datetime value the shared picker can parse."""
    if isinstance(value, datetime):
        pattern = "%Y-%m-%dT%H:%M" if track_time else "%Y-%m-%d"
        return value.strftime(pattern)
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return ""
    if not track_time:
        return text.split("T")[0][:10]
    if "T" in text:
        return text[:16]
    if " " in text:
        return text.replace(" ", "T")[:16]
    return text[:10]


def _track_form_field_value(field, track_time):
    value = field.value()
    if field.name in {"start_date", "end_date"}:
        return _track_form_date_value(value, track_time)
    return "" if value is None else str(value)


def _track_form_field_type(field):
    widget = field.field.widget
    if isinstance(widget, forms.Select):
        return "select"
    if field.name in {"start_date", "end_date"} or isinstance(
        widget,
        (forms.DateInput, forms.DateTimeInput),
    ):
        return "date"
    if isinstance(widget, forms.Textarea):
        return "textarea"
    if getattr(widget, "input_type", "") == "number":
        return "number"
    return "text"


def _track_form_field_options(field):
    options = []
    for value, label in getattr(field.field, "choices", None) or []:
        if isinstance(label, (list, tuple)):
            for option_value, option_label in label:
                options.append(
                    {"value": str(option_value), "label": str(option_label)},
                )
        else:
            options.append({"value": str(value), "label": str(label)})
    return options


def _track_form_field_payload(field, track_time):
    attrs = field.field.widget.attrs
    field_type = _track_form_field_type(field)
    return {
        "name": field.name,
        "label": str(field.label),
        "type": field_type,
        "value": _track_form_field_value(field, track_time),
        "required": bool(field.field.required),
        "options": _track_form_field_options(field) if field_type == "select" else [],
        "help": str(field.help_text or ""),
        "min": None if attrs.get("min") is None else str(attrs.get("min")),
        "max": None if attrs.get("max") is None else str(attrs.get("max")),
        "step": None if attrs.get("step") is None else str(attrs.get("step")),
        "placeholder": str(attrs.get("placeholder") or ""),
    }


def _track_form_labels():
    return {
        "title": str(gettext("Tracking")),
        "close": str(gettext("Close")),
        "loading": str(gettext("Loading")),
        "loadFailed": str(gettext("Couldn't load this form.")),
        "retry": str(gettext("Try again")),
        "save": str(gettext("Save")),
        "saving": str(gettext("Saving")),
        "delete": str(gettext("Delete")),
        "required": str(gettext("%s is required.")),
        "invalidNumber": str(gettext("Enter a number.")),
        "minimum": str(gettext("Must be at least %s.")),
        "maximum": str(gettext("Must be at most %s.")),
        "dateOrder": str(gettext("End date must be on or after the start date.")),
        "general": str(gettext("General")),
        "notesPlaceholder": str(gettext("Add any notes or comments...")),
        "since": str(gettext("Since")),
        "sinceHint": str(gettext("Leave empty to start the rewatch today")),
        "dateTime": {},
    }


def _track_tab_labels(context):
    """Return labels only for the tabs this modal actually offers."""
    labels = {}
    if context.get("tags_tab_available"):
        labels.update({
            "tags": str(gettext("Tags")),
            "noTags": str(gettext("No tags yet. Create one below.")),
            "newTag": str(gettext("New tag...")),
            "addTag": str(gettext("Add")),
            "add": str(gettext("Add")),
            "remove": str(gettext("Remove")),
            "tagFailed": str(gettext("Couldn't update this tag.")),
        })
    if context.get("metadata_tab_available"):
        labels.update({
            "metadata": str(gettext("Metadata")),
            "artwork": str(gettext("Artwork URL")),
            "saveArtwork": str(gettext("Save artwork")),
            "artworkFailed": str(gettext("Couldn't save the artwork.")),
            "artworkUnavailable": str(
                gettext("Save this item before changing its artwork."),
            ),
        })
    if context.get("collection_tab_available"):
        labels.update({
            "collection": str(gettext("Collection")),
            "noCollection": str(gettext("No owned copies recorded.")),
        })
    if context.get("episode_plays_tab_available"):
        labels.update({
            "plays": str(gettext("Plays")),
            "noPlays": str(gettext("No plays recorded yet.")),
        })
    if context.get("status_history_tab_available"):
        labels.update({
            "statusHistory": str(gettext("Status history")),
            "statusHistoryHelp": str(
                gettext("Every status change, newest first, with what made it."),
            ),
            "noStatusChanges": str(gettext("No status changes recorded yet.")),
            "noStatus": str(gettext("No Status")),
            "sourceNotRecorded": str(gettext("source not recorded")),
        })
    if context.get("discover_tab_available"):
        labels.update({
            "discover": str(gettext("Discover")),
            "discoverTitle": str(gettext("Discover Visibility")),
            "discoverHelp": str(
                gettext("Control whether this item appears in your Discover recommendations."),
            ),
            "discoverStatus": str(gettext("Status")),
            "discoverVisible": str(gettext("Visible")),
            "discoverHiddenOption": str(gettext("Hidden")),
            "currentlyVisible": str(gettext("Currently visible in Discover.")),
            "currentlyHidden": str(gettext("Currently hidden from Discover.")),
            "save": str(gettext("Save")),
            "hidden": str(gettext("Hidden from Discover")),
            "shown": str(gettext("Shown in Discover")),
            "hide": str(gettext("Hide from Discover")),
            "unhide": str(gettext("Show in Discover")),
            "discoverFailed": str(gettext("Couldn't update Discover.")),
        })
    return labels


def _track_tabs_presentation(context):
    """Serialize the available Track tabs for the shared dialog."""
    tabs = [{
        "id": "general",
        "label": str(gettext("General")),
        "kind": "general",
        "icon": file_icon("edit"),
    }]
    tags_data = context.get("tag_assignment_data")
    if context.get("tags_tab_available") and tags_data:
        tabs.append({
            "id": "tags",
            "icon": file_icon("tag"),
            "label": str(gettext("Tags")),
            "kind": "tags",
            "tags": tags_data.get("tags", []),
            "itemId": tags_data.get("item", {}).get("id"),
            "urls": tags_data.get("urls", {}),
            "csrfToken": tags_data.get("csrfToken", ""),
            "previewGenresJson": tags_data.get("previewGenresJson", "[]"),
            "previewImpliedGenresJson": tags_data.get(
                "previewImpliedGenresJson",
                "[]",
            ),
        })
    if context.get("metadata_tab_available"):
        tabs.append({
            "id": "metadata",
            "icon": file_icon("database"),
            "label": str(gettext("Metadata")),
            "kind": "server",
        })
    if context.get("collection_tab_available"):
        tabs.append({
            "id": "collection",
            "icon": file_icon("circle-check"),
            "label": str(gettext("Collection")),
            "kind": "server",
        })
    if context.get("episode_plays_tab_available"):
        label = context.get("episode_plays_tab_label") or gettext("Plays")
        tabs.append({
            "id": "episode-plays",
            "icon": file_icon("history"),
            "label": str(label),
            "kind": "server",
        })
    if context.get("status_history_tab_available"):
        tabs.append({
            "id": "status-history",
            "icon": file_icon("clock-reversing"),
            "label": str(gettext("Status history")),
            "kind": "status-history",
            "changes": [
                {
                    "id": index,
                    "old": change.get("old") or "",
                    "next": change.get("new") or "",
                    "date": str(
                        user_datetime_format(change.get("date"), context.get("user")),
                    ),
                    "reason": change.get("reason") or "",
                }
                for index, change in enumerate(context.get("status_changes") or [])
            ],
        })
    discover_item = context.get("metadata_item")
    if context.get("discover_tab_available") and discover_item is not None:
        tabs.append({
            "id": "discover",
            "icon": file_icon("eye"),
            "label": str(gettext("Discover")),
            "kind": "discover",
            "hidden": bool(context.get("is_hidden_from_discover")),
            "canToggle": True,
            "itemId": discover_item.id,
            "url": reverse("discover_toggle_hidden"),
        })
    return tabs


def _track_form_presentation(
    request,
    form,
    *,
    track_form_id,
    title,
    title_subtitle,
    media_type,
    media,
    action,
    delete_action,
    hidden_fields,
    general_fields,
    rewatch_action,
    rewatch_url,
    save_as_new,
    date_suggestion=None,
):
    """Serialize the General tab into the shared TrackForm model."""
    track_time = bool(settings.TRACK_TIME)
    hidden = {
        field.name: _track_form_field_value(field, track_time)
        for field in hidden_fields
    }
    hidden["track_form_id"] = track_form_id
    if request.GET.get("edition_id"):
        hidden["edition_id"] = request.GET["edition_id"]
    repeat_identity = {}
    if rewatch_action:
        repeat_identity["rewatchLabel"] = str(gettext(rewatch_action["label"]))
        repeat_identity["rewatchAction"] = rewatch_action["action"]
        repeat_identity["rewatchUrl"] = rewatch_url
    if save_as_new:
        repeat_identity["canSaveAsNew"] = True
        repeat_identity["saveAsNewLabel"] = str(gettext("Save as new entry"))
    fields = [
        _track_form_field_payload(field, track_time)
        for field in general_fields
    ]
    suggestion = date_suggestion or {}
    for field in fields:
        if field["name"] not in {"start_date", "end_date"}:
            continue
        is_start = field["name"] == "start_date"
        quick_actions = []
        if is_start:
            quick_actions.append(
                {
                    "id": "start-now",
                    "label": str(
                        pgettext("date picker shortcut", "Start Now"),
                    ),
                },
            )
        else:
            quick_actions.append(
                {
                    "id": "just-finished",
                    "label": str(
                        pgettext("date picker shortcut", "Just Finished"),
                    ),
                },
            )
        if is_start:
            quick_actions.append(
                {
                    "id": "release-date",
                    "label": str(
                        pgettext("date picker shortcut", "Release Date"),
                    ),
                    "requiresSuggestion": True,
                },
            )
        field["quickActions"] = quick_actions
        field["suggestionDate"] = suggestion.get("date", "")
        suggestion_label = suggestion.get("label") or ""
        field["suggestionLabel"] = (
            (
                str(pgettext("date picker shortcut", suggestion_label))
                if suggestion_label == "Release Date"
                else str(gettext(suggestion_label))
            )
            if suggestion.get("date") and suggestion_label
            else ""
        )
        field["suggestionExtras"] = suggestion.get("extras", [])
        field["runtimeMinutes"] = str(suggestion.get("runtime_minutes") or "")
        copy_from = "end_date" if is_start else "start_date"
        if copy_from in form.fields:
            copy_label = str(gettext("End") if is_start else gettext("Start"))
            field["copyFrom"] = copy_from
            field["copyFromLabel"] = copy_label
            field["copyEmptyMessage"] = str(
                gettext("%(label)s is empty") % {"label": copy_label},
            )
    notes_field = form["notes"] if "notes" in form.fields else None
    return {
        "model": {
            "mediaType": media_type,
            "title": str(title),
            "titleSubtitle": title_subtitle,
            "formId": track_form_id,
            "instanceId": str(media.id) if media is not None else None,
            "isNew": media is None,
            "action": action,
            "deleteAction": delete_action if media is not None else "",
            "csrfToken": get_token(request),
            "fields": fields,
            "hidden": hidden,
            "notes": (
                {
                    "value": _track_form_field_value(notes_field, track_time),
                    "label": str(notes_field.label),
                    "help": str(notes_field.help_text or ""),
                }
                if notes_field is not None
                else None
            ),
            "submitLabel": str(
                gettext("Update") if media is not None else gettext("Add"),
            ),
            "deleteLabel": str(gettext("Delete")),
            "trackTime": track_time,
            "labels": _track_form_labels(),
            "repeatIdentity": repeat_identity,
        },
        "errors": {
            name: " ".join(str(message) for message in messages)
            for name, messages in form.errors.items()
        },
    }


def _track_modal_release_date_shortcut(*candidates):
    """Return an ISO release-date string for the shared track modal shortcut."""
    for candidate in candidates:
        if not candidate:
            continue
        if isinstance(candidate, dict):
            candidate = helpers.extract_release_datetime(candidate)  # noqa: PLW2901  # deliberate in-loop normalisation
        elif isinstance(candidate, str):
            candidate = parse_date(candidate[:10])  # noqa: PLW2901  # deliberate in-loop normalisation

        if not candidate:
            continue
        if isinstance(candidate, datetime):
            if timezone.is_aware(candidate):
                candidate = timezone.localtime(candidate)  # noqa: PLW2901  # deliberate in-loop normalisation
            return candidate.date().isoformat()
        if isinstance(candidate, date):
            return candidate.isoformat()
    return ""


def _track_modal_release_runtime_minutes(media_type, *candidates):
    """Return a trusted runtime in minutes for date shortcuts."""
    for candidate in candidates:
        if not candidate:
            continue

        runtime_minutes = None
        if isinstance(candidate, dict):
            runtime_minutes = candidate.get("runtime_minutes")
            if runtime_minutes is None:
                runtime_minutes = (candidate.get("details") or {}).get("runtime")
        else:
            runtime_minutes = getattr(candidate, "runtime_minutes", None)
            if runtime_minutes is None:
                runtime_minutes = getattr(candidate, "runtime", None)

        if isinstance(runtime_minutes, str):
            stripped_runtime = runtime_minutes.strip()
            runtime_minutes = (
                int(stripped_runtime)
                if stripped_runtime.isdigit()
                else stats.parse_runtime_to_minutes(stripped_runtime)
            )
        elif isinstance(runtime_minutes, float):
            runtime_minutes = int(runtime_minutes)

        if (
            isinstance(runtime_minutes, int)
            and 0 < runtime_minutes < RUNTIME_UNKNOWN_AIRED
        ):
            return str(runtime_minutes)

    return ""


def _track_modal_date_suggestion(label, iso_date, runtime_minutes="", extras=()):
    """Normalize a labeled date suggestion for the shared date/time picker.

    ``extras`` are further ``{"label", "date"}`` quick dates offered next to it.
    """
    return {
        "label": label,
        "date": iso_date or "",
        "runtime_minutes": runtime_minutes or "",
        "extras": list(extras),
    }


def _library_panel_url(source, media_type, media_id, season_number, episode_number):
    """Return the URL that lazy-loads Radarr/Sonarr/Seerr details for a title."""
    query = urlencode(
        {
            key: value
            for key, value in (
                ("season_number", season_number),
                ("episode_number", episode_number),
            )
            if value is not None
        },
    )
    url = reverse("library_panel", args=[source, media_type, media_id])
    return f"{url}?{query}" if query else url


RELEASE_TYPE_SHORTCUT_LABELS = {
    "digital": gettext_noop("Digital release"),
    "physical": gettext_noop("Physical release"),
}


def _track_modal_other_release_dates(media_type, item, user):
    """Return a movie's stored digital and physical dates for the user's region.

    Read from the calendar's events, so opening the modal never calls a provider.
    """
    if media_type != MediaTypes.MOVIE.value or item is None:
        return []

    from events.models import Event

    events = (
        Event.objects.filter(item=item, region=user.watch_provider_region)
        .exclude(release_type="")
        .order_by("release_type", "datetime")
    )
    return [
        {
            "label": RELEASE_TYPE_SHORTCUT_LABELS[event.release_type],
            "date": event.datetime.date().isoformat(),
        }
        for event in events
    ]


def _tags_tab_context(item, user, request):
    """Return the shared tag editor context for an item-backed track modal."""
    if item is None:
        return {"tags_tab_available": False}

    from app.tag_views import _tag_assignment_data, _user_tags_for_item

    user_tags = _user_tags_for_item(user, item)
    preview_genres = list(item.genres or [])
    preview_implied_genres = list(getattr(item, "implied_genres", None) or [])
    return {
        "tags_tab_available": True,
        "tags_item": item,
        "user_tags": user_tags,
        "preview_genres_json": json.dumps(preview_genres),
        "preview_implied_genres_json": json.dumps(preview_implied_genres),
        "tag_assignment_data": _tag_assignment_data(
            request,
            item,
            user_tags,
            preview_genres,
            preview_implied_genres,
        ),
        "tag_surface": "track",
    }


def _rewatch_action(media, media_type):
    """Return the rewatch action offered for a tracked show or season, if any.

    Only finished entries can start a pass; an open one can always be ended.
    """
    if media is None or media_type not in {
        MediaTypes.TV.value,
        MediaTypes.SEASON.value,
    }:
        return None

    if media.is_rewatching:
        return {"action": "stop", "label": gettext_noop("End rewatch")}
    if media.status == Status.COMPLETED.value:
        return {"action": "start", "label": gettext_noop("Rewatch")}
    return None


def _render_standard_track_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
    *,
    form_override=None,
    bulk_form_override=None,
    initial_active_tab="general",
    track_form_id=None,
    return_url=None,
    track_action_update=False,
):
    """Build and render the standard media track modal context."""
    instance_id = request.GET.get("instance_id") or request.POST.get("instance_id")
    if instance_id:
        model = apps.get_model(app_label="app", model_name=media_type)
        try:
            media = BasicMedia.objects.get_media(
                request.user,
                media_type,
                instance_id,
            )
        except model.DoesNotExist:
            media = None
            instance_id = None
    elif request.GET.get("is_create"):
        media = None
    else:
        media = None
        # A season tracked under a different identity bucket (e.g. via the
        # show's TV identity) must still resolve here, so the modal receives
        # the correct instance_id instead of trying to create a duplicate
        # row on save — see #623.
        if media_type == MediaTypes.SEASON.value and season_number is not None:
            media = metadata_resolution.find_tracked_season(
                request.user,
                media_id,
                source,
                int(season_number),
            )
        if media is None:
            user_medias = BasicMedia.objects.filter_media(
                request.user,
                media_id,
                media_type,
                source,
                season_number=season_number,
                episode_number=episode_number,
            )
            if request.GET.get("focus") == "notes":
                # The Notes shortcut edits the entry the detail page treats as
                # current, resolved now so it stays right after a new entry.
                media = max(user_medias, key=helpers.entry_activity_key, default=None)
            else:
                media = user_medias.first()
        if media:
            instance_id = media.id

    if media_type in {MediaTypes.SEASON.value, MediaTypes.EPISODE.value} and media:
        season_number = media.item.season_number
    if media_type == MediaTypes.EPISODE.value and media:
        episode_number = media.item.episode_number

    initial_data = {
        "media_id": media_id,
        "source": source,
        "media_type": media_type,
        "season_number": season_number,
        "episode_number": episode_number,
        "instance_id": instance_id,
    }
    if media_type == MediaTypes.EPISODE.value and media:
        # Editing an episode watch can also save the current form as a new
        # entry. Give that create operation its own stable idempotency key.
        initial_data["watch_operation_id"] = uuid4()
    route_identity_media_type = None
    route_library_media_type = None

    max_progress = None
    metadata_resolution_result = None
    metadata_item = None
    base_metadata = None
    if media:
        title = media.item
        metadata_item = media.item
        if (
            media_type == MediaTypes.ANIME.value
            and media.item.media_type == MediaTypes.TV.value
            and media.item.library_media_type == MediaTypes.ANIME.value
        ):
            route_identity_media_type = MediaTypes.TV.value
            route_library_media_type = MediaTypes.ANIME.value
        if media_type == MediaTypes.GAME.value:
            initial_data["progress"] = helpers.minutes_to_hhmm(media.progress)
        elif media_type in (
            MediaTypes.BOOK.value,
            MediaTypes.COMIC.value,
            MediaTypes.MANGA.value,
        ):
            if media_type == MediaTypes.BOOK.value:
                if media.item.book_max_progress:
                    max_progress = media.item.book_max_progress
                elif media.item.format == "audiobook":
                    max_progress = None
                else:
                    try:
                        with services.interactive_request_scope():
                            metadata = services.get_media_metadata(
                                media.item.media_type,
                                media.item.media_id,
                                media.item.source,
                            )
                        number_of_pages = metadata.get("max_progress") or metadata.get(
                            "details",
                            {},
                        ).get("number_of_pages")
                        if number_of_pages:
                            media.item.number_of_pages = number_of_pages
                            max_progress = number_of_pages
                    except Exception:  # noqa: S110  # deliberate best-effort; failure is non-fatal here
                        pass
            else:
                media_list = [media]
                BasicMedia.objects.annotate_max_progress(media_list, media_type)
                if hasattr(media, "max_progress"):
                    max_progress = media.max_progress

            if (
                request.user.book_comic_manga_progress_percentage
                and max_progress
                and media.progress
            ):
                percentage = round((media.progress / max_progress) * 100, 1)
                initial_data["progress"] = percentage
    else:
        language = metadata_resolution.metadata_language_default(request.user)
        try:
            with services.interactive_request_scope():
                metadata = services.get_media_metadata(
                    media_type,
                    media_id,
                    source,
                    [season_number],
                    episode_number=episode_number,
                    language=language,
                )
        except services.ProviderAPIError:
            if media_type != MediaTypes.EPISODE.value:
                raise
            # The provider can list an episode in its season yet 404 the
            # episode itself; the season payload still names it.
            logger.warning(
                "Falling back to season metadata for media_id=%s S%sE%s",
                media_id,
                season_number,
                episode_number,
            )
            with services.interactive_request_scope():
                metadata = dict(
                    services.get_media_metadata(
                        MediaTypes.SEASON.value,
                        media_id,
                        source,
                        [season_number],
                        language=language,
                    ),
                )
            metadata["episode_title"] = next(
                (
                    episode.get("name") or episode.get("title")
                    for episode in metadata.get("episodes") or []
                    if isinstance(episode, dict)
                    and str(episode.get("episode_number")) == str(episode_number)
                ),
                None,
            )
        base_metadata = metadata
        title = metadata["title"]
        route_identity_media_type = metadata.get("identity_media_type")
        route_library_media_type = metadata.get("library_media_type")
        if media_type == MediaTypes.SEASON.value:
            title += f" S{season_number}"
        item_lookup = {
            "media_id": media_id,
            "source": source,
            "media_type": metadata_resolution.get_tracking_media_type(
                media_type,
                source=source,
                identity_media_type=route_identity_media_type,
            ),
            "season_number": season_number,
            "episode_number": episode_number,
        }
        if metadata_resolution.is_grouped_anime_route(
            media_type,
            source=source,
            identity_media_type=route_identity_media_type,
            library_media_type=route_library_media_type,
        ):
            item_lookup["library_media_type"] = MediaTypes.ANIME.value
        metadata_item = Item.objects.filter(**item_lookup).first()

        # Suggest "In progress" if the user already has an in-progress entry for this media
        if request.user.is_authenticated:
            existing_in_progress = (
                BasicMedia.objects.filter_media(
                    request.user,
                    media_id,
                    media_type,
                    source,
                    season_number=season_number,
                )
                .filter(status=Status.IN_PROGRESS.value)
                .exists()
            )
            # An episode form logs a watch, so it keeps the Completed default.
            if media_type != MediaTypes.EPISODE.value:
                initial_data["status"] = helpers.default_status_for_new_entry(
                    has_in_progress_entry=existing_in_progress,
                )
            elif existing_in_progress:
                initial_data["status"] = Status.IN_PROGRESS.value

    title_subtitle = ""
    if media_type == MediaTypes.EPISODE.value and episode_number is not None:
        episode_metadata = base_metadata or {}
        if not episode_metadata.get("episode_title") and media:
            # The tracked path does no provider work of its own; the episode
            # payload is cached, so this is a cache hit in the common case.
            try:
                with services.interactive_request_scope():
                    episode_metadata = services.get_media_metadata(
                        media_type,
                        media_id,
                        source,
                        [season_number],
                        episode_number=episode_number,
                        language=metadata_resolution.metadata_language_default(
                            request.user,
                            metadata_item,
                        ),
                    )
            except services.ProviderAPIError:
                logger.warning(
                    "Could not resolve episode title for media_id=%s S%sE%s",
                    media_id,
                    season_number,
                    episode_number,
                )
                episode_metadata = {}
        episode_name = episode_metadata.get("episode_title")
        show_title = episode_metadata.get("title") or (
            media.item.title if media else ""
        )
        episode_label = f"S{int(season_number):02d}E{int(episode_number):02d}"
        if episode_name:
            title = episode_name
            title_subtitle = f"{show_title} · {episode_label}"
        else:
            title = f"{show_title} {episode_label}".strip()

    if route_identity_media_type:
        initial_data["identity_media_type"] = route_identity_media_type
    if route_library_media_type:
        initial_data["library_media_type"] = route_library_media_type
    if "image_url" not in initial_data:
        preferred_image = None
        if (
            metadata_item
            and metadata_item.image
            and metadata_item.image != settings.IMG_NONE
        ):
            preferred_image = metadata_item.image
        elif (
            base_metadata
            and base_metadata.get("image")
            and base_metadata["image"] != settings.IMG_NONE
        ):
            preferred_image = base_metadata["image"]
        if preferred_image:
            initial_data["image_url"] = preferred_image

    form_media_type = metadata_resolution.get_tracking_media_type(
        media_type,
        source=source,
        identity_media_type=route_identity_media_type,
    )
    form_class = get_form_class(form_media_type)
    if form_override is not None:
        form = form_override
    elif media_type in (
        MediaTypes.BOOK.value,
        MediaTypes.COMIC.value,
        MediaTypes.MANGA.value,
    ):
        form = form_class(
            instance=media,
            initial=initial_data,
            user=request.user,
            max_progress=max_progress,
        )
    else:
        form = form_class(
            instance=media,
            initial=initial_data,
            user=request.user,
        )

    notes_value = (media.notes or "") if media and hasattr(media, "notes") else ""
    notes_count = len([line for line in notes_value.split("\n") if line.strip()])

    hidden_field_names = {
        "instance_id",
        "media_type",
        "identity_media_type",
        "library_media_type",
        "source",
        "media_id",
        "season_number",
        "episode_number",
        "watch_operation_id",
        "start_date_cleared",
    }
    metadata_field_names = {"image_url"}
    field_groups = _track_modal_field_groups(
        form,
        hidden_field_names=hidden_field_names,
        metadata_field_names=metadata_field_names,
    )
    general_fields = field_groups["general_fields"]
    metadata_fields = field_groups["metadata_fields"]
    hidden_fields = field_groups["hidden_fields"]
    image_field = form["image_url"] if "image_url" in form.fields else None

    display_provider = source
    identity_provider = source
    grouped_preview = None
    grouped_preview_target = None
    can_update_metadata_provider = False
    can_migrate_grouped_anime = False
    library_move_context = None
    metadata_provider_mapping_status = "identity"
    metadata_provider_options = []

    if media_type in (MediaTypes.TV.value, MediaTypes.ANIME.value):
        if base_metadata is None:
            try:
                base_metadata = services.get_media_metadata(
                    media_type,
                    media_id,
                    source,
                    [season_number],
                    language=metadata_resolution.metadata_language_default(
                        request.user,
                        metadata_item,
                    ),
                )
            except services.ProviderAPIError:
                if metadata_item is None:
                    raise
                logger.warning(
                    "Falling back to stored metadata for tracking modal media_id=%s "
                    "due to provider API error",
                    media_id,
                )
                base_metadata = stored_metadata_fallback(metadata_item)
        metadata_resolution_result = metadata_resolution.resolve_detail_metadata(
            request.user,
            item=metadata_item,
            route_media_type=media_type,
            media_id=media_id,
            source=source,
            base_metadata=base_metadata,
            persist_links=False,
        )
        display_provider = metadata_resolution_result.display_provider
        identity_provider = metadata_resolution_result.identity_provider
        grouped_preview = metadata_resolution_result.grouped_preview
        grouped_preview_target = metadata_resolution_result.grouped_preview_target
        metadata_provider_mapping_status = metadata_resolution_result.mapping_status
        metadata_provider_options = (
            metadata_resolution.available_metadata_provider_options(
                media_type,
                identity_provider=identity_provider,
            )
        )
        can_migrate_grouped_anime = bool(
            metadata_item is not None
            and metadata_item.source == Sources.MAL.value
            and metadata_item.media_type == MediaTypes.ANIME.value
            and display_provider in {Sources.TMDB.value, Sources.TVDB.value}
            and grouped_preview
            and Anime.objects.filter(user=request.user, item=metadata_item).exists()
        )
    elif metadata_item is not None and custom_metadata.supports_custom_provider(
        media_type
    ):
        metadata_provider_options = (
            metadata_resolution.available_metadata_provider_options(
                media_type,
                identity_provider=identity_provider,
            )
        )
        preference = MetadataProviderPreference.objects.filter(
            user=request.user,
            item=metadata_item,
        ).first()
        allowed_providers = {choice.value for choice in metadata_provider_options}
        if preference and preference.provider in allowed_providers:
            display_provider = preference.provider
            if (
                display_provider == Sources.MANUAL.value
                and identity_provider != Sources.MANUAL.value
            ):
                metadata_provider_mapping_status = "custom"

    can_update_metadata_provider = bool(
        metadata_item is not None and metadata_provider_options
    )

    can_update_metadata_language = bool(
        metadata_item is not None
        and identity_provider in {Sources.TMDB.value, Sources.TVDB.value}
    )
    metadata_language_options = []
    selected_metadata_language = ""
    if can_update_metadata_language:
        try:
            metadata_language_options = tmdb.metadata_languages()
        except Exception as exc:  # pragma: no cover - defensive provider fallback
            logger.warning("Could not load TMDB metadata languages: %s", exc)
            metadata_language_options = [
                ("", f"Server Default ({settings.TMDB_LANG})"),
            ]
        language_preference = MetadataProviderPreference.objects.filter(
            user=request.user,
            item=metadata_item,
        ).first()
        if language_preference:
            selected_metadata_language = language_preference.language

    can_manage_hardcover_edition = bool(
        media_type == MediaTypes.BOOK.value and source == Sources.HARDCOVER.value
    )

    if metadata_item is not None:
        library_move_context = library_migration.get_move_context(
            request.user,
            metadata_item,
        )

    hardcover_selected_edition = None
    if can_manage_hardcover_edition and metadata_item is not None:
        preference = HardcoverEditionPreference.objects.filter(
            user=request.user,
            item=metadata_item,
        ).first()
        if preference:
            with suppress(Exception):
                hardcover_selected_edition = next(
                    (
                        edition
                        for edition in hardcover.editions(media_id, user=request.user)
                        if edition["id"] == preference.edition_id
                    ),
                    None,
                )

    manual_metadata_form = None
    can_edit_custom_metadata = bool(
        metadata_item is not None
        and display_provider == Sources.MANUAL.value
        and custom_metadata.supports_custom_metadata(metadata_item)
    )
    if can_edit_custom_metadata:
        manual_metadata_form = custom_metadata.ManualMetadataForm(
            item=metadata_item,
            prefix="metadata",
        )

    can_change_cover = bool(
        metadata_item is not None
        and request.user.is_authenticated
        and cover_views.user_has_item(request.user, metadata_item)
    )

    metadata_tab_available = bool(
        can_change_cover
        or metadata_fields
        or can_update_metadata_provider
        or can_update_metadata_language
        or can_migrate_grouped_anime
        or library_move_context
        or manual_metadata_form
        or can_manage_hardcover_edition
        or (
            metadata_item is not None
            and media_type in (MediaTypes.MOVIE.value, MediaTypes.TV.value)
        )
        or (media_type == MediaTypes.TV.value and media is not None)
    )

    is_season_modal = media_type == MediaTypes.SEASON.value
    episode_plays_domain = bulk_episode_tracking.build_episode_play_domain(
        request.user,
        MediaTypes.TV.value if is_season_modal else media_type,
        source,
        media_id,
        metadata_item=None if is_season_modal else metadata_item,
        base_metadata=None if is_season_modal else base_metadata,
        metadata_resolution_result=metadata_resolution_result,
        season_number=int(season_number) if is_season_modal else None,
    )
    episode_plays_tab_available = bool(episode_plays_domain)
    if return_url is None:
        return_url = (
            request.GET.get("return_url")
            or request.GET.get("next")
            or request.POST.get("return_url", "")
            or request.POST.get("next", "")
        )
    home_row_id = (
        request.GET.get("home_row_id") or request.POST.get("home_row_id") or ""
    )
    if episode_plays_tab_available:
        if bulk_form_override is not None:
            episode_plays_form = bulk_form_override
        else:
            bulk_initial = _bulk_episode_form_initial_data(
                return_url, episode_plays_domain
            )
            bulk_initial["instance_id"] = instance_id or ""
            episode_plays_form = BulkEpisodeTrackForm(
                initial=bulk_initial,
                domain=episode_plays_domain,
            )
    else:
        episode_plays_form = None

    track_form_id = track_form_id or f"track-form-{uuid4().hex}"
    release_date_shortcut = _track_modal_release_date_shortcut(
        getattr(metadata_item, "release_datetime", None) if metadata_item else None,
        (
            metadata_resolution_result.header_metadata
            if metadata_resolution_result is not None
            else None
        ),
        base_metadata,
    )
    release_date_runtime_minutes = _track_modal_release_runtime_minutes(
        media_type,
        metadata_item,
        (
            metadata_resolution_result.header_metadata
            if metadata_resolution_result is not None
            else None
        ),
        base_metadata,
    )
    other_release_dates = _track_modal_other_release_dates(
        media_type,
        metadata_item,
        request.user,
    )
    date_suggestion = _track_modal_date_suggestion(
        # Name the theatrical date once a digital or physical one sits beside it.
        gettext_noop("Theatrical release") if other_release_dates else "Release Date",
        release_date_shortcut,
        release_date_runtime_minutes,
        other_release_dates,
    )
    rewatch_action = _rewatch_action(media, media_type)

    context = {
        "user": request.user,
        "title": title,
        "title_subtitle": title_subtitle,
        "media_type": media_type,
        "form": form,
        "media": media,
        "return_url": return_url,
        "max_progress": max_progress,
        "display_provider": display_provider,
        "display_provider_label": metadata_resolution.metadata_provider_label(
            display_provider,
        ),
        "identity_provider": identity_provider,
        "identity_provider_label": metadata_resolution.metadata_provider_label(
            identity_provider,
        ),
        "grouped_preview": grouped_preview,
        "grouped_preview_target": grouped_preview_target,
        "metadata_provider_mapping_status": metadata_provider_mapping_status,
        "metadata_provider_options": metadata_provider_options,
        "can_update_metadata_provider": can_update_metadata_provider,
        "can_update_metadata_language": can_update_metadata_language,
        "metadata_language_options": metadata_language_options,
        "selected_metadata_language": selected_metadata_language,
        "can_manage_hardcover_edition": can_manage_hardcover_edition,
        "hardcover_edition_media_id": media_id
        if can_manage_hardcover_edition
        else None,
        "hardcover_selected_edition": hardcover_selected_edition,
        "hardcover_edition_id_hint": (
            request.GET.get("edition_id") if can_manage_hardcover_edition else None
        ),
        "can_migrate_grouped_anime": can_migrate_grouped_anime,
        "can_move_library_item": bool(library_move_context),
        "library_move_context": library_move_context,
        "metadata_tab_available": metadata_tab_available,
        "metadata_item": metadata_item,
        "match_item": metadata_item,
        "tv_provider_switch_target": (
            library_migration.tv_provider_switch_target(request.user, metadata_item)
            if metadata_item is not None
            else None
        ),
        "current_instance": media,
        "general_hidden_fields": hidden_fields,
        "general_fields": general_fields,
        "general_submit_formaction": (
            f"{reverse('episode_save')}?next={return_url}"
            if media_type == MediaTypes.EPISODE.value
            else (
                f"{reverse('media_save')}?next={return_url}"
                + (f"&home_row_id={home_row_id}" if home_row_id else "")
            )
        ),
        "general_delete_formaction": f"{reverse('media_delete')}?next={return_url}",
        "general_rewatch_formaction": (
            f"{reverse('media_rewatch')}?action={rewatch_action['action']}"
            f"&next={return_url}"
            if rewatch_action
            else ""
        ),
        "general_rewatch_label": rewatch_action["label"] if rewatch_action else "",
        "general_rewatch_action": rewatch_action["action"] if rewatch_action else "",
        "general_rewatch_started_at": (
            getattr(media, "rewatch_started_at", None) if rewatch_action else None
        ),
        "general_existing_instance": media,
        "episode_save_as_new": bool(
            media_type == MediaTypes.EPISODE.value and media,
        ),
        "metadata_fields": metadata_fields,
        "image_field": image_field,
        "can_change_cover": can_change_cover,
        "image_save_item_id": (
            metadata_item.id
            if media and metadata_item and not can_edit_custom_metadata
            else None
        ),
        "date_suggestion": date_suggestion,
        "manual_metadata_form": manual_metadata_form,
        "manual_metadata_formaction": (
            reverse("update_manual_item_metadata", args=[metadata_item.id])
            if can_edit_custom_metadata
            else ""
        ),
        "can_edit_custom_metadata": can_edit_custom_metadata,
        "track_form_id": track_form_id,
        "track_action_update": track_action_update,
        "initial_active_tab": initial_active_tab,
        "notes_count": notes_count,
        "notes_focus": request.GET.get("focus") == "notes",
        "episode_plays_tab_available": episode_plays_tab_available,
        "episode_plays_form": episode_plays_form,
        "episode_plays_formaction": reverse("episode_bulk_save"),
        "episode_plays_tab_label": "Episode Plays",
        "episode_plays_submit_label": "Save plays",
        "episode_plays_domain": _episode_domain_template_payload(episode_plays_domain),
        "episode_plays_mode_notice": (
            episode_plays_domain.get("mode_notice", "") if episode_plays_domain else ""
        ),
        "episode_plays_domain_script_id": f"{track_form_id}-episode-domain",
        "collection_tab_available": False,
        "collection_context": None,
    }
    context.update(_tags_tab_context(metadata_item, request.user, request))
    context["status_history_tab_available"] = bool(
        media is not None
        and media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value),
    )
    context["status_changes"] = (
        history_processor.status_change_log(
            media,
            limit=history_processor.STATUS_HISTORY_TAB_LENGTH,
        )
        if context["status_history_tab_available"]
        else []
    )
    koreader_account = getattr(request.user, "koreader_account", None)
    context["show_koreader_document_field"] = bool(
        media_type == MediaTypes.BOOK.value
        and koreader_account
        and koreader_account.is_connected,
    )
    context["koreader_document_id"] = ""
    if context["show_koreader_document_field"] and metadata_item:
        from integrations.koreader_links import get_document_hash_for_item

        context["koreader_document_id"] = get_document_hash_for_item(
            request.user,
            metadata_item,
        )
    if (
        (media_type == MediaTypes.EPISODE.value and episode_number is not None)
        or (media_type == MediaTypes.SEASON.value and season_number is not None)
        or media_type in (MediaTypes.MOVIE.value, MediaTypes.TV.value)
    ):
        context["collection_tab_available"] = True
        if source in (Sources.TMDB.value, Sources.TVDB.value):
            context["library_panel_url"] = _library_panel_url(
                source, media_type, media_id, season_number, episode_number
            )
        context["collection_context"] = build_collection_modal_context(
            request,
            source,
            media_type,
            media_id,
            season_number=season_number,
            episode_number=episode_number,
            metadata=base_metadata,
        )
    context.update(_build_track_modal_discover_tab_context(request.user, metadata_item))
    context["episode_plays_presentation_id"] = (
        f"{track_form_id}-episode-plays-props"
    )
    context["episode_plays_presentation"] = _bulk_episode_presentation(
        request,
        episode_plays_form,
        episode_plays_domain,
        track_form_id=track_form_id,
        formaction=reverse("episode_bulk_save"),
        return_url=return_url,
        submit_label=context["episode_plays_submit_label"],
    )
    context["track_form_props_id"] = f"{track_form_id}-shared-form"
    presentation = _track_form_presentation(
        request,
        form,
        track_form_id=track_form_id,
        title=title,
        title_subtitle=title_subtitle,
        media_type=media_type,
        media=media,
        action=context["general_submit_formaction"],
        delete_action=context["general_delete_formaction"],
        hidden_fields=hidden_fields,
        general_fields=general_fields,
        rewatch_action=rewatch_action,
        rewatch_url=context["general_rewatch_formaction"],
        save_as_new=bool(media_type == MediaTypes.EPISODE.value and media),
        date_suggestion=date_suggestion,
    )
    presentation["tabs"] = _track_tabs_presentation(context)
    presentation["model"]["labels"].update(_track_tab_labels(context))
    context["track_form_presentation"] = presentation
    response = render(
        request,
        "app/components/fill_track.html",
        context,
    )
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def _render_podcast_show_track_modal(
    request,
    show,
    *,
    form_override=None,
    bulk_form_override=None,
    initial_active_tab="general",
    track_form_id=None,
    return_url=None,
    track_action_update=False,
):
    """Build and render the podcast show tracking modal with bulk episode plays."""
    from app.forms import PodcastShowTrackerForm
    from app.models import PodcastShowTracker

    tracker = PodcastShowTracker.objects.filter(user=request.user, show=show).first()
    if return_url is None:
        return_url = (
            request.GET.get("return_url")
            or request.GET.get("next")
            or request.POST.get("return_url", "")
            or request.POST.get("next", "")
        )
    home_row_id = (
        request.GET.get("home_row_id") or request.POST.get("home_row_id") or ""
    )

    if form_override is not None:
        form = form_override
    else:
        form = PodcastShowTrackerForm(
            instance=tracker,
            initial={"show_id": show.id},
            user=request.user,
        )

    field_groups = _track_modal_field_groups(
        form,
        hidden_field_names={"show_id"},
        metadata_field_names=set(),
    )
    episode_plays_domain = bulk_episode_tracking.build_episode_play_domain(
        request.user,
        MediaTypes.PODCAST.value,
        show.source,
        show.podcast_uuid,
        podcast_show=show,
    )
    episode_plays_tab_available = bool(episode_plays_domain)
    if episode_plays_tab_available:
        if bulk_form_override is not None:
            episode_plays_form = bulk_form_override
        else:
            bulk_initial = _bulk_episode_form_initial_data(
                return_url,
                episode_plays_domain,
            )
            bulk_initial["instance_id"] = tracker.id if tracker else ""
            episode_plays_form = BulkEpisodeTrackForm(
                initial=bulk_initial,
                domain=episode_plays_domain,
            )
    else:
        episode_plays_form = None

    track_form_id = track_form_id or f"track-form-{uuid4().hex}"
    date_suggestion = _track_modal_date_suggestion("Release Date", "")
    context = {
        "user": request.user,
        "title": show.title,
        "media_type": MediaTypes.PODCAST.value,
        "form": form,
        "media": tracker,
        "return_url": return_url,
        "metadata_tab_available": False,
        "metadata_fields": [],
        "general_hidden_fields": field_groups["hidden_fields"],
        "general_fields": field_groups["general_fields"],
        "general_submit_formaction": (
            f"{reverse('podcast_show_save')}?next={return_url}"
            + (f"&home_row_id={home_row_id}" if home_row_id else "")
        ),
        "general_delete_formaction": (
            f"{reverse('podcast_show_delete')}?next={return_url}"
        ),
        "general_existing_instance": tracker,
        "image_field": None,
        "image_save_item_id": None,
        "date_suggestion": date_suggestion,
        "track_form_id": track_form_id,
        "track_action_update": track_action_update,
        "initial_active_tab": initial_active_tab,
        "episode_plays_tab_available": episode_plays_tab_available,
        "episode_plays_form": episode_plays_form,
        "episode_plays_formaction": reverse("episode_bulk_save"),
        "episode_plays_tab_label": "Episode Plays",
        "episode_plays_submit_label": "Save plays",
        "episode_plays_domain": _episode_domain_template_payload(
            episode_plays_domain,
        ),
        "episode_plays_mode_notice": (
            episode_plays_domain.get("mode_notice", "")
            if episode_plays_domain
            else ""
        ),
        "episode_plays_domain_script_id": f"{track_form_id}-episode-domain",
    }
    context["track_form_props_id"] = f"{track_form_id}-shared-form"
    presentation = _track_form_presentation(
        request,
        form,
        track_form_id=track_form_id,
        title=show.title,
        title_subtitle="",
        media_type=MediaTypes.PODCAST.value,
        media=tracker,
        action=context["general_submit_formaction"],
        delete_action=context["general_delete_formaction"],
        hidden_fields=field_groups["hidden_fields"],
        general_fields=field_groups["general_fields"],
        rewatch_action=None,
        rewatch_url="",
        save_as_new=False,
        date_suggestion=date_suggestion,
    )
    presentation["tabs"] = _track_tabs_presentation(context)
    presentation["model"]["labels"].update(_track_tab_labels(context))
    context["track_form_presentation"] = presentation
    response = render(
        request,
        "app/components/fill_track.html",
        context,
    )
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


@never_cache
@require_GET
def track_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
):
    """Return the tracking form for a media item."""
    track_action_update = (
        request.GET.get("track_action_update") == "1"
        or request.POST.get("track_action_update") == "1"
    )
    standard_modal = (
        request.GET.get("standard_modal") == "1"
        or request.POST.get("standard_modal") == "1"
    )
    episode_number = request.GET.get("episode_number")
    if episode_number is not None:
        try:
            episode_number = int(episode_number)
        except (TypeError, ValueError):
            episode_number = None

    # A list card for an album carries the album page's id, which is not a
    # recording; the album has its own tracking form.
    if media_type == MediaTypes.MUSIC.value and source == Sources.MUSICBRAINZ.value:
        from app.music_album_views import album_track_modal
        from app.services.music import album_for_media_id

        album = album_for_media_id(media_id, user=request.user)
        if album:
            return album_track_modal(request, album.id)

    # Handle podcast shows (identified by podcast_uuid)
    if (
        not standard_modal
        and media_type == MediaTypes.PODCAST.value
        and source
        in {
            Sources.POCKETCASTS.value,
            Sources.GPODDER.value,
            Sources.AUDIOBOOKSHELF.value,
            Sources.MANUAL.value,
        }
    ):
        from app.models import PodcastEpisode
        from app.services.podcast_import import resolve_show_for_media_id

        # Check if this is a show (podcast_uuid or an iTunes search id) or an
        # episode (episode_uuid).
        show = resolve_show_for_media_id(media_id, source=source)
        if show:
            return _render_podcast_show_track_modal(request, show)

        # This is an episode (episode_uuid) - use music-style modal
        episode = PodcastEpisode.objects.filter(episode_uuid=media_id).first()
        if episode:
            from app.models import Podcast

            show = episode.show
            request.GET.get("instance_id")

            # Get all Podcast entries for this episode to aggregate history
            # Each Podcast entry has its own history, so we need to combine them
            all_podcasts = list(
                Podcast.objects.filter(
                    user=request.user,
                    show=show,
                    episode=episode,
                ).order_by("-end_date")
            )

            # Render without creating a tracking Item during this GET.
            item = Item.objects.filter(
                media_id=episode.episode_uuid,
                source=source,
                media_type=media_type,
            ).first() or Item(
                media_id=episode.episode_uuid,
                source=source,
                media_type=media_type,
                title=episode.title,
                image=show.image or settings.IMG_NONE,
                runtime_minutes=(episode.duration // 60) if episode.duration else None,
            )

            # Create adapter objects to match template expectations
            class PodcastEpisodeAdapter:
                """Adapter to make PodcastEpisode work like Track in template."""

                def __init__(self, episode):
                    self.title = episode.title
                    self.track_number = episode.episode_number
                    self.duration_formatted = (
                        self._format_duration(episode.duration)
                        if episode.duration
                        else None
                    )
                    self.runtime_minutes = (
                        episode.duration // 60 if episode.duration else ""
                    )
                    self.musicbrainz_recording_id = None  # Not used for podcasts
                    self.id = episode.id
                    self.published = episode.published  # For "Published date" button
                    self.episode_uuid = (
                        episode.episode_uuid
                    )  # For form submission when music is None

                def _format_duration(self, seconds):
                    """Format duration in seconds to MM:SS or H:MM:SS."""
                    hours = seconds // 3600
                    minutes = (seconds % 3600) // 60
                    secs = seconds % 60
                    if hours > 0:
                        return f"{hours}:{minutes:02d}:{secs:02d}"
                    return f"{minutes}:{secs:02d}"

            class PodcastShowAdapter:
                """Adapter to make PodcastShow work like Album in template."""

                def __init__(self, show):
                    self.image = show.image or settings.IMG_NONE
                    self.release_date = None  # Podcasts don't have release dates
                    self.id = show.id

            # Create a wrapper object that aggregates history from all podcast entries
            # This allows the template to show all history records like music does
            if all_podcasts:
                # Aggregate all history records from all podcast entries
                # Only include history records with end_date (completed plays)
                all_history = []
                for podcast in all_podcasts:
                    # Only include history records with end_date (completed plays)
                    history = podcast.plays.filter(end_date__isnull=False)
                    # Convert queryset to list if needed to ensure proper evaluation
                    if hasattr(history, "__iter__") and not isinstance(
                        history, (list, tuple)
                    ):
                        history = list(history)
                    all_history.extend(history)

                # Sort by end_date descending (most recent first) for display
                # The template filter will re-sort if needed
                all_history.sort(
                    key=lambda x: x.end_date or datetime.min.replace(tzinfo=UTC),
                    reverse=True,
                )

                class PodcastHistoryWrapper:
                    """Wrapper to aggregate history from multiple Podcast entries."""

                    def __init__(self, podcasts, item, history_list):
                        self.item = item
                        self.id = podcasts[0].id if podcasts else 0
                        self._podcasts = podcasts
                        self._history_list = history_list
                        in_progress_entry = next(
                            (entry for entry in podcasts if not entry.end_date),
                            None,
                        )
                        self.in_progress_instance_id = (
                            in_progress_entry.id if in_progress_entry else None
                        )

                    @property
                    def completed_play_count(self):
                        """Return count of completed plays (history records with end_date)."""
                        # Since we already filtered all_history to only include records with end_date,
                        # we can just count the length of the filtered history_list
                        return len(self._history_list)

                    @property
                    def has_in_progress_entry(self):
                        return bool(self.in_progress_instance_id)

                    @property
                    def history(self):
                        """Return a queryset-like object that aggregates all history."""

                        class HistoryProxy:
                            def __init__(self, history_list):
                                self._history = history_list

                            def all(self):
                                return self._history

                            def count(self):
                                return len(self._history)

                            def filter(self, **kwargs):
                                # Simple filtering for history_user
                                if "history_user" in kwargs:
                                    user = kwargs["history_user"]
                                    filtered = [
                                        h
                                        for h in self._history
                                        if getattr(h, "history_user", None) == user
                                        or getattr(h, "history_user", None) is None
                                    ]
                                    return HistoryProxy(filtered)
                                return self

                            def order_by(self, order):
                                # Re-sort based on order string (e.g., 'end_date' or '-end_date')
                                if order == "end_date":
                                    sorted_list = sorted(
                                        self._history,
                                        key=lambda x: (
                                            x.end_date
                                            or datetime.min.replace(tzinfo=UTC)
                                        ),
                                    )
                                elif order == "-end_date":
                                    sorted_list = sorted(
                                        self._history,
                                        key=lambda x: (
                                            x.end_date
                                            or datetime.min.replace(tzinfo=UTC)
                                        ),
                                        reverse=True,
                                    )
                                else:
                                    sorted_list = self._history
                                return HistoryProxy(sorted_list)

                        return HistoryProxy(self._history_list)

                podcast = PodcastHistoryWrapper(all_podcasts, item, all_history)
            else:
                podcast = _DummyPodcastWrapper(item)

            return render(
                request,
                "app/components/fill_track_song.html",
                {
                    "user": request.user,
                    "album": PodcastShowAdapter(
                        show
                    ),  # Use show as "album" for template compatibility
                    "track": PodcastEpisodeAdapter(
                        episode
                    ),  # Use episode as "track" for template compatibility
                    "music": podcast,  # Use podcast as "music" for template compatibility
                    "request": request,
                    "csrf_token": request.META.get("CSRF_COOKIE", ""),
                    "TRACK_TIME": True,
                    "IMG_NONE": settings.IMG_NONE,
                },
            )

    return _render_standard_track_modal(
        request,
        source,
        media_type,
        media_id,
        season_number=season_number,
        episode_number=episode_number,
        track_action_update=track_action_update,
    )
