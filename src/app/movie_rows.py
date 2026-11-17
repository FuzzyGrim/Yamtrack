"""Movie tracker rows as library ranking reads them, without models.

Ordering a movie library by runtime or time watched needs, per movie, the
item's runtime and how many times it was watched. Building a ``Movie`` model
for every row (with its field tracker and a second copy of its item) cost
about a second on a library of 2,000 movies. ``movie_rows`` reads the same
columns as value rows and wraps them in ``MovieRow``, whose answers are the
``Media`` members of the same name.

The displayed row is the newest by creation, the higher id on a tie, as the
library-query scan keeps it. With more than one row (repeat viewings),
``aggregated_progress`` counts the rows that record a viewing, as
``MediaManager._aggregate_item_data`` does for movies.
"""

from app.models import Media, Movie, Status


class MovieRow:
    """One movie tracker row, with its repeat viewings counted."""

    __slots__ = (
        "_total_runtime_minutes_cache",
        "aggregated_progress",
        "created_at",
        "end_date",
        "item",
        "max_progress",
        "pk",
        "progress",
        "status",
    )

    def __init__(self, item, rows):
        """Wrap the user's rows of one movie (value dicts, at least one)."""
        newest = max(rows, key=lambda row: (row["created_at"], row["pk"]))
        self.item = item
        self.pk = newest["pk"]
        self.status = newest["status"]
        self.progress = newest["progress"]
        self.end_date = newest["end_date"]
        self.created_at = newest["created_at"]
        if len(rows) > 1:
            self.aggregated_progress = sum(
                1 for row in rows if row["end_date"] or row["status"] == Status.COMPLETED.value
            )

    @property
    def item_id(self):
        """Return the tracked item's id."""
        return self.item.pk

    total_runtime_minutes = Media.total_runtime_minutes
    average_runtime_minutes = Media.average_runtime_minutes
    time_watched_minutes = Media.time_watched_minutes
    _get_known_item_runtime_minutes = Media._get_known_item_runtime_minutes
    _plays_sort_value = Media._plays_sort_value


def movie_rows(user, items):
    """Return ``{item_id: MovieRow}`` for the items the user tracks as movies.

    One value query whatever the number of items, and no model.
    """
    by_id = {item.pk: item for item in items}
    grouped = {}
    for row in (
        Movie.objects.filter(user=user, item_id__in=by_id)
        .order_by()
        .values("pk", "item_id", "status", "progress", "end_date", "created_at")
    ):
        grouped.setdefault(row["item_id"], []).append(row)
    return {item_id: MovieRow(by_id[item_id], rows) for item_id, rows in grouped.items()}
