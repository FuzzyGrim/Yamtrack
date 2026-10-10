from datetime import datetime
from zoneinfo import ZoneInfo

from events.models import SentinelDatetime


def date_parser(date_str):
    """Parse string in %Y-%m-%d to datetime. Raises ValueError if invalid."""
    year_only_parts = 1
    year_month_parts = 2
    default_month_day = "-01-01"
    default_day = "-01"
    parts = date_str.split("-")
    if len(parts) == year_only_parts:
        date_str += default_month_day
    elif len(parts) == year_month_parts:
        date_str += default_day

    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ZoneInfo("UTC"))
    return dt.replace(
        hour=SentinelDatetime.HOUR,
        minute=SentinelDatetime.MINUTE,
        second=SentinelDatetime.SECOND,
        microsecond=SentinelDatetime.MICROSECOND,
        tzinfo=ZoneInfo("UTC"),
    )


def unknown_release_datetime():
    """Return the far-future sentinel datetime for unknown release dates.

    Content stamped with this datetime is treated as unreleased everywhere:
    it is excluded from released-progress counts and recognized by
    ``Event.is_max_datetime`` so it never renders a bogus air date.
    """
    return SentinelDatetime.max_datetime()


def resolve_episode_datetimes(episode_datetimes, current_datetime):
    """Resolve unknown episode air datetimes for a single season.

    ``episode_datetimes`` maps episode numbers to their known air datetime, or
    ``None`` when the provider has no date. Episodes without a date become the
    far-future sentinel so they are treated as unreleased, except when a later
    episode in the same season has already aired -- then the undated episode is
    assumed to have aired too and inherits the nearest later air datetime (see
    issue #884).
    """
    aired_datetimes = {
        number: value
        for number, value in episode_datetimes.items()
        if value is not None and value <= current_datetime
    }
    latest_aired_number = max(aired_datetimes, default=None)

    resolved = {}
    for number, value in episode_datetimes.items():
        if value is not None:
            resolved[number] = value
        elif latest_aired_number is not None and number < latest_aired_number:
            resolved[number] = min(
                aired_value
                for aired_number, aired_value in aired_datetimes.items()
                if aired_number > number
            )
        else:
            resolved[number] = unknown_release_datetime()
    return resolved
