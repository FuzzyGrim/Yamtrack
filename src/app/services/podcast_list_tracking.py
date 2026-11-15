"""Resolve podcast show trackers for existing list items without creating items."""

from django.urls import reverse

from app.models import PodcastShowTracker
from app.models.choices import MediaTypes


def podcast_show_trackers_for_items(user, items):
    """Return show trackers keyed by item id, scoped to the requested user."""
    items = [item for item in items if item.media_type == MediaTypes.PODCAST.value]
    if not items:
        return {}
    trackers = PodcastShowTracker.objects.filter(
        user=user, show__podcast_uuid__in=[item.media_id for item in items]
    ).select_related("show")
    by_identity = {(row.show.podcast_uuid, row.show.source): row for row in trackers}
    result = {}
    for item in items:
        tracker = by_identity.get((item.media_id, item.source))
        if tracker is not None:
            tracker.item = item
            tracker.item_id = item.pk
            tracker.formatted_score = tracker.score
            tracker.score_url = reverse("update_podcast_show_score", args=[tracker.pk])
            result[item.pk] = tracker
    return result
