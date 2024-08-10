from app.models import MEDIA_TYPES, Item
from django.db import models
from django.db.models import Q


class EventManager(models.Manager):
    """Custom manager for the Event model."""

    def user_events(self, user):
        """Get all upcoming media events for the specified user within the next week."""
        media_types_with_user = [media for media in MEDIA_TYPES if media != "episode"]

        query = Q()
        for media_type in media_types_with_user:
            query |= Q(**{f"item__{media_type}__user": user})

        return self.filter(
            query,
        )


class Event(models.Model):
    """Calendar event model."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    episode_number = models.IntegerField(null=True)
    date = models.DateField()
    objects = EventManager()

    class Meta:
        """Meta class for Event model."""

        ordering = ["date"]

    def __str__(self):
        """Return event title."""
        if self.item.media_type == "season":
            return f"{self.item.__str__()}E{self.episode_number}"
        if self.episode_number:
            return f"{self.item.__str__()} - Ep. {self.episode_number}"
        return self.item.__str__()
