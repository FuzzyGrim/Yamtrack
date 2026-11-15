from collections import defaultdict
from datetime import timedelta

from django.apps import apps
from django.db.models import Q
from django.utils import formats, timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext, pgettext, pgettext_lazy

from app import config, helpers
from app.models import MediaTypes, Status
from app.templatetags import app_tags

# Status phrases per activity ("watch", "read" or "play", see config), written
# as whole sentences so each language can phrase them naturally.
INITIAL_STATUS_DESCRIPTIONS = {
    Status.IN_PROGRESS.value: {
        "watch": pgettext_lazy("history", "Marked as currently watching"),
        "read": pgettext_lazy("history", "Marked as currently reading"),
        "play": pgettext_lazy("history", "Marked as currently playing"),
    },
    Status.COMPLETED.value: {
        "watch": pgettext_lazy("history", "Marked as finished watching"),
        "read": pgettext_lazy("history", "Marked as finished reading"),
        "play": pgettext_lazy("history", "Marked as finished playing"),
    },
    Status.PLANNING.value: {
        "watch": pgettext_lazy("history", "Added to watching list"),
        "read": pgettext_lazy("history", "Added to reading list"),
        "play": pgettext_lazy("history", "Added to playing list"),
    },
    Status.PAUSED.value: {
        "watch": pgettext_lazy("history", "Marked as paused watching"),
        "read": pgettext_lazy("history", "Marked as paused reading"),
        "play": pgettext_lazy("history", "Marked as paused playing"),
    },
}

STATUS_TRANSITION_DESCRIPTIONS = {
    (Status.PLANNING.value, Status.IN_PROGRESS.value): {
        "watch": pgettext_lazy("history", "Currently watching"),
        "read": pgettext_lazy("history", "Currently reading"),
        "play": pgettext_lazy("history", "Currently playing"),
    },
    (Status.IN_PROGRESS.value, Status.COMPLETED.value): {
        "watch": pgettext_lazy("history", "Finished watching"),
        "read": pgettext_lazy("history", "Finished reading"),
        "play": pgettext_lazy("history", "Finished playing"),
    },
    (Status.IN_PROGRESS.value, Status.PAUSED.value): {
        "watch": pgettext_lazy("history", "Paused watching"),
        "read": pgettext_lazy("history", "Paused reading"),
        "play": pgettext_lazy("history", "Paused playing"),
    },
    (Status.PAUSED.value, Status.IN_PROGRESS.value): {
        "watch": pgettext_lazy("history", "Resumed watching"),
        "read": pgettext_lazy("history", "Resumed reading"),
        "play": pgettext_lazy("history", "Resumed playing"),
    },
    (Status.IN_PROGRESS.value, Status.DROPPED.value): {
        "watch": pgettext_lazy("history", "Stopped watching"),
        "read": pgettext_lazy("history", "Stopped reading"),
        "play": pgettext_lazy("history", "Stopped playing"),
    },
}

INITIAL_PROGRESS_DESCRIPTIONS = {
    # Translators: %(unit)s is a progress unit such as "episode".
    "watch": pgettext_lazy("history", "Watched up to %(unit)s %(number)s"),
    # Translators: %(unit)s is a progress unit such as "chapter" or "page".
    "read": pgettext_lazy("history", "Read up to %(unit)s %(number)s"),
    # Translators: %(unit)s is a progress unit such as "play".
    "play": pgettext_lazy("history", "Played up to %(unit)s %(number)s"),
}


def _status_label(status):
    """Return the translated label of a status, or the raw value if unknown."""
    if status in Status.values:
        return Status(status).label
    return status


def _journal_queryset(model, user, start_date, end_date):
    """Build the base non-deletion history queryset for a media type."""
    queryset = model.objects.filter(history_user_id=user).exclude(history_type="-")
    if start_date:
        queryset = queryset.filter(history_date__gte=start_date)
    if end_date:
        queryset = queryset.filter(history_date__lte=end_date)
    return queryset


