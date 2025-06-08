class CalendarTriggerMixin:
    """Mixin to handle calendar trigger disabling functionality."""

    _disable_calendar_triggers = False  # Instance-level flag


def disable_fetch_releases():
    """Context manager to disable fetching releases task.

    Applies for models using CalendarTriggerMixin.
    """
    return _DisableCalendarTriggers()


class _DisableCalendarTriggers:
    """Context manager for disabling calendar triggers during bulk operations."""

    def __enter__(self):
        """Disable calendar triggers for Item model."""
        from app.models import Item

        self.original_value = Item._disable_calendar_triggers
        Item._disable_calendar_triggers = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Restore calendar triggers."""
        from app.models import Item

        Item._disable_calendar_triggers = self.original_value
