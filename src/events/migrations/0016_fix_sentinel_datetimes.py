from datetime import UTC, datetime

from django.db import migrations

# Both placeholder datetimes used to sit on the bounds of the datetime range:
# content with an unknown release date was stamped 9999-12-31 23:59:59 (one
# second short of datetime.max) and content of unknown age was stamped
# datetime.min. Converting the first to a timezone ahead of UTC, or the second
# to a timezone behind it, raises OverflowError, which 500s the admin change
# form and anything else that localizes the value (issue #884). Move both to
# the sentinel time of day so the conversion stays in range.

MAX_RELEASE_DATETIME = datetime(9999, 12, 31, 11, 59, 59, 999999, tzinfo=UTC)
MIN_RELEASE_DATETIME = datetime(1, 1, 1, 11, 59, 59, 999999, tzinfo=UTC)


def fix_sentinel_datetimes(apps, schema_editor):
    """Pull both out-of-range placeholders back to the sentinel time of day."""
    event_model = apps.get_model("events", "Event")

    event_model.objects.filter(datetime__gt=MAX_RELEASE_DATETIME).update(
        datetime=MAX_RELEASE_DATETIME,
    )
    event_model.objects.filter(datetime__lt=MIN_RELEASE_DATETIME).update(
        datetime=MIN_RELEASE_DATETIME,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0015_normalize_unknown_date_events"),
    ]

    operations = [
        migrations.RunPython(
            fix_sentinel_datetimes,
            migrations.RunPython.noop,
        ),
    ]