def get_journal_page(user, start_date=None, end_date=None, limit=20, cursor=None):
    """Return one keyset page of the user's non-deletion history, newest first.

    Returns ``(rows, has_next)`` where each row is a
    ``(history_date, media_type, history_id)`` tuple, merged across the separate
    ``historical<media_type>`` tables. Rows follow a *total* order —
    ``(history_date, history_id, media_type)`` descending — so records that share
    a ``history_date`` (e.g. a bulk import stamps a whole batch with one
    timestamp) never reshuffle between pages.

    ``cursor`` is the last row of the previous page (or ``None`` for the first);
    only rows strictly older than it are returned. This is keyset pagination:
    each request reads at most ``limit + 1`` rows per media type regardless of
    how deep the feed is scrolled, instead of re-scanning everything above the
    current page. ``start_date``/``end_date`` are optional aware datetimes.
    """
    index = []
    for media_type in MediaTypes.values:
        model = apps.get_model(app_label="app", model_name=f"historical{media_type}")
        queryset = _journal_queryset(model, user, start_date, end_date)
        if cursor is not None:
            cur_date, cur_type, cur_id = cursor
            cond = Q(history_date__lt=cur_date) | Q(
                history_date=cur_date,
                history_id__lt=cur_id,
            )
            # media_type is constant per table, so the third-key tiebreak is an
            # all-or-nothing condition on this table's rows sharing the cursor's
            # (date, id).
            if media_type < cur_type:
                cond |= Q(history_date=cur_date, history_id=cur_id)
            queryset = queryset.filter(cond)

        rows = queryset.order_by("-history_date", "-history_id").values_list(
            "history_date",
            "history_id",
        )[: limit + 1]
        for history_date, history_id in rows:
            index.append((history_date, media_type, history_id))

    # Sort key mirrors the keyset order: (history_date, history_id, media_type).
    index.sort(key=lambda entry: (entry[0], entry[2], entry[1]), reverse=True)
    return index[:limit], len(index) > limit


def get_journal_count(user, start_date=None, end_date=None):
    """Count the user's non-deletion history rows across all media types."""
    total = 0
    for media_type in MediaTypes.values:
        model = apps.get_model(app_label="app", model_name=f"historical{media_type}")
        total += _journal_queryset(model, user, start_date, end_date).count()
    return total


def parse_journal_cursor(request):
    """Parse the keyset ``cursor_*`` params into a ``(date, media_type, id)``.

    Returns ``None`` (first page) when any part is missing or malformed.
    """
    cursor_date = parse_datetime(request.GET.get("cursor_date") or "")
    cursor_type = request.GET.get("cursor_type")
    try:
        cursor_id = int(request.GET["cursor_id"])
    except (KeyError, TypeError, ValueError):
        return None
    if cursor_date is None or cursor_type not in MediaTypes.values:
        return None
    return (cursor_date, cursor_type, cursor_id)


def build_journal_days(entries, user):
    """Group consecutive same-day entries so the feed can show day separators."""
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    days = []
    for entry in entries:
        day = timezone.localdate(entry["date"])
        if not days or days[-1]["day"] != day:
            if day == today:
                label = gettext("Today")
            elif day == yesterday:
                label = gettext("Yesterday")
            else:
                label = formats.date_format(day, user.date_format)
            days.append(
                {
                    "day": day,
                    "day_iso": day.isoformat(),
                    "label": label,
                    "entries": [],
                },
            )
        days[-1]["entries"].append(entry)
    return days


def build_journal_entries(index_page, user):
    """Turn a page of the journal index into renderable activity entries.

    ``index_page`` is a page from :func:`get_journal_page`. Historical records,
    their display items and their predecessors are fetched in batches per media
    type, then each record is rendered into human-readable changes.
    """
    records = _fetch_history_records(index_page)
    display = _resolve_display_items(records)
    prev_records = _fetch_prev_records(records)

    entries = []
    for _history_date, media_type, history_id in index_page:
        record = records.get((media_type, history_id))
        if record is None:
            continue
        info = display.get((media_type, record.id))
        if info is None:
            continue

        if media_type == MediaTypes.EPISODE.value:
            changes = _episode_changes(record, info)
        else:
            # A creation ("+") has no previous record; otherwise use the
            # batched predecessor instead of a per-row ``prev_record`` query.
            prev_record = (
                None
                if record.history_type == "+"
                else prev_records.get((media_type, record.history_id))
            )
            processed = process_history_entry(
                (record, prev_record),
                media_type,
                user,
            )
            changes = processed["changes"]

        if not changes:
            continue

        entries.append(
            {
                "id": record.history_id,
                "date": record.history_date,
                "media_type": media_type,
                "item": info["item"],
                "changes": changes,
                "accent": _entry_accent(changes),
            },
        )
    return entries


