from datetime import UTC

from django.conf import settings
from django.db import migrations
from django.db.models import Q

# Migration 0062 originally truncated start_date/end_date with TruncMinute()
# without a tzinfo, so the backend localized every value and wrote the naive
# local wall time straight back into the column, which Django reads as UTC
# again. Every tracked date on a deployment with a non-UTC TZ therefore moved
# by its UTC offset.
#
# 0062 used .update(), which writes no history rows, so the newest historical
# record for an untouched row still holds the original value. That gives a
# per-row test: replay the shift on the historical value and, when the result
# matches what is stored, restore the value 0062 should have written. A row
# edited after the upgrade has a matching history record instead, so it fails
# the test and is left alone.

BATCH_SIZE = 2000

START_AND_END_DATE_MODELS = [
    "Movie",
    "Anime",
    "Manga",
    "Game",
    "Book",
    "Comic",
    "BoardGame",
    "BasicMedia",
]
START_AND_END_DATE_FIELDS = ("start_date", "end_date")


def shifted_value(original, tz):
    """Replay what the buggy 0062 wrote for an original UTC value."""
    try:
        local = original.astimezone(tz)
    except (OverflowError, ValueError):
        # 0062 would have crashed on this row instead of shifting it.
        return None
    return local.replace(tzinfo=UTC, second=0, microsecond=0)


def latest_history(history_model, ids, fields):
    """Map each id to the field values of its newest historical record."""
    records = (
        history_model.objects.filter(id__in=ids)
        .order_by("id", "-history_date", "-history_id")
        .values_list("id", *fields)
        .iterator()
    )

    latest = {}
    for record in records:
        latest.setdefault(record[0], record[1:])
    return latest


def restore_model_dates(apps, model_name, fields, tz):
    """Undo the timezone shift on every row of a single model."""
    model = apps.get_model("app", model_name)
    history_model = apps.get_model("app", f"Historical{model_name}")

    tracked = Q()
    for field in fields:
        tracked |= Q(**{f"{field}__isnull": False})

    ids = list(
        model.objects.filter(tracked).order_by("pk").values_list("pk", flat=True),
    )

    for offset in range(0, len(ids), BATCH_SIZE):
        batch = ids[offset : offset + BATCH_SIZE]
        originals = latest_history(history_model, batch, fields)

        updates = []
        for media in model.objects.filter(pk__in=batch):
            original_values = originals.get(media.pk)
            if original_values is None:
                # No history to compare against, so the shift is undetectable.
                continue

            restored = False
            for field, original in zip(fields, original_values, strict=True):
                if original is None:
                    continue

                stored = getattr(media, field)
                expected = original.replace(second=0, microsecond=0)
                if stored == expected or stored != shifted_value(original, tz):
                    continue

                setattr(media, field, expected)
                restored = True

            if restored:
                updates.append(media)

        if updates:
            model.objects.bulk_update(updates, fields)


def restore_shifted_dates(apps, _schema_editor):
    """Pull every date 0062 shifted back onto its original instant."""
    tz = settings.TZ

    for model_name in START_AND_END_DATE_MODELS:
        restore_model_dates(apps, model_name, START_AND_END_DATE_FIELDS, tz)

    restore_model_dates(apps, "Episode", ("end_date",), tz)


class Migration(migrations.Migration):
    """Repair start_date/end_date values shifted by the original migration 0062."""

    dependencies = [
        ("app", "0063_season_image_fallback_to_tv"),
    ]

    operations = [
        migrations.RunPython(
            restore_shifted_dates,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
