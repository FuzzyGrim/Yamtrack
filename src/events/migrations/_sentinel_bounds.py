"""Shared helper for pulling out-of-range event placeholders back in range.

Legacy placeholders sat on the bounds of the datetime range: unknown release
dates were stamped 9999-12-31 23:59:59 and content of unknown age was stamped
datetime.min. Both are unsafe to read back:

* localizing them to a timezone ahead of (or behind) UTC raises
  ``OverflowError`` (issue #884);
* ``datetime.min`` written while ``TIME_ZONE`` was ahead of UTC was stored as a
  UTC value in year 0, which PostgreSQL happily keeps but psycopg refuses to
  decode with ``DataError: timestamp too small (before year 1)`` (issue #1662).

Clamping is done with ``UPDATE`` statements so no undecodable row is ever
fetched into Python.
"""

from datetime import UTC, datetime

MAX_RELEASE_DATETIME = datetime(9999, 12, 31, 11, 59, 59, 999999, tzinfo=UTC)
MIN_RELEASE_DATETIME = datetime(1, 1, 1, 11, 59, 59, 999999, tzinfo=UTC)


def clamp_out_of_range_datetimes(event_model):
    """Pull both out-of-range placeholders back to the sentinel time of day."""
    event_model.objects.filter(datetime__gt=MAX_RELEASE_DATETIME).update(
        datetime=MAX_RELEASE_DATETIME,
    )
    event_model.objects.filter(datetime__lt=MIN_RELEASE_DATETIME).update(
        datetime=MIN_RELEASE_DATETIME,
    )