def _entry_accent(changes):
    """Classify an entry by its primary change for iconography and colour.

    Returns a :class:`~app.models.Status` value for status-driven entries, or
    ``"score"``/``"default"`` for the remaining cases (see
    :data:`app.config.JOURNAL_ACCENT_EXTRA`).
    """
    primary = changes[0]
    field = primary.get("field")

    if field == "status":
        return primary.get("new")
    if field == "score":
        return "score"
    if field in ("progress", "start_date"):
        return Status.IN_PROGRESS.value
    if field == "end_date":
        return Status.COMPLETED.value
    return "default"


def _fetch_history_records(index_page):
    """Fetch historical records for a page, keyed by ``(media_type, history_id)``."""
    ids_by_type = defaultdict(list)
    for _history_date, media_type, history_id in index_page:
        ids_by_type[media_type].append(history_id)

    records = {}
    for media_type, history_ids in ids_by_type.items():
        model = apps.get_model(app_label="app", model_name=f"historical{media_type}")
        for record in model.objects.filter(history_id__in=history_ids):
            records[(media_type, record.history_id)] = record
    return records


def _resolve_display_items(records):
    """Resolve the item shown per record, keyed by ``(media_type, object_id)``.

    Episodes are displayed with their parent season's item (poster and link),
    since episodes have no standalone detail page.
    """
    ids_by_type = defaultdict(set)
    for (media_type, _history_id), record in records.items():
        ids_by_type[media_type].add(record.id)

    display = {}
    for media_type, object_ids in ids_by_type.items():
        model = apps.get_model(app_label="app", model_name=media_type)
        if media_type == MediaTypes.EPISODE.value:
            queryset = model.objects.filter(id__in=object_ids).select_related(
                "item",
                "related_season__item",
            )
            for obj in queryset:
                display[(media_type, obj.id)] = {
                    "item": obj.related_season.item,
                    "episode_number": obj.item.episode_number,
                }
        else:
            queryset = model.objects.filter(id__in=object_ids).select_related("item")
            for obj in queryset:
                display[(media_type, obj.id)] = {"item": obj.item}
    return display


def _fetch_prev_records(records):
    """Map each non-episode record to its predecessor, batched per media type.

    Mirrors django-simple-history's ``prev_record`` (the newest row for the same
    object with a strictly earlier ``history_date``) but fetches every involved
    object's history in one query per media type instead of one query per row.
    Episodes are excluded because their journal entries never diff against a
    predecessor.
    """
    ids_by_type = defaultdict(set)
    for (media_type, _history_id), record in records.items():
        if media_type != MediaTypes.EPISODE.value:
            ids_by_type[media_type].add(record.id)

    prev = {}
    for media_type, object_ids in ids_by_type.items():
        model = apps.get_model(app_label="app", model_name=f"historical{media_type}")
        by_object = defaultdict(list)
        history = model.objects.filter(id__in=object_ids).order_by(
            "history_date",
            "history_id",
        )
        for row in history:
            by_object[row.id].append(row)

        for rows in by_object.values():
            # rows ascend by (history_date, history_id); a row's predecessor is
            # the newest earlier row whose history_date is strictly less.
            for i, row in enumerate(rows):
                for candidate in reversed(rows[:i]):
                    if candidate.history_date < row.history_date:
                        prev[(media_type, row.history_id)] = candidate
                        break
    return prev


def _episode_changes(record, info):
    """Build a change description for an episode history record.

    A newly-watched episode is a completion of that episode (it receives an
    ``end_date``), so it is tagged ``end_date`` to earn the completed accent;
    later edits are treated as progress. Notes added with the watch follow as
    their own change, like they do for other media.
    """
    episode_number = info["episode_number"]
    if record.history_type == "+":
        description = f"Watched episode {episode_number}"
        changes = [{"description": description, "field": "end_date"}]
        if record.notes:
            changes.append(
                {
                    "description": format_description("notes", None, record.notes),
                    "field": "notes",
                },
            )
        return changes
    description = f"Updated episode {episode_number}"
    return [{"description": description, "field": "progress"}]


