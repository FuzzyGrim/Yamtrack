from django.apps import apps
from django.contrib.humanize.templatetags.humanize import ordinal
from django.template.defaultfilters import pluralize
from django.utils import formats, timezone

from app import helpers, media_type_config
from app.models import Media, MediaTypes


def process_history_entries(history_records, media_type):
    """Process all history records into timeline entries."""
    timeline_entries = []
    last = history_records.first()

    for _ in range(history_records.count()):
        entry = process_history_entry((last, last.prev_record), media_type)
        if entry["changes"]:
            timeline_entries.append(entry)
        last = last.prev_record

    return timeline_entries


def process_history_entry(entry, media_type):
    """Process a single history entry to organize and format changes."""
    new_record, old_record = entry
    processed_entry = {
        "id": new_record.history_id,
        "date": new_record.history_date,
        "changes": [],
    }

    if old_record is not None:
        return process_changed_entry(
            new_record,
            old_record,
            media_type,
            processed_entry,
        )
    return process_creation_entry(new_record, media_type, processed_entry)


def process_changed_entry(new_record, old_record, media_type, processed_entry):
    """Process an entry representing a change to existing media."""
    delta = new_record.diff_against(old_record)
    changes = organize_changes(delta.changes, media_type)
    apply_date_status_integration(changes)
    build_changes_list(changes, processed_entry)
    return processed_entry


def process_creation_entry(new_record, media_type, processed_entry):
    """Process an entry representing media creation."""
    history_model = apps.get_model(
        app_label="app",
        model_name=f"historical{media_type}",
    )

    changes = collect_creation_changes(new_record, history_model, media_type)
    apply_date_status_integration(changes)
    build_changes_list(changes, processed_entry)
    return processed_entry


def organize_changes(changes, media_type):
    """Organize changes into categories."""
    organized = {
        "date_changes": {"start_date": None, "end_date": None},
        "status_change": None,
        "other_changes": [],
    }

    repeats_change = None
    end_date_change = None

    for change in changes:
        if change.field == "progress" and media_type == MediaTypes.MOVIE.value:
            continue

        change_data = {
            "description": format_description(
                change.field,
                change.old,
                change.new,
                media_type,
            ),
            "field": change.field,
            "old": change.old,
            "new": change.new,
        }

        if change.field == "status":
            organized["status_change"] = change_data
        elif change.field == "repeats":
            repeats_change = change_data
        elif change.field == "end_date":
            end_date_change = change_data
        elif change.field in organized["date_changes"]:
            organized["date_changes"][change.field] = change_data
        else:
            organized["other_changes"].append(change_data)

    # Handle combined repeats and end_date case
    if repeats_change and end_date_change:
        combined_description = (
            f"{repeats_change['description']} on "
            f"{format_datetime(end_date_change['new'])}"
        )
        combined_change = {
            "description": combined_description,
            "field": "combined_repeats_end_date",
            "old": None,
            "new": None,
        }
        organized["other_changes"].append(combined_change)
    else:
        if repeats_change:
            organized["other_changes"].append(repeats_change)
        if end_date_change:
            organized["date_changes"]["end_date"] = end_date_change

    return organized


def collect_creation_changes(new_record, history_model, media_type):
    """Collect changes for a creation entry."""
    organized = {
        "date_changes": {"start_date": None, "end_date": None},
        "status_change": None,
        "other_changes": [],
    }

    for field in history_model._meta.get_fields():  # noqa: SLF001
        if (
            field.name.startswith("history_")
            or field.name in ["id"]
            or not hasattr(new_record, field.attname)
            or (field.name == "progress" and media_type == MediaTypes.MOVIE.value)
        ):
            continue

        value = getattr(new_record, field.attname, None)
        if not value:
            continue

        change_data = {
            "field": field.name,
            "new": value,
            "description": format_description(
                field.name,
                None,
                value,
                media_type,
            ),
        }

        if field.name == "status":
            organized["status_change"] = change_data
        elif field.name in organized["date_changes"]:
            organized["date_changes"][field.name] = change_data
        elif field.name not in ["item", "user", "related_tv"]:
            organized["other_changes"].append(change_data)

    return organized


def apply_date_status_integration(changes):
    """Integrate status changes with date changes where appropriate."""
    date_changes = changes["date_changes"]
    status_change = changes["status_change"]

    # Process start date with status
    if (
        date_changes["start_date"]
        and status_change
        and status_change["new"] == Media.Status.IN_PROGRESS.value
    ):
        date_changes["start_date"]["description"] = (
            f"Started on {format_datetime(date_changes['start_date']['new'])}"
        )
        changes["status_change"] = None

    # Process end date with status
    if (
        date_changes["end_date"]
        and status_change
        and status_change["new"] == Media.Status.COMPLETED.value
    ):
        date_changes["end_date"]["description"] = (
            f"Finished on {format_datetime(date_changes['end_date']['new'])}"
        )
        changes["status_change"] = None


def build_changes_list(changes, processed_entry):
    """Build the final changes list in the desired order."""
    # Add date changes
    if changes["date_changes"]["start_date"]:
        processed_entry["changes"].append(changes["date_changes"]["start_date"])
    if changes["date_changes"]["end_date"]:
        processed_entry["changes"].append(changes["date_changes"]["end_date"])

    # Add status if not integrated with dates
    if changes["status_change"]:
        processed_entry["changes"].append(changes["status_change"])

    # Add other changes
    processed_entry["changes"].extend(changes["other_changes"])


