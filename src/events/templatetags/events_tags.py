import datetime

from django import template
from django.utils import formats
from django.utils.text import capfirst

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary using the key."""
    return dictionary.get(key, [])


def _month_before_day(date_format):
    """Return whether a date format string puts the month before the day."""

    def first_index(chars):
        return next(
            (index for index, char in enumerate(date_format) if char in chars),
            len(date_format),
        )

    return first_index("mMnbEFN") < first_index("dj")


@register.simple_tag
def calendar_day_label(day, month, year, user):
    """Return a translated day heading such as "Monday, November 2".

    The weekday and month names follow the active language, and the order of
    day and month follows the user's preferred date format.
    """
    date_obj = datetime.date(int(year), int(month), int(day))
    label_format = "l, F j" if _month_before_day(user.date_format) else "l j F"
    return capfirst(formats.date_format(date_obj, label_format))