def process_history_entries(history_records, media_type, media_entry_number, user):
    """Process all history records into timeline entries."""
    timeline_entries = []
    last = history_records.first()

    for _ in range(history_records.count()):
        entry = process_history_entry((last, last.prev_record), media_type, user)
        if entry["changes"]:
            entry["media_entry_number"] = media_entry_number
            timeline_entries.append(entry)
        last = last.prev_record

    return timeline_entries


def process_history_entry(entry, media_type, user):
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
            user,
        )
    return process_creation_entry(new_record, media_type, processed_entry, user)


def process_changed_entry(new_record, old_record, media_type, processed_entry, user):
    """Process an entry representing a change to existing media."""
    delta = new_record.diff_against(old_record)
    changes = organize_changes(delta.changes, media_type, user)
    apply_date_status_integration(changes, user)
    build_changes_list(changes, processed_entry)
    return processed_entry


def process_creation_entry(new_record, media_type, processed_entry, user):
    """Process an entry representing media creation."""
    history_model = apps.get_model(
        app_label="app",
        model_name=f"historical{media_type}",
    )

    changes = collect_creation_changes(new_record, history_model, media_type, user)
    apply_date_status_integration(changes, user)
    build_changes_list(changes, processed_entry)
    return processed_entry


def organize_changes(changes, media_type, user):
    """Organize changes into categories."""
    organized = {
        "date_changes": {"start_date": None, "end_date": None},
        "status_change": None,
        "other_changes": [],
    }

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
                user,
            ),
            "field": change.field,
            "old": change.old,
            "new": change.new,
        }

        if change.field == "status":
            organized["status_change"] = change_data
        elif change.field == "end_date":
            end_date_change = change_data
        elif change.field in organized["date_changes"]:
            organized["date_changes"][change.field] = change_data
        else:
            organized["other_changes"].append(change_data)

    if end_date_change:
        organized["date_changes"]["end_date"] = end_date_change

    return organized


def collect_creation_changes(new_record, history_model, media_type, user):
    """Collect changes for a creation entry."""
    organized = {
        "date_changes": {"start_date": None, "end_date": None},
        "status_change": None,
        "other_changes": [],
    }

    for field in history_model._meta.get_fields():
        if (
            field.name.startswith("history_")
            or field.name == "id"
            or not hasattr(new_record, field.attname)
            or (field.name == "progress" and media_type == MediaTypes.MOVIE.value)
        ):
            continue

        value = getattr(new_record, field.attname, None)
        if not value and not (
            media_type == MediaTypes.EPISODE.value and field.name == "end_date"
        ):
            continue

        change_data = {
            "field": field.name,
            "new": value,
            "description": format_description(
                field.name,
                None,
                value,
                media_type,
                user,
            ),
        }

        if field.name == "status":
            organized["status_change"] = change_data
        elif field.name in organized["date_changes"]:
            organized["date_changes"][field.name] = change_data
        elif field.name not in ["item", "user", "related_tv"]:
            organized["other_changes"].append(change_data)

    return organized


def apply_date_status_integration(changes, user):
    """Integrate status changes with date changes where appropriate."""
    date_changes = changes["date_changes"]
    status_change = changes["status_change"]

    # Process start date with status
    if (
        date_changes["start_date"]
        and status_change
        and status_change["new"] == Status.IN_PROGRESS.value
    ):
        date_changes["start_date"]["description"] = pgettext(
            "history",
            "Started on %(date)s",
        ) % {"date": app_tags.datetime_format(date_changes["start_date"]["new"], user)}
        changes["status_change"] = None

    # Process end date with status
    if (
        date_changes["end_date"]
        and status_change
        and status_change["new"] == Status.COMPLETED.value
    ):
        date_changes["end_date"]["description"] = pgettext(
            "history",
            "Finished on %(date)s",
        ) % {"date": app_tags.datetime_format(date_changes["end_date"]["new"], user)}
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


