from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import migrations
from django.utils import timezone

from app.models import MediaTypes
from events.models import SentinelDatetime

# TV episodes without a known air date used to be stamped with datetime.min
# (year 1), which counted as "already released" and inflated dashboard progress
# (issue #884). They are now stamped with a far-future sentinel instead. This
# migration rewrites the legacy placeholders for season events only, applying
# the same "a later episode already aired -> assume this one aired too"
# heuristic used at sync time. Anime and other media keep the legacy behaviour.

LEGACY_THRESHOLD = datetime(1900, 1, 1, tzinfo=ZoneInfo("UTC"))


def normalize_unknown_date_events(apps, schema_editor):
    event_model = apps.get_model("events", "Event")
    now = timezone.now()

    item_ids = set(
        event_model.objects.filter(
            datetime__lt=LEGACY_THRESHOLD,
            item__media_type=MediaTypes.SEASON.value,
        ).values_list("item_id", flat=True),
    )

    to_update = []
    for item_id in item_ids:
        events = list(event_model.objects.filter(item_id=item_id))
        aired = {
            event.content_number: event.datetime
            for event in events
            if event.datetime >= LEGACY_THRESHOLD
            and event.datetime <= now
            and event.content_number is not None
        }

        for event in events:
            if event.datetime >= LEGACY_THRESHOLD:
                continue

            later_aired = [
                value
                for number, value in aired.items()
                if event.content_number is not None and number > event.content_number
            ]
            event.datetime = (
                min(later_aired) if later_aired else SentinelDatetime.max_datetime()
            )
            to_update.append(event)

    if to_update:
        event_model.objects.bulk_update(to_update, ["datetime"])


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0014_delete_empty_content_number_comic_events"),
    ]

    operations = [
        migrations.RunPython(
            normalize_unknown_date_events,
            migrations.RunPython.noop,
        ),
    ]
