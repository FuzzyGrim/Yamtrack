from django.db import migrations

from events.migrations._sentinel_bounds import clamp_out_of_range_datetimes

# Both placeholder datetimes used to sit on the bounds of the datetime range:
# content with an unknown release date was stamped 9999-12-31 23:59:59 (one
# second short of datetime.max) and content of unknown age was stamped
# datetime.min. Converting the first to a timezone ahead of UTC, or the second
# to a timezone behind it, raises OverflowError, which 500s the admin change
# form and anything else that localizes the value (issue #884). Move both to
# the sentinel time of day so the conversion stays in range.
#
# Migration 0015 now clamps as well, so this is a no-op for installs that
# applied 0015 after that fix, and still repairs installs that ran 0015 before.


def fix_sentinel_datetimes(apps, schema_editor):
    """Pull both out-of-range placeholders back to the sentinel time of day."""
    clamp_out_of_range_datetimes(apps.get_model("events", "Event"))


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