def format_description(field_name, old_value, new_value, media_type=None, user=None):  # noqa: C901, PLR0911, PLR0912
    """Format change description in a human-readable way.

    Provides natural language descriptions for various types of changes,
    taking into account the media type and status transitions.
    """
    if field_name in {"start_date", "end_date"}:
        new_value = app_tags.datetime_format(new_value, user)
        old_value = app_tags.datetime_format(old_value, user)

    activity = config.get_activity(media_type) if media_type else None

    # If old_value is None, treat it as an initial setting
    if old_value is None:
        if field_name == "status":
            if new_value == Status.DROPPED.value:
                return pgettext("history", "Marked as dropped")
            if new_value in INITIAL_STATUS_DESCRIPTIONS:
                return str(INITIAL_STATUS_DESCRIPTIONS[new_value][activity])

        if field_name == "score":
            return pgettext("history", "Rated %(score)s/10") % {"score": new_value}

        if field_name == "progress" and media_type:
            if media_type == MediaTypes.GAME.value:
                return pgettext("history", "Played for %(time)s") % {
                    "time": helpers.minutes_to_hhmm(new_value),
                }
            return INITIAL_PROGRESS_DESCRIPTIONS[activity] % {
                "unit": config.get_translated_unit(media_type).lower(),
                "number": new_value,
            }

        if field_name == "start_date":
            if new_value:
                return pgettext("history", "Started on %(date)s") % {"date": new_value}
            return pgettext("history", "Started without date")

        if field_name == "end_date":
            if new_value:
                return pgettext("history", "Finished on %(date)s") % {"date": new_value}
            return pgettext("history", "Finished without date")

        if field_name == "notes":
            return pgettext("history", "Added notes")

        # Translators: %(field)s is the name of a tracked field, e.g. "repeats".
        return pgettext("history", "Set %(field)s to %(value)s") % {
            "field": field_name.replace("_", " ").lower(),
            "value": new_value,
        }

    # Regular change (old_value to new_value)
    if field_name == "status":
        transition = STATUS_TRANSITION_DESCRIPTIONS.get((old_value, new_value))
        if transition:
            return str(transition[activity])
        return pgettext("history", "Changed status from %(old)s to %(new)s") % {
            "old": _status_label(old_value),
            "new": _status_label(new_value),
        }

    if field_name == "score":
        if old_value == 0:
            return pgettext("history", "Rated %(score)s/10") % {"score": new_value}
        return pgettext("history", "Changed rating from %(old)s to %(new)s") % {
            "old": old_value,
            "new": new_value,
        }

    if field_name == "progress":
        diff = new_value - old_value

        if media_type == MediaTypes.GAME.value:
            time = helpers.minutes_to_hhmm(abs(diff))
            if diff > 0:
                return pgettext("history", "Added %(time)s of playtime") % {
                    "time": time,
                }
            return pgettext("history", "Removed %(time)s of playtime") % {
                "time": time,
            }

        unit = config.get_translated_unit(media_type, plural=new_value != 1)
        # Translators: %(unit)s is a progress unit such as "episodes" or "pages".
        return pgettext("history", "Progress set to %(number)s %(unit)s") % {
            "number": new_value,
            "unit": unit.lower(),
        }

    if field_name == "start_date":
        if not new_value:
            return pgettext("history", "Removed start date")
        if not old_value:
            return pgettext("history", "Started on %(date)s") % {"date": new_value}
        return pgettext("history", "Start date changed to %(date)s") % {
            "date": new_value,
        }

    if field_name == "end_date":
        if not new_value:
            return pgettext("history", "Removed end date")
        if not old_value:
            return pgettext("history", "Ended on %(date)s") % {"date": new_value}
        return pgettext("history", "End date changed to %(date)s") % {
            "date": new_value,
        }

    if field_name == "notes":
        if not old_value:
            return pgettext("history", "Added notes")
        if not new_value:
            return pgettext("history", "Removed notes")
        return pgettext("history", "Updated notes")

    # Translators: %(field)s is the name of a tracked field, e.g. "repeats".
    return pgettext("history", "Updated %(field)s from %(old)s to %(new)s") % {
        "field": field_name.replace("_", " ").lower(),
        "old": old_value,
        "new": new_value,
    }
