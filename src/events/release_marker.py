"""One change token for stored release dates (``Event`` rows).

Release dates are shared by every user tracking an item, so one token covers
them all. Every path that creates, moves or deletes an ``Event`` calls
``bump()``; the calendar link's validator reads ``current()``. Bumping only
on a real change keeps the hourly calendar reload from invalidating every
feed when nothing moved.
"""

from django.db import transaction

from app import feed_validation

_KEY = "calendar_release_dates_marker"


def current() -> str:
    """Return the current token."""
    return feed_validation.shared_marker(_KEY)


def bump() -> None:
    """Record a change now and again on commit.

    A concurrent reader can render the rows from before an open write; the
    second bump makes sure what it stored is never matched again.
    """
    feed_validation.bump_shared_marker(_KEY)
    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(lambda: feed_validation.bump_shared_marker(_KEY))