def format_description(field_name, old_value, new_value, media_type=None):  # noqa: C901, PLR0911, PLR0912
    """Format change description in a human-readable way.

    Provides natural language descriptions for various types of changes,
    taking into account the media type and status transitions.
    """
    if field_name in {"start_date", "end_date"}:
        new_value = format_datetime(new_value)
        old_value = format_datetime(old_value)

    # If old_value is None, treat it as an initial setting
    if old_value is None:
        if field_name == "status":
            verb = media_type_config.get_verb(media_type, past_tense=False)
            action = "Marked as"
            if new_value == Media.Status.IN_PROGRESS.value:
                return f"{action} currently {verb}ing"
            if new_value == Media.Status.COMPLETED.value:
                return f"{action} finished {verb}ing"
            if new_value == Media.Status.PLANNING.value:
                return f"Added to {verb}ing list"
            if new_value == Media.Status.DROPPED.value:
                return f"{action} dropped"
            if new_value == Media.Status.PAUSED.value:
                return f"{action} paused {verb}ing"

        if field_name == "score":
            return f"Rated {new_value}/10"

        if field_name == "progress" and media_type:
            verb = media_type_config.get_verb(media_type, past_tense=True).title()
            if media_type == MediaTypes.GAME.value:
                return f"{verb} for {helpers.minutes_to_hhmm(new_value)}"
            unit = media_type_config.get_unit(media_type, short=False).lower()
            return f"{verb} up to {unit} {new_value}"

        if field_name == "repeats":
            verb = media_type_config.get_verb(media_type, past_tense=True)
            return f"{verb.title()} for the {ordinal(new_value + 1)} time"

        if field_name in ["start_date", "end_date"]:
            field_display = "Started" if field_name == "start_date" else "Finished"
            return f"{field_display} on {new_value}"

        if field_name == "notes":
            return "Added notes"

        return f"Set {field_name.replace('_', ' ').lower()} to {new_value}"

    # Regular change (old_value to new_value)
    if field_name == "status":
        verb = media_type_config.get_verb(media_type, past_tense=False)
        # Status transitions
        transitions = {
            (
                Media.Status.PLANNING.value,
                Media.Status.IN_PROGRESS.value,
            ): f"Currently {verb}ing",
            (
                Media.Status.IN_PROGRESS.value,
                Media.Status.COMPLETED.value,
            ): f"Finished {verb}ing",
            (
                Media.Status.IN_PROGRESS.value,
                Media.Status.PAUSED.value,
            ): f"Paused {verb}ing",
            (
                Media.Status.PAUSED.value,
                Media.Status.IN_PROGRESS.value,
            ): f"Resumed {verb}ing",
            (
                Media.Status.IN_PROGRESS.value,
                Media.Status.DROPPED.value,
            ): f"Stopped {verb}ing",
            (
                Media.Status.COMPLETED.value,
                Media.Status.REPEATING.value,
            ): f"Started re{verb}ing",
            (
                Media.Status.REPEATING.value,
                Media.Status.COMPLETED.value,
            ): f"Finished re{verb}ing",
        }
        return transitions.get(
            (old_value, new_value),
            f"Changed status from {old_value} to {new_value}",
        )

    if field_name == "score":
        if old_value == 0:
            return f"Rated {new_value}/10"
        return f"Changed rating from {old_value} to {new_value}"

    if field_name == "progress":
        diff = new_value - old_value
        diff_abs = abs(diff)

        if media_type == MediaTypes.GAME.value:
            if diff > 0:
                return f"Added {helpers.minutes_to_hhmm(diff_abs)} of playtime"
            return f"Removed {helpers.minutes_to_hhmm(diff_abs)} of playtime"

        unit = (
            f"{media_type_config.get_unit(media_type, short=False).lower()}"
            f"{pluralize(diff_abs)}"
        )

        return f"Progress set to {new_value} {unit}"

    if field_name == "repeats":
        # Handle combined case in organize_changes function
        verb = media_type_config.get_verb(media_type, past_tense=False).title()
        if new_value > old_value:
            return (
                f"Finished {verb.lower()}ing again for the "
                f"{ordinal(new_value + 1)} time"
            )
        return f"Adjusted repeat count from {old_value} to {new_value}"

    if field_name in ["start_date", "end_date"]:
        field_display = "Start" if field_name == "start_date" else "End"
        if not new_value:
            return f"Removed {field_display.lower()} date"
        if not old_value:
            return f"{field_display}ed on {new_value}"
        return f"{field_display}ed again on {new_value}"

    if field_name == "notes":
        if not old_value:
            return "Added notes"
        if not new_value:
            return "Removed notes"
        return "Updated notes"

    field_label = field_name.replace("_", " ").lower()
    return f"Updated {field_label} from {old_value} to {new_value}"


def format_datetime(value):
    """Format a datetime object to a readable string."""
    if not value:
        return value

    local_dt = timezone.localtime(value)
    return formats.date_format(
        local_dt,
        "DATETIME_FORMAT",
    )
